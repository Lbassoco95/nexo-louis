#!/usr/bin/env python3
"""donna_reconstruir.py — reconstruir el TEXTO de una junta a partir de su AUDIO guardado.

Cuando la transcripción en vivo falla (se cortó el micrófono, la pestaña se cerró, el servidor se reinició…) lo único que queda es el audio. Aquí ese audio se manda a
OpenAI (`gpt-4o-transcribe-diarize`: transcribe y etiqueta quién habló, con tiempos), con el mismo mecanismo de piezas de 20 min de la revisión de voces, y con el resultado
se crea una JUNTA nueva (intervenciones con hora y voz) ligada a ese mismo audio. Se hace SOLO cuando Polo lo pide con un botón y confirma que el audio sale hacia OpenAI.
"""
import json
import re
import time
from pathlib import Path

import donna_audio
import donna_db
import donna_diariza
import donna_personas

SCHEMA = "CREATE TABLE IF NOT EXISTS audio_reconstruccion (aid TEXT PRIMARY KEY, ts INTEGER NOT NULL, estado TEXT NOT NULL, datos TEXT NOT NULL DEFAULT '{}');"
HUECO_S = 2.0            # dos frases seguidas de la misma voz con menos de esto entre ellas forman una sola intervención
MAX_CHARS = 2800         # tope por intervención (la base guarda hasta 3000)


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


def _guardar(aid: str, estado: str, datos: dict):
    if estado == "procesando":
        import os
        datos = {**datos, "pid": os.getpid()}                  # si ese proceso ya no existe, quedó huérfana (reinicio) y se puede relanzar
    c = _c()
    try:
        c.execute("INSERT OR REPLACE INTO audio_reconstruccion(aid,ts,estado,datos) VALUES(?,?,?,?)", (aid, int(time.time()), estado, json.dumps(datos, ensure_ascii=False)))
        c.commit()
    finally:
        c.close()


def estado(aid: str) -> dict:
    c = _c()
    try:
        r = c.execute("SELECT ts, estado, datos FROM audio_reconstruccion WHERE aid=?", (aid,)).fetchone()
    finally:
        c.close()
    if not r:
        return {"estado": "nada"}
    d = json.loads(r["datos"] or "{}")
    d.update(estado=r["estado"], ts=r["ts"])
    return d


def _hora(seg: float) -> str:
    s = int(max(0, seg))
    return "%02d:%02d" % (s // 60, s % 60)                     # minutos sin tope, como en las demás juntas («170:01»)


def _turnos(segs: list) -> list:
    """Segmentos [{spk, ini, fin, texto}] → intervenciones: se unen las frases seguidas de la misma voz (con una pausa corta) para que se lea como una junta, no como renglones sueltos."""
    out = []
    for s in sorted(segs, key=lambda x: (x["ini"], x["fin"])):
        t = (s.get("texto") or "").strip()
        if not t:
            continue
        u = out[-1] if out else None
        if u and u["spk"] == s["spk"] and s["ini"] - u["fin"] <= HUECO_S and len(u["texto"]) + 1 + len(t) <= MAX_CHARS:
            u["texto"] += " " + t
            u["fin"] = max(u["fin"], s["fin"])
        else:
            out.append({"spk": s["spk"], "ini": s["ini"], "fin": s["fin"], "texto": t[:MAX_CHARS]})
    return out


def validar(aid: str, usuario: str) -> dict:
    """Comprueba que ese audio existe, es de este usuario, está suelto (sin junta) y NO se está grabando ahora. → su fila."""
    e = donna_audio.estado(aid) if donna_audio.valido(aid) else None
    if not e or e["estado"] == "borrado":
        raise ValueError("audio no encontrado")
    if e["usuario"] != usuario and usuario != "admin":
        raise ValueError("audio no encontrado")
    if e.get("sesion_id"):
        raise ValueError("Ese audio ya está ligado a una junta.")
    if donna_audio.activo(e):
        raise ValueError("Ese audio se está grabando ahora mismo. Termina y guarda la junta primero.")
    return e


def procesar(aid: str, usuario: str = "admin", llamar=None, key: str = None, trabajo_base: Path = None, titulo: str = "") -> dict:
    """Pasada completa. Nunca lanza: deja el resultado (o el error) en la tabla para que la app lo muestre. → {estado, sesion_id?, error?}"""
    try:
        e = validar(aid, usuario)
    except ValueError as ex:
        return {"estado": "error", "error": str(ex)}
    _guardar(aid, "procesando", {})
    try:
        import app_media
        key = key if key is not None else app_media.openai_key()
        if not key and llamar is None:
            raise RuntimeError("Falta OPENAI_API_KEY en el servidor")
        audio = donna_audio.ensamblar(aid)
        dur = donna_diariza._dur(audio)
        base = (trabajo_base or donna_audio.DIR.parent / "diariza_tmp") / ("rec-" + aid)
        prog = lambda k, n: _guardar(aid, "procesando", {"progreso": "pieza %d de %d" % (k + 1, n), "piezas": n})
        segs, info = donna_diariza.diarizar_archivo(key, audio, base, llamar, prog, donna_diariza.MAX_S)
        turnos = _turnos(segs)
        if not turnos:
            raise RuntimeError("El identificador no devolvió texto (¿el audio está vacío o muy bajo?)")
        off = (e.get("offset_ms") or 0) / 1000.0
        voz = {}
        for t in turnos:
            voz.setdefault(t["spk"], len(voz) + 1)
        lineas = [{"t": _hora(t["ini"] + off), "voz": voz[t["spk"]], "es": t["texto"], "orig": "", "idioma": ""} for t in turnos]
        cuando = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("creado") or time.time()))
        res = donna_personas.guardar_sesion((titulo or "Transcripción reconstruida del audio · %s" % cuando)[:120], int(dur + off), [{"n": n, "nombre": ""} for n in voz.values()], lineas, usuario=usuario)
        sid = res["sesion_id"]
        if not donna_audio.ligar_tramo(aid, sid, usuario):
            raise RuntimeError("Se creó la junta pero no pude ligarle el audio")
        out = {"sesion_id": sid, "turnos": len(lineas), "voces": len(voz), "minutos": round(dur / 60, 1), "piezas": info["piezas"], "etiquetas_alineadas": info["etiquetas_alineadas"]}
        _guardar(aid, "lista", out)
        return {"estado": "lista", **out}
    except Exception as ex:
        _guardar(aid, "error", {"error": str(ex)[:300]})
        return {"estado": "error", "error": str(ex)[:300]}
    finally:
        import shutil
        shutil.rmtree((trabajo_base or donna_audio.DIR.parent / "diariza_tmp") / ("rec-" + aid), ignore_errors=True)
