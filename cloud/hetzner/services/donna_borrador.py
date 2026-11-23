#!/usr/bin/env python3
"""donna_borrador.py — la junta de «Escuchar» se guarda SOLA mientras se graba (cada ~20 s) para poder RETOMARLA si se cierra la app, se apaga el teléfono o se cae la conexión.

Un borrador es el estado de la escucha (turnos con su texto y hora, voces, nombres, id de la junta con Donna). Solo texto: el audio nunca se guarda.
Se borra solo al guardar la junta de verdad (o cuando Polo lo descarta); los que llevan 30 días sin tocarse se limpian.
"""
import json
import re
import time

import donna_db

SCHEMA = """CREATE TABLE IF NOT EXISTS escucha_borradores (
  id TEXT PRIMARY KEY, usuario TEXT NOT NULL, creado INTEGER NOT NULL, actualizado INTEGER NOT NULL,
  duracion INTEGER NOT NULL DEFAULT 0, n INTEGER NOT NULL DEFAULT 0, ultimo TEXT NOT NULL DEFAULT '', datos TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_borr_usr ON escucha_borradores(usuario, actualizado);"""
_ID = re.compile(r"^[a-z0-9]{6,24}$")
MAX_BYTES = 900_000
DIAS = 30


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


def guardar(bid: str, usuario: str, datos: dict) -> dict:
    if not _ID.match(bid or ""):
        raise ValueError("id de borrador inválido")
    turnos = datos.get("turnos")
    if not isinstance(turnos, list) or not turnos:
        raise ValueError("el borrador no trae turnos")
    limpio = {
        "turnos": [{"o": str(t.get("o", ""))[:4], "a": str(t.get("a", ""))[:3000], "b": str(t.get("b", ""))[:3000], "t": int(t.get("t") or 0),
                    "s": (int(t["s"]) if t.get("s") is not None else None), "f": float(t.get("f") or 0), "l": int(t.get("l") or 0), "m": 1 if t.get("m") else 0}
                   for t in turnos[:4000] if isinstance(t, dict)],
        "spk": [{"f0": float(s.get("f0") or 0), "hf": float(s.get("hf") or 0), "n": int(s.get("n") or 1)} for s in (datos.get("spk") or [])[:8] if isinstance(s, dict)],
        "names": {str(k)[:3]: str(v)[:40] for k, v in (datos.get("names") or {}).items()} if isinstance(datos.get("names"), dict) else {},
        "live_id": str(datos.get("live_id") or "")[:40],
        "duracion": max(0, min(int(datos.get("duracion") or 0), 86400)),
    }
    blob = json.dumps(limpio, ensure_ascii=False)
    if len(blob.encode("utf-8")) > MAX_BYTES:
        raise ValueError("la junta es demasiado larga para el borrador; guárdala")
    ult = ""
    for t in reversed(limpio["turnos"]):
        ult = (t["b"] or t["a"]).strip()
        if ult:
            break
    now = int(time.time())
    c = _c()
    try:
        prev = c.execute("SELECT usuario FROM escucha_borradores WHERE id=?", (bid,)).fetchone()
        if prev and prev["usuario"] != usuario:
            raise ValueError("ese borrador es de otra persona")
        c.execute("INSERT INTO escucha_borradores(id,usuario,creado,actualizado,duracion,n,ultimo,datos) VALUES(?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET actualizado=excluded.actualizado, duracion=excluded.duracion, n=excluded.n, ultimo=excluded.ultimo, datos=excluded.datos",
                  (bid, usuario, now, now, limpio["duracion"], len(limpio["turnos"]), ult[:140], blob))
        c.execute("DELETE FROM escucha_borradores WHERE actualizado<?", (now - DIAS * 86400,))
        c.commit()
    finally:
        c.close()
    return {"id": bid, "n": len(limpio["turnos"])}


def listar(usuario: str) -> list:
    c = _c()
    try:
        return [dict(r) for r in c.execute("SELECT id, creado, actualizado, duracion, n, ultimo FROM escucha_borradores WHERE usuario=? ORDER BY actualizado DESC LIMIT 10", (usuario,))]
    finally:
        c.close()


def leer(bid: str, usuario: str):
    c = _c()
    try:
        r = c.execute("SELECT * FROM escucha_borradores WHERE id=?", (bid,)).fetchone()
    finally:
        c.close()
    if not r or (r["usuario"] != usuario and usuario != "admin"):
        return None
    d = json.loads(r["datos"])
    d["id"] = r["id"]
    d["actualizado"] = r["actualizado"]
    return d


def descartar(bid: str, usuario: str) -> int:
    if not _ID.match(bid or ""):
        return 0
    c = _c()
    try:
        n = c.execute("DELETE FROM escucha_borradores WHERE id=? AND (usuario=? OR ?='admin')", (bid, usuario, usuario)).rowcount
        c.commit()
        return n
    finally:
        c.close()
