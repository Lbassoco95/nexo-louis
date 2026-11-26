#!/usr/bin/env python3
"""auditar-audio.py — ¿están TODAS las grabaciones? Cruza la base de datos con los archivos del disco (solo lee, no cambia ni borra nada).

Uso (en el servidor):  python3 ~/donna-deploy/cloud/hetzner/tools/auditar-audio.py [DIAS]        (por defecto, los últimos 7 días)

Para cada grabación de audio dice: cuándo empezó y terminó, cuánto pesa, si TODAS sus partes están en el disco (cada parte son ~10 s; si falta una hay un hueco),
si fue borrada (por ti, o por vencer su plazo) y a qué junta pertenece. También busca archivos en el disco que la base no conoce, y los borradores y copias de rescate de texto.
"""
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB = os.environ.get("DONNA_DB", "/opt/openclaw/state/donna.db")
DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state")) / "escucha_audio"
TIMESLICE_S = 10


def hora(ts):
    return time.strftime("%d-%b %H:%M", time.localtime(ts)) if ts else "?"


def mins(s):
    s = int(s)
    return "%d h %02d min" % (s // 3600, s % 3600 // 60) if s >= 3600 else "%d min" % max(1, round(s / 60))


def q(c, sql, a=()):
    """Consulta que no revienta si una tabla todavía no existe en esta base."""
    try:
        return [dict(r) for r in c.execute(sql, a)]
    except sqlite3.Error:
        return []


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    c.row_factory = sqlite3.Row
    desde = time.time() - dias * 86400
    sesiones = {r["id"]: r for r in q(c, "SELECT id, titulo, fecha, duracion, lineas FROM sesiones_escucha")}
    filas = q(c, "SELECT * FROM escucha_audio ORDER BY creado")
    conocidos = {f["aid"] for f in filas}
    print("AUDIOS DE LOS ÚLTIMOS %d DÍAS (%s en adelante)\n" % (dias, hora(desde)))
    problemas, total_mb, n = [], 0.0, 0
    for f in filas:
        if (f["actualizado"] or f["creado"] or 0) < desde:
            continue
        n += 1
        ruta = DIR / f["aid"]
        partes = sorted(ruta.glob("*.part")) if ruta.is_dir() else []
        idx = sorted(int(re.sub(r"\D", "", p.stem) or -1) for p in partes)
        faltan = [i for i in range(idx[-1] + 1) if i not in set(idx)] if idx else []
        mb = sum(p.stat().st_size for p in partes) / 1048576
        total_mb += mb
        dur = (f.get("dur_s") or 0) or (f["partes"] or len(partes)) * TIMESLICE_S
        marcas = []
        if f["estado"] == "borrado":
            marcas.append("🗑️ BORRADO: el archivo ya no existe (lo borraste, o venció su plazo)")
        elif not partes:
            marcas.append("⚠️ FALTAN LOS ARCHIVOS EN EL DISCO")
        else:
            if len(partes) < (f["partes"] or 0):
                marcas.append("⚠️ el disco tiene %d partes y la base dice %d" % (len(partes), f["partes"]))
            if faltan:
                marcas.append("⚠️ HUECOS: faltan las partes %s (≈ %s de audio)" % (", ".join(map(str, faltan[:6])) + ("…" if len(faltan) > 6 else ""), mins(len(faltan) * TIMESLICE_S)))
            if f["bytes"] and mb * 1048576 < 0.97 * f["bytes"]:
                marcas.append("⚠️ el disco tiene menos de lo que la base registró (%.0f de %.0f MB)" % (mb, f["bytes"] / 1048576))
        s = sesiones.get(f["sesion_id"]) if f["sesion_id"] else None
        if f["estado"] == "borrado":
            liga = ""
        elif s:
            liga = "→ junta «%s» (%s · %s intervenciones)" % (s["titulo"], s["fecha"], s["lineas"])
        elif f["sesion_id"]:
            liga = "⚠️ ligado a una junta que ya no existe (%s)" % f["sesion_id"]
            marcas.append(liga)
        elif f["estado"] == "grabando" and time.time() - (f["actualizado"] or 0) < 180:
            liga = "🔴 se está grabando ahora mismo (todavía sin junta: se liga al guardarla)"
        else:
            liga = "SIN JUNTA: no está ligado a ninguna (en Escuchar puedes ligarlo a una junta o reconstruir su texto)"
            marcas.append("⚠️ " + liga)
        icono = "✓" if not [m for m in marcas if m.startswith("⚠️")] and f["estado"] != "borrado" else ("🗑️" if f["estado"] == "borrado" else "⚠️")
        print("%s %s · %s → %s (%s) · %.0f MB · %d partes · estado: %s" % (icono, f["aid"][:8], hora(f["creado"]), hora(f["actualizado"]), mins(dur), mb, len(partes), f["estado"]))
        if liga and not liga.startswith("⚠️ ligado") and not liga.startswith("SIN JUNTA"):
            print("     " + liga)
        for m in marcas:
            print("     " + m)
        if any(m.startswith("⚠️") for m in marcas) or f["estado"] == "borrado":
            problemas.append(f["aid"][:8])
    sueltos = []
    if DIR.is_dir():
        for d in sorted(DIR.iterdir()):
            if d.is_dir() and d.name != "export" and d.name not in conocidos and any(d.glob("*.part")):
                sueltos.append(d.name)
    if sueltos:
        print("\n⚠️ ARCHIVOS EN EL DISCO QUE LA BASE NO CONOCE:")
        for a in sueltos:
            print("   %s · %.0f MB" % (a[:8], sum(p.stat().st_size for p in (DIR / a).glob("*.part")) / 1048576))
    print("\nJUNTAS GUARDADAS EN ESE PERIODO (con y sin audio)")
    ligadas = {f["sesion_id"] for f in filas if f["sesion_id"] and f["estado"] != "borrado"}
    for sid, s in sorted(sesiones.items(), key=lambda kv: kv[1]["fecha"]):
        if s["fecha"] >= time.strftime("%Y-%m-%d", time.localtime(desde)):
            print("  • %s · %s · %s intervenciones · %s%s" % (s["fecha"], s["titulo"], s["lineas"], mins(s["duracion"]), "" if sid in ligadas else "  (sin audio)"))
    bor = q(c, "SELECT id, actualizado FROM escucha_borradores WHERE actualizado>?", (desde,))
    res = q(c, "SELECT id, actualizado FROM escucha_rescate WHERE actualizado>?", (desde,))
    if bor or res:
        print("\nTEXTO SIN GUARDAR COMO JUNTA")
        for b in bor:
            print("  • borrador %s · último autoguardado %s" % (b["id"][:8], hora(b["actualizado"])))
        for r in res:
            print("  • copia de rescate %s · %s" % (r["id"][:8], hora(r["actualizado"])))
    print("\nRESUMEN: %d audio(s), %.1f GB en disco · %s" % (n, total_mb / 1024, ("revisa los de arriba marcados con ⚠️/🗑️: " + ", ".join(problemas)) if problemas else "todo completo ✓"))
    try:
        st = os.statvfs(str(DIR))
        print("Espacio libre en el servidor: %.1f GB" % (st.f_bavail * st.f_frsize / 1024 ** 3))
    except OSError:
        pass


if __name__ == "__main__":
    main()
