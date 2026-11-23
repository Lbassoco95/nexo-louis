#!/usr/bin/env python3
"""donna_remoto.py — detener una escucha desde CUALQUIER dispositivo, y no guardar dos veces la misma junta.

Problema: una pestaña olvidada (o el teléfono) sigue grabando y no se sabe cuál. Aquí el servidor sabe qué escuchas están activas (conexión en vivo +
autoguardados/rescates recientes) y puede dar la ORDEN de detener. La orden llega por dos caminos, para que funcione aunque una pantalla esté medio colgada:
  1) por la conexión en vivo (mensaje `detener_remoto`), al instante;
  2) en la respuesta del autoguardado (cada ~20 s) de esa pantalla.
La pantalla que la recibe se detiene y GUARDA la junta (como «Detener y guardar», sin pedir nombres). Si dos pantallas intentan guardar la misma junta,
la segunda recibe un aviso claro en lugar de duplicarla.
"""
import threading
import time

import donna_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS escucha_ordenes (rid TEXT PRIMARY KEY, orden TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS escucha_guardadas (rid TEXT PRIMARY KEY, ref TEXT NOT NULL DEFAULT '', ts INTEGER NOT NULL);
"""
ACTIVA_S = 120          # una escucha cuenta como activa si hubo autoguardado/rescate/conexión en los últimos 2 min
VIGENCIA_S = 600        # una orden de detener caduca a los 10 min
_EN_VIVO = {}           # rid -> {user, ev, desde}
_LOCK = threading.Lock()


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


# ------------------------------------------------------------------ conexiones en vivo (memoria)
def registrar(rid: str, usuario: str) -> threading.Event:
    ev = threading.Event()
    with _LOCK:
        _EN_VIVO[rid] = {"user": usuario, "ev": ev, "desde": time.time()}
    if orden_de(rid):                                         # llegó una orden mientras se reconectaba
        ev.set()
    return ev


def quitar(rid: str):
    with _LOCK:
        _EN_VIVO.pop(rid, None)


# ------------------------------------------------------------------ qué escuchas hay activas
def _filas(sql: str, usuario: str, desde: int) -> list:
    c = donna_db.conn()
    try:
        if usuario == "admin":
            sql = sql.replace("usuario=? AND ", "")
            return [dict(r) for r in c.execute(sql, (desde,))]
        return [dict(r) for r in c.execute(sql, (usuario, desde))]
    except Exception:
        return []
    finally:
        c.close()


def activas(usuario: str) -> list:
    """[{id, hace_s, n, en_vivo}] de las escuchas de este usuario con actividad reciente (admin ve todas)."""
    import donna_borrador, donna_rescate
    donna_borrador._c().close()
    donna_rescate._c().close()
    now = int(time.time())
    desde = now - ACTIVA_S
    out = {}
    for r in _filas("SELECT id, n, actualizado FROM escucha_borradores WHERE usuario=? AND actualizado>?", usuario, desde):
        out[r["id"]] = {"id": r["id"], "n": r["n"], "hace_s": now - r["actualizado"], "en_vivo": False}
    for r in _filas("SELECT r.id AS id, r.actualizado AS actualizado, (SELECT COUNT(*) FROM escucha_rescate_lineas l WHERE l.rid=r.id) AS n FROM escucha_rescate r WHERE r.usuario=? AND r.actualizado>?", usuario, desde):
        o = out.setdefault(r["id"], {"id": r["id"], "n": r["n"], "hace_s": now - r["actualizado"], "en_vivo": False})
        o["hace_s"] = min(o["hace_s"], now - r["actualizado"])
    with _LOCK:
        vivos = {k: v for k, v in _EN_VIVO.items() if usuario == "admin" or v["user"] == usuario}
    for rid, v in vivos.items():
        o = out.setdefault(rid, {"id": rid, "n": 0, "hace_s": 0, "en_vivo": True})
        o["en_vivo"] = True
    return sorted(out.values(), key=lambda x: x["hace_s"])


# ------------------------------------------------------------------ órdenes
def ordenar(usuario: str, excepto: str = "") -> list:
    """Orden de DETENER a todas las escuchas activas del usuario (menos `excepto`). Devuelve los ids."""
    ids = [a["id"] for a in activas(usuario) if a["id"] != excepto]
    if not ids:
        return []
    now = int(time.time())
    c = _c()
    try:
        for rid in ids:
            c.execute("INSERT OR REPLACE INTO escucha_ordenes(rid, orden, ts) VALUES(?,?,?)", (rid, "detener", now))
        c.commit()
    finally:
        c.close()
    with _LOCK:
        for rid in ids:
            if rid in _EN_VIVO:
                _EN_VIVO[rid]["ev"].set()                    # por la conexión en vivo, al instante
    return ids


def orden_de(rid: str) -> str:
    """'detener' si hay una orden vigente para esa escucha; '' si no."""
    c = _c()
    try:
        r = c.execute("SELECT orden, ts FROM escucha_ordenes WHERE rid=?", (rid,)).fetchone()
    finally:
        c.close()
    return r["orden"] if r and time.time() - r["ts"] <= VIGENCIA_S else ""


def limpiar(rid: str):
    c = _c()
    try:
        c.execute("DELETE FROM escucha_ordenes WHERE rid=?", (rid,))
        c.execute("DELETE FROM escucha_ordenes WHERE ts<?", (int(time.time()) - VIGENCIA_S,))
        c.commit()
    finally:
        c.close()


# ------------------------------------------------------------------ una junta no se guarda dos veces
def ya_guardada(rid: str) -> bool:
    if not rid:
        return False
    c = _c()
    try:
        return c.execute("SELECT 1 FROM escucha_guardadas WHERE rid=?", (rid,)).fetchone() is not None
    finally:
        c.close()


def marcar_guardada(rid: str, ref: str = ""):
    if not rid:
        return
    c = _c()
    try:
        c.execute("INSERT OR REPLACE INTO escucha_guardadas(rid, ref, ts) VALUES(?,?,?)", (rid, ref[:60], int(time.time())))
        c.execute("DELETE FROM escucha_guardadas WHERE ts<?", (int(time.time()) - 30 * 86400,))
        c.commit()
    finally:
        c.close()
