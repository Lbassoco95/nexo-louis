#!/usr/bin/env python3
"""medir-audio.py — ¿qué tan BAJO quedó el audio guardado de una junta cuando HAY VOZ? (solo lee, no cambia nada).

Uso (en el servidor):  python3 ~/donna-deploy/cloud/hetzner/tools/medir-audio.py [ID_DE_JUNTA]      (sin ID: la última junta con audio)
La primera versión promediaba también los silencios y exageraba lo bajo que está el audio. Este mide el nivel (RMS, en dBFS) de cada
segundo del tramo y reporta el de los segundos CON VOZ (p90: el 10 % de los segundos más fuertes), el de la mediana (que suele ser pausa o ruido de fondo)
y el pico. Referencia: una voz cercana y clara tiene ~-26 a -18 dB RMS; con ~-34 dB se oye bajo; con menos de -42 dB casi no se entiende.
Para cada tramo: nivel global y por bloques de 10 minutos, para ver si cambia (te moviste, se alejó el micrófono…).
"""
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

DB = os.environ.get("DONNA_DB", "/opt/openclaw/state/donna.db")
DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state")) / "escucha_audio"


def serie(archivo):
    """Nivel RMS (dBFS) de cada segundo del audio."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(archivo), "-af",
                        "aresample=16000,asetnsamples=n=16000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=3600)
    out = []
    for m in re.finditer(r"lavfi\.astats\.Overall\.RMS_level=(\S+)", r.stdout):
        x = m.group(1)
        out.append(-90.0 if x in ("-inf", "inf") else max(-90.0, float(x)))
    return out


def pico(archivo):
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(archivo), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
    m = re.search(r"max_volume:\s*(-?[0-9.]+) dB", r.stderr)
    return float(m.group(1)) if m else None


def pct(v, q):
    w = sorted(v)
    return w[min(len(w) - 1, int(q * len(w)))] if w else None


def veredicto(voz):
    if voz is None:
        return "sin datos"
    if voz > -26:
        return "bien"
    if voz > -34:
        return "algo bajo (Donna lo compensa)"
    if voz > -42:
        return "BAJO"
    return "MUY BAJO / casi inaudible"


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
    print("Junta %s · %d tramo(s)   (puede tardar un par de minutos)" % (sid, len(tr)))
    todo = []
    for t in tr:
        d = DIR / t["aid"]
        fuentes = sorted(d.glob("audio.*")) if d.exists() else []
        if not fuentes:
            print("\n  tramo %s: no hay archivo ensamblado todavía (abre la junta una vez para que se arme)" % t["aid"][:8])
            continue
        v = serie(fuentes[0])
        todo += v
        voz = pct(v, 0.90)
        print("\n  tramo %s · desde el minuto %d de la junta · %d min" % (t["aid"][:8], t["offset_ms"] // 60000, len(v) // 60))
        print("    CON VOZ (p90): %5.1f dB · mediana (pausas/ruido): %5.1f dB · pico: %s dB  → %s" % (voz, pct(v, 0.5), pico(fuentes[0]), veredicto(voz)))
        for i in range(0, len(v), 600):
            bloque = v[i:i + 600]
            if len(bloque) >= 60:
                x = pct(bloque, 0.90)
                print("      min %3d–%3d: con voz %5.1f dB  → %s" % ((t["offset_ms"] / 1000 + i) // 60, (t["offset_ms"] / 1000 + i + len(bloque)) // 60, x, veredicto(x)))
    if todo:
        print("\nTODA LA JUNTA: con voz %.1f dB (p90) · %.0f %% de los segundos son silencio o ruido muy bajo (< -60 dB)" % (pct(todo, 0.90), 100.0 * sum(1 for x in todo if x < -60) / len(todo)))
    print("\nCómo leerlo: «con voz» es lo que importa para entender. Si está por debajo de -34 dB, acerca el micrófono o sube la entrada")
    print("(Ajustes del Sistema → Sonido → Entrada). Donna sube el volumen al reproducir y en los fragmentos, pero subir un audio muy bajo también sube el ruido.")


if __name__ == "__main__":
    main()
