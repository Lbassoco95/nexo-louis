#!/usr/bin/env python3
"""medir-audio.py — ¿qué tan BAJO quedó el audio guardado de una junta? Mide el volumen real de cada tramo (solo lee, no cambia nada).

Uso (en el servidor):  python3 ~/donna-deploy/cloud/hetzner/tools/medir-audio.py [ID_DE_JUNTA]      (sin ID: la última junta que tiene audio)
Para cada tramo mide, con ffmpeg, el volumen medio y el pico en dBFS (0 dB es lo máximo; una voz clara suele estar entre -25 y -15 dB de media),
tanto de todo el tramo como de ventanas de 60 s repartidas a lo largo de la junta, para ver si el volumen cambia (te moviste, se alejó el micrófono…).
Sirve para saber si el problema es lo que CAPTÓ el micrófono (entonces ningún programa lo arregla del todo: hay que acercarlo o subir la entrada)
o lo que se hace después con el audio.
"""
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

DB = os.environ.get("DONNA_DB", "/opt/openclaw/state/donna.db")
DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state")) / "escucha_audio"


def nivel(archivo, desde=None, dur=None):
    cmd = ["ffmpeg", "-nostdin", "-hide_banner"]
    if desde is not None:
        cmd += ["-ss", "%.1f" % desde, "-t", "%.1f" % dur]
    cmd += ["-i", str(archivo), "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    m = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", r.stderr)
    x = re.search(r"max_volume:\s*(-?[0-9.]+) dB", r.stderr)
    return (float(m.group(1)), float(x.group(1))) if m and x else None


def duracion(archivo):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(archivo)], capture_output=True, text=True, timeout=120)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def veredicto(media):
    if media is None:
        return "no se pudo medir"
    if media > -30:
        return "bien"
    if media > -40:
        return "bajo"
    if media > -50:
        return "MUY BAJO"
    return "CASI INAUDIBLE"


def main():
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    c.row_factory = sqlite3.Row
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    if not sid:
        r = c.execute("SELECT sesion_id FROM escucha_audio WHERE sesion_id!='' AND estado!='borrado' ORDER BY actualizado DESC LIMIT 1").fetchone()
        if not r:
            print("No hay juntas con audio guardado.")
            return
        sid = r["sesion_id"]
    tr = [dict(x) for x in c.execute("SELECT aid, offset_ms, bytes FROM escucha_audio WHERE sesion_id=? AND estado!='borrado' ORDER BY offset_ms", (sid,))]
    print("Junta %s · %d tramo(s)" % (sid, len(tr)))
    for t in tr:
        d = DIR / t["aid"]
        fuentes = sorted(d.glob("audio.*")) if d.exists() else []
        if not fuentes:
            print("  tramo %s: no hay archivo ensamblado todavía (abre la junta una vez para que se arme)" % t["aid"][:8])
            continue
        f = fuentes[0]
        dur = duracion(f)
        tot = nivel(f)
        print("\n  tramo %s · empieza en el minuto %d de la junta · dura %d min · %.0f MB" % (t["aid"][:8], t["offset_ms"] // 60000, dur // 60, t["bytes"] / 1048576))
        if tot:
            print("    TODO EL TRAMO:  media %5.1f dB · pico %5.1f dB  → %s" % (tot[0], tot[1], veredicto(tot[0])))
        paso = max(300.0, dur / 6)
        pos = 0.0
        while pos < dur - 20:
            n = nivel(f, pos, 60)
            if n:
                print("    min %3d (60 s):  media %5.1f dB · pico %5.1f dB  → %s" % ((t["offset_ms"] / 1000 + pos) // 60, n[0], n[1], veredicto(n[0])))
            pos += paso
    print("\nCómo leerlo: la media de una voz cercana y clara está entre -25 y -15 dB. Por debajo de -40 dB el micrófono captó poco: acércalo, sube el volumen de entrada")
    print("(Ajustes del Sistema → Sonido → Entrada) o usa un micrófono externo. Donna ya amplifica al grabar y al reproducir, pero amplificar un audio muy bajo sube también el ruido.")


if __name__ == "__main__":
    main()
