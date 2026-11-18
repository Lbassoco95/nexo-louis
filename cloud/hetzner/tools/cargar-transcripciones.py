#!/usr/bin/env python3
"""cargar-transcripciones.py — carga a la base de Donna (TRANSCRIPCIONES.md) TODAS las transcripciones y conversaciones traducidas que aún no estén.
Uso en el servidor:  python3 ~/donna-deploy/cloud/hetzner/tools/cargar-transcripciones.py
Es lo mismo que le pides a Donna con «carga las transcripciones a tu base», pero directo y sin límite de tiempo. Se puede repetir sin duplicar."""
import os, sys
OC = os.environ.get("OPENCLAW_HOME", "/opt/openclaw")
os.environ.setdefault("PUSH_STATE_DIR", OC + "/state")
os.environ.setdefault("OPENCLAW_SPACES", OC + "/spaces/general")
sys.path.insert(0, OC + "/scripts")
import app_media as M

r = M.ingerir_todo()
print(f"✓ Transcripciones cargadas a la base de Donna: {r['nuevas']} nuevas, {r['ya']} ya estaban" + (f", {r['errores']} con error" if r["errores"] else "") + f" (de {r['total']} listas).")
print("  Archivo:", os.environ["OPENCLAW_SPACES"] + "/TRANSCRIPCIONES.md")
