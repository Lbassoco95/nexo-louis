#!/usr/bin/env python3
"""app_chats.py — conversaciones guardadas de Donna (nueva / cargar anteriores / renombrar / borrar / buscar).

Cada conversación es un JSON en PUSH_STATE_DIR/chats/<usuario>/<id>.json (0600): {id, titulo, creado, actualizado, msgs:[{r,t,ts,model?}]}.
Así se ven igual desde el iPhone, la tablet o la computadora. Sin llamadas a modelos."""
import os
import re
import json
import time
import secrets
import threading
from pathlib import Path

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
ID_RE = re.compile(r"^c[0-9a-f]{10}$")
MAX_MSGS = 400
MAX_TEXTO = 20000
MAX_CHATS = 500
_LOCK = threading.RLock()


def _dir(user: str) -> Path:
    u = re.sub(r"[^a-zA-Z0-9_.-]", "_", user or "master")[:40] or "master"
    d = STATE_DIR / "chats" / u
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(user: str, cid: str) -> Path:
    if not ID_RE.match(cid or ""):
        raise ValueError("id de conversación inválido")
    return _dir(user) / f"{cid}.json"


def nuevo_id() -> str:
    return "c" + secrets.token_hex(5)


def _limpiar(msgs) -> list:
    out = []
    for m in (msgs or [])[-MAX_MSGS:]:
        if not isinstance(m, dict) or m.get("r") not in ("u", "a"):
            continue
        t = str(m.get("t", ""))[:MAX_TEXTO]
        if not t.strip():
            continue
        e = {"r": m["r"], "t": t, "ts": int(m.get("ts") or time.time() * 1000)}
        if m.get("model"):
            e["model"] = str(m["model"])[:40]
        if re.match(r"^a[0-9a-f]{10}$", str(m.get("art", ""))):
            e["art"] = m["art"]
        out.append(e)
    return out


def titulo_auto(msgs) -> str:
    for m in msgs:
        if m["r"] == "u":
            t = re.sub(r"\s+", " ", m["t"]).strip()
            return (t[:48] + "…") if len(t) > 48 else t
    return "Conversación nueva"


def guardar(user: str, cid: str, msgs, titulo: str = "") -> dict:
    """Crea o reemplaza el contenido. Si no tiene título propio, lo deduce del primer mensaje. Conversación vacía → no se guarda."""
    p = _path(user, cid)
    msgs = _limpiar(msgs)
    with _LOCK:
        if not msgs:
            return {"id": cid, "vacio": True}
        prev = {}
        try:
            prev = json.loads(p.read_text())
        except Exception:
            pass
        now = int(time.time() * 1000)
        tit = (titulo or "").strip()[:80] or (prev.get("titulo") if prev.get("titulo_manual") else "") or titulo_auto(msgs)
        d = {"id": cid, "titulo": tit, "titulo_manual": bool(prev.get("titulo_manual") or (titulo or "").strip()),
             "creado": prev.get("creado") or now, "actualizado": now, "msgs": msgs}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        _podar(user)
    return {"id": cid, "titulo": d["titulo"], "actualizado": now}


def _podar(user: str) -> None:
    fs = sorted(_dir(user).glob("c*.json"), key=lambda f: f.stat().st_mtime)
    for f in fs[:-MAX_CHATS]:
        f.unlink(missing_ok=True)


def obtener(user: str, cid: str):
    try:
        return json.loads(_path(user, cid).read_text())
    except (FileNotFoundError, ValueError):
        return None


def listar(user: str, q: str = "", limite: int = 200) -> list:
    q = (q or "").strip().lower()
    out = []
    for f in _dir(user).glob("c*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        msgs = d.get("msgs", [])
        if q and q not in d.get("titulo", "").lower() and not any(q in m["t"].lower() for m in msgs):
            continue
        ult = msgs[-1]["t"] if msgs else ""
        out.append({"id": d["id"], "titulo": d.get("titulo", ""), "actualizado": d.get("actualizado", 0),
                    "n": len(msgs), "resumen": re.sub(r"\s+", " ", re.sub(r"[*_#`>|]", "", ult)).strip()[:90]})
    out.sort(key=lambda x: -x["actualizado"])
    return out[:limite]


def renombrar(user: str, cid: str, titulo: str) -> dict:
    titulo = (titulo or "").strip()[:80]
    if not titulo:
        raise ValueError("el título no puede estar vacío")
    with _LOCK:
        d = obtener(user, cid)
        if not d:
            raise ValueError("no existe esa conversación")
        d["titulo"], d["titulo_manual"] = titulo, True
        _path(user, cid).write_text(json.dumps(d, ensure_ascii=False))
    return {"id": cid, "titulo": titulo}


def borrar(user: str, cid: str) -> bool:
    p = _path(user, cid)
    existed = p.exists()
    p.unlink(missing_ok=True)
    return existed
