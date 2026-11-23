#!/usr/bin/env python3
"""donna_aprender.py — Polo la CORRIGE y Donna aprende sin tocar código; y cuando algo SÍ necesita código, lo deja registrado con la evidencia.

Dos cosas distintas:
  • REGLAS (lo que Donna puede corregir sola): «cuando X, haz Y; no hagas Z». Se guardan en la base (no en un TXT), se meten en CADA conversación y Polo las ve, pausa,
    edita o borra en la app. Ejemplo: «para revisar qué se dijo en una junta, lee la transcripción literal, nunca el resumen».
  • MEJORAS (lo que necesita código): una capacidad que Donna no tiene (p. ej. ejecutar el agente Vizumi de Kawiil Central) o un fallo. Donna las registra con la conversación
    exacta donde falló; en la app se copian como texto listo para dárselo a Claude Code. Así «venir al código» ya no empieza de cero.
"""
import re
import time

import donna_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS reglas (id TEXT PRIMARY KEY, creado INTEGER NOT NULL, actualizado INTEGER NOT NULL, cuando TEXT NOT NULL, regla TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
  origen TEXT NOT NULL DEFAULT '', estado TEXT NOT NULL DEFAULT 'activa');
CREATE TABLE IF NOT EXISTS mejoras (id TEXT PRIMARY KEY, creado INTEGER NOT NULL, titulo TEXT NOT NULL, detalle TEXT NOT NULL, evidencia TEXT NOT NULL DEFAULT '', tipo TEXT NOT NULL DEFAULT 'capacidad',
  estado TEXT NOT NULL DEFAULT 'pendiente', nota TEXT NOT NULL DEFAULT '', veces INTEGER NOT NULL DEFAULT 1);
"""


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


def _tok(s: str) -> set:
    import donna_personas
    return {t for t in re.findall(r"[a-z0-9]{4,}", donna_personas.norm(s))}


def _parecido(a: str, b: str, umbral: float = 0.6) -> bool:
    ta, tb = _tok(a), _tok(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= umbral


# ------------------------------------------------------------------ reglas
def regla_agregar(cuando: str, regla: str, error: str = "", origen: str = "", estado: str = "activa") -> dict:
    cuando, regla = " ".join((cuando or "").split())[:200], " ".join((regla or "").split())[:400]
    if len(regla) < 12 or len(cuando) < 4:
        raise ValueError("Una regla necesita «cuándo aplica» y «qué hacer» (con algo de detalle).")
    now = int(time.time())
    c = _c()
    try:
        for r in c.execute("SELECT * FROM reglas WHERE estado!='borrada'"):
            if _parecido(r["cuando"] + " " + r["regla"], cuando + " " + regla, 0.7):         # misma regla dicha otra vez: se actualiza, no se duplica
                if estado == "propuesta":                                                      # una propuesta nunca pisa una regla que ya existe (activa, pausada o por aceptar)
                    return {"id": r["id"], "nueva": False}
                c.execute("UPDATE reglas SET cuando=?, regla=?, error=CASE WHEN ?!='' THEN ? ELSE error END, origen=CASE WHEN ?!='' THEN ? ELSE origen END, estado='activa', actualizado=? WHERE id=?",
                          (cuando, regla, error[:300], error[:300], origen[:600], origen[:600], now, r["id"]))
                c.commit()
                return {"id": r["id"], "nueva": False}
        rid = donna_db._id()
        c.execute("INSERT INTO reglas(id,creado,actualizado,cuando,regla,error,origen,estado) VALUES(?,?,?,?,?,?,?,?)", (rid, now, now, cuando, regla, error[:300], origen[:600], "propuesta" if estado == "propuesta" else "activa"))
        c.commit()
        return {"id": rid, "nueva": True}
    finally:
        c.close()


def reglas_list(estados=("activa", "pausada", "propuesta")) -> list:
    c = _c()
    try:
        return [{k: r[k] for k in r.keys()} for r in c.execute("SELECT * FROM reglas WHERE estado IN (%s) ORDER BY (estado='activa') DESC, actualizado DESC" % ",".join("?" * len(estados)), list(estados))]
    finally:
        c.close()


def regla_accion(rid: str, accion: str, regla: str = "", cuando: str = "") -> dict:
    c = _c()
    try:
        if not c.execute("SELECT 1 FROM reglas WHERE id=?", (rid,)).fetchone():
            raise ValueError("regla no encontrada")
        now = int(time.time())
        if accion == "aceptar":
            c.execute("UPDATE reglas SET estado='activa', actualizado=? WHERE id=? AND estado='propuesta'", (now, rid))
        elif accion == "rechazar":
            c.execute("UPDATE reglas SET estado='borrada', actualizado=? WHERE id=? AND estado='propuesta'", (now, rid))
        elif accion == "pausar":
            c.execute("UPDATE reglas SET estado='pausada', actualizado=? WHERE id=?", (now, rid))
        elif accion == "activar":
            c.execute("UPDATE reglas SET estado='activa', actualizado=? WHERE id=?", (now, rid))
        elif accion == "borrar":
            c.execute("UPDATE reglas SET estado='borrada', actualizado=? WHERE id=?", (now, rid))
        elif accion == "editar":
            regla = " ".join((regla or "").split())[:400]
            if len(regla) < 12:
                raise ValueError("Escribe la regla completa.")
            c.execute("UPDATE reglas SET regla=?, cuando=CASE WHEN ?!='' THEN ? ELSE cuando END, actualizado=? WHERE id=?", (regla, cuando[:200], cuando[:200], now, rid))
        else:
            raise ValueError("acción no válida")
        c.commit()
        return {"ok": True}
    finally:
        c.close()


def contexto_reglas(n: int = 25) -> str:
    """Se agrega al contexto de Donna en TODAS las conversaciones: lo que Polo le ha corregido (y las reglas de oficio base). Las propuestas por aceptar NO se aplican."""
    try:
        import donna_oficio
        donna_oficio.sembrar()
    except Exception:
        pass
    L = [r for r in reglas_list(("activa",))][:n]
    if not L:
        return ""
    return ("\n\n# REGLAS QUE POLO ME HA CORREGIDO (obedécelas SIEMPRE; mandan sobre tus hábitos)\n"
            + "\n".join("- Cuando %s: %s" % (r["cuando"].rstrip(".:"), r["regla"]) for r in L) + "\n")


# ------------------------------------------------------------------ mejoras (lo que necesita código)
def mejora_agregar(titulo: str, detalle: str, evidencia: str = "", tipo: str = "capacidad") -> dict:
    titulo, detalle = " ".join((titulo or "").split())[:140], " ".join((detalle or "").split())[:900]
    if len(titulo) < 8 or len(detalle) < 15:
        raise ValueError("Una mejora necesita un título y qué falló o qué falta.")
    tipo = tipo if tipo in ("capacidad", "bug", "dato") else "capacidad"
    c = _c()
    try:
        for r in c.execute("SELECT * FROM mejoras WHERE estado='pendiente'"):
            if _parecido(r["titulo"], titulo, 0.6):
                c.execute("UPDATE mejoras SET veces=veces+1, evidencia=CASE WHEN ?!='' THEN ? ELSE evidencia END WHERE id=?", (evidencia[:2500], evidencia[:2500], r["id"]))
                c.commit()
                return {"id": r["id"], "nueva": False}
        mid = donna_db._id()
        c.execute("INSERT INTO mejoras(id,creado,titulo,detalle,evidencia,tipo) VALUES(?,?,?,?,?,?)", (mid, int(time.time()), titulo, detalle, evidencia[:2500], tipo))
        c.commit()
        return {"id": mid, "nueva": True}
    finally:
        c.close()


def mejoras_list(estados=("pendiente",)) -> list:
    c = _c()
    try:
        return [{k: r[k] for k in r.keys()} for r in c.execute("SELECT * FROM mejoras WHERE estado IN (%s) ORDER BY veces DESC, creado DESC" % ",".join("?" * len(estados)), list(estados))]
    finally:
        c.close()


def mejora_estado(mid: str, estado: str, nota: str = "") -> dict:
    if estado not in ("resuelta", "descartada", "pendiente"):
        raise ValueError("estado no válido")
    c = _c()
    try:
        cur = c.execute("UPDATE mejoras SET estado=?, nota=? WHERE id=?", (estado, nota[:300], mid))
        c.commit()
        if cur.rowcount == 0:
            raise ValueError("mejora no encontrada")
        return {"ok": True}
    finally:
        c.close()


def mejoras_markdown() -> str:
    """Texto listo para pegárselo a Claude Code: qué pasó, con la conversación exacta, y qué se espera."""
    L = mejoras_list()
    if not L:
        return "No hay mejoras pendientes."
    out = ["# Mejoras pendientes de Donna (para el código)\n"]
    for m in L:
        out.append("## %s  _(%s%s)_\n%s\n" % (m["titulo"], m["tipo"], (", pasó %d veces" % m["veces"]) if m["veces"] > 1 else "", m["detalle"]))
        if m["evidencia"]:
            out.append("**Evidencia (conversación o datos reales):**\n```\n%s\n```\n" % m["evidencia"])
    return "\n".join(out)
