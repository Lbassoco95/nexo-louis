#!/usr/bin/env python3
"""verificar-junta.py — ¿la junta quedó guardada COMPLETA? Compara lo que dice la junta con lo que de verdad hay en la base, y revisa borradores, rescate y audio.

Uso (en el servidor):  python3 ~/donna-deploy/cloud/hetzner/tools/verificar-junta.py [N]      (N = cuántas juntas recientes revisar; por defecto 3)
Solo lee la base (no cambia nada). Marca con ⚠️ lo que huele mal: pocas intervenciones para la duración, textos cortados a 3000 caracteres, borradores sin guardar,
rescates o audio sin junta, y diferencias entre lo declarado y lo guardado.
"""
import os
import sqlite3
import sys
import time

DB = os.environ.get("DONNA_DB", "/opt/openclaw/state/donna.db")


def q(c, sql, a=()):
    try:
        return c.execute(sql, a).fetchall()
    except sqlite3.Error:
        return []


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    c.row_factory = sqlite3.Row
    ses = q(c, "SELECT id, titulo, duracion, lineas, datetime(ts,'unixepoch','localtime') AS cuando, archivo FROM sesiones_escucha ORDER BY ts DESC LIMIT ?", (n,))
    print("JUNTAS GUARDADAS (las %d más recientes)" % len(ses))
    for s in ses:
        iv = q(c, "SELECT COUNT(*) AS n, COALESCE(MAX(LENGTH(texto)),0) AS mx, COALESCE(SUM(LENGTH(texto)),0) AS chars, SUM(CASE WHEN voz_n=0 THEN 1 ELSE 0 END) AS sinvoz FROM intervenciones WHERE sesion_id=?", (s["id"],))[0]
        mins = max(1, round(s["duracion"] / 60))
        avisos = []
        if iv["n"] != s["lineas"]:
            avisos.append("declara %d líneas y hay %d" % (s["lineas"], iv["n"]))
        if mins >= 10 and iv["n"] < mins / 3:
            avisos.append("muy pocas intervenciones para %d min (¿se perdió texto?)" % mins)
        if iv["mx"] >= 2900:
            avisos.append("hay una intervención de %d caracteres: puede estar cortada a 3000" % iv["mx"])
        if iv["n"] and iv["sinvoz"] == iv["n"]:
            avisos.append("ninguna voz identificada")
        audio = q(c, "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b, MAX(vence) AS v FROM escucha_audio WHERE sesion_id=? AND estado!='borrado'", (s["id"],))
        voces = q(c, "SELECT estado FROM junta_voces WHERE sesion_id=?", (s["id"],))
        print("\n• %s · %s · %d min · id %s" % (s["cuando"], s["titulo"], mins, s["id"]))
        print("  intervenciones: %d (%d caracteres en total, la más larga %d) · sin identificar: %s" % (iv["n"], iv["chars"], iv["mx"], iv["sinvoz"]))
        if audio and audio[0]["n"]:
            print("  audio: %d tramo(s), %.1f MB, vence %s · revisión de voces: %s" % (audio[0]["n"], audio[0]["b"] / 1e6, time.strftime("%Y-%m-%d", time.localtime(audio[0]["v"])) if audio[0]["v"] else "sin plazo", voces[0]["estado"] if voces else "sin correr"))
        else:
            print("  audio: no hay")
        for a in avisos:
            print("  ⚠️ " + a)
        if not avisos:
            print("  ✓ consistente")
    print("\nBORRADORES SIN GUARDAR")
    bs = q(c, "SELECT id, n, duracion, datetime(actualizado,'unixepoch','localtime') AS cuando FROM escucha_borradores ORDER BY actualizado DESC LIMIT 5")
    for b in bs:
        print("  ⚠️ %s · %d intervenciones · %d min · último autoguardado %s" % (b["id"], b["n"], round(b["duracion"] / 60), b["cuando"]))
    if not bs:
        print("  ninguno")
    print("\nCOPIA DE RESCATE DEL SERVIDOR (texto)")
    rs = q(c, "SELECT r.id, datetime(r.actualizado,'unixepoch','localtime') AS cuando, (SELECT COUNT(*) FROM escucha_rescate_lineas l WHERE l.rid=r.id) AS n FROM escucha_rescate r ORDER BY r.actualizado DESC LIMIT 5")
    for r in rs:
        print("  %s · %d frases · %s" % (r["id"], r["n"], r["cuando"]))
    if not rs:
        print("  ninguna (se borra al guardar la junta)")
    con_audio = q(c, "SELECT sesion_id FROM escucha_audio WHERE sesion_id!='' AND estado!='borrado' ORDER BY actualizado DESC LIMIT 1")
    if con_audio:
        print("\nPARA PROBAR LAS VOCES con el audio de la junta más reciente que lo tiene:")
        print("  cd /opt/openclaw/scripts && python3 ~/donna-deploy/cloud/hetzner/tools/probar-diarizacion.py sesion:%s 120" % con_audio[0]["sesion_id"])
    huerf = q(c, "SELECT aid, bytes, datetime(actualizado,'unixepoch','localtime') AS cuando FROM escucha_audio WHERE sesion_id='' AND estado!='borrado' ORDER BY actualizado DESC LIMIT 5")
    if huerf:
        print("\nAUDIO SIN JUNTA GUARDADA")
        for h in huerf:
            print("  ⚠️ %s · %.1f MB · %s" % (h["aid"], h["bytes"] / 1e6, h["cuando"]))


if __name__ == "__main__":
    main()
