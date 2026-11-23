#!/usr/bin/env python3
"""probar-diarizacion.py — prueba la identificación de voces de OpenAI con un audio REAL, antes de confiar en ella.

La parte de OpenAI (formato de la petición y de la respuesta de `gpt-4o-transcribe-diarize`) se escribió según su documentación pública pero no se pudo
probar contra la API real desde el repositorio. Este script la ejerce de verdad con tu llave y te muestra qué devuelve.

Uso (en el servidor, como polo):
  cd /opt/openclaw/scripts && python3 ~/donna-deploy/cloud/hetzner/tools/probar-diarizacion.py AUDIO.m4a [SEGUNDOS]
  · AUDIO: cualquier audio/video que ffmpeg lea (una nota de voz con dos personas…) — o «sesion:ID»: el audio guardado de una junta de Escuchar
    (el ID sale de tools/verificar-junta.py). Ej.:  python3 .../probar-diarizacion.py sesion:398ae52de1a6 120   ·   o   sesion:ultima   (la junta guardada más reciente con audio)
  · SEGUNDOS: cuánto probar (por defecto 120 s: cuesta centavos). Con 0 procesa todo.

Muestra: voces detectadas, tiempo hablado por voz, primeras líneas con su voz y el tiempo que tardó. Nada se guarda en tu base de datos.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/opt/openclaw/scripts")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services"))
if not (len(sys.argv) > 1 and sys.argv[1].startswith("sesion:")):     # con un archivo suelto: base temporal (no toca donna.db); con «sesion:ID» hay que leer la real
    os.environ.setdefault("PUSH_STATE_DIR", tempfile.mkdtemp())
import app_media  # noqa: E402
import donna_diariza as DZ  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    seg = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    if sys.argv[1].startswith("sesion:"):
        import donna_audio
        ref = sys.argv[1].split(":", 1)[1]
        if ref in ("ultima", "última"):
            ref = donna_audio.ultima_con_audio()
            if not ref:
                sys.exit("Ninguna junta guardada tiene audio todavía. Guarda primero la junta (en Escuchar: «Guardarla así» o «Detener y guardar»).")
            print("Usando la junta guardada más reciente con audio:", ref)
        tr = donna_audio.tramos(ref)
        if not tr:
            sys.exit("Esa junta no tiene audio guardado (se grabó antes del audio, lo apagaste en Ajustes, o ya se borró). Prueba con otra o con un archivo.")
        audio = donna_audio.ensamblar(tr[0]["aid"])
    else:
        audio = Path(sys.argv[1]).expanduser()
    if not audio.exists():
        sys.exit("No encuentro %s. Pon la ruta REAL de un audio (o sesion:ID)." % audio)
    key = app_media.openai_key()
    if not key:
        sys.exit("Falta OPENAI_API_KEY en el servidor.")
    trabajo = Path(tempfile.mkdtemp())
    try:
        entrada = audio
        if seg > 0:
            entrada = trabajo / "corto.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(audio), "-t", str(seg), "-vn", "-ac", "1", "-ar", "16000", str(entrada)], capture_output=True, check=True)
        t0 = time.time()
        print("Modelo:", DZ.MODELO, "· audio:", audio.name, "· procesando", ("%d s" % seg) if seg else "todo")
        segs, info = DZ.diarizar_archivo(key, entrada, trabajo / "w")
        dt = time.time() - t0
        por = defaultdict(float)
        for s in segs:
            por[s["spk"]] += s["fin"] - s["ini"]
        print("\nTardó %.0f s · %d segmentos · %d voces · piezas: %d · etiquetas alineadas: %s" % (dt, len(segs), len(por), info["piezas"], info["etiquetas_alineadas"]))
        for lab, t in sorted(por.items(), key=lambda kv: -kv[1]):
            print("  voz %-8s %5.0f s hablados" % (lab, t))
        print("\nPrimeras líneas:")
        for s in segs[:12]:
            print("  [%5.1f–%5.1f] %-6s %s" % (s["ini"], s["fin"], s["spk"], s["texto"][:100]))
        print("\n¿Se distinguió bien a cada persona y las interrupciones? Si no, dime qué ves y probamos pyannote con el MISMO audio.")
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


if __name__ == "__main__":
    main()
