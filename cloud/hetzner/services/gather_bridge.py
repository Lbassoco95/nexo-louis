#!/usr/bin/env python3
"""
gather_bridge — refleja el estado de Louis en el Bot Status Monitor de Gather (Smart Object).

Estados (prioridad de mayor a menor):
  alert    → algún servicio/timer clave de Louis está en 'failed'
  working  → un agente arrancó una tarea en los últimos 90s
  question → hay entregables LISTO esperando Vo.Bo. o pendientes que vencen hoy
  on       → todo en orden

PRIVACIDAD: el feed de Gather lo ven todos los Members/Guests del espacio (incluye externos).
Aquí solo viajan conteos y textos genéricos — nunca clientes, temas legales ni títulos.

Uso: lo llama scheduler.py cada ~5 min vía run_once(); también CLI:
  gather_bridge.py            # calcula y envía solo si cambió
  gather_bridge.py --dry-run  # imprime lo que enviaría
  gather_bridge.py --ping     # verifica URL + secreto
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import louis_core as core

TZ_CDMX = timezone(timedelta(hours=-6))
HOME_OC = Path("/opt/openclaw") if Path("/opt/openclaw").exists() else Path.home() / ".openclaw"
STATE_FILE = HOME_OC / "logs" / "gather_status.json"
SEND_JS = Path(__file__).parent / "gather" / "send.mjs"
HEARTBEAT_S = 6 * 3600  # reenvía aunque no cambie, para que el objeto no quede viejo

# Servicios cuyo fallo debe prender la alerta.
UNITS_CLAVE = [
    "openclaw-gateway", "telegram-bridge", "scheduler",
    "dof-daily", "dof-daily-tarde", "dof-harvest", "dof-contents", "dof-pdfs",
    "sjf-update", "sjf-weekly", "legal-digest", "legal-estado",
]


def _units_fallidas() -> list[str]:
    malas = []
    for u in UNITS_CLAVE:
        try:
            r = subprocess.run(["systemctl", "is-failed", "--quiet", f"{u}.service"], timeout=5)
            if r.returncode == 0:  # 0 = failed
                malas.append(u)
        except Exception:
            continue
    return malas


def _agente_trabajando(ventana_s: int = 90) -> bool:
    f = core.AGENT_ACTIVITY_FILE
    if not f.exists():
        return False
    ultimo = {}
    try:
        for ln in f.read_text().splitlines()[-400:]:
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            if ev.get("agente"):
                ultimo[ev["agente"]] = ev
    except Exception:
        return False
    now = time.time()
    for ev in ultimo.values():
        if ev.get("evento") != "start":
            continue
        try:
            t = time.mktime(time.strptime(ev["ts"], "%Y-%m-%dT%H:%M:%S"))
            if now - t < ventana_s:
                return True
        except Exception:
            return True
    return False


def calcular() -> dict:
    agenda = core._read_space_file("AGENDA.md")
    hoy = [d for d in core._extract_deadlines(agenda, 30)
           if re.search(r"\bhoy\b|urgente|vence hoy", d.lower())]
    listos = [e for e in core._entregables_lista_tablero() if e.get("estado") == "listo"]
    malas = _units_fallidas()

    if malas:
        state = "alert"
    elif _agente_trabajando():
        state = "working"
    elif listos or hoy:
        state = "question"
    else:
        state = "on"

    # Textos genéricos (sin nombres de cliente/tema). Un id estable por línea.
    activity, remove = [], []
    for _id, n, texto in (
        ("vobo", len(listos), f"{len(listos)} entregable(s) esperando Vo.Bo."),
        ("hoy", len(hoy), f"{len(hoy)} pendiente(s) que vencen hoy"),
        ("falla", len(malas), f"{len(malas)} servicio(s) de Louis con falla"),
    ):
        (activity if n else remove).append({"id": _id, "text": texto} if n else _id)
    activity.append({"id": "ts", "text": "Actualizado " + datetime.now(TZ_CDMX).strftime("%d/%m %H:%M")})
    return {"state": state, "activity": activity, "remove": remove}


def _firma(job: dict) -> str:
    # El timestamp no cuenta para decidir si hubo cambio.
    return json.dumps({"s": job["state"],
                       "a": [x for x in job["activity"] if x["id"] != "ts"],
                       "r": job["remove"]}, sort_keys=True)


def _enviar(job: dict) -> str:
    r = subprocess.run(["node", str(SEND_JS)], input=json.dumps(job), capture_output=True,
                       text=True, timeout=60, cwd=str(SEND_JS.parent))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:])
    return r.stdout.strip()


def run_once(dry_run: bool = False) -> str:
    if not (os.environ.get("GATHER_STATUS_URL") and os.environ.get("GATHER_STATUS_SECRET")):
        return "gather: sin GATHER_STATUS_URL/SECRET (omitido)"
    job = calcular()
    if dry_run:
        return json.dumps(job, ensure_ascii=False, indent=2)
    firma = _firma(job)
    prev = {}
    try:
        prev = json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    if prev.get("firma") == firma and time.time() - prev.get("t", 0) < HEARTBEAT_S:
        return "gather: sin cambios"
    out = _enviar(job)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"firma": firma, "t": time.time()}))
    return f"gather: {job['state']} ({out})"


if __name__ == "__main__":
    if "--ping" in sys.argv:
        r = subprocess.run(["node", str(SEND_JS)], input='{"ping":true}', capture_output=True,
                           text=True, timeout=60, cwd=str(SEND_JS.parent))
        print(r.stdout or r.stderr)
    else:
        print(run_once(dry_run="--dry-run" in sys.argv))
