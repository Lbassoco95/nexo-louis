#!/usr/bin/env python3
"""
kawiil_agents.py — Cliente CLI para que Louis hable con los 14 agentes especialistas
de Kawiil HQ (amatl, atl, balam, coyolli, metztli, nelli, ollin, teocuitl,
tepantli, tequitl, tlahtoani, tlahtolli, tochtli, yollotl).

Subcomandos:
  list                                  Lista agentes activos
  info <agent>                          Detalle de un agente
  assign <agent> "<tarea>"              Crea tarea + ejecuta inmediato → devuelve respuesta
  dispatch <agent> "<tarea>"            Versión asíncrona (background), devuelve task_id
  tasks [agent] [--limit N]             Lista tareas (todas o de un agente)
  task <task_id>                        Detalle de una tarea
  chat <agent> "<msg>" [--session ID]   Conversación con un agente (mantiene contexto si --session)

Credenciales: lee ~/.openclaw/credentials/kawiil-agents.env
"""

import os
import sys
import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

HOME = Path.home()
CREDS = HOME / ".openclaw" / "credentials" / "kawiil-agents.env"


def load_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


ENV = load_env(CREDS)
BASE_URL = ENV.get("KAWIIL_AGENTS_URL", "http://localhost:8000")
DISPATCH_TOKEN = ENV.get("KAWIIL_DISPATCH_TOKEN", "")


def http_get(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} GET {path}: {err}")


def http_post(path: str, body: dict, dispatch: bool = False) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if dispatch and DISPATCH_TOKEN:
        req.add_header("X-Kawiil-Dispatch-Token", DISPATCH_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} POST {path}: {err}")


# === Comandos ===

def cmd_list(args):
    """Lista agentes activos (idle/working)."""
    show_archived = "--all" in args
    data = http_get("/api/agents")
    agents = data.get("agents", [])
    print(f"{'AGENTE':25} {'STATUS':12} {'DESCRIPCIÓN'}")
    print("-" * 100)
    for a in agents:
        if not show_archived and a.get("status") == "archived":
            continue
        desc = (a.get("description") or "").replace("\n", " ")
        print(f"{a['name']:25} {a['status']:12} {desc[:65]}")
    if not show_archived:
        print(f"\n(Usa --all para ver también los {sum(1 for a in agents if a['status']=='archived')} agentes archivados)")


def cmd_info(args):
    """Detalle de un agente (capabilities, model, descripción completa)."""
    if not args:
        print("Uso: kawiil_agents.py info <agent_name>", file=sys.stderr)
        sys.exit(1)
    name = args[0]
    data = http_get("/api/agents")
    agent = next((a for a in data.get("agents", []) if a["name"] == name), None)
    if not agent:
        print(f"Agente '{name}' no encontrado.", file=sys.stderr)
        sys.exit(1)
    print(f"=== {agent.get('display_name', name)} ({name}) ===")
    print(f"ID:            {agent['id']}")
    print(f"Status:        {agent['status']}")
    print(f"Model:         {agent.get('model', '?')}")
    print(f"Role:          {agent.get('role', '?')}")
    print(f"\nDescripción:")
    print(f"  {agent.get('description', '(sin descripción)')}")
    caps = agent.get("capabilities") or []
    if caps:
        print(f"\nCapabilities ({len(caps)}):")
        for c in caps:
            print(f"  - {c}")


def cmd_assign(args):
    """Crea tarea y la ejecuta inmediatamente. Devuelve el resultado."""
    if len(args) < 2:
        print("Uso: kawiil_agents.py assign <agent_name> \"<tarea>\"", file=sys.stderr)
        sys.exit(1)
    agent_name = args[0]
    task_desc = " ".join(args[1:])

    body = {
        "agent_name": agent_name,
        "title": task_desc[:100],
        "description": task_desc,
        "execute": True,
    }
    result = http_post("/api/tasks", body)

    # Extraer resultado
    output = result.get("output") or result.get("result") or {}
    if isinstance(output, dict):
        text = output.get("result") or output.get("analysis") or json.dumps(output, ensure_ascii=False)
    else:
        text = str(output)

    print(f"=== Respuesta de {agent_name} ===")
    print(f"Task ID: {result.get('id', '?')}")
    print(f"Status:  {result.get('status', '?')}")
    print()
    print(text)


def cmd_dispatch(args):
    """Despacha tarea asíncronamente (background). Devuelve task_id para hacer follow-up."""
    if len(args) < 2:
        print("Uso: kawiil_agents.py dispatch <agent_name> \"<tarea>\"", file=sys.stderr)
        sys.exit(1)
    agent_name = args[0]
    task_desc = " ".join(args[1:])

    if not DISPATCH_TOKEN:
        print("ERROR: KAWIIL_DISPATCH_TOKEN no configurado en .env", file=sys.stderr)
        sys.exit(1)

    # Necesita agent_id, no name — buscar primero
    data = http_get("/api/agents")
    agent = next((a for a in data.get("agents", []) if a["name"] == agent_name), None)
    if not agent:
        print(f"Agente '{agent_name}' no encontrado.", file=sys.stderr)
        sys.exit(1)

    body = {
        "agent_id": agent["id"],
        "user_id": "polo",
        "title": task_desc[:100],
        "description": task_desc,
        "task_type": "manual",
        "priority": "normal",
    }
    result = http_post("/api/tasks/dispatch", body, dispatch=True)
    print(f"Task creada (background) — ID: {result.get('id', '?')}")
    print(f"Status inicial: {result.get('status', '?')}")
    print(f"\nConsulta progreso con:")
    print(f"  python3 kawiil_agents.py task {result.get('id', '?')}")


def cmd_tasks(args):
    """Lista tareas recientes. Si se especifica agente, filtra."""
    agent_filter = None
    limit = 20
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        else:
            agent_filter = args[i]
            i += 1

    params = {"limit": str(limit)}
    if agent_filter:
        data = http_get("/api/agents")
        agent = next((a for a in data.get("agents", []) if a["name"] == agent_filter), None)
        if not agent:
            print(f"Agente '{agent_filter}' no encontrado.", file=sys.stderr)
            sys.exit(1)
        params["agent_id"] = agent["id"]

    qs = urllib.parse.urlencode(params)
    data = http_get(f"/api/tasks?{qs}")
    tasks = data.get("tasks") or data.get("items") or (data if isinstance(data, list) else [])

    if not tasks:
        print("(sin tareas)")
        return

    print(f"{'STATUS':12} {'AGENT':20} {'TÍTULO':50} {'CREATED'}")
    print("-" * 110)
    for t in tasks:
        ag = t.get("agent_name") or t.get("agent_id", "?")[:8]
        title = (t.get("title") or t.get("description") or "")[:48]
        created = (t.get("created_at") or "")[:19]
        print(f"{t.get('status', '?'):12} {ag:20} {title:50} {created}")


def cmd_task(args):
    """Detalle de una tarea por ID."""
    if not args:
        print("Uso: kawiil_agents.py task <task_id>", file=sys.stderr)
        sys.exit(1)
    task_id = args[0]
    task = http_get(f"/api/tasks/{task_id}")
    print(json.dumps(task, indent=2, ensure_ascii=False))


def cmd_chat(args):
    """Conversación con un agente (mantiene contexto si pasas --session)."""
    if len(args) < 2:
        print("Uso: kawiil_agents.py chat <agent_name> \"<mensaje>\" [--session SESSION_ID]", file=sys.stderr)
        sys.exit(1)
    # Por simplicidad, chat es como assign pero con un contexto de sesión preservado.
    # Para mantener contexto real necesitamos sessions endpoint que no veo en el repo;
    # por ahora cada mensaje es independiente. Cuando agreguemos sessions table, lo mejoramos.
    cmd_assign(args)


# === Main ===
def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]

    handlers = {
        "list": cmd_list,
        "info": cmd_info,
        "assign": cmd_assign,
        "dispatch": cmd_dispatch,
        "tasks": cmd_tasks,
        "task": cmd_task,
        "chat": cmd_chat,
    }

    if cmd not in handlers:
        print(f"Comando desconocido: {cmd}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    try:
        handlers[cmd](args)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
