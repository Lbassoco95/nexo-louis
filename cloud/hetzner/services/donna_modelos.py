#!/usr/bin/env python3
"""donna_modelos.py — qué modelo usa cada tarea de Donna, cuánto cuesta de verdad, y cómo cambiarlo sin tocar código.

Criterio: cada rol usa el modelo MÁS BARATO que haga bien su trabajo, y solo se sube a uno más fuerte cuando hace falta (p. ej. cuando el verificador no pudo respaldar
una respuesta en la junta). Se cambia con variables de entorno en /opt/openclaw/openclaw.env (DONNA_MODELO_<ROL>) y se mide con tools/probar-modelos.py.
El uso real (tokens por rol y modelo) se guarda en la base para ver el costo estimado en la app. Los precios son los que conocemos de los modelos listados; para un modelo
sin precio aquí se muestran los tokens sin inventar un costo (verifica precios vigentes en tu consola del proveedor).
"""
import os
import time

import donna_db

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
DEFAULTS = {"junta": HAIKU, "junta_escalada": SONNET}
ENV = {"junta": "DONNA_MODELO_JUNTA", "junta_escalada": "DONNA_MODELO_JUNTA_ESCALADA"}
# USD por millón de tokens (entrada, salida). Solo lo que conocemos; lo demás se reporta en tokens.
PRECIOS = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-6": (3.0, 15.0), "claude-sonnet-4-5": (3.0, 15.0)}
SCHEMA = "CREATE TABLE IF NOT EXISTS uso_modelos (n INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, rol TEXT NOT NULL, modelo TEXT NOT NULL, tin INTEGER NOT NULL DEFAULT 0, tout INTEGER NOT NULL DEFAULT 0);"


def modelo(rol: str) -> str:
    return (os.environ.get(ENV.get(rol, ""), "") or "").strip() or DEFAULTS[rol]


def escalar_activo() -> bool:
    return os.environ.get("DONNA_JUNTA_ESCALAR", "1") != "0" and modelo("junta_escalada") != modelo("junta")


def precio(m: str):
    for k, v in PRECIOS.items():
        if (m or "").startswith(k):
            return v
    return None


def costo(m: str, tin: int, tout: int):
    p = precio(m)
    return None if not p else (tin * p[0] + tout * p[1]) / 1_000_000


def uso_registrar(rol: str, m: str, tin: int, tout: int):
    try:
        c = donna_db.conn()
        c.executescript(SCHEMA)
        c.execute("INSERT INTO uso_modelos(ts,rol,modelo,tin,tout) VALUES(?,?,?,?,?)", (int(time.time()), rol, m, int(tin or 0), int(tout or 0)))
        c.commit()
        c.close()
    except Exception:
        pass


def uso_resumen(dias: int = 7) -> list:
    c = donna_db.conn()
    try:
        c.executescript(SCHEMA)
        out = []
        for r in c.execute("SELECT rol, modelo, COUNT(*) n, SUM(tin) tin, SUM(tout) tout FROM uso_modelos WHERE ts>=? GROUP BY rol, modelo ORDER BY tin+tout DESC", (int(time.time()) - dias * 86400,)):
            out.append({"rol": r["rol"], "modelo": r["modelo"], "llamadas": r["n"], "tokens_in": r["tin"], "tokens_out": r["tout"], "costo_usd": costo(r["modelo"], r["tin"], r["tout"])})
        return out
    finally:
        c.close()
