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


# ============================================================================
# Dashboard visual de agentes
# ============================================================================
AGENTS_DIR = Path("/opt/openclaw/spaces/general/agents")
ACTIVITY_FILE = Path("/opt/openclaw/logs/agent-activity.jsonl")


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Louis · Agentes en vivo</title>
<style>
  :root { --bg:#0b0f1a; --panel:#131a2b; --txt:#e6ecf5; --dim:#8a96ad;
          --idle:#2b3650; --active:#27e0a0; --line:#1f2a44; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { padding:14px 20px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:14px; }
  header h1 { font-size:17px; margin:0; font-weight:600; }
  .badge { font-size:12px; color:var(--dim); }
  .dot { width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:5px; }
  .layout { display:flex; height:calc(100vh - 52px); }
  #stage { flex:1; position:relative; overflow:hidden; }
  aside { width:320px; border-left:1px solid var(--line); background:var(--panel);
          overflow-y:auto; padding:14px; }
  aside h2 { font-size:13px; text-transform:uppercase; letter-spacing:.5px;
             color:var(--dim); margin:0 0 10px; }
  .ev { font-size:12.5px; padding:7px 9px; border-radius:8px; background:#0f1626;
        margin-bottom:6px; border-left:3px solid var(--idle); }
  .ev.start { border-left-color:var(--active); }
  .ev.end   { border-left-color:#3d7bff; }
  .ev.error { border-left-color:#ff5d5d; }
  .ev.created { border-left-color:#d6a93b; }
  .ev .t { color:var(--dim); font-size:11px; }
  .ev .a { font-weight:600; }
  svg { width:100%; height:100%; display:block; }
  .nodeLabel { font-size:11px; fill:var(--txt); }
  .nodeSpec  { font-size:9px; fill:var(--dim); }
  text { pointer-events:none; user-select:none; }
  .empty { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
           color:var(--dim); text-align:center; font-size:14px; max-width:340px; }
  @keyframes pulse { 0%{opacity:.35} 50%{opacity:1} 100%{opacity:.35} }
</style>
</head>
<body>
<header>
  <h1>🧠 Louis — Agentes en vivo</h1>
  <span class="badge"><span class="dot" style="background:var(--active)"></span><b id="nActive">0</b> trabajando</span>
  <span class="badge"><span class="dot" style="background:var(--idle)"></span><b id="nTotal">0</b> agentes</span>
  <span class="badge" id="clock" style="margin-left:auto"></span>
</header>
<div class="layout">
  <div id="stage">
    <svg id="svg" viewBox="0 0 1000 800" preserveAspectRatio="xMidYMid meet"></svg>
    <div class="empty" id="empty">Esperando actividad de agentes…<br>
      <span style="font-size:12px">Cuando Louis invoque un agente desde Telegram, su círculo se encenderá aquí.</span>
    </div>
  </div>
  <aside>
    <h2>Actividad reciente</h2>
    <div id="timeline"></div>
  </aside>
</div>
<script>
const SVG = document.getElementById('svg');
const NS = 'http://www.w3.org/2000/svg';
const COLORS = { legal:'#7aa2ff', kawiil:'#ffb86b', seguridad:'#ff7ab6',
                 yoltik:'#9b7bff', general:'#5bd6c0' };
function color(g){ return COLORS[g] || '#5bd6c0'; }

function el(tag, attrs){ const e=document.createElementNS(NS,tag);
  for(const k in attrs) e.setAttribute(k, attrs[k]); return e; }

function layout(agents){
  // Louis al centro; agentes en anillo. Si un agente tiene parent agente,
  // se ubica cerca de su parent.
  const cx=500, cy=400, R=280;
  const pos={ __louis__:{x:cx,y:cy} };
  const n=agents.length || 1;
  agents.forEach((a,i)=>{
    const ang = (i/n)*Math.PI*2 - Math.PI/2;
    pos[a.nombre] = { x: cx + R*Math.cos(ang), y: cy + R*Math.sin(ang) };
  });
  return pos;
}

function render(data){
  document.getElementById('nActive').textContent = data.activos;
  document.getElementById('nTotal').textContent  = data.total_agentes;
  document.getElementById('clock').textContent =
     new Date(data.ts*1000).toLocaleTimeString('es-MX');
  const agents = data.agentes || [];
  document.getElementById('empty').style.display = agents.length ? 'none':'block';

  SVG.innerHTML='';
  const pos = layout(agents);

  // Conexiones: agente→parent (o →Louis centro).
  agents.forEach(a=>{
    const p = a.parent && pos[a.parent] ? pos[a.parent] : pos.__louis__;
    const me = pos[a.nombre];
    const line = el('line',{x1:me.x,y1:me.y,x2:p.x,y2:p.y,
      stroke: a.activo ? 'var(--active)' : 'var(--line)',
      'stroke-width': a.activo ? 2 : 1, opacity: a.activo?0.8:0.5});
    if(a.activo) line.style.animation='pulse 1.4s infinite';
    SVG.appendChild(line);
  });

  // Nodo central Louis
  SVG.appendChild(el('circle',{cx:pos.__louis__.x,cy:pos.__louis__.y,r:34,
    fill:'#1b2438',stroke:'#3d7bff','stroke-width':2}));
  const lt=el('text',{x:pos.__louis__.x,y:pos.__louis__.y+5,
    'text-anchor':'middle',class:'nodeLabel'}); lt.textContent='LOUIS'; SVG.appendChild(lt);

  // Nodos de agentes
  agents.forEach(a=>{
    const me=pos[a.nombre];
    const c=el('circle',{cx:me.x,cy:me.y,r: a.activo?22:16,
      fill: a.activo ? color(a.grupo) : 'var(--idle)',
      stroke: color(a.grupo), 'stroke-width':2});
    if(a.activo){ c.style.animation='pulse 1.2s infinite'; }
    SVG.appendChild(c);
    const short = a.nombre.length>22 ? a.nombre.slice(0,21)+'…' : a.nombre;
    const t=el('text',{x:me.x,y:me.y+ (a.activo?38:32),
      'text-anchor':'middle',class:'nodeLabel'}); t.textContent=short; SVG.appendChild(t);
  });

  // Timeline
  const tl=document.getElementById('timeline');
  tl.innerHTML='';
  (data.eventos||[]).slice().reverse().forEach(ev=>{
    const d=document.createElement('div'); d.className='ev '+(ev.evento||'');
    const hora=(ev.ts||'').split('T')[1]||'';
    const verbo={start:'▶ inició',end:'✓ terminó',error:'✗ error',created:'＋ creado'}[ev.evento]||ev.evento;
    d.innerHTML=`<div class="t">${hora}</div>`+
      `<div><span class="a">${ev.agente||'?'}</span> — ${verbo}</div>`+
      (ev.detalle?`<div class="t">${(ev.detalle||'').slice(0,90)}</div>`:'');
    tl.appendChild(d);
  });
}

async function tick(){
  try{ const r=await fetch('/v1/activity',{cache:'no-store'});
       render(await r.json()); }
  catch(e){ /* reintenta en el próximo tick */ }
}
tick(); setInterval(tick, 2000);
</script>
</body>
</html>"""


def _build_activity_payload() -> dict:
    """Arma el JSON que consume el dashboard: catálogo de agentes + eventos recientes.

    Un agente se considera 'activo' si su último evento es 'start' en los últimos
    90 segundos (heurística suficiente para animar el círculo mientras trabaja).
    """
    now = time.time()

    # 1) Catálogo de agentes (nombre, especialidad, modelo) leído del disco.
    agentes = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md")):
            nombre = f.stem
            especialidad, modelo = "", "claude-sonnet-4-6"
            try:
                text = f.read_text()[:600]
                if text.startswith("---"):
                    for line in text.splitlines():
                        if line.startswith("especialidad:"):
                            especialidad = line.split(":", 1)[1].strip()
                        elif line.startswith("modelo:"):
                            modelo = line.split(":", 1)[1].strip()
            except Exception:
                pass
            # Grupo por prefijo para colorear/agrupar (legal-, kawiil-, seguridad-, etc.)
            grupo = nombre.split("-", 1)[0] if "-" in nombre else "general"
            agentes.append({
                "nombre": nombre, "especialidad": especialidad,
                "modelo": modelo, "grupo": grupo,
            })

    # 2) Eventos recientes (últimas ~400 líneas del jsonl).
    eventos = []
    if ACTIVITY_FILE.exists():
        try:
            lines = ACTIVITY_FILE.read_text().splitlines()[-400:]
            for ln in lines:
                try:
                    eventos.append(json.loads(ln))
                except Exception:
                    continue
        except Exception:
            pass

    # 3) Estado activo por agente: último evento, hace cuánto.
    estado = {}
    enlaces = {}  # parent→hijo (para dibujar conexiones)
    for ev in eventos:
        ag = ev.get("agente")
        if not ag:
            continue
        estado[ag] = ev
        parent = ev.get("parent")
        if parent and ev.get("evento") == "start":
            enlaces[ag] = parent

    for a in agentes:
        ev = estado.get(a["nombre"])
        activo = False
        ultimo = None
        if ev:
            ultimo = ev.get("ts")
            if ev.get("evento") == "start":
                # ¿el start fue reciente?
                try:
                    t = time.mktime(time.strptime(ev["ts"], "%Y-%m-%dT%H:%M:%S"))
                    activo = (now - t) < 90
                except Exception:
                    activo = True
        a["activo"] = activo
        a["ultimo_evento"] = ev.get("evento") if ev else None
        a["ultimo_ts"] = ultimo
        a["parent"] = enlaces.get(a["nombre"])

    # Solo devolvemos los agentes con actividad alguna vez + los activos, para no
    # saturar la vista con 106 círculos. Si nunca ha habido actividad, mostramos
    # los primeros 24 como catálogo.
    con_actividad = [a for a in agentes if a["ultimo_ts"]]
    if con_actividad:
        visibles = con_actividad
    else:
        visibles = agentes[:24]

    return {
        "ts": int(now),
        "total_agentes": len(agentes),
        "activos": sum(1 for a in agentes if a["activo"]),
        "agentes": visibles,
        "eventos": eventos[-40:],  # timeline reciente
    }


class Handler(BaseHTTPRequestHandler):
    def _safe_write(self, body: bytes):
        """Escribe la respuesta tolerando que el cliente cierre la conexión antes
        de tiempo (health-checks, curl con -m). Evita el BrokenPipeError que
        ensuciaba el log y podía matar el thread del worker."""
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # cliente desconectó — normal en health-checks, no es error

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        self._safe_write(body)

    def _send_html(self, code, html: str):
        body = html.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        self._safe_write(body)

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

        # Dashboard visual de agentes — página única, sin auth (solo lectura, detrás de Caddy).
        if self.path in ("/", "/dashboard", "/agentes"):
            self._send_html(200, _DASHBOARD_HTML)
            return

        # Feed de actividad de agentes (JSON) que consume el dashboard.
        if self.path == "/v1/activity":
            self._send_json(200, _build_activity_payload())
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
                    "default_chat": "deepseek",
                    "deepseek": getattr(core, "DEEPSEEK_MODEL", "deepseek-chat"),
                    "ollama_fast": core.OLLAMA_FAST_MODEL,
                    "ollama_quality": core.OLLAMA_QUALITY_MODEL,
                    "tool_use": core.CLAUDE_SONNET,
                },
                "routing": {
                    "chat_default": "deepseek",
                    "tools_search_memory_agents": "sonnet",
                    "oss_explicit": "ollama gpt-oss:20b",
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
