#!/usr/bin/env python3
"""donna_rescate.py — copia de RESCATE del texto de «Escuchar», guardada por el SERVIDOR mientras se graba.

Por qué: el texto de la junta vivía solo en la pantalla; si la pestaña se recargaba, el teléfono se apagaba o el servicio se reiniciaba, la junta se perdía.
Ahora el servidor anota cada frase (lo que se oyó y su traducción al español, con la hora) apenas el transcriptor la termina. Es SOLO TEXTO: el audio no se guarda.
Se borra al guardar la junta de verdad, cuando Polo la descarta, o a los 30 días. Se recupera desde Escuchar («Rescate del servidor»).
"""
import re
import time

import donna_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS escucha_rescate (id TEXT PRIMARY KEY, usuario TEXT NOT NULL, creado INTEGER NOT NULL, actualizado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS escucha_rescate_lineas (n INTEGER PRIMARY KEY AUTOINCREMENT, rid TEXT NOT NULL, ts INTEGER NOT NULL, t_ms INTEGER NOT NULL DEFAULT 0, k TEXT NOT NULL, texto TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_resc_l ON escucha_rescate_lineas(rid, n);
"""
_ID = re.compile(r"^[a-z0-9]{6,24}$")
DIAS = 30
_CJK = re.compile(r"[㐀-鿿]")


def valido(rid) -> bool:
    return bool(_ID.match(str(rid or "")))


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


def agregar(rid: str, usuario: str, k: str, t_ms: int, texto: str) -> bool:
    """k: 'in' (lo que se oyó) | 'out' (su traducción al español). Nunca lanza: el rescate no debe tumbar la escucha."""
    texto = " ".join((texto or "").split())[:3000]
    if not valido(rid) or k not in ("in", "out") or not texto:
        return False
    try:
        now = int(time.time())
        c = _c()
        try:
            r = c.execute("SELECT usuario FROM escucha_rescate WHERE id=?", (rid,)).fetchone()
            if r and r["usuario"] != usuario:
                return False
            if not r:
                c.execute("INSERT INTO escucha_rescate(id,usuario,creado,actualizado) VALUES(?,?,?,?)", (rid, usuario, now, now))
            else:
                c.execute("UPDATE escucha_rescate SET actualizado=? WHERE id=?", (now, rid))
            c.execute("INSERT INTO escucha_rescate_lineas(rid,ts,t_ms,k,texto) VALUES(?,?,?,?,?)", (rid, now, int(t_ms or 0), k, texto))
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def turnos(rid: str, c=None) -> list:
    """Las líneas en forma de turnos de Escuchar: {o idioma, a original, b español, t ms}. Sin voces (no se sabe quién habló)."""
    cerrar = c is None
    c = c or _c()
    try:
        out = []
        for r in c.execute("SELECT t_ms, k, texto FROM escucha_rescate_lineas WHERE rid=? ORDER BY n", (rid,)):
            if r["k"] == "in":
                out.append({"o": "zh" if _CJK.search(r["texto"]) else "en", "a": r["texto"], "b": "", "t": r["t_ms"], "s": None, "f": 0, "l": 0, "m": 0})
            elif out and not out[-1]["b"]:
                out[-1]["b"] = r["texto"]
            else:
                out.append({"o": "es", "a": r["texto"], "b": "", "t": r["t_ms"], "s": None, "f": 0, "l": 0, "m": 0})
        return out
    finally:
        if cerrar:
            c.close()


def listar(usuario: str) -> list:
    c = _c()
    try:
        c.execute("DELETE FROM escucha_rescate_lineas WHERE rid IN (SELECT id FROM escucha_rescate WHERE actualizado<?)", (int(time.time()) - DIAS * 86400,))
        c.execute("DELETE FROM escucha_rescate WHERE actualizado<?", (int(time.time()) - DIAS * 86400,))
        c.commit()
        out = []
        for r in c.execute("SELECT id, creado, actualizado FROM escucha_rescate WHERE usuario=? ORDER BY actualizado DESC LIMIT 10", (usuario,)):
            t = turnos(r["id"], c)
            if not t:
                continue
            ult = (t[-1]["b"] or t[-1]["a"])[:140]
            out.append({"id": r["id"], "creado": r["creado"], "actualizado": r["actualizado"], "n": len(t), "duracion": int(t[-1]["t"] / 1000), "ultimo": ult})
        return out
    finally:
        c.close()


def a_borrador(rid: str, usuario: str):
    c = _c()
    try:
        r = c.execute("SELECT * FROM escucha_rescate WHERE id=?", (rid,)).fetchone()
        if not r or (r["usuario"] != usuario and usuario != "admin"):
            return None
        t = turnos(rid, c)
        if not t:
            return None
        return {"turnos": t, "spk": [], "names": {}, "live_id": "", "reemplaza": "", "duracion": int(t[-1]["t"] / 1000), "id": rid, "actualizado": r["actualizado"], "desde_rescate": True}
    finally:
        c.close()


def descartar(rid: str, usuario: str) -> int:
    if not valido(rid):
        return 0
    c = _c()
    try:
        r = c.execute("SELECT usuario FROM escucha_rescate WHERE id=?", (rid,)).fetchone()
        if not r or (r["usuario"] != usuario and usuario != "admin"):
            return 0
        c.execute("DELETE FROM escucha_rescate_lineas WHERE rid=?", (rid,))
        c.execute("DELETE FROM escucha_rescate WHERE id=?", (rid,))
        c.commit()
        return 1
    finally:
        c.close()
