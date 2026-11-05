#!/usr/bin/env python3
"""dof_vigia_dispatch.py — cada día entrega al Vigía DOF las notas nuevas del Diario Oficial.

Corre en louis-prod (timer dof-vigia.timer) después de que el harvest ya bajó y clasificó las notas.
Para cada nota INCLUIDA (incluido=1) de la fecha pedida que el Vigía no haya recibido todavía:

  --brief     deja un brief del día en _briefs/ con dispatch_preparar_brief de Cerebro (para Cowork / personas).
  --dispatch  crea una tarea asíncrona para el agente legal_dof_watchdog (POST /api/tasks/dispatch en la VM kawiil-agents).

Sin ninguna bandera es PRUEBA: imprime lo que haría y no escribe ni envía nada.
Solo viaja información pública del DOF (título, dependencia, tipo, enlace); nunca el texto completo ni datos de clientes.
Si la VM no contesta (Mac dormida), las notas NO se marcan como entregadas y se reintentan en la siguiente corrida.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof/biblioteca_dof.db"))
STATE_PATH = Path(os.environ.get("DOF_VIGIA_STATE", "/opt/openclaw/legal/dof/vigia_state.json"))
AGENTS_ENV = Path(os.environ.get("KAWIIL_AGENTS_ENV", str(Path.home() / ".openclaw/credentials/kawiil-agents.env")))
CEREBRO_DIR = os.environ.get("CEREBRO_DIR", "/opt/openclaw/scripts")
AGENTE = os.environ.get("DOF_VIGIA_AGENTE", "legal_dof_watchdog")
URL_NOTA = "https://www.dof.gob.mx/nota_detalle.php?codigo={cod}&fecha={f}"
MAX_POR_CORRIDA = 10


def hoy_cdmx() -> str:
    return dt.datetime.now(ZoneInfo("America/Mexico_City")).date().isoformat()


def cargar_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for ln in path.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.removeprefix("export ").partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def cargar_estado(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"entregadas": []}


def guardar_estado(path: Path, estado: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    estado["entregadas"] = sorted(set(estado["entregadas"]))[-5000:]   # acota el archivo
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado))
    tmp.replace(path)


def seleccionar(db_path: Path, desde: str, hasta: str, entregadas: set, maximo: int) -> tuple[list[dict], int]:
    """Notas incluidas entre `desde` y `hasta` (inclusive) aún no entregadas, las más recientes primero.
    Devuelve (hasta `maximo` notas, total pendientes)."""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        filas = [dict(r) for r in c.execute(
            "SELECT cod_nota, fecha, edicion, titulo, nombre_cod_orga_uno AS dependencia, tipo_documento "
            "FROM notas WHERE incluido = 1 AND fecha BETWEEN ? AND ? ORDER BY fecha DESC, cod_nota", (desde, hasta))]
    pendientes = [n for n in filas if n["cod_nota"] not in entregadas]
    return pendientes[:maximo], len(pendientes)


def texto_tarea(n: dict) -> str:
    return (
        "Revisa esta publicación del Diario Oficial de la Federación y responde SOLO el JSON de tu formato "
        "(relevant, affected_clients, summary, suggested_actions, source_metadata).\n"
        f"Fecha: {n['fecha']} ({n.get('edicion') or '?'})\n"
        f"Dependencia emisora: {n.get('dependencia') or 'no indicada'}\n"
        f"Tipo de instrumento: {n.get('tipo_documento') or 'no indicado'}\n"
        f"Título: {n['titulo']}\n"
        f"Número de nota DOF: {n['cod_nota']}\n"
        f"Enlace: {URL_NOTA.format(cod=n['cod_nota'], f=n['fecha'])}"
    )


def insumos_brief(notas: list[dict]) -> str:
    return "\n".join(f"- [{n.get('tipo_documento') or '?'}] {n['titulo']} — {n.get('dependencia') or 's/d'} "
                     f"({URL_NOTA.format(cod=n['cod_nota'], f=n['fecha'])})" for n in notas)


def _http(url: str, token: str | None = None, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Kawiil-Dispatch-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def con_reintentos(fn, intentos: int = 3, espera: int = 30):
    for i in range(1, intentos + 1):
        try:
            return fn()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if i == intentos:
                raise
            print(f"  sin respuesta de la VM ({type(e).__name__}); reintento {i}/{intentos - 1} en {espera}s", file=sys.stderr)
            time.sleep(espera)


def despachar(notas: list[dict], base_url: str, token: str, al_entregar=lambda cod: None) -> int:
    """Crea una tarea por nota y avisa de cada entrega al instante (para no repetirla si luego algo falla).
    Se detiene en el primer fallo de conexión. Devuelve cuántas entregó."""
    agentes = con_reintentos(lambda: _http(f"{base_url}/api/agents")).get("agents", [])
    ag = next((a for a in agentes if a["name"] == AGENTE), None)
    if not ag:
        raise RuntimeError(f"Agente {AGENTE} no encontrado en la VM")
    hechas = 0
    for n in notas:
        body = {"agent_id": ag["id"], "user_id": "louis-dof-vigia", "title": f"DOF {n['fecha']} · {n['titulo']}"[:100],
                "description": texto_tarea(n), "task_type": "manual", "priority": "normal"}
        con_reintentos(lambda: _http(f"{base_url}/api/tasks/dispatch", token=token, body=body))
        al_entregar(n["cod_nota"])
        hechas += 1
    return hechas


def dejar_brief(fecha: str, notas: list[dict], total: int) -> str:
    sys.path.insert(0, CEREBRO_DIR)
    from cerebro_kawiil_mcp import dispatch_preparar_brief   # herramienta real de Cerebro
    return dispatch_preparar_brief(
        tarea=f"Revisión DOF {fecha}", cliente="Kawiil (interno)", insumos=insumos_brief(notas), urgencia="normal",
        contexto_adicional=f"{len(notas)} de {total} notas incluidas del DOF hasta el {fecha}; entregadas al agente {AGENTE}.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--fecha", default=None, help="AAAA-MM-DD (default: hoy CDMX)")
    p.add_argument("--dias", type=int, default=3,
                   help="ventana hacia atrás desde --fecha (incluye hoy): lo que el tope dejó pendiente se atiende los días siguientes")
    p.add_argument("--max", type=int, default=MAX_POR_CORRIDA, help="tope de notas por corrida (costo)")
    p.add_argument("--brief", action="store_true", help="dejar el brief del día con dispatch_preparar_brief")
    p.add_argument("--dispatch", action="store_true", help="crear las tareas en el agente (sin esto es prueba)")
    a = p.parse_args(argv)

    fecha = a.fecha or hoy_cdmx()
    estado = cargar_estado(STATE_PATH)
    desde = (dt.date.fromisoformat(fecha) - dt.timedelta(days=max(a.dias, 1) - 1)).isoformat()
    notas, total = seleccionar(DB_PATH, desde, fecha, set(estado["entregadas"]), a.max)
    print(f"DOF {desde}..{fecha}: {total} notas incluidas pendientes; esta corrida procesa {len(notas)} (tope {a.max}).")
    for n in notas:
        print(f"  - {n['cod_nota']} [{n.get('tipo_documento') or '?'}] {n['titulo'][:90]}")
    if not notas:
        return 0
    if not (a.brief or a.dispatch):
        print("PRUEBA: no se escribió ni se envió nada (usa --brief y/o --dispatch).")
        return 0

    if a.brief:
        print(dejar_brief(fecha, notas, total))
    if a.dispatch:
        env = cargar_env(AGENTS_ENV)
        base, token = env.get("KAWIIL_AGENTS_URL", ""), env.get("KAWIIL_DISPATCH_TOKEN", "")
        if not base or not token:
            print("ERROR: faltan KAWIIL_AGENTS_URL o KAWIIL_DISPATCH_TOKEN en", AGENTS_ENV, file=sys.stderr)
            return 3
        def registrar(cod):
            estado["entregadas"] = list(estado["entregadas"]) + [cod]
            guardar_estado(STATE_PATH, estado)

        try:
            hechas = despachar(notas, base.rstrip("/"), token, registrar)
        except Exception as e:
            print(f"ERROR al despachar: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        print(f"Entregadas al {AGENTE}: {hechas}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
