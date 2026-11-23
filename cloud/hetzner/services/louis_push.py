#!/usr/bin/env python3
"""louis_push.py — Notificaciones push (Web Push) para la app móvil de Louis.

Sin dependencias raras: solo `cryptography` (ya presente en Ubuntu) + urllib.
  • VAPID (RFC 8292): JWT ES256 con una llave EC P-256 que se genera una vez y se guarda en
    PUSH_STATE_DIR/push_vapid.json (0600).
  • Cifrado de payload aes128gcm (RFC 8291), validado contra el vector del RFC en tests.
  • Suscripciones en PUSH_STATE_DIR/push_subs.json. Las que el servicio de push reporta
    como expiradas (404/410) se eliminan solas.

Uso desde otros módulos (nunca lanza; si no hay suscripciones, no hace nada):
    import louis_push; louis_push.notify_async("Título", "Cuerpo", tab="hoy")
"""
import os
import json
import time
import base64
import hashlib
import logging
import re
import uuid
import fcntl
import threading
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger("louis-push")

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
VAPID_FILE = STATE_DIR / "push_vapid.json"
SUBS_FILE = STATE_DIR / "push_subs.json"
VAPID_SUB = os.environ.get("VAPID_SUBJECT") or "https://" + (os.environ.get("LOUIS_DOMAIN", "") or "louis.kawiil.mx").strip().strip('"\'')
AGENT_NAME = os.environ.get("AGENT_NAME", "Donna").strip() or "Donna"
MAX_SUBS = 10
_LOCK = threading.Lock()

# Solo se acepta enviar a los servicios de push de los navegadores (evita SSRF si alguien
# registrara un endpoint arbitrario).
_ALLOWED_SUFFIXES = (
    ".googleapis.com", ".push.services.mozilla.com", ".push.apple.com",
    ".notify.windows.com", ".push.microsoft.com",
)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def endpoint_ok(endpoint: str) -> bool:
    try:
        u = urlparse(endpoint)
        h = (u.hostname or "").lower()
        return u.scheme == "https" and any(h == s.lstrip(".") or h.endswith(s) for s in _ALLOWED_SUFFIXES)
    except Exception:
        return False


# ---------------------------------------------------------------- llaves VAPID
def _load_vapid():
    with _LOCK:
        if VAPID_FILE.exists():
            d = json.loads(VAPID_FILE.read_text())
            priv = serialization.load_pem_private_key(d["pem"].encode(), password=None)
        else:
            priv = ec.generate_private_key(ec.SECP256R1())
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
            tmp = VAPID_FILE.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"pem": pem, "creada": int(time.time())}, f)
            os.replace(tmp, VAPID_FILE)
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return priv, _b64u(pub)


def public_key() -> str:
    """Llave pública VAPID (base64url) que el navegador usa como applicationServerKey."""
    return _load_vapid()[1]


def _vapid_headers(endpoint: str) -> dict:
    priv, pub = _load_vapid()
    u = urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": VAPID_SUB}
    signing = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." + \
        _b64u(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(priv.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing + "." + _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={jwt}, k={pub}"}


# ---------------------------------------------------------------- cifrado RFC 8291
def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, _as_priv: bytes = None, _salt: bytes = None) -> bytes:
    """Cuerpo aes128gcm listo para POST. `_as_priv`/`_salt` solo para el test con el vector del RFC."""
    ua_pub = _unb64u(p256dh_b64)
    auth = _unb64u(auth_b64)
    as_priv = ec.derive_private_key(int.from_bytes(_as_priv, "big"), ec.SECP256R1()) if _as_priv \
        else ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ecdh = as_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    prk = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(ecdh)
    salt = _salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(prk)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = delimitador del último registro
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + ct


# ---------------------------------------------------------------- suscripciones
def _read_subs() -> list:
    try:
        return json.loads(SUBS_FILE.read_text())
    except Exception:
        return []


def _write_subs(subs: list) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SUBS_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(subs, f)
    os.replace(tmp, SUBS_FILE)


def add_sub(sub: dict, label: str = "") -> bool:
    ep = (sub or {}).get("endpoint", "")
    k = (sub or {}).get("keys") or {}
    if not endpoint_ok(ep) or not k.get("p256dh") or not k.get("auth"):
        return False
    try:  # valida que las llaves sean decodificables antes de guardarlas
        _unb64u(k["auth"]); ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64u(k["p256dh"]))
    except Exception:
        return False
    with _LOCK:
        subs = [s for s in _read_subs() if s.get("endpoint") != ep]
        subs.append({"endpoint": ep, "keys": {"p256dh": k["p256dh"], "auth": k["auth"]},
                     "label": (label or "")[:60], "alta": int(time.time())})
        _write_subs(subs[-MAX_SUBS:])
    return True


def remove_sub(endpoint: str) -> None:
    with _LOCK:
        _write_subs([s for s in _read_subs() if s.get("endpoint") != endpoint])


def count() -> int:
    return len(_read_subs())



# ---------------------------------------------------------------- bandeja de notificaciones
# Cada aviso (push, copia de Telegram, recordatorio, briefing…) se guarda aquí para poder verlo
# en la app aunque la notificación se haya descartado. Algunos avisos traen BOTONES de confirmación
# (cerrar los pendientes sugeridos, agregar las altas de Slack): se detectan por las frases fijas
# con que el propio código invita a responder. Las acciones se ejecutan en el gateway, con lista cerrada.
INBOX_MAX = 200          # la bandeja vive en la base de datos (donna_db.py); este es el tope de lo que se lista

ACTION_RULES = [
    (re.compile(r"cierra todos los sugeridos", re.I),
     {"id": "steward_cerrar_sugeridos", "label": "Cerrar los sugeridos",
      "confirm": "¿Cerrar todos los pendientes que propuse? Se mueven a AGENDA-ARCHIVO.md y se respalda la AGENDA."}),
    (re.compile(r"agrega todas las altas", re.I),
     {"id": "altas_todas", "label": "Agregar todas las altas",
      "confirm": "¿Agregar a tu AGENDA todas las altas que propuse del seguimiento de Slack?"}),
]


def actions_for(text: str) -> list:
    return [dict(a) for rx, a in ACTION_RULES if rx.search(text or "")]


def tg_md_to_app(text: str) -> str:
    """Los textos de Telegram usan *negrita* (un asterisco); la app usa Markdown estándar (**negrita**)."""
    return re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"**\1**", text or "")


def inbox_add(title: str, body: str, tab: str = "chat", actions: list = None, kind: str = "", link: str = "") -> dict:
    import donna_db
    return donna_db.notif_add(title, body, tab, actions, kind, link)


def inbox_list(limit: int = 50) -> list:
    import donna_db
    return donna_db.notif_list(limit)


def unread_count() -> int:
    import donna_db
    return donna_db.notif_sin_leer()


def pending_confirmations() -> int:
    """Avisos con un botón de confirmación que aún no se ha usado."""
    import donna_db
    return donna_db.notif_pendientes_accion()


def inbox_mark_read(ids=None) -> int:
    """ids=None → todas. Devuelve cuántas cambió."""
    import donna_db
    return donna_db.notif_marcar_leidas(None if ids is None else list(ids))


def inbox_get(nid: str):
    import donna_db
    return donna_db.notif_get(nid)


def inbox_set_done(nid: str, action_id: str, result: str) -> bool:
    import donna_db
    return donna_db.notif_set_done(nid, action_id, result)


def resurgir_pospuestas() -> int:
    """Las notificaciones pospuestas a las que ya les tocó vuelven a sonar (misma entrada de la bandeja, sin duplicarla)."""
    import donna_db
    n = 0
    for r in donna_db.notif_pospuestas_vencidas():
        notify("⏰ " + r["title"], (r["body"] or "")[:200], tab="notifs", kind=r["kind"], link=r["link"], existing=r)
        n += 1
    return n


# ---------------------------------------------------------------- envío
def _send_one(sub: dict, payload: bytes, ttl: int, urgency: str, topic: str = "") -> int:
    body = encrypt(payload, sub["keys"]["p256dh"], sub["keys"]["auth"])
    h = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
         "TTL": str(ttl), "Urgency": urgency, **_vapid_headers(sub["endpoint"])}
    if topic:
        h["Topic"] = topic
    req = urllib.request.Request(sub["endpoint"], data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def notify(title: str, body: str = "", tab: str = "chat", tag: str = "", ttl: int = 3600,
           urgency: str = "normal", actions: list = None, full_body: str = None, kind: str = "", link: str = "", acts: list = None,
           existing: dict = None) -> int:
    """Guarda el aviso en la bandeja y lo envía a los teléfonos suscritos. Devuelve cuántos aceptaron.
    Nunca lanza."""
    try:
        try:
            rec = existing if existing else inbox_add(title, full_body if full_body is not None else body, tab, actions, kind, link)
        except Exception:
            log.warning("no pude guardar en la bandeja", exc_info=True)
            rec = {"id": ""}
        subs = _read_subs()
        if not subs:
            return 0
        payload = json.dumps({"title": (title or AGENT_NAME)[:80], "body": (body or "")[:300],
                              "tab": "notifs" if rec.get("id") else tab, "nid": rec.get("id", ""), "link": (link or "")[:80],
                              "kind": (kind or "")[:20], "acts": [{"action": str(x.get("action", ""))[:20], "title": str(x.get("title", ""))[:30]} for x in (acts or [])][:2],
                              "badge": unread_count(),
                              "tag": tag or hashlib.md5((title or "").encode()).hexdigest()[:8],
                              "ts": int(time.time())}, ensure_ascii=False).encode()
        ok, muertas = 0, []
        topic = _b64u(hashlib.sha256(tag.encode()).digest())[:32] if tag else ""
        for s in subs:
            try:
                st = _send_one(s, payload, ttl, urgency, topic)
            except Exception as e:
                log.warning("push falló (%s): %s", s["endpoint"][:40], e)
                continue
            if st in (200, 201, 202):
                ok += 1
            elif st in (404, 410):
                muertas.append(s["endpoint"])
            else:
                log.warning("push rechazado %s por %s", st, s["endpoint"][:40])
        for ep in muertas:
            remove_sub(ep)
        return ok
    except Exception:
        log.exception("notify falló")
        return 0


def notify_async(*a, **kw) -> None:
    """Fire-and-forget: no bloquea al scheduler ni al chat."""
    threading.Thread(target=notify, args=a, kwargs=kw, daemon=True).start()


def notify_text_async(text: str, tab: str = "") -> None:
    """Convierte un mensaje largo (briefing, recordatorio, alerta) en un push: 1ª línea = título,
    resto = cuerpo (recortado). El texto COMPLETO queda en la bandeja de la app, con botones de
    confirmación si el mensaje invita a «cerrar los sugeridos» / «agregar las altas»."""
    clean = re.sub(r"<[^>]+>", "", text or "")
    plain = re.sub(r"[*_`#>]+", "", clean).strip()
    if not plain:
        return
    lines = [l.strip() for l in plain.splitlines() if l.strip()]
    title, body = lines[0][:80], " · ".join(lines[1:])[:280]
    if not tab:
        low = plain[:240].lower()
        tab = "hoy" if any(k in low for k in ("seguimiento", "cierre del día", "pendientes", "vence", "agenda")) else "chat"
    # En la bandeja el título ya se muestra aparte: el cuerpo es el RESTO (sin repetir la 1ª línea).
    raw = clean.splitlines()
    i0 = next((i for i, l in enumerate(raw) if l.strip()), 0)
    resto = tg_md_to_app("\n".join(raw[i0 + 1:]).strip())
    notify_async(title, body, tab=tab, actions=actions_for(clean), full_body=resto, kind="mensaje")


# ---------------------------------------------------------------- interruptor de Telegram
TELEGRAM_OFF_FLAG = STATE_DIR / "telegram_off"


def telegram_muted() -> bool:
    """True si Telegram está apagado para envíos salientes PROACTIVOS (archivo-bandera, sin reiniciar nada).
    Si la bandera existe, está apagado aunque no haya teléfono suscrito: los avisos quedan en la bandeja de la app.
    (Las respuestas del bot a lo que Polo le escribe no pasan por aquí.)"""
    try:
        return TELEGRAM_OFF_FLAG.exists()
    except Exception:
        return False
