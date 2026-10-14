#!/usr/bin/env python3
"""
gather_bridge — refleja el estado de Louis en Smart Objects de Gather:
  • Bot Status Monitor (target "status")  — GATHER_STATUS_URL / GATHER_STATUS_SECRET
  • Inbox              (target "inbox")   — GATHER_INBOX_URL  / GATHER_INBOX_SECRET
Cada objeto es opcional: sin sus variables se omite en silencio.

── Bot Status Monitor ──
Estados (prioridad de mayor a menor):
  alert    → algún servicio/timer clave de Louis está en 'failed'
  working  → un agente arrancó una tarea en los últimos 90s
  question → hay entregables LISTO esperando Vo.Bo. o pendientes que vencen hoy
  on       → todo en orden

── Inbox ──
  contador → pendientes abiertos en AGENDA (la altura de la pila de papeles)
  feed     → conteos genéricos: vencen hoy, entregables en borrador/LISTO, briefs sin
             despachar, estancados 7+ días

PRIVACIDAD: el feed de Gather lo ven todos los Members/Guests del espacio (incluye externos).
Aquí solo viajan conteos y textos genéricos — nunca clientes, temas legales ni títulos.

Uso: lo llama scheduler.py cada ~5 min vía run_once(); también CLI:
  gather_bridge.py            # calcula y envía solo si cambió
  gather_bridge.py --dry-run  # imprime lo que enviaría
  gather_bridge.py --ping     # verifica URL + secreto de cada objeto configurado
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import louis_core as core

TZ_CDMX = timezone(timedelta(hours=-6))
HOME_OC = Path("/opt/openclaw") if Path("/opt/openclaw").exists() else Path.home() / ".openclaw"
STATE_DIR = HOME_OC / "logs"
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


def _generico(items) -> tuple[list, list]:
    """items: (id, n, texto). Con n>0 → activity.add; con n==0 → activity.remove."""
    activity, remove = [], []
    for _id, n, texto in items:
        if n:
            activity.append({"id": _id, "text": texto})
        else:
            remove.append(_id)
    activity.append({"id": "ts", "text": "Actualizado " + datetime.now(TZ_CDMX).strftime("%d/%m %H:%M")})
    return activity, remove


def _estancados(agenda: str, dias: int = 7) -> int:
    hoy = datetime.now(TZ_CDMX).date()
    n = 0
    for line in agenda.splitlines():
        if not re.match(r"^\s*-\s*\[\s*\]\s+", line):
            continue
        m = re.search(r"\[(?:auto|capturado)\s+(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            try:
                if (hoy - date.fromisoformat(m.group(1))).days >= dias:
                    n += 1
            except Exception:
                pass
    return n


def calcular_status(agenda: str, ents: list) -> dict:
    hoy = [d for d in core._extract_deadlines(agenda, 30)
           if re.search(r"\bhoy\b|urgente|vence hoy", d.lower())]
    listos = [e for e in ents if e.get("estado") == "listo"]
    malas = _units_fallidas()

    if malas:
        state = "alert"
    elif _agente_trabajando():
        state = "working"
    elif listos or hoy:
        state = "question"
    else:
        state = "on"

    activity, remove = _generico((
        ("vobo", len(listos), f"{len(listos)} entregable(s) esperando Vo.Bo."),
        ("hoy", len(hoy), f"{len(hoy)} pendiente(s) que vencen hoy"),
        ("falla", len(malas), f"{len(malas)} servicio(s) de Louis con falla"),
    ))
    return {"state": state, "activity": activity, "remove": remove}


def calcular_inbox(agenda: str, ents: list) -> dict:
    abiertos = sum(1 for ln in agenda.splitlines() if re.match(r"^\s*-\s*\[\s*\]\s+", ln))
    hoy = [d for d in core._extract_deadlines(agenda, 30)
           if re.search(r"\bhoy\b|urgente|vence hoy", d.lower())]
    borr = sum(1 for e in ents if e.get("estado") == "borrador")
    listos = sum(1 for e in ents if e.get("estado") == "listo")
    briefs = sum(1 for e in ents if e.get("estado") == "brief")
    estanc = _estancados(agenda)

    activity, remove = _generico((
        ("hoy", len(hoy), f"{len(hoy)} pendiente(s) que vencen hoy"),
        ("listo", listos, f"{listos} entregable(s) LISTO para Vo.Bo."),
        ("borrador", borr, f"{borr} entregable(s) en borrador"),
        ("brief", briefs, f"{briefs} brief(s) por despachar a agentes"),
        ("estancado", estanc, f"{estanc} pendiente(s) estancados 7+ días"),
    ))
    return {"count": abiertos, "activity": activity, "remove": remove}


# target → (calculador, clave del job que decide "cambió")
TARGETS = {"status": calcular_status, "inbox": calcular_inbox}


def _configurado(target: str) -> bool:
    t = target.upper()
    return bool(os.environ.get(f"GATHER_{t}_URL") and os.environ.get(f"GATHER_{t}_SECRET"))


def _firma(job: dict) -> str:
    # El timestamp no cuenta para decidir si hubo cambio.
    return json.dumps({"s": job.get("state"), "c": job.get("count"),
                       "a": [x for x in job["activity"] if x["id"] != "ts"],
                       "r": job["remove"]}, sort_keys=True)


def _node(job: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(SEND_JS)], input=json.dumps(job), capture_output=True,
                          text=True, timeout=60, cwd=str(SEND_JS.parent))


def _enviar(job: dict) -> str:
    r = _node(job)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:])
    return r.stdout.strip()


def run_once(dry_run: bool = False) -> str:
    activos = [t for t in TARGETS if _configurado(t)]
    if not activos:
        return "gather: sin variables GATHER_* (omitido)"
    agenda = core._read_space_file("AGENDA.md")
    ents = core._entregables_lista_tablero()
    resultados = []
    for t in activos:
        try:
            job = TARGETS[t](agenda, ents)
            job["target"] = t
            if dry_run:
                resultados.append(f"[{t}] " + json.dumps(job, ensure_ascii=False, indent=2))
                continue
            sf = STATE_DIR / f"gather_{t}.json"
            firma = _firma(job)
            prev = {}
            try:
                prev = json.loads(sf.read_text())
            except Exception:
                pass
            if prev.get("firma") == firma and time.time() - prev.get("t", 0) < HEARTBEAT_S:
                resultados.append(f"{t}: sin cambios")
                continue
            out = _enviar(job)
            sf.parent.mkdir(parents=True, exist_ok=True)
            sf.write_text(json.dumps({"firma": firma, "t": time.time()}))
            resultados.append(f"{t}: {job.get('state', job.get('count'))} ({out})")
        except Exception as e:  # un objeto caído no debe frenar al otro
            resultados.append(f"{t}: ERROR {str(e)[:200]}")
    return "gather: " + " | ".join(resultados)


if __name__ == "__main__":
    if "--ping" in sys.argv:
        for t in TARGETS:
            if not _configurado(t):
                print(f"[{t}] sin configurar")
                continue
            r = _node({"target": t, "ping": True})
            print(f"[{t}]", r.stdout.strip() or r.stderr.strip()[-300:])
    else:
        print(run_once(dry_run="--dry-run" in sys.argv))
