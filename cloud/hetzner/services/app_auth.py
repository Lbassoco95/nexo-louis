#!/usr/bin/env python3
"""app_auth.py — usuario y contraseña para la app móvil, con sesiones propias. Solo stdlib.

Por qué: pegar el token maestro del gateway en el celular era incómodo y, peor, ese token da acceso TOTAL
(ejecutar herramientas, agentes…). Ahora la app entra con usuario/contraseña y recibe una SESIÓN:
  • token aleatorio con prefijo «dns_» — en disco solo se guarda su SHA-256, nunca el token;
  • vence a los 30 días, se puede revocar (cerrar sesión / cerrar todas);
  • alcance limitado a /v1/app/* (lo decide el gateway), NO a /v1/tools ni /v1/agents.
Contraseñas: scrypt con sal propia (nunca en texto plano). Archivos en PUSH_STATE_DIR (por defecto
/opt/openclaw/state), 0600, que NO se tocan en los deploys.

Anti-adivinanza: límite de fallos por IP, por usuario y global (ventana de 15 min); mientras está bloqueado, ni la
contraseña correcta entra. Usuario inexistente y contraseña mala responden lo mismo y tardan lo mismo.
"""
import os
import re
import json
import time
import hmac
import fcntl
import base64
import hashlib
import secrets
import threading
from collections import deque
from pathlib import Path

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
USERS_FILE = STATE_DIR / "app_users.json"
SESSIONS_FILE = STATE_DIR / "app_sessions.json"
LOCK_FILE = STATE_DIR / "app_auth.lock"

SESSION_DAYS = 30
MAX_SESSIONS_PER_USER = 10
TOUCH_EVERY_S = 600           # no escribir en disco en cada petición
MIN_PASSWORD_LEN = 12
TOKEN_PREFIX = "dns_"

# scrypt: ~16 MB y ~50 ms por intento — barato para uno, caro para adivinar millones
_N, _R, _P, _DK = 2 ** 14, 8, 1, 32

# límites (ventana 900 s)
WINDOW_S = 900
LIM_IP, LIM_USER, LIM_GLOBAL = 5, 10, 30

_TLOCK = threading.Lock()
_FAILS_IP: dict = {}
_FAILS_USER: dict = {}
_FAILS_ALL: deque = deque()
_COMMON = {"password", "contraseña", "contrasena", "123456789012", "qwertyuiopas", "kawiilkawiil",
           "donnadonnadonna", "passwordpassword", "administrador"}


# ------------------------------------------------------------------ utilidades
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode())


class _FileLock:
    """Exclusión entre hilos y entre PROCESOS (gateway y la herramienta de línea de comandos)."""
    def __enter__(self):
        _TLOCK.acquire()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._f = open(LOCK_FILE, "a")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._f, fcntl.LOCK_UN)
            self._f.close()
        finally:
            _TLOCK.release()


def _read(path: Path, default):
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default


def _write(path: Path, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _norm_user(u) -> str:
    return re.sub(r"[^a-z0-9._-]", "", str(u or "").strip().lower())[:64]


# ------------------------------------------------------------------ contraseñas
def hash_password(password: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DK, maxmem=64 * 1024 * 1024)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        alg, n, r, p, salt, dk = stored.split("$")
        if alg != "scrypt":
            return False
        calc = hashlib.scrypt((password or "").encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(_unb64(dk)), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(calc, _unb64(dk))
    except Exception:
        return False


_DUMMY_HASH = hash_password("x" * 20, salt=b"\x00" * 16)   # para igualar el tiempo cuando el usuario no existe


def password_problem(user: str, password: str):
    """None si la contraseña es aceptable; si no, el motivo en español."""
    if len(password or "") < MIN_PASSWORD_LEN:
        return f"Debe tener al menos {MIN_PASSWORD_LEN} caracteres."
    low = password.lower()
    if low == _norm_user(user) or low in _COMMON or len(set(password)) < 5:
        return "Es demasiado fácil de adivinar. Usa una frase larga (varias palabras)."
    return None


def set_password(user: str, password: str) -> str:
    """Crea o cambia la contraseña. Devuelve '' si salió bien, o el motivo del rechazo."""
    u = _norm_user(user)
    if not u:
        return "Usuario inválido (solo letras, números, punto, guion)."
    prob = password_problem(u, password)
    if prob:
        return prob
    with _FileLock():
        users = _read(USERS_FILE, {})
        users[u] = {"hash": hash_password(password), "cambiada": int(time.time())}
        _write(USERS_FILE, users)
    return ""


def delete_user(user: str) -> bool:
    u = _norm_user(user)
    with _FileLock():
        users = _read(USERS_FILE, {})
        if u not in users:
            return False
        users.pop(u)
        _write(USERS_FILE, users)
    revoke_all(u)
    return True


def list_users() -> list:
    return sorted(_read(USERS_FILE, {}).keys())


# ------------------------------------------------------------------ límite de intentos
def _prune(dq: deque, now: float) -> None:
    while dq and now - dq[0] > WINDOW_S:
        dq.popleft()


def _retry_after(ip: str, user: str, now: float) -> int:
    """Segundos de espera si alguna de las tres cuentas está en el límite; 0 si puede intentar."""
    worst = 0
    for dq, lim in ((_FAILS_IP.setdefault(ip, deque()), LIM_IP), (_FAILS_USER.setdefault(user, deque()), LIM_USER),
                    (_FAILS_ALL, LIM_GLOBAL)):
        _prune(dq, now)
        if len(dq) >= lim:
            worst = max(worst, int(WINDOW_S - (now - dq[0])) + 1)
    return worst


def _register_fail(ip: str, user: str, now: float) -> None:
    _FAILS_IP.setdefault(ip, deque()).append(now)
    _FAILS_USER.setdefault(user, deque()).append(now)
    _FAILS_ALL.append(now)
    if len(_FAILS_IP) > 5000:           # evita crecer sin fin con IPs aleatorias
        _FAILS_IP.clear()


def reset_limits() -> None:
    with _TLOCK:
        _FAILS_IP.clear(); _FAILS_USER.clear(); _FAILS_ALL.clear()


# ------------------------------------------------------------------ sesiones
def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(user: str, password: str, ip: str = "?", ua: str = "") -> dict:
    """{ok:True, token, expires, user} | {ok:False, error, retry_after?}"""
    u = _norm_user(user)
    now = time.time()
    with _TLOCK:
        wait = _retry_after(ip, u, now)
    if wait:
        return {"ok": False, "error": "Demasiados intentos fallidos.", "retry_after": wait}
    rec = _read(USERS_FILE, {}).get(u)
    ok = verify_password(password or "", rec["hash"] if rec else _DUMMY_HASH)   # mismo costo exista o no
    if not (ok and rec):
        with _TLOCK:
            _register_fail(ip, u, now)
        return {"ok": False, "error": "Usuario o contraseña incorrectos."}
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    exp = int(now + SESSION_DAYS * 86400)
    with _FileLock():
        ses = _read(SESSIONS_FILE, {})
        ses = {k: v for k, v in ses.items() if v.get("exp", 0) > now}                      # limpia vencidas
        ses[_th(token)] = {"user": u, "created": round(now, 3), "last": int(now), "exp": exp,   # ms: orden estable al evictar
                           "device": (ua or "")[:140], "ip": ip}
        mine = sorted([(v["created"], k) for k, v in ses.items() if v["user"] == u])
        for _, k in mine[:-MAX_SESSIONS_PER_USER]:                                         # tope por usuario
            ses.pop(k, None)
        _write(SESSIONS_FILE, ses)
    return {"ok": True, "token": token, "expires": exp, "user": u}


def check_session(token: str):
    """Sesión (dict con user/exp/…) o None. Renueva la caducidad si ya pasó la mitad del plazo."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    now = time.time()
    h = _th(token)
    s = _read(SESSIONS_FILE, {}).get(h)
    if not s or s.get("exp", 0) <= now:
        return None
    if now - s.get("last", 0) > TOUCH_EVERY_S:
        with _FileLock():
            ses = _read(SESSIONS_FILE, {})
            if h in ses:
                ses[h]["last"] = int(now)
                if ses[h]["exp"] - now < SESSION_DAYS * 43200:           # menos de la mitad → renovar
                    ses[h]["exp"] = int(now + SESSION_DAYS * 86400)
                _write(SESSIONS_FILE, ses)
                s = ses[h]
    return dict(s)


def revoke(token: str) -> bool:
    h = _th(token or "")
    with _FileLock():
        ses = _read(SESSIONS_FILE, {})
        if h not in ses:
            return False
        ses.pop(h)
        _write(SESSIONS_FILE, ses)
    return True


def revoke_all(user: str = None) -> int:
    """Cierra todas las sesiones (de un usuario, o de todos si user=None). Devuelve cuántas."""
    u = _norm_user(user) if user else None
    with _FileLock():
        ses = _read(SESSIONS_FILE, {})
        keep = {k: v for k, v in ses.items() if u and v.get("user") != u}
        _write(SESSIONS_FILE, keep)
    return len(ses) - len(keep)


def list_sessions(user: str = None, current_token: str = None) -> list:
    now = time.time()
    cur = _th(current_token) if current_token else ""
    out = []
    for k, v in _read(SESSIONS_FILE, {}).items():
        if v.get("exp", 0) <= now or (user and v.get("user") != _norm_user(user)):
            continue
        out.append({"id": k[:8], "user": v["user"], "device": v.get("device", ""), "ip": v.get("ip", ""),
                    "created": v["created"], "last": v["last"], "exp": v["exp"], "current": k == cur})
    return sorted(out, key=lambda x: -x["last"])
