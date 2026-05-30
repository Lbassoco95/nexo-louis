#!/usr/bin/env python3
"""
openclaw_gateway.py — Gateway HTTP nativo para Louis.

NO depende de un "binario OpenClaw" externo (openclaw.ai 403). Es una capa
delgada que envuelve `louis_core` y expone tres rutas:

  GET  /healthz                    → liveness probe
  GET  /v1/status                  → estado del gateway + routing
  POST /v1/chat/completions        → endpoint compatible con OpenAI (usa louis_core)
  POST /v1/tools/{name}            → ejecutar tool directamente (debug)

Diseñado para correr en 127.0.0.1:3000 detrás de Caddy en louis.kawiil.mx.

Pruebas rápidas (en el VPS):
  curl -s http://127.0.0.1:3000/v1/status | jq '.agents_count,.models.ollama'
  curl -s http://127.0.0.1:3000/v1/agents | jq '.count'
  curl -s -X POST http://127.0.0.1:3000/v1/agents/legal-regulatory-compliance \\
    -H 'Content-Type: application/json' \\
    -d '{"tarea":"Resumen obligaciones CNBV","contexto":"transmisor de dinero"}'

Sólo stdlib (http.server) — sin FastAPI/uvicorn — para minimizar deps.
"""

import os
import sys
import json
import time
import logging
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# louis_core vive en /opt/openclaw/scripts/
SCRIPTS_DIR = Path("/opt/openclaw/scripts")
if SCRIPTS_DIR.exists():
    sys.path.insert(0, str(SCRIPTS_DIR))

import louis_core as core  # noqa: E402

LOG_FILE = Path("/opt/openclaw/logs/openclaw-gateway.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("openclaw-gateway")

HOST = os.environ.get("OPENCLAW_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENCLAW_PORT", "3000"))
TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")  # opcional, si está, valida Bearer


def _require_auth(handler) -> bool:
    """Si TOKEN está configurado, exige Authorization: Bearer."""
    if not TOKEN:
        return True
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth[7:].strip() == TOKEN


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        if not n:
            return {}
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8"))

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # ---------- GET ----------
    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": "openclaw-gateway",
                                  "version": "1.0", "uptime_started": getattr(self.server, "started_at", 0)})
            return

        if self.path == "/v1/status":
            if not _require_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            try:
                status = core._verificar_conexiones(incluir_m365=False)
            except Exception as e:
                status = f"(error: {e})"
            agents_dir = Path("/opt/openclaw/spaces/general/agents")
            agents_count = len(list(agents_dir.glob("*.md"))) if agents_dir.exists() else 0
            self._send_json(200, {
                "service": "openclaw-gateway",
                "louis_core_loaded": True,
                "memory_files": core.MEMORY_FILES,
                "agents_count": agents_count,
                "agents_dir": str(agents_dir),
                "tools_count": len(core.TOOLS_DEFINITION),
                "models": {
                    "default_chat": "ollama",
                    "ollama_fast": core.OLLAMA_FAST_MODEL,
                    "ollama_quality": core.OLLAMA_QUALITY_MODEL,
                    "tool_use": core.CLAUDE_SONNET,
                },
                "routing": {
                    "chat_default": "ollama",
                    "memory_write_auto": "sonnet",
                    "legal_agents_auto": "sonnet",
                    "m365_explicit": "/sonnet",
                },
                "introspection": status,
            })
            return

        if self.path == "/v1/tools":
            self._send_json(200, {
                "tools": [{"name": t["name"], "description": t["description"]} for t in core.TOOLS_DEFINITION]
            })
            return

        if self.path == "/v1/agents":
            # Lista sub-agentes registrados en spaces/general/agents/
            agents = []
            try:
                from pathlib import Path as _P
                agents_dir = _P("/opt/openclaw/spaces/general/agents")
                if agents_dir.exists():
                    for f in sorted(agents_dir.glob("*.md")):
                        name = f.stem
                        # Lee frontmatter si existe
                        try:
                            content = f.read_text(errors="replace")
                            description = ""
                            modelo = ""
                            if content.startswith("---"):
                                end = content.find("---", 3)
                                if end > 0:
                                    fm = content[3:end]
                                    for line in fm.splitlines():
                                        if line.startswith("description:"):
                                            description = line.split(":", 1)[1].strip().strip('"').strip("'")
                                        elif line.startswith("model:") or line.startswith("modelo:"):
                                            modelo = line.split(":", 1)[1].strip().strip('"').strip("'")
                            agents.append({
                                "name": name,
                                "description": description[:200],
                                "modelo": modelo or "claude-sonnet-4-6",
                                "path": str(f),
                            })
                        except Exception as e:
                            agents.append({"name": name, "description": f"(error leyendo: {e})", "path": str(f)})
            except Exception as e:
                self._send_json(500, {"error": f"listando agents: {e}"})
                return
            self._send_json(200, {"count": len(agents), "agents": agents})
            return

        if self.path.startswith("/v1/agents/") and self.command == "GET":
            # GET /v1/agents/{name} — detalles de un agente específico
            name = self.path[len("/v1/agents/"):]
            from pathlib import Path as _P
            f = _P(f"/opt/openclaw/spaces/general/agents/{name}.md")
            if not f.exists():
                self._send_json(404, {"error": f"agente '{name}' no existe"})
                return
            self._send_json(200, {
                "name": name,
                "content": f.read_text(errors="replace"),
                "size": f.stat().st_size,
            })
            return

        self._send_json(404, {"error": f"GET {self.path} no existe"})

    # ---------- POST ----------
    def do_POST(self):
        if not _require_auth(self):
            self._send_json(401, {"error": "unauthorized"})
            return

        try:
            body = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": f"JSON inválido: {e}"})
            return

        # OpenAI-compatible chat completions
        if self.path == "/v1/chat/completions":
            messages = body.get("messages", [])
            if not messages:
                self._send_json(400, {"error": "messages requerido"})
                return

            # Tomar último user msg como input; los previos van a history
            user_msg = ""
            history = []
            for m in messages:
                role = m.get("role")
                content = m.get("content", "")
                if role == "system":
                    continue  # Louis usa su propio system prompt
                if role == "user":
                    if user_msg:
                        history.append({"role": "user", "content": user_msg})
                    user_msg = content if isinstance(content, str) else json.dumps(content)
                elif role == "assistant":
                    history.append({"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content)})

            if not user_msg:
                self._send_json(400, {"error": "ningún mensaje role=user"})
                return

            try:
                api_key = core.load_anthropic_key()
                sys_prompt = core.load_system_prompt(channel="api")
                # Mensaje crudo: call_llm maneja prefijos (/sonnet, /oss…) y los limpia internamente.
                response, model_used = core.call_llm(api_key, sys_prompt, history, user_msg)
            except Exception as e:
                log.exception("call_llm falló")
                self._send_json(500, {"error": str(e), "trace": traceback.format_exc()})
                return

            now = int(time.time())
            self._send_json(200, {
                "id": f"openclaw-{now}",
                "object": "chat.completion",
                "created": now,
                "model": model_used,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": response},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
            })
            return

        # Ejecutar tool directo
        if self.path.startswith("/v1/tools/"):
            tool_name = self.path[len("/v1/tools/"):]
            args = body.get("args", {})
            result = core.execute_tool(tool_name, args)
            self._send_json(200, {"tool": tool_name, "result": result})
            return

        # Crear sub-agente — POST /v1/agents
        if self.path == "/v1/agents":
            nombre = body.get("nombre") or body.get("name")
            especialidad = body.get("especialidad") or body.get("description", "")
            prompt = body.get("prompt") or body.get("system_prompt", "")
            modelo = body.get("modelo") or body.get("model", "claude-sonnet-4-6")
            if not nombre or not prompt:
                self._send_json(400, {"error": "nombre y prompt requeridos"})
                return
            t0 = time.time()
            try:
                result = core.execute_tool("crear_agente", {
                    "nombre": nombre,
                    "especialidad": especialidad or "(sin descripción)",
                    "prompt": prompt,
                    "modelo": modelo,
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            ms = int((time.time() - t0) * 1000)
            self._send_json(200, {
                "ok": result.startswith("OK"),
                "agent": nombre,
                "model_used": modelo,
                "response": result,
                "latency_ms": ms,
            })
            return

        # Invocar sub-agente — POST /v1/agents/{name}
        if self.path.startswith("/v1/agents/"):
            agent_name = self.path[len("/v1/agents/"):].strip("/")
            tarea = body.get("tarea", "") or body.get("task", "")
            contexto = body.get("contexto", "") or body.get("context", "")
            modelo_override = body.get("modelo_override") or body.get("model")
            if not tarea:
                self._send_json(400, {"error": "'tarea' requerido en body"})
                return
            t0 = time.time()
            try:
                result = core.execute_tool("invocar_agente", {
                    "nombre": agent_name,
                    "tarea": tarea,
                    "contexto": contexto,
                    "modelo_override": modelo_override,
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            ms = int((time.time() - t0) * 1000)
            model_used = "ollama" if "vía Ollama" in result else "claude"
            self._send_json(200, {
                "ok": not result.startswith("ERROR"),
                "agent": agent_name,
                "tarea": tarea,
                "model_used": model_used,
                "response": result,
                "latency_ms": ms,
            })
            return

        self._send_json(404, {"error": f"POST {self.path} no existe"})


def main():
    log.info("Arrancando openclaw-gateway en %s:%d", HOST, PORT)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.started_at = int(time.time())
    log.info("Tools cargados: %d", len(core.TOOLS_DEFINITION))
    log.info("Memory files: %s", ", ".join(core.MEMORY_FILES))
    log.info("Auth: %s", "Bearer required" if TOKEN else "DISABLED (sólo accesible vía 127.0.0.1)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("SIGINT — apagando")
        srv.shutdown()


if __name__ == "__main__":
    main()
