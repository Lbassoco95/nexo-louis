#!/usr/bin/env python3
"""
slack_digest.py — Nexo: lectura diaria de Slack (resumen de la tarde + seguimiento de la mañana).

Corre en el servidor, dentro del scheduler:
  • 18:17 CDMX (lun-vie)  run_digest()    resume lo que pasó en Slack desde la última corrida y
                                          guarda las ACCIONES ABIERTAS (A1, A2, …).
  • 07:41 CDMX (lun-vie)  run_followup()  vuelve a leer Slack desde ese resumen, clasifica cada
                                          acción (resuelta / avanzó / sin cambio / sin respuesta),
                                          la cruza con la AGENDA y propone altas.

Qué lee: con SLACK_USER_TOKEN (xoxp-, en slack.env) ve todo lo que ve Polo: canales, grupos y
mensajes directos. Con solo SLACK_BOT_TOKEN (xoxb-) ve únicamente los canales a los que Louis fue
invitado y sus propios DMs; el reporte lo avisa ("cobertura limitada").

Garantías (están en el código, no solo en el prompt):
  • Solo lectura en Slack. Lo único que escribe es UN mensaje (con hilo) al DM de Polo con el bot.
  • No edita la AGENDA: propone altas y Polo las aprueba con «agrega altas 1, 3».
  • Los temas regulatorios/legales (REGULATORY_RE) nunca se dan por resueltos sin una confirmación
    explícita en el texto de la evidencia (acuse, sello, "ya se presentó"…).
  • Los mensajes con contraseñas, tokens o cuentas/CLABE se OMITEN antes de llegar al modelo; solo se
    registra canal y fecha en el hallazgo de seguridad.
"""

import json
import re
import time
import logging
import threading
import unicodedata
from datetime import datetime, timedelta, date
from pathlib import Path

import louis_core as core

log = logging.getLogger("slack_digest")

TZ = core.TZ_CDMX
DIGEST_DIR = core.STATE_DIR / "slack_digests"
SLACK_ENV = core.HOME_OC / "credentials" / "slack.env"
OWNER_ID_DEFAULT = "U06EJ0SRVPY"          # Polo en Slack (para el DM con el bot)

SKIP_CHANNEL_NAMES = {"memes"}
MAX_PAGES = 3                             # páginas de historial por conversación
MAX_THREADS = 60                          # hilos leídos por corrida (cuida el límite de Slack)
CHUNK_CHARS = 45_000                      # tamaño de bloque que se manda al modelo
FALLBACK_HOURS = 26                       # si no hay corrida previa
KEEP_DIGESTS = 30                         # cuántos resúmenes diarios se conservan
TG_CHUNK = 3800

_LOCK = threading.Lock()
_INTENTOS: dict = {}                       # (modo, fecha) → intentos hoy; evita reintentar sin fin

# Contraseñas, tokens, claves, CLABE/cuentas/tarjetas. Si un mensaje coincide NO se manda al modelo.
_CRED_RE = re.compile(
    r"contrase|password|passwd|clave de acceso|\btoken\b|restablec|xox[abprs]-|xapp-|sk-[A-Za-z0-9]{10,}"
    r"|\b[A-Za-z0-9+/=_-]{32,}\b|\b\d{16,18}\b", re.I)
_CONFIRM_RE = re.compile(
    r"acuse|sello|sellad|confirm|ya (se )?(present|ingres|entreg|envi|firm|pag|resolv|atend|cumpli)|"
    r"qued[oó] (listo|hecho|atendid)|recibid|fue (present|ingres|entreg|atendid)", re.I)
_SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
                  "channel_archive", "channel_unarchive", "pinned_item", "unpinned_item", "bot_add",
                  "bot_remove", "group_join", "group_leave"}

_ESTADO_RANK = {"sin_cambio": 0, "avanzo": 1, "resuelta": 2}


# ───────────────────────────── utilidades ─────────────────────────────

def _now() -> datetime:
    return datetime.now(TZ)


def _puede_intentar(modo: str, hoy: date, force: bool) -> bool:
    """Máximo 3 intentos por día y modo (el scheduler reintenta cada minuto si algo truena)."""
    k = (modo, hoy.isoformat())
    if not force and _INTENTOS.get(k, 0) >= 3:
        return False
    _INTENTOS[k] = _INTENTOS.get(k, 0) + 1
    return True


def _tok(s: str) -> set:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    stop = {"para", "como", "pero", "porque", "sobre", "entre", "desde", "hasta", "esta", "este", "estos",
            "estas", "esa", "ese", "una", "uno", "unos", "unas", "los", "las", "del", "con", "sin", "por",
            "que", "hoy", "ayer", "manana", "pendiente", "pendientes", "revisar", "enviar", "seguimiento",
            "falta", "favor", "ver", "hay", "todo", "todos", "ahora", "cliente", "clientes", "oficio"}
    return {t for t in re.findall(r"[a-z0-9]{4,}", s) if t not in stop}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _parse_iso(s) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _dias_habiles(desde: date, hasta: date) -> int:
    """Días hábiles (lun-vie) entre dos fechas, sin contar el de inicio."""
    if not desde or desde >= hasta:
        return 0
    n, d = 0, desde
    while d < hasta:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _fmt_fecha(iso) -> str:
    d = _parse_iso(iso)
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{d.day}-{meses[d.month - 1]}" if d else "s/f"


def _split(text: str, n: int = TG_CHUNK) -> list:
    """Parte un texto largo en bloques de ≤n, cortando en saltos de línea."""
    out, cur = [], ""
    for line in text.splitlines():
        while len(line) > n:
            if cur:
                out.append(cur); cur = ""
            out.append(line[:n]); line = line[n:]
        if len(cur) + len(line) + 1 > n and cur:
            out.append(cur); cur = ""
        cur += (line + "\n")
    if cur.strip():
        out.append(cur)
    return out


# ───────────────────────────── lectura de Slack ─────────────────────────────

def _call(fn, *a, **kw):
    """Llama a la API de Slack; si responde 429 espera Retry-After y reintenta una vez."""
    for intento in (1, 2):
        try:
            return fn(*a, **kw)
        except Exception as e:
            resp = getattr(e, "response", None)
            if intento == 1 and resp is not None and getattr(resp, "status_code", 0) == 429:
                wait = 5
                try:
                    wait = int(resp.headers.get("Retry-After", 5))
                except Exception:
                    pass
                time.sleep(min(wait, 40))
                continue
            raise


def _user_names(client) -> dict:
    """id → (nombre, es_bot). Si falta el permiso users:read devuelve {} y se usan los ids."""
    out, cursor = {}, None
    try:
        for _ in range(10):
            kw = {"limit": 200}
            if cursor:
                kw["cursor"] = cursor
            r = _call(client.users_list, **kw)
            for u in r.get("members", []):
                prof = u.get("profile") or {}
                nombre = u.get("real_name") or prof.get("real_name") or u.get("name") or u["id"]
                out[u["id"]] = (nombre, bool(u.get("is_bot") or u.get("is_app_user") or u["id"] == "USLACKBOT"))
            cursor = (r.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(1.2)
    except Exception as e:
        log.warning(f"slack_digest: no pude listar usuarios: {e}")
    return out


def _label(c: dict, names: dict) -> str:
    if c.get("is_im"):
        return "DM con " + names.get(c.get("user"), (c.get("user", "?"), False))[0]
    if c.get("is_mpim"):
        n = re.sub(r"^mpdm-|-\d+$", "", c.get("name", "grupo")).replace("--", " + ")
        return "Grupo " + n
    return "#" + (c.get("name") or c.get("id", "?"))


def _clean(text: str, names: dict) -> str:
    t = re.sub(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>", lambda m: "@" + names.get(m.group(1), ("alguien", False))[0], text or "")
    t = re.sub(r"<!(?:channel|here|everyone)[^>]*>", "@todos", t)
    t = re.sub(r"<#[A-Z0-9]+\|([^>]*)>", r"#\1", t)
    t = re.sub(r"<https?://[^>|]+\|([^>]+)>", r"\1", t)
    t = re.sub(r"<https?://[^>]+>", "[enlace]", t)
    return re.sub(r"\s+", " ", t).strip()[:700]


def _clients() -> list:
    """[(etiqueta, WebClient)] según los tokens disponibles; el de usuario va primero."""
    from slack_sdk import WebClient
    creds = core.load_env_file(SLACK_ENV)
    out = []
    user = creds.get("SLACK_USER_TOKEN", "")
    bot = creds.get("SLACK_BOT_TOKEN", "")
    if user.startswith("xoxp-"):
        out.append(("usuario", WebClient(token=user)))
    if bot.startswith("xoxb-"):
        out.append(("bot", WebClient(token=bot)))
    return out


def collect(since_ts: float) -> tuple:
    """Lee Slack desde since_ts. Devuelve (mensajes, stats). Cada mensaje:
    {ts, canal, autor, texto}. Nunca devuelve mensajes con credenciales o cuentas bancarias."""
    try:
        clients = _clients()
    except ImportError:
        return [], {"error": "slack_sdk no instalado"}
    if not clients:
        return [], {"error": "sin SLACK_USER_TOKEN ni SLACK_BOT_TOKEN válidos en slack.env"}

    msgs, vistos = [], set()
    stats = {"tokens": [e for e, _ in clients], "convs": 0, "con_mensajes": 0, "vacias": 0, "errores": 0,
             "hilos": 0, "mensajes": 0, "seguridad": [], "limitada": "usuario" not in [e for e, _ in clients]}
    threads_left = MAX_THREADS
    for etiqueta, client in clients:
        names = _user_names(client)
        try:
            convs = core._slack_all_channels(client, "public_channel,private_channel,mpim,im")
        except Exception as e:
            stats["errores"] += 1
            log.warning(f"slack_digest: no pude listar conversaciones ({etiqueta}): {e}")
            continue
        for c in convs:
            cid = c.get("id")
            if not cid or cid in vistos or c.get("is_archived"):
                continue
            if not (c.get("is_im") or c.get("is_mpim")) and not c.get("is_member"):
                continue
            if c.get("is_im") and names.get(c.get("user"), ("", False))[1]:
                continue                                    # DM con app o bot
            nombre = (c.get("name") or "").lower()
            if nombre in SKIP_CHANNEL_NAMES:
                continue
            vistos.add(cid)
            stats["convs"] += 1
            canal = _label(c, names)
            try:
                got, cursor = [], None
                for _ in range(MAX_PAGES):
                    kw = {"channel": cid, "oldest": str(since_ts), "limit": 200}
                    if cursor:
                        kw["cursor"] = cursor
                    h = _call(client.conversations_history, **kw)
                    got += h.get("messages", [])
                    cursor = (h.get("response_metadata") or {}).get("next_cursor")
                    if not h.get("has_more") or not cursor:
                        break
                    time.sleep(1.1)
                # hilos con respuestas nuevas
                extra = []
                for m in got:
                    if threads_left > 0 and m.get("reply_count") and float(m.get("latest_reply", 0) or 0) >= since_ts:
                        try:
                            r = _call(client.conversations_replies, channel=cid, ts=m["ts"],
                                      oldest=str(since_ts), limit=100)
                            extra += [x for x in r.get("messages", []) if x.get("ts") != m["ts"]]
                            threads_left -= 1
                            stats["hilos"] += 1
                            time.sleep(1.1)
                        except Exception:
                            stats["errores"] += 1
                n_ok = 0
                for m in got + extra:
                    if m.get("subtype") in _SKIP_SUBTYPES or m.get("bot_id"):
                        continue
                    texto = m.get("text") or ""
                    if not texto.strip() and m.get("files"):
                        texto = "[adjuntó: " + ", ".join((f.get("title") or f.get("name") or "archivo") for f in m["files"][:3]) + "]"
                    if not texto.strip():
                        continue
                    ts = float(m.get("ts", 0))
                    if _CRED_RE.search(texto):
                        stats["seguridad"].append({"canal": canal, "fecha": datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d"),
                                                   "tipo": "credencial o dato bancario en claro"})
                        continue                            # el contenido NO sale de aquí
                    autor = names.get(m.get("user"), (m.get("user") or "?", False))[0]
                    msgs.append({"ts": ts, "canal": canal, "autor": autor, "texto": _clean(texto, names)})
                    n_ok += 1
                if n_ok:
                    stats["con_mensajes"] += 1
                else:
                    stats["vacias"] += 1
            except Exception as e:
                stats["errores"] += 1
                log.warning(f"slack_digest: error leyendo {canal}: {str(e)[:120]}")
            time.sleep(1.1)
    msgs.sort(key=lambda m: m["ts"])
    stats["mensajes"] = len(msgs)
    return msgs, stats


def _chunks(msgs: list) -> list:
    """Agrupa los mensajes en bloques de texto (por conversación, en orden de tiempo)."""
    por_canal = {}
    for m in msgs:
        por_canal.setdefault(m["canal"], []).append(m)
    bloques, cur = [], ""
    for canal, lst in por_canal.items():
        txt = f"### {canal}\n" + "\n".join(
            f"[{datetime.fromtimestamp(m['ts'], TZ).strftime('%d-%m %H:%M')}] {m['autor']}: {m['texto']}" for m in lst) + "\n"
        if len(txt) > CHUNK_CHARS:                           # conversación enorme: se parte
            for i in range(0, len(txt), CHUNK_CHARS):
                if cur:
                    bloques.append(cur); cur = ""
                bloques.append(txt[i:i + CHUNK_CHARS])
            continue
        if len(cur) + len(txt) > CHUNK_CHARS and cur:
            bloques.append(cur); cur = ""
        cur += txt
    if cur:
        bloques.append(cur)
    return bloques


# ───────────────────────────── modelo ─────────────────────────────

_SYS_DIGEST = (
    "Eres Nexo, asistente ejecutivo de Polo Bassoco (CEO de Kawiil/Yoltik, México). Recibes mensajes de "
    "Slack de un periodo, agrupados por conversación, con formato «[dd-mm HH:MM] Autor: texto». Extrae SOLO "
    "lo relevante para el trabajo (clientes, cumplimiento, legal, contabilidad, finanzas, comercial, equipo). "
    "Ignora contenido personal o familiar; de recursos humanos solo «tema de RRHH», sin detalle. Devuelve "
    "ÚNICAMENTE un JSON:\n"
    '{"temas":[{"tema":"cliente o asunto corto","resumen":"1-2 frases de lo que pasó","fecha":"YYYY-MM-DD","fuente":"conversación"}],'
    ' "acciones":[{"que":"acción abierta concreta","de":"quién la pide","para":"a quién se la pide","fecha":"YYYY-MM-DD",'
    '"fuente":"conversación","respuesta":"si|no|no se"}],'
    ' "fechas":[{"fecha":"YYYY-MM-DD","que":"cita o vencimiento futuro"}]}\n'
    "Reglas: máximo 12 temas, 15 acciones y 8 fechas; no inventes nada; usa las fechas reales de los mensajes "
    "(año 2026); «respuesta»: si en el mismo bloque hay una respuesta posterior que atiende la acción. Una "
    "acción es algo que alguien pidió o prometió y sigue pendiente. No copies contraseñas, claves ni números de cuenta.")

_SYS_FOLLOW = (
    "Eres Nexo, asistente ejecutivo de Polo Bassoco (CEO de Kawiil/Yoltik, México). Recibes (1) las ACCIONES "
    "ABIERTAS de la última revisión, cada una con su id, y (2) mensajes nuevos de Slack. Para cada acción que los "
    "mensajes afecten, indica su estado. Devuelve ÚNICAMENTE un JSON:\n"
    '{"acciones":[{"id":"A3","estado":"resuelta|avanzo|sin_cambio","evidencia":"qué se dijo, quién y cuándo, en una frase",'
    '"fecha":"YYYY-MM-DD","fuente":"conversación"}],'
    ' "nuevos":[{"que":"cosa nueva que no estaba en la lista","de":"quién","para":"a quién","fecha":"YYYY-MM-DD","fuente":"conversación"}]}\n'
    "Reglas: «resuelta» SOLO si un mensaje confirma explícitamente que quedó hecho (acuse, sello, «ya se presentó», "
    "«ya quedó»…); si solo hay actividad, «avanzo». Incluye solo las acciones mencionadas. Máximo 15 nuevos. No inventes. "
    "Ignora contenido personal o familiar y no copies contraseñas, claves ni cuentas.")


def _llm_json(system: str, user: str, max_tokens: int = 3000) -> dict:
    key = core.load_anthropic_key()
    raw = core.call_haiku(key, system, [], user, max_tokens=max_tokens, timeout=120)
    d = core._distill_parse_json(raw)
    if not d:
        log.warning(f"slack_digest: el modelo no devolvió JSON válido: {raw[:160]!r}")
    return d


def _dedupe(items: list, campo: str, umbral: float = 0.7) -> list:
    out, toks = [], []
    for it in items:
        t = _tok(str(it.get(campo, "")))
        if any(_jaccard(t, u) >= umbral for u in toks):
            continue
        out.append(it); toks.append(t)
    return out


def _es_regulatorio(*textos) -> bool:
    return bool(core.REGULATORY_RE.search(" ".join(str(t or "") for t in textos)))


def build_digest(msgs: list) -> dict:
    temas, acciones, fechas = [], [], []
    for bloque in _chunks(msgs):
        d = _llm_json(_SYS_DIGEST, bloque)
        temas += [x for x in (d.get("temas") or []) if isinstance(x, dict)]
        acciones += [x for x in (d.get("acciones") or []) if isinstance(x, dict)]
        fechas += [x for x in (d.get("fechas") or []) if isinstance(x, dict)]
    temas = _dedupe(temas, "tema", 0.8)
    acciones = _dedupe(acciones, "que", 0.7)
    fechas = _dedupe(fechas, "que", 0.8)
    for i, a in enumerate(acciones, 1):
        a["id"] = f"A{i}"
        a["regulatorio"] = _es_regulatorio(a.get("que"), a.get("fuente"))
    for t in temas:
        t["regulatorio"] = _es_regulatorio(t.get("tema"), t.get("resumen"))
    return {"temas": temas, "acciones": acciones, "fechas": fechas}


def _resumen_ejecutivo(dg: dict, hoy: date) -> str:
    compacto = json.dumps({"acciones": [{k: a.get(k) for k in ("id", "que", "de", "para", "fecha", "respuesta", "regulatorio")}
                                        for a in dg["acciones"][:40]],
                           "fechas": dg["fechas"][:12]}, ensure_ascii=False)[:9000]
    try:
        key = core.load_anthropic_key()
        txt = core.call_haiku(
            key,
            "Eres Nexo, asistente ejecutivo de Polo Bassoco. Con las acciones abiertas y fechas que recibes, escribe en "
            "español, máximo 7 viñetas cortas, lo MÁS urgente para hoy y mañana. Lo regulatorio/legal marcado "
            "regulatorio=true va con «⚠️ verificar». No inventes nada; no copies datos sensibles.",
            [], f"HOY es {hoy.isoformat()}.\n{compacto}", max_tokens=700, timeout=60)
        if txt and not txt.startswith("("):
            return txt.strip()
    except Exception as e:
        log.warning(f"slack_digest: resumen ejecutivo falló: {e}")
    return ""


# ───────────────────────────── salida ─────────────────────────────

def _cobertura_txt(stats: dict) -> str:
    if stats.get("error"):
        return f"⚠️ No pude leer Slack: {stats['error']}"
    base = (f"{stats['mensajes']} mensajes en {stats['con_mensajes']} de {stats['convs']} conversaciones"
            + (f", {stats['errores']} con error" if stats.get("errores") else ""))
    if stats.get("limitada"):
        base += ("\n⚠️ Cobertura LIMITADA: solo veo canales donde Louis está invitado. Faltan los mensajes directos "
                 "y grupos; para verlos hace falta SLACK_USER_TOKEN.")
    return base


def _seguridad_txt(stats: dict) -> str:
    seg = stats.get("seguridad") or []
    if not seg:
        return ""
    por = {}
    for s in seg:
        por.setdefault(s["canal"], set()).add(s["fecha"])
    lineas = [f"• {c} ({', '.join(sorted(fs)[:3])})" for c, fs in list(por.items())[:8]]
    return ("🔐 *Seguridad:* hay contraseñas, claves o datos bancarios escritos en claro en Slack (omití sus valores). "
            "Conviene rotarlos:\n" + "\n".join(lineas))


def _publicar_slack_dm(titulo: str, cuerpo: str) -> bool:
    """Deja el detalle completo en el DM de Polo con el bot (un mensaje + hilo). Best-effort."""
    try:
        from slack_sdk import WebClient
        creds = core.load_env_file(SLACK_ENV)
        tok = creds.get("SLACK_BOT_TOKEN", "")
        if not tok.startswith("xoxb-"):
            return False
        owner = creds.get("SLACK_OWNER_USER") or OWNER_ID_DEFAULT
        client = WebClient(token=tok)
        partes = _split(f"{titulo}\n{cuerpo}", 3500)
        r = client.chat_postMessage(channel=owner, text=partes[0])
        ts, ch = r.get("ts"), r.get("channel")
        for p in partes[1:]:
            client.chat_postMessage(channel=ch, thread_ts=ts, text=p)
        return True
    except Exception as e:
        log.warning(f"slack_digest: no pude publicar en el DM de Slack: {e}")
        return False


def _mandar(send, texto: str) -> None:
    for p in _split(texto, TG_CHUNK):
        send(p)
        time.sleep(1)


def _latest_digest(antes_de: date | None = None) -> dict | None:
    if not DIGEST_DIR.exists():
        return None
    for f in sorted(DIGEST_DIR.glob("*.json"), reverse=True):
        d = _parse_iso(f.stem)
        if antes_de and d and d >= antes_de:
            continue
        try:
            return json.loads(f.read_text())
        except Exception:
            continue
    return None


def _prune() -> None:
    try:
        files = sorted(DIGEST_DIR.glob("*.json"))
        for f in files[:-KEEP_DIGESTS]:
            f.unlink()
    except Exception:
        pass


def run_digest(send, force: bool = False) -> str:
    """Resumen de la tarde. `send(texto)` manda a Telegram. Idempotente por fecha."""
    if not _LOCK.acquire(blocking=False):
        return "(slack_digest en curso)"
    try:
        ahora = _now()
        hoy = ahora.date()
        DIGEST_DIR.mkdir(parents=True, exist_ok=True)
        if not force and (DIGEST_DIR / f"{hoy.isoformat()}.json").exists():
            return "(resumen de Slack ya corrió hoy)"
        if not _puede_intentar("digest", hoy, force):
            return "(slack_digest: 3 intentos hoy sin éxito, no reintento)"
        prev = _latest_digest()
        since = prev["ts"] if prev and prev.get("ts") else (ahora - timedelta(hours=FALLBACK_HOURS)).timestamp()
        msgs, stats = collect(since)
        if stats.get("error") or not msgs:
            aviso = stats.get("error") or "no hubo mensajes nuevos de trabajo desde la última revisión."
            # marca el día para no reintentar cada minuto; guarda el corte para la próxima
            (DIGEST_DIR / f"{hoy.isoformat()}.json").write_text(json.dumps(
                {"ts": ahora.timestamp(), "since": since, "fecha": hoy.isoformat(), "stats": stats,
                 "temas": [], "acciones": (prev or {}).get("acciones", []) if not stats.get("error") else [],
                 "fechas": [], "resumen": ""}, ensure_ascii=False))
            if stats.get("error"):
                _mandar(send, f"🌆 *Resumen de Slack — {_fmt_fecha(hoy.isoformat())}*\n⚠️ {aviso}")
            return f"OK slack_digest: {aviso}"
        dg = build_digest(msgs)
        resumen = _resumen_ejecutivo(dg, hoy)
        data = {"ts": ahora.timestamp(), "since": since, "fecha": hoy.isoformat(), "stats": stats,
                "resumen": resumen, **dg}
        (DIGEST_DIR / f"{hoy.isoformat()}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
        _prune()

        esperan = [a for a in dg["acciones"] if "polo" in str(a.get("para", "")).lower() and a.get("respuesta") != "si"]
        reg = [a for a in dg["acciones"] if a.get("regulatorio")]
        L = [f"🌆 *Resumen de Slack — {_fmt_fecha(hoy.isoformat())}*", f"_{_cobertura_txt(stats)}_", ""]
        if resumen:
            L += [resumen, ""]
        if dg["fechas"]:
            L.append("⏰ *Fechas próximas:*")
            L += [f"• {_fmt_fecha(f.get('fecha'))} — {f.get('que', '')}" for f in sorted(dg["fechas"], key=lambda x: str(x.get("fecha")))[:6]]
            L.append("")
        if reg:
            L.append("⚠️ *Regulatorio / legal — verificar (no se da por cerrado sin confirmación):*")
            L += [f"• {a['id']} {a.get('que', '')} ({_fmt_fecha(a.get('fecha'))}, {a.get('fuente', '')})" for a in reg[:6]]
            L.append("")
        if esperan:
            L.append(f"⏳ *Esperan algo de ti ({len(esperan)}):*")
            L += [f"• {a['id']} {a.get('que', '')} — {a.get('de', '?')} ({_fmt_fecha(a.get('fecha'))})" for a in esperan[:8]]
            L.append("")
        seg = _seguridad_txt(stats)
        if seg:
            L += [seg, ""]
        L.append(f"_{len(dg['acciones'])} acciones abiertas guardadas; mañana a las 7:41 reviso cómo avanzaron. "
                 "Detalle completo en tu DM de Slack con Louis._")
        _mandar(send, "\n".join(L))

        det = ["*Temas:*"] + [f"• *{t.get('tema', '')}* ({_fmt_fecha(t.get('fecha'))}, {t.get('fuente', '')}): {t.get('resumen', '')}"
                              for t in dg["temas"]]
        det += ["", "*Acciones abiertas:*"] + [
            f"• {a['id']}{' ⚠️' if a.get('regulatorio') else ''} {a.get('que', '')} — de {a.get('de', '?')} para {a.get('para', '?')} "
            f"({_fmt_fecha(a.get('fecha'))}, {a.get('fuente', '')}; respuesta: {a.get('respuesta', 'no se')})" for a in dg["acciones"]]
        _publicar_slack_dm(f"[Nexo · resumen] {_fmt_fecha(hoy.isoformat())}", "\n".join(det))
        return f"OK slack_digest: {stats['mensajes']} mensajes, {len(dg['acciones'])} acciones"
    except Exception as e:
        log.exception("slack_digest.run_digest falló")
        return f"ERROR slack_digest: {e}"
    finally:
        _LOCK.release()


def _agenda_cubre(texto: str, items_tok: list) -> bool:
    t = _tok(texto)
    if len(t) < 2:
        return True                                            # demasiado vago para proponer un alta
    for it in items_tok:
        comun = t & it
        if len(comun) >= 2 and len(comun) / min(len(t), len(it) or 1) >= 0.5:
            return True
    return False


def run_followup(send, force: bool = False) -> str:
    """Seguimiento de la mañana: ¿qué avanzó desde el último resumen? Propone altas a la AGENDA."""
    if not _LOCK.acquire(blocking=False):
        return "(slack_digest en curso)"
    try:
        ahora = _now()
        hoy = ahora.date()
        est = {}
        try:
            est = json.loads(core.SLACK_FOLLOWUP_STATE.read_text())
        except Exception:
            pass
        if not force and est.get("fecha") == hoy.isoformat():
            return "(seguimiento de Slack ya corrió hoy)"
        if not _puede_intentar("followup", hoy, force):
            return "(slack_followup: 3 intentos hoy sin éxito, no reintento)"
        prev = _latest_digest(antes_de=hoy) or _latest_digest()
        if not prev:
            since, acciones = (ahora - timedelta(hours=FALLBACK_HOURS)).timestamp(), []
        else:
            since, acciones = prev.get("ts") or (ahora - timedelta(hours=FALLBACK_HOURS)).timestamp(), prev.get("acciones") or []
        msgs, stats = collect(since)
        core.SLACK_FOLLOWUP_STATE.parent.mkdir(parents=True, exist_ok=True)
        if stats.get("error"):
            core.SLACK_FOLLOWUP_STATE.write_text(json.dumps({"fecha": hoy.isoformat(), "altas": []}))
            _mandar(send, f"🌅 *Seguimiento de Slack — {_fmt_fecha(hoy.isoformat())}*\n⚠️ No pude leer Slack: {stats['error']}")
            return f"OK slack_followup: {stats['error']}"

        # 1) clasificar acciones con los mensajes nuevos
        base = json.dumps([{k: a.get(k) for k in ("id", "que", "de", "para", "fecha", "fuente")} for a in acciones],
                          ensure_ascii=False)
        upd, nuevos = {}, []
        for bloque in _chunks(msgs):
            d = _llm_json(_SYS_FOLLOW, f"ACCIONES ABIERTAS:\n{base}\n\nMENSAJES NUEVOS:\n{bloque}")
            for u in (d.get("acciones") or []):
                if not isinstance(u, dict) or not u.get("id"):
                    continue
                rank = _ESTADO_RANK.get(u.get("estado"), 0)
                cur = upd.get(u["id"])
                if not cur or rank > _ESTADO_RANK.get(cur.get("estado"), 0):
                    upd[u["id"]] = u
            nuevos += [x for x in (d.get("nuevos") or []) if isinstance(x, dict)]
        nuevos = _dedupe(nuevos, "que", 0.7)

        # 2) candado regulatorio + días hábiles sin respuesta
        resueltas, avanzaron, sin_cambio, sin_resp = [], [], [], []
        for a in acciones:
            u = upd.get(a["id"])
            estado = (u or {}).get("estado", "sin_cambio")
            if estado == "resuelta" and (a.get("regulatorio") or _es_regulatorio(a.get("que"))) \
                    and not _CONFIRM_RE.search((u or {}).get("evidencia", "")):
                estado = "avanzo"
                u = dict(u or {}, evidencia=(u or {}).get("evidencia", "") + " (⚠️ verificar la confirmación)")
            if estado == "resuelta":
                resueltas.append((a, u))
            elif estado == "avanzo":
                avanzaron.append((a, u))
            else:
                sin_cambio.append(a)
                f = _parse_iso(a.get("fecha")) or (_parse_iso(prev.get("fecha")) if prev else None)
                dh = _dias_habiles(f, hoy) if f else 0
                if dh >= 2 and a.get("respuesta") != "si":
                    sin_resp.append((a, dh))

        # 3) cruce con la AGENDA: qué de Slack no está registrado
        try:
            items = core._steward_open_items(hoy)
        except Exception:
            items = []
        items_tok = [_tok(i["texto"]) for i in items]
        candidatos = [a for a in acciones if a not in sin_cambio or a.get("regulatorio")]  # con movimiento o regulatorias
        candidatos += [a for a in sin_cambio if a.get("respuesta") != "si" and a not in candidatos]
        altas = []
        for a in candidatos + [dict(n, id="N") for n in nuevos]:
            if not _agenda_cubre(a.get("que", ""), items_tok):
                txt = (f"{'⚠️ ' if a.get('regulatorio') or _es_regulatorio(a.get('que')) else ''}{a.get('que', '')} "
                       f"(de {a.get('de', '?')} para {a.get('para', '?')}, {_fmt_fecha(a.get('fecha'))} · {a.get('fuente', '')})")
                if all(_jaccard(_tok(txt), _tok(x["texto"])) < 0.7 for x in altas):
                    altas.append({"n": len(altas) + 1, "texto": txt})
        altas = altas[:12]
        core.SLACK_FOLLOWUP_STATE.write_text(json.dumps(
            {"fecha": hoy.isoformat(), "altas": altas}, ensure_ascii=False, indent=1))

        # 4) reporte
        L = [f"🌅 *Seguimiento de Slack — {_fmt_fecha(hoy.isoformat())}*", f"_{_cobertura_txt(stats)}_"]
        if not prev:
            L.append("_No había resumen previo: este reporte cubre solo las últimas horas._")
        L.append(f"_{len(acciones)} acciones de ayer: ✅ {len(resueltas)} resueltas · ↗️ {len(avanzaron)} avanzaron · "
                 f"➖ {len(sin_cambio)} sin cambio · ⏳ {len(sin_resp)} sin respuesta · 🆕 {len(nuevos)} nuevas_")
        L.append("")
        if resueltas:
            L.append("✅ *Resueltas (con evidencia):*")
            L += [f"• {a['id']} {a.get('que', '')} — {u.get('evidencia', '')}" for a, u in resueltas[:8]]
            L.append("")
        if avanzaron:
            L.append("↗️ *Avanzaron:*")
            L += [f"• {a['id']} {a.get('que', '')} — {u.get('evidencia', '')}" for a, u in avanzaron[:8]]
            L.append("")
        if sin_resp:
            L.append("⏳ *Sin respuesta:*")
            L += [f"• {a['id']} {a.get('que', '')} — espera a {a.get('para', '?')} desde hace {dh} días hábiles"
                  for a, dh in sorted(sin_resp, key=lambda x: -x[1])[:8]]
            L.append("")
        if nuevos:
            L.append("🆕 *Nuevo desde ayer:*")
            L += [f"• {n.get('que', '')} ({n.get('de', '?')} → {n.get('para', '?')}, {n.get('fuente', '')})" for n in nuevos[:8]]
            L.append("")
        reg = [a for a in acciones if a.get("regulatorio") and a not in [x for x, _ in resueltas]]
        if reg:
            L.append("⚠️ *Regulatorio / legal — verificar:*")
            L += [f"• {a['id']} {a.get('que', '')}" for a in reg[:6]]
            L.append("")
        if altas:
            L.append("📋 *No están en la AGENDA — propongo agregarlas:*")
            L += [f"{x['n']}. {x['texto']}" for x in altas]
            L.append("_Responde «agrega altas 1, 3» o «agrega todas las altas». No agrego nada sin tu OK._")
            L.append("")
        seg = _seguridad_txt(stats)
        if seg:
            L += [seg, ""]
        _mandar(send, "\n".join(L).strip())
        _publicar_slack_dm(f"[Nexo · seguimiento] {_fmt_fecha(hoy.isoformat())}", "\n".join(L).strip())
        return f"OK slack_followup: {len(resueltas)} resueltas, {len(avanzaron)} avanzaron, {len(altas)} altas propuestas"
    except Exception as e:
        log.exception("slack_digest.run_followup falló")
        return f"ERROR slack_followup: {e}"
    finally:
        _LOCK.release()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    modo = sys.argv[1] if len(sys.argv) > 1 else "digest"
    enviar = lambda t: print("\n---- TELEGRAM ----\n" + t)
    print(run_followup(enviar, force=True) if modo == "followup" else run_digest(enviar, force=True))
