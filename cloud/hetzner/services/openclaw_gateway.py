#!/usr/bin/env python3
"""
openclaw_gateway.py — Gateway HTTP nativo para Louis.

NO depende de un "binario OpenClaw" externo (openclaw.ai 403). Es una capa
delgada que envuelve `louis_core` y expone tres rutas:

  GET  /healthz                    → liveness probe
  GET  /v1/status                  → estado del gateway + routing
  POST /v1/chat/completions        → endpoint compatible con OpenAI (usa louis_core)
  POST /v1/tools/{name}            → ejecutar tool directamente (debug)
  GET  /app                        → app móvil (PWA); datos en /v1/app/* (Bearer)
  POST /v1/app/chat/stream         → chat en streaming (SSE) · GET /v1/app/chat/result?rid=
  *    /v1/app/push/*              → notificaciones push (key, subscribe, unsubscribe, test)

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
import re
import sys
import json
import time
import hmac
import secrets
import base64
import ipaddress
import logging
import threading
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# louis_core vive en /opt/openclaw/scripts/
SCRIPTS_DIR = Path("/opt/openclaw/scripts")
if SCRIPTS_DIR.exists():
    sys.path.insert(0, str(SCRIPTS_DIR))

import louis_core as core  # noqa: E402
import app_auth  # noqa: E402  (usuario/contraseña de la app, sesiones propias)
import app_media  # noqa: E402  (adjuntos, grabaciones y archivos de la app)
import app_translate  # noqa: E402  (traductor español/inglés/chino de la app)

# Solo StreamHandler: el servicio de systemd ya redirige stdout/stderr al log
# (StandardOutput=append:.../openclaw-gateway.log). Con FileHandler además, cada
# línea salía duplicada y el log crecía al doble.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("openclaw-gateway")

HOST = os.environ.get("OPENCLAW_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENCLAW_PORT", "3000"))
# ---- Autenticación (falla CERRADO) -----------------------------------------
# Antes, sin OPENCLAW_GATEWAY_TOKEN el gateway no pedía nada y cualquiera en internet
# podía hablar con el agente (con memoria y tools) o ejecutar tools. Ahora, sin
# credenciales configuradas, se rechaza todo salvo /healthz. Para trabajar en local
# sin credenciales: OPENCLAW_ALLOW_NO_AUTH=1 (nunca en producción).
TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")       # API completa (Bearer)
DOC_TOKEN = os.environ.get("DOC_CHAT_TOKEN", "")           # chat embebido en documentos (Bearer, alcance mínimo)
DASH_USER = os.environ.get("TABLERO_USER", "")             # tablero/dashboard (Basic)
DASH_PASS = os.environ.get("TABLERO_PASSWORD", "")
ALLOW_NO_AUTH = os.environ.get("OPENCLAW_ALLOW_NO_AUTH", "") == "1"


def _same(a: str, b: str) -> bool:
    """Comparación en tiempo constante; vacío nunca coincide."""
    return bool(a) and bool(b) and hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _bearer(handler) -> str:
    auth = handler.headers.get("Authorization", "")
    return auth[7:].strip() if auth.startswith("Bearer ") else ""


def _require_auth(handler) -> bool:
    """API completa: exige Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>."""
    if not TOKEN:
        return ALLOW_NO_AUTH
    return _same(_bearer(handler), TOKEN)


def _client_ip(handler) -> str:
    """IP real del cliente. Detrás de Caddy (loopback) se confía en X-Real-IP; si no, en la conexión directa."""
    peer = handler.client_address[0] if handler.client_address else "?"
    if peer in ("127.0.0.1", "::1"):
        real = (handler.headers.get("X-Real-IP") or "").strip()
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            pass
    return peer


_ATAJO_RUTAS = {"/v1/app/media/quick", "/v1/app/ping", "/v1/app/clientlog"}


def _require_app_auth(handler) -> bool:
    """Rutas /v1/app/*: acepta el token MAESTRO (administrador, CLI) o una SESIÓN de usuario/contraseña
    (prefijo dns_). Las sesiones SOLO valen aquí: /v1/tools, /v1/agents y /v1/chat/completions siguen
    exigiendo el token maestro. Deja en handler._auth = ('admin'|'sesion', sesión)."""
    if _require_auth(handler):
        handler._auth = ("admin", None)
        return True
    s = app_auth.check_session(_bearer(handler))
    if s:
        # Usuarios «atajo-…» (para Atajos de iPhone) SOLO pueden subir archivos: si ese token se filtra, no lee nada.
        if str(s.get("user", "")).startswith("atajo-") and handler.path.split("?", 1)[0] not in _ATAJO_RUTAS:
            return False
        handler._auth = ("sesion", s)
        return True
    return False


def _require_doc_auth(handler) -> bool:
    """Chat de documentos: exige Bearer <DOC_CHAT_TOKEN> (distinto del token principal)."""
    if not DOC_TOKEN:
        return ALLOW_NO_AUTH
    return _same(_bearer(handler), DOC_TOKEN)


def _require_dashboard_auth(handler) -> bool:
    """Tablero/dashboard: HTTP Basic con TABLERO_USER / TABLERO_PASSWORD. No depende de Caddy."""
    if not (DASH_USER and DASH_PASS):
        return ALLOW_NO_AUTH
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        user, _, pw = base64.b64decode(auth[6:].strip()).decode("utf-8").partition(":")
    except Exception:
        return False
    return _same(user, DASH_USER) and _same(pw, DASH_PASS)



# ---- App móvil (PWA) --------------------------------------------------------
# El cascarón (HTML/manifest/sw/icono) es público y NO contiene datos ni secretos:
# el token se escribe una vez en el teléfono. Todos los datos van por /v1/app/* con Bearer.
_APP_DIR = next((d for d in (Path(__file__).resolve().parent / "app", Path("/opt/openclaw/scripts/app")) if d.is_dir()),
                Path(__file__).resolve().parent / "app")
_APP_FILES = {
    "/app/": ("index.html", "text/html; charset=utf-8"),
    "/app/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/app/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/app/icon.svg": ("icon.svg", "image/svg+xml"),
    # Script del widget para iPhone (app Scriptable). Público: NO contiene el token (lo pide una vez).
    "/app/widget-scriptable.js": ("widget-scriptable.js", "text/javascript; charset=utf-8"),
}


_NAME_SAFE = re.compile(r"[^\w .\-]", re.UNICODE)


def _agent_name() -> str:
    """Nombre del asistente para la interfaz (solo letras, espacios, punto y guion: va dentro de HTML/JS/JSON)."""
    return _NAME_SAFE.sub("", core.AGENT_NAME).strip() or "Asistente"


_HOST_SAFE = re.compile(r"^[A-Za-z0-9.\-]{1,100}(:\d{1,5})?$")


def _base_url(host: str = "") -> str:
    """https://<dominio>: LOUIS_DOMAIN (el de Caddy) y, si no está, el Host de la petición (validado)."""
    dom = (os.environ.get("LOUIS_DOMAIN", "") or "").strip().strip("\"'")
    if not dom and host and _HOST_SAFE.match(host):
        dom = host
    return "https://" + (dom or "donna.kawiil.mx")


def _app_file(ruta: str, host: str = ""):
    """(bytes, content-type) del cascarón de la app, o None. {{AGENT_NAME}} y {{BASE_URL}} se sustituyen al servir."""
    ent = _APP_FILES.get(ruta)
    if not ent:
        return None
    try:
        raw = (_APP_DIR / ent[0]).read_bytes()
    except OSError:
        return None
    raw = raw.replace(b"{{AGENT_NAME}}", _agent_name().encode("utf-8"))
    raw = raw.replace(b"{{BASE_URL}}", _base_url(host).encode("utf-8"))
    return raw, ent[1]


# ---- Chat en streaming (SSE) para la app móvil --------------------------------
# call_llm devuelve la respuesta completa (enrutado + tools + varios modelos), así que el
# streaming es a nivel de servicio: la respuesta se calcula en un hilo mientras el cliente
# recibe latidos de progreso, y al terminar se emite en fragmentos. El resultado queda
# guardado 1 h por `rid`: si el teléfono se bloquea a media respuesta, la app lo recupera
# (/v1/app/chat/result) y, si el cliente ya no estaba, se avisa por push.
_RID_OK = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_RESULTS: dict = {}
_RESULTS_LOCK = threading.Lock()
_CHAT_SLOTS = threading.BoundedSemaphore(4)


def _split_messages(messages: list):
    """(último mensaje de usuario, historial previo) — misma lógica que /v1/chat/completions."""
    user_msg, history = "", []
    for m in messages or []:
        role, content = m.get("role"), m.get("content", "")
        text = content if isinstance(content, str) else json.dumps(content)
        if role == "user":
            if user_msg:
                history.append({"role": "user", "content": user_msg})
            user_msg = text
        elif role == "assistant":
            history.append({"role": "assistant", "content": text})
    return user_msg, history


def _gc_results():
    now = time.time()
    with _RESULTS_LOCK:
        for k in [k for k, v in _RESULTS.items() if now - v["t0"] > 3600]:
            _RESULTS.pop(k, None)


# Historial persistente de la app (igual que telegram-history.jsonl / slack-history.jsonl). Sirve para
# dos cosas: (1) que call_llm sepa si ya hubo conversación hoy — SIN archivo trata TODO mensaje como
# «primera conversación del día» y responde con el briefing completo cada vez; (2) que la destilación
# nocturna de memoria (run_memory_distillation) también aprenda de lo conversado por la app.
APP_HISTORY_FILE = core.SPACE / "app-history.jsonl"


def _hist_append(role: str, content: str):
    try:
        core.append_history(APP_HISTORY_FILE, role, content)
    except Exception:
        log.warning("no pude escribir app-history.jsonl", exc_info=True)


_VOZ_BLOQUE = (
    "\n\n# MODO VOZ (tu respuesta se LEERÁ EN VOZ ALTA)\n"
    "Responde como en una conversación hablada: máximo 3 o 4 frases cortas, español de México, SIEMPRE de tú (nunca voseo: no digas «vos», «tenés», "
    "«reenviás»), sin markdown, sin listas, sin tablas, sin emojis, sin enlaces. Di los números y fechas como se dicen en voz alta. Si la respuesta "
    "requiere una lista o una tabla, di que la dejas en el chat y da solo lo esencial. Responde SOLO a lo último que dijo Polo: si saluda, saluda y "
    "pregunta en qué le ayudas; no retomes temas viejos (adjuntos, tareas pendientes) si él no los menciona. Si no entendiste algo, pregunta brevemente."
)


def _chat_worker(rid: str, user_msg: str, history: list, voz: bool = False):
    res = _RESULTS[rid]
    try:
        api_key = core.load_anthropic_key()
        sys_prompt = core.load_system_prompt(channel="api") + (_VOZ_BLOQUE if voz else "")
        # Mismo orden que telegram-bridge: el mensaje entra al historial ANTES de call_llm.
        _hist_append("user", user_msg)
        text, model = core.call_llm(api_key, sys_prompt, history, user_msg, history_file=APP_HISTORY_FILE)
        if text:
            _hist_append("assistant", text)
        out = {"text": text or "(sin respuesta)", "model": model, "error": None}
    except Exception as e:
        log.exception("chat stream falló")
        out = {"text": "", "model": "", "error": str(e)[:200] or "error"}
    with _RESULTS_LOCK:
        res.update(out, done=True)
        gone = res.get("client_gone")
    _CHAT_SLOTS.release()
    if gone:
        _push_answer(res)


def _push_answer(res: dict):
    """El cliente se fue antes de recibir la respuesta → avisa por push (si hay suscripciones)."""
    if res.get("pushed"):
        return
    res["pushed"] = True
    try:
        import louis_push
        txt = res.get("text") or ("⚠️ " + (res.get("error") or "error"))
        louis_push.notify_async(f"{core.AGENT_NAME} respondió", re.sub(r"[*_`#>]+", "", txt).strip()[:200], tab="chat",
                                tag="resp-" + res.get("rid", ""))
    except Exception:
        log.exception("push de respuesta falló")


# ---- Acciones de confirmación de la bandeja (lista CERRADA: solo estas, nunca texto libre) ----------
# Cada acción aplica algo que el asistente PROPUSO y que antes se confirmaba escribiendo «cierra todos los
# sugeridos» / «agrega todas las altas». El botón solo funciona sobre la propuesta del MISMO DÍA que el aviso:
# si ya hay una propuesta más nueva, se rechaza (no se cierra algo distinto de lo que se veía en pantalla).
def _fecha_cdmx(ts: int) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts, core.TZ_CDMX).strftime("%Y-%m-%d")


def _estado_steward_fecha() -> str:
    try:
        return str(core._steward_load_state().get("date", ""))
    except Exception:
        return ""


def _estado_altas_fecha() -> str:
    try:
        return str(json.loads(core.SLACK_FOLLOWUP_STATE.read_text()).get("fecha", ""))
    except Exception:
        return ""


_ACTIONS = {
    "steward_cerrar_sugeridos": (lambda: core.aplicar_cierres_steward("todos los sugeridos"), _estado_steward_fecha),
    "altas_todas": (lambda: core.aplicar_altas_seguimiento("todas"), _estado_altas_fecha),
}


def _run_notif_action(nid: str, action_id: str):
    """(código HTTP, dict). Valida: la acción existe, el aviso la ofrece, no se usó ya y la propuesta sigue vigente."""
    import louis_push
    rec = louis_push.inbox_get(nid)
    if not rec:
        return 404, {"error": "aviso no encontrado"}
    if action_id not in _ACTIONS or action_id not in [a.get("id") for a in rec.get("actions") or []]:
        return 400, {"error": "acción no permitida para este aviso"}
    if action_id in (rec.get("done") or {}):
        return 200, {"ok": True, "ya": True, "result": rec["done"][action_id].get("result", "")}
    fn, fecha_estado = _ACTIONS[action_id]
    if fecha_estado() != _fecha_cdmx(rec["ts"]):
        return 409, {"error": "Esta propuesta ya no está vigente (hay una más reciente). Pídele a la asistente que la repita."}
    result = str(fn())
    louis_push.inbox_set_done(nid, action_id, result)
    return 200, {"ok": True, "result": result}

_AGENT_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")

# Límite global para el chat de documentos (token con alcance mínimo, pero aun así
# cuesta dinero si alguien lo extrae de un HTML): 20/min y 300/día.
_DOC_RATE_LOCK = threading.Lock()
_DOC_HITS_MIN: deque = deque()
_DOC_HITS_DAY: deque = deque()


def _doc_rate_ok() -> bool:
    now = time.time()
    with _DOC_RATE_LOCK:
        while _DOC_HITS_MIN and now - _DOC_HITS_MIN[0] > 60:
            _DOC_HITS_MIN.popleft()
        while _DOC_HITS_DAY and now - _DOC_HITS_DAY[0] > 86400:
            _DOC_HITS_DAY.popleft()
        if len(_DOC_HITS_MIN) >= 20 or len(_DOC_HITS_DAY) >= 300:
            return False
        _DOC_HITS_MIN.append(now)
        _DOC_HITS_DAY.append(now)
        return True


_DOC_CHAT_SYSTEM = (
    "Eres un asistente que ayuda a entender el documento cuyo contenido viene en el "
    "mensaje. Responde SOLO con base en ese contenido. No tienes acceso a correo, "
    "agenda, clientes, archivos ni herramientas: si la pregunta no se puede responder "
    "con el documento, dilo. Español mexicano, claro y conciso."
)
_MAX_BODY = 1_000_000  # 1 MB
_SHARE_MISS: dict = {}                 # ip -> intentos fallidos a /s/<token> en el último minuto
_SHARE_LOCK = threading.Lock()
_VSESS: dict = {}                      # vsid -> {"h": [{role, content}], "t": última actividad}: contexto SOLO de esta conversación por voz
_VSESS_LOCK = threading.Lock()
_ADIOS_RE = re.compile(r"^\W*(adi[oó]s|hasta luego|eso es todo|terminar|terminamos|ya es todo)\W*$", re.I)
_CLOG_HITS: deque = deque()
_CLOG_LOCK = threading.Lock()


def _clientlog_ok() -> bool:
    """Registro de errores de la app del teléfono: máx. 30 por minuto (para diagnosticar sin dejar abierta una puerta de spam)."""
    now = time.time()
    with _CLOG_LOCK:
        while _CLOG_HITS and now - _CLOG_HITS[0] > 60:
            _CLOG_HITS.popleft()
        if len(_CLOG_HITS) >= 30:
            return False
        _CLOG_HITS.append(now)
        return True


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
<title>@@AGENT_NAME@@ · Agentes en vivo</title>
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


_TABLERO_HTML = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tablero Kawiil — Seguimiento</title>
<style>
:root{--bg:#0e1116;--card:#161b22;--bd:#262d36;--tx:#e6edf3;--mut:#8b949e;--ac:#58a6ff;--warn:#d29922;--bad:#f85149;--ok:#3fb950;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px 20px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
h1{font-size:18px;margin:0}.upd{color:var(--mut);font-size:12px}
.nav a{color:var(--ac);margin-left:14px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;padding:20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px}
.card h2{font-size:14px;margin:0 0 10px}
.li{padding:8px 0;border-top:1px solid var(--bd);font-size:13px}.li:first-of-type{border-top:0}
.click{cursor:pointer}.click:hover{color:var(--ac)}
.tag{font-size:11px;color:var(--mut)}.b-bad{color:var(--bad)}.b-warn{color:var(--warn)}
.pill{font-size:10px;padding:1px 7px;border-radius:9px;border:1px solid var(--bd);color:var(--mut)}
.empty{color:var(--mut);font-style:italic}a{color:var(--ac);text-decoration:none}
#ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);align-items:flex-start;justify-content:center;padding:40px 16px;z-index:9}
#mod{background:var(--card);border:1px solid var(--bd);border-radius:14px;max-width:820px;width:100%;max-height:85vh;overflow:auto;padding:22px}
#mod h3{margin:0 0 6px}#mbody pre{white-space:pre-wrap;word-wrap:break-word;font:13px/1.55 ui-monospace,Menlo,monospace;color:var(--tx)}
.x{float:right;cursor:pointer;color:var(--mut);font-size:20px}
</style></head>
<body>
<header><h1>🧭 Tablero Kawiil</h1>
  <span class="nav"><a href="/tablero">Seguimiento</a><a href="/dashboard">Agentes (grafo)</a></span>
  <span class="upd" id="upd">cargando…</span></header>
<div class="grid" id="grid"></div>
<div id="ov" onclick="if(event.target.id==='ov')cerrar()"><div id="mod">
  <span class="x" onclick="cerrar()">✕</span>
  <h3 id="mtitle">…</h3><div id="mbody"></div></div></div>
<script>
function esc(s){return (s||'').toString().replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function ea2(s){return esc(s).replace(/"/g,'&quot;')}
function vc(t){t=(t||'').toLowerCase();if(t.includes('vence hoy')||t.includes('urgente'))return 'b-bad';if(/\d{1,2}:\d{2}/.test(t)||t.includes('hoy'))return 'b-warn';return ''}
function cerrar(){document.getElementById('ov').style.display='none'}
async function abrirEnt(f){
  const ov=document.getElementById('ov');ov.style.display='flex';
  document.getElementById('mtitle').textContent='Cargando…';document.getElementById('mbody').innerHTML='';
  try{
    const d=await fetch('/v1/entregable?f='+encodeURIComponent(f)).then(r=>r.json());
    if(!d.ok){document.getElementById('mtitle').textContent='No disponible';document.getElementById('mbody').textContent=d.error||'';return;}
    document.getElementById('mtitle').textContent=(d.meta&&d.meta.titulo)||f;
    const head=Object.entries(d.meta||{}).filter(([k])=>['cliente','estado','fecha_actualizacion','tipo','autor'].includes(k)).map(([k,v])=>k+': '+v).join('  ·  ');
    document.getElementById('mbody').innerHTML='<div class="tag" style="margin-bottom:12px">'+esc(head)+'</div><pre>'+esc(d.cuerpo||'(sin contenido)')+'</pre>';
  }catch(e){document.getElementById('mbody').textContent='error: '+e}
}
async function load(){
 try{
  const [t,a]=await Promise.all([
    fetch('/v1/tablero').then(r=>r.json()),
    fetch('/v1/activity').then(r=>r.json()).catch(()=>({}))]);
  const g=document.getElementById('grid');g.innerHTML='';
  // Vencimientos
  let v=(t.vencimientos||[]).map(x=>`<div class="li ${vc(x)}">${esc(x)}</div>`).join('')||'<div class="empty">sin vencimientos</div>';
  g.innerHTML+=`<div class="card"><h2>⏰ Vencimientos / seguimiento</h2>${v}</div>`;
  // Entregables clickeables
  let ents=(t.entregables||[]).map(x=>`<div class="li click" onclick="abrirEnt('${ea2(x.archivo)}')">${esc(x.icono)} ${esc(x.titulo)}${x.cliente?' — '+esc(x.cliente):''} <span class="pill">${esc(x.estado)}</span></div>`).join('')||'<div class="empty">sin entregables</div>';
  g.innerHTML+=`<div class="card"><h2>📦 Entregables (Cerebro) <span class="tag">— clic para ver</span></h2><div class="tag" style="margin-bottom:6px">${esc(t.entregables_resumen||'')}</div>${ents}</div>`;
  // SJF clickeable → SCJN
  let s=t.sjf||{};
  let sj=(s.recientes||[]).map(x=>`<div class="li"><a href="${ea2(x.url)}" target="_blank" rel="noopener">${esc(x.rubro)}</a><br><span class="tag">${esc(x.fecha)} · reg ${esc(x.reg)}</span></div>`).join('')|| (s.error?`<div class="empty">error: ${esc(s.error)}</div>`:'<div class="empty">n/d</div>');
  g.innerHTML+=`<div class="card"><h2>⚖️ SJF — últimas tesis <span class="tag">— clic para abrir en SCJN</span></h2><div class="tag" style="margin-bottom:6px">total ${esc(s.total||0)} · último ingreso ${esc(s.ultima_fecha||'?')}</div>${sj}</div>`;
  // DOF
  let d=t.dof||{};
  let df=(d.dias||[]).map(x=>{let e=Object.entries(x.ediciones||{}).map(([k,n])=>k+':'+n).join(' · ');return `<div class="li">${esc(x.fecha)} — ${esc(e)}</div>`}).join('')||(d.error?`<div class="empty">error: ${esc(d.error)}</div>`:'<div class="empty">n/d</div>');
  g.innerHTML+=`<div class="card"><h2>📰 DOF — publicaciones</h2>${df}</div>`;
  // Agentes
  let ev=(a&&(a.events||a.activity||a.recent))||[];
  let ealist=Array.isArray(ev)?ev.slice(0,10).map(x=>`<div class="li">${esc(typeof x==='string'?x:(x.title||x.summary||x.agent||x.name||JSON.stringify(x).slice(0,90)))}</div>`).join(''):'';
  g.innerHTML+=`<div class="card"><h2>🤖 Agentes — actividad</h2>${ealist||'<div class="empty">sin actividad reciente</div>'}<div class="li"><a href="/dashboard">→ ver grafo completo de agentes</a></div></div>`;
  document.getElementById('upd').textContent='Actualizado '+(t.generado||'')+' · auto-refresh 60s';
 }catch(e){document.getElementById('upd').textContent='error: '+e}
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')cerrar()});
load();setInterval(load,60000);
</script></body></html>"""


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

    def _send_json(self, code, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        self._safe_write(body)

    def do_OPTIONS(self):
        # Preflight CORS: el chat embebido en los HTML llama desde otro origen/file://
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception:
            pass

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
        if n > _MAX_BODY:
            raise ValueError("cuerpo demasiado grande")
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8"))

    def _challenge(self):
        """401 con WWW-Authenticate para que el navegador pida usuario y contraseña."""
        body = b'{"error": "unauthorized"}'
        try:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Louis", charset="UTF-8"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        self._safe_write(body)

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # ---------- GET ----------
    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": "openclaw-gateway",
                                  "version": "1.0", "uptime_started": getattr(self.server, "started_at", 0)})
            return

        _r0 = self.path.split("?", 1)[0]
        if _r0.startswith("/s/"):                              # documento compartido: público, solo lectura, con el enlace secreto
            self._shared_page(_r0[3:])
            return
        if _r0 == "/app":
            # El service worker (alcance /app/) solo controla /app/…: sin la diagonal final la
            # app no queda bajo su control y las notificaciones push nunca se registran.
            _q = self.path[len("/app"):]
            try:
                self.send_response(302)
                self.send_header("Location", "/app/" + (_q if _q.startswith("?") else ""))
                self.send_header("Content-Length", "0")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        if _r0 in _APP_FILES:
            f = _app_file(_r0, self.headers.get("Host", ""))
            if not f:
                self._send_json(404, {"error": "app no instalada"})
                return
            body, ctype = f
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if ctype.startswith("text/html"):
                    self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            self._safe_write(body)
            return

        if _r0.startswith("/v1/app/"):
            if not _require_app_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            try:
                if _r0 == "/v1/app/ping":
                    kind, ses = self._auth
                    self._send_json(200, {"ok": True, "modo": kind, "user": (ses or {}).get("user", "")})
                elif _r0 == "/v1/app/sessions":
                    kind, ses = self._auth
                    self._send_json(200, {"sesiones": app_auth.list_sessions(
                        (ses or {}).get("user") if kind == "sesion" else None, _bearer(self))})
                elif _r0 == "/v1/app/chat/result":
                    from urllib.parse import urlparse, parse_qs
                    rid = (parse_qs(urlparse(self.path).query).get("rid") or [""])[0]
                    _gc_results()
                    with _RESULTS_LOCK:
                        r = _RESULTS.get(rid) if _RID_OK.match(rid) else None
                        out = ({"found": True, "done": bool(r.get("done")), "text": r.get("text", ""),
                                "model": r.get("model", ""), "error": r.get("error")} if r else {"found": False})
                    self._send_json(200, out)
                elif _r0 == "/v1/app/push/key":
                    import louis_push
                    self._send_json(200, {"key": louis_push.public_key()})
                elif _r0 == "/v1/app/push/status":
                    import louis_push
                    self._send_json(200, {"subs": louis_push.count()})
                elif _r0 == "/v1/app/hoy":
                    import louis_push
                    d = core.build_app_hoy()
                    d["sin_leer"] = louis_push.unread_count()                  # para la campana y el widget
                    d["confirmaciones"] = louis_push.pending_confirmations()   # avisos con botón sin usar
                    self._send_json(200, d)
                elif _r0 == "/v1/app/notifs":
                    import louis_push
                    from urllib.parse import urlparse, parse_qs
                    try:
                        lim = int((parse_qs(urlparse(self.path).query).get("limit") or ["50"])[0])
                    except ValueError:
                        lim = 50
                    self._send_json(200, {"items": louis_push.inbox_list(lim), "unread": louis_push.unread_count(),
                                          "confirmaciones": louis_push.pending_confirmations()})
                elif _r0 == "/v1/app/artefactos":
                    import app_artifacts
                    self._send_json(200, {"items": app_artifacts.listar()})
                elif _r0 == "/v1/app/artefacto":
                    import app_artifacts
                    from urllib.parse import urlparse, parse_qs
                    aid = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
                    try:
                        art = app_artifacts.obtener(aid)
                    except ValueError:
                        art = None
                    self._send_json(200 if art else 404, art or {"error": "no existe ese artefacto"})
                elif _r0 == "/v1/app/media" or _r0.startswith("/v1/app/media/"):
                    self._media_get(_r0)
                elif _r0 == "/v1/app/entregables":
                    self._send_json(200, {"entregables": core._entregables_lista_tablero()})
                elif _r0 == "/v1/app/entregable":
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(self.path).query)
                    self._send_json(200, core._entregable_detalle((q.get("f") or [""])[0]))
                else:
                    self._send_json(404, {"error": "no encontrado"})
            except Exception as e:
                log.exception("/v1/app falló")
                self._send_json(500, {"error": str(e)[:200]})
            return

        # Todo lo que muestra datos (dashboard, tablero, entregables) exige usuario y
        # contraseña AQUÍ, sin depender de que Caddy lo haga: en producción se vio que
        # el tráfico llegaba al gateway sin pasar por esa protección.
        _ruta = self.path.split("?", 1)[0]
        if (_ruta in ("/", "/dashboard", "/agentes", "/tablero", "/seguimiento",
                      "/v1/activity", "/v1/tablero")
                or _ruta.startswith("/v1/entregable")):
            if not _require_dashboard_auth(self):
                self._challenge()
                return

        # Dashboard visual de agentes — solo lectura, con usuario y contraseña.
        if self.path in ("/", "/dashboard", "/agentes"):
            self._send_html(200, _DASHBOARD_HTML.replace("@@AGENT_NAME@@", _agent_name()))
            return

        # Feed de actividad de agentes (JSON) que consume el dashboard.
        if self.path == "/v1/activity":
            self._send_json(200, _build_activity_payload())
            return

        # Tablero de seguimiento (vencimientos + entregables + SJF + DOF + agentes).
        # Protegido por basic-auth en Caddy (datos de clientes/legal).
        if self.path in ("/tablero", "/seguimiento"):
            self._send_html(200, _TABLERO_HTML)
            return
        if self.path == "/v1/tablero":
            try:
                self._send_json(200, core.build_tablero_data())
            except Exception as e:
                self._send_json(500, {"error": str(e)[:200]})
            return
        # Drill-down: detalle de un entregable de Cerebro (?f=<archivo>).
        if self.path.startswith("/v1/entregable"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            archivo = (q.get("f") or [""])[0]
            try:
                self._send_json(200, core._entregable_detalle(archivo))
            except Exception as e:
                self._send_json(500, {"error": str(e)[:200]})
            return

        if self.path == "/v1/status":
            if not _require_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            try:
                status = core._verificar_conexiones(incluir_m365=False, incluir_gateway=False)
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

        # Rutas de API (listar tools, agentes y su contenido): exigen el token Bearer.
        if self.path == "/v1/tools" or self.path.startswith("/v1/agents"):
            if not _require_auth(self):
                self._send_json(401, {"error": "unauthorized"})
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
            # Sin esta validación, '/v1/agents/../AGENDA' leía cualquier .md fuera de
            # la carpeta de agentes (toda la memoria de Louis).
            if not _AGENT_NAME_OK.match(name):
                self._send_json(400, {"error": "nombre de agente inválido"})
                return
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
    def _doc_chat(self):
        """Chat de seguimiento embebido en los documentos HTML. A propósito NO usa
        louis_core.call_llm: sin tools, sin memoria, solo el contexto del documento que
        manda el navegador. Token propio (DOC_CHAT_TOKEN) y límite de uso."""
        if not _require_doc_auth(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        if not _doc_rate_ok():
            self._send_json(429, {"error": "demasiadas solicitudes, intenta en unos minutos"})
            return
        try:
            body = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": f"JSON inválido: {e}"})
            return
        msgs = [m for m in (body.get("messages") or []) if isinstance(m, dict) and m.get("role") == "user"]
        texto = msgs[-1].get("content", "") if msgs else ""
        if not isinstance(texto, str) or not texto.strip():
            self._send_json(400, {"error": "messages requerido"})
            return
        try:
            api_key = core.load_anthropic_key()
            respuesta = core.call_haiku(api_key, _DOC_CHAT_SYSTEM, [], texto[:12000])
        except Exception:
            log.exception("doc-chat falló")
            self._send_json(500, {"error": "no pude responder ahora"})
            return
        now = int(time.time())
        self._send_json(200, {
            "id": f"doc-{now}", "object": "chat.completion", "created": now, "model": "haiku",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": respuesta},
                         "finish_reason": "stop"}],
        })

    def _sse(self, event: str, data: dict) -> bool:
        try:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return False

    def _shared_page(self, token: str):
        ip = _client_ip(self)
        now = time.time()
        with _SHARE_LOCK:
            hits = _SHARE_MISS.setdefault(ip, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= 20:
                self._send_json(429, {"error": "demasiados intentos"})
                return
        html = app_media.share_page(token) if re.fullmatch(r"[A-Za-z0-9_-]{20,60}", token or "") else None
        if html is None:
            with _SHARE_LOCK:
                _SHARE_MISS[ip].append(now)
            body = "<!doctype html><meta charset=utf-8><meta name=robots content=noindex><title>No disponible</title><p style='font:16px system-ui;padding:24px'>Este enlace no existe o ya caducó.</p>".encode()
            code = 404
        else:
            body, code = html.encode("utf-8"), 200
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
            self.end_headers()
        except OSError:
            return
        self._safe_write(body)

    # ---- adjuntos (fotos, audios, videos, archivos) ----
    def _media_user(self) -> str:
        kind, ses = getattr(self, "_auth", (None, None))
        return (ses or {}).get("user", "") if kind == "sesion" else "admin"

    def _media_get(self, path: str):
        from urllib.parse import urlparse, parse_qs
        parts = [x for x in path.split("/") if x]          # v1 app media [id] [accion]
        if len(parts) == 4 and parts[3] == "search":
            q = parse_qs(urlparse(self.path).query)
            self._send_json(200, {"items": app_media.buscar_items((q.get("q") or [""])[0])})
            return
        if len(parts) == 3:
            q = parse_qs(urlparse(self.path).query)
            self._send_json(200, {"items": app_media.listar(int((q.get("limit") or ["50"])[0] or 50), (q.get("tipo") or [""])[0]),
                                  "libre_mb": app_media.free_bytes() // 1024 ** 2})
            return
        mid = parts[3]
        if not app_media.ID_RE.match(mid) or not app_media.get(mid):
            self._send_json(404, {"error": "no existe ese adjunto"})
            return
        if len(parts) == 4:
            self._send_json(200, app_media.get(mid))
        elif parts[4] == "parts":
            self._send_json(200, {"partes": app_media.parts_have(mid)})
        elif parts[4] == "shares":
            self._send_json(200, {"activos": app_media.share_list(mid)})
        elif parts[4] == "text":
            self._send_json(200, {"texto": app_media.leer_texto(mid, 0, 30000)})
        elif parts[4] == "file":
            fp, m = app_media.file_path(mid)
            if not fp:
                self._send_json(404, {"error": "archivo no disponible"})
                return
            size = fp.stat().st_size
            try:
                self.send_response(200)
                self.send_header("Content-Type", m.get("mime") or "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % app_media.safe_name(m["nombre"]).encode("ascii", "replace").decode())
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "sandbox")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(fp, "rb") as f:
                    while True:
                        blk = f.read(1024 * 1024)
                        if not blk:
                            break
                        self.wfile.write(blk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self._send_json(404, {"error": "ruta desconocida"})

    def _media_post(self, path: str, body: dict) -> bool:
        parts = [x for x in path.split("/") if x]
        try:
            if parts == ["v1", "app", "media", "init"]:
                r = app_media.init(str(body.get("nombre", "")), str(body.get("mime", "")), int(body.get("tamano") or 0),
                                   self._media_user(), str(body.get("origen", "")))
                self._send_json(200, r)
                return True
            if len(parts) == 5 and parts[:3] == ["v1", "app", "media"] and parts[4] == "share":
                if not app_media.ID_RE.match(parts[3]):
                    raise ValueError("id inválido")
                r = app_media.share_create(parts[3], int(body.get("dias") or 7), self._media_user())
                r["url"] = _base_url(self.headers.get("Host", "")) + "/s/" + r["token"]
                self._send_json(200, r)
                return True
            if len(parts) == 5 and parts[:3] == ["v1", "app", "media"] and parts[4] == "unshare":
                if not app_media.ID_RE.match(parts[3]):
                    raise ValueError("id inválido")
                self._send_json(200, {"revocados": app_media.share_revoke(parts[3])})
                return True
            if len(parts) == 5 and parts[:3] == ["v1", "app", "media"] and parts[4] == "finish":
                if not app_media.ID_RE.match(parts[3]):
                    raise ValueError("id inválido")
                m = app_media.finish(parts[3], int(body.get("partes") or 0))
                self._send_json(200, app_media.public(m))
                return True
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return True
        except Exception as e:
            log.exception("media falló")
            self._send_json(500, {"error": str(e)[:200]})
            return True
        return False

    # ---- conversación por voz con Donna ----
    def _voice_post(self, path: str, body: dict) -> bool:
        try:
            if path == "/v1/app/speak":
                audio = app_translate.texto_a_voz(str(body.get("texto", ""))[:1500], str(body.get("idioma", "es") or "es"))
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self._safe_write(audio)
                return True
            if path == "/v1/app/voice/ask":
                user_msg, history = _split_messages(body.get("messages", []))
                if not user_msg.strip():
                    raise ValueError("no hay mensaje")
                if not _CHAT_SLOTS.acquire(blocking=False):
                    self._send_json(429, {"error": "Donna está atendiendo otras consultas; reintenta en un momento"})
                    return True
                rid = "v" + secrets.token_hex(6)
                res = {"rid": rid, "t0": time.time(), "done": False, "client_gone": False}
                with _RESULTS_LOCK:
                    _RESULTS[rid] = res
                _chat_worker(rid, user_msg, history, voz=True)          # bloquea hasta tener la respuesta (libera el cupo)
                with _RESULTS_LOCK:
                    out = _RESULTS.pop(rid, res)
                if out.get("error"):
                    self._send_json(502, {"error": out["error"]})
                else:
                    self._send_json(200, {"respuesta": out.get("text", ""), "model": out.get("model", "")})
                return True
            if path == "/v1/app/clientlog":
                if _clientlog_ok():
                    log.warning("clientlog [%s]: %s", self._media_user(), str(body.get("msg", ""))[:500].replace("\n", " "))
                self._send_json(200, {"ok": True})
                return True
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return True
        except RuntimeError as e:
            self._send_json(502, {"error": str(e)})
            return True
        except Exception as e:
            log.exception("voz falló")
            self._send_json(500, {"error": str(e)[:200]})
            return True
        return False

    # ---- turnos en streaming: cada parte aparece y suena en cuanto está lista ----
    def _sse_open(self) -> bool:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            return True
        except OSError:
            return False

    def _stream_voz(self, texto: str, idioma: str, t0: float, marks: dict) -> bool:
        """Manda el audio por trozos (1-2 frases) a medida que se sintetiza. False si el cliente ya no está."""
        import base64
        for i, mp3 in app_translate.voz_por_trozos(texto, idioma):
            if i == 0:
                marks["audio1"] = int((time.time() - t0) * 1000)
            if not self._sse("audio", {"i": i, "b64": base64.b64encode(mp3).decode("ascii")}):
                return False
        return True

    def _voz_primero(self, texto: str, idioma: str, t0: float, marks: dict, mostrar_texto) -> bool:
        """PRIORIDAD A LA VOZ: manda el primer trozo de audio, DESPUÉS el texto (mostrar_texto()), y luego el resto del audio.
        Si la voz falla, el texto se manda igual y el error se propaga. False si el cliente ya no está."""
        import base64
        it = app_translate.voz_por_trozos(texto, idioma)
        try:
            primero = next(it, None)
        except Exception:
            if not mostrar_texto():
                return False
            raise
        if primero is not None:
            marks["audio1"] = int((time.time() - t0) * 1000)
            if not self._sse("audio", {"i": 0, "b64": base64.b64encode(primero[1]).decode("ascii")}):
                return False
        if not mostrar_texto():
            return False
        try:
            for i, mp3 in it:
                if not self._sse("audio", {"i": i, "b64": base64.b64encode(mp3).decode("ascii")}):
                    return False
        except Exception as e:
            self._sse("fail", {"error": "la voz se cortó: " + str(e)[:120]})
            return False
        return True

    def _read_audio_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > app_translate.MAX_AUDIO:
            raise ValueError("el audio de un turno debe pesar entre 1 byte y 25 MB")
        return self.rfile.read(n)

    def _translate_stream(self):
        """PUT /v1/app/translate/stream?de=&a=&sid=&nombre= — audio → [stt] texto → [tr] traducción → [audio]… → [done]."""
        from urllib.parse import urlparse, parse_qs
        t0 = time.time()
        q = parse_qs(urlparse(self.path).query)
        try:
            audio = self._read_audio_body()
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        if not self._sse_open():
            return
        marks = {}
        try:
            texto = app_translate.voz_a_texto(audio, (q.get("nombre") or ["voz.webm"])[0])
            marks["stt"] = int((time.time() - t0) * 1000)
            if not self._sse("stt", {"texto": texto, "ms": marks["stt"]}):
                return
            r = app_translate.traducir(texto, (q.get("de") or ["auto"])[0], (q.get("a") or [""])[0])
            marks["tr"] = int((time.time() - t0) * 1000)
            sid = (q.get("sid") or [""])[0]
            if sid:
                app_translate.registrar_turno(sid, r, "voz")
            mostrar = lambda: self._sse("tr", {**r, "ms": marks["tr"]})
            if (q.get("voz") or ["1"])[0] != "0":
                if not self._voz_primero(r["traduccion"], r["destino"], t0, marks, mostrar):
                    return
            elif not mostrar():
                return
            self._sse("done", {"ms": {**marks, "total": int((time.time() - t0) * 1000)}})
        except (ValueError, RuntimeError) as e:
            self._sse("fail", {"error": str(e)})
        except Exception as e:
            log.exception("translate stream falló")
            self._sse("fail", {"error": str(e)[:200]})

    def _ask_voice(self, user_msg: str, history: list):
        """Pregunta a Donna en modo voz (bloquea hasta tener respuesta). → (texto, modelo, error)."""
        if not _CHAT_SLOTS.acquire(blocking=False):
            return "", "", "Donna está atendiendo otras consultas; reintenta en un momento"
        rid = "v" + secrets.token_hex(6)
        res = {"rid": rid, "t0": time.time(), "done": False, "client_gone": False}
        with _RESULTS_LOCK:
            _RESULTS[rid] = res
        _chat_worker(rid, user_msg, history, voz=True)          # libera el cupo al terminar
        with _RESULTS_LOCK:
            out = _RESULTS.pop(rid, res)
        return out.get("text", ""), out.get("model", ""), out.get("error")

    def _voice_turn(self):
        """PUT /v1/app/voice/turn?vsid=&nombre= — audio → [stt] lo que dijiste → [respuesta] → [audio]… → [done].
        El contexto es SOLO esta conversación por voz (servidor), no el historial viejo del chat."""
        from urllib.parse import urlparse, parse_qs
        t0 = time.time()
        q = parse_qs(urlparse(self.path).query)
        vsid = re.sub(r"[^A-Za-z0-9_-]", "", (q.get("vsid") or [""])[0])[:40] or "anon"
        try:
            audio = self._read_audio_body()
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        if not self._sse_open():
            return
        marks = {}
        try:
            texto = app_translate.voz_a_texto(audio, (q.get("nombre") or ["voz.webm"])[0])
            marks["stt"] = int((time.time() - t0) * 1000)
            if not self._sse("stt", {"texto": texto, "ms": marks["stt"]}):
                return
            if _ADIOS_RE.match(texto):
                self._sse("done", {"fin": True, "ms": {**marks, "total": int((time.time() - t0) * 1000)}})
                return
            with _VSESS_LOCK:
                for k in [k for k, v in _VSESS.items() if time.time() - v["t"] > 1800]:
                    _VSESS.pop(k, None)
                ses = _VSESS.setdefault(vsid, {"h": [], "t": time.time()})
                hist = list(ses["h"][-10:])
            resp, modelo, err = self._ask_voice(texto, hist)
            marks["llm"] = int((time.time() - t0) * 1000)
            if err or not resp.strip():
                self._sse("fail", {"error": err or "Donna no respondió"})
                return
            with _VSESS_LOCK:
                ses["h"] += [{"role": "user", "content": texto}, {"role": "assistant", "content": resp}]
                ses["h"] = ses["h"][-12:]
                ses["t"] = time.time()
            mostrar = lambda: self._sse("respuesta", {"texto": resp, "model": modelo, "ms": marks["llm"]})
            if not self._voz_primero(resp, "es", t0, marks, mostrar):
                return
            self._sse("done", {"ms": {**marks, "total": int((time.time() - t0) * 1000)}})
        except (ValueError, RuntimeError) as e:
            self._sse("fail", {"error": str(e)})
        except Exception as e:
            log.exception("voice turn falló")
            self._sse("fail", {"error": str(e)[:200]})

    def _voice_transcribe(self):
        """PUT /v1/app/voice/transcribe — cuerpo = audio de UN turno → {texto}."""
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > app_translate.MAX_AUDIO:
                raise ValueError("el audio de un turno debe pesar entre 1 byte y 25 MB")
            from urllib.parse import urlparse, parse_qs
            nombre = (parse_qs(urlparse(self.path).query).get("nombre") or ["voz.webm"])[0]
            self._send_json(200, {"texto": app_translate.voz_a_texto(self.rfile.read(n), nombre)})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except RuntimeError as e:
            self._send_json(502, {"error": str(e)})
        except Exception as e:
            log.exception("transcripción de voz falló")
            self._send_json(500, {"error": str(e)[:200]})

    # ---- traductor ----
    def _translate_post(self, path: str, body: dict) -> bool:
        try:
            if path == "/v1/app/translate/new":
                self._send_json(200, {"sid": app_translate.new_sid()})
                return True
            if path == "/v1/app/translate":
                sid = str(body.get("sid", ""))
                r = app_translate.traducir(str(body.get("texto", "")), str(body.get("de", "auto")), str(body.get("a", "")))
                if sid:
                    app_translate.registrar_turno(sid, r, "texto")
                self._send_json(200, r)
                return True
            if path == "/v1/app/translate/speak":
                audio = app_translate.texto_a_voz(str(body.get("texto", "")), str(body.get("idioma", "")))
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self._safe_write(audio)
                return True
            if path == "/v1/app/translate/finish":
                m = app_translate.guardar_sesion(str(body.get("sid", "")), str(body.get("titulo", "")))
                self._send_json(200, m)
                return True
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return True
        except RuntimeError as e:
            self._send_json(502, {"error": str(e)})
            return True
        except Exception as e:
            log.exception("traductor falló")
            self._send_json(500, {"error": str(e)[:200]})
            return True
        return False

    def _translate_audio(self):
        """PUT /v1/app/translate/audio?de=&a=&sid= — cuerpo = audio de UN turno (hasta 25 MB) → transcribe y traduce."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > app_translate.MAX_AUDIO:
                raise ValueError("el audio de un turno debe pesar entre 1 byte y 25 MB")
            audio = self.rfile.read(n)
            nombre = (q.get("nombre") or ["voz.webm"])[0]
            texto = app_translate.voz_a_texto(audio, nombre)
            r = app_translate.traducir(texto, (q.get("de") or ["auto"])[0], (q.get("a") or [""])[0])
            sid = (q.get("sid") or [""])[0]
            if sid:
                app_translate.registrar_turno(sid, r, "voz")
            self._send_json(200, r)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except RuntimeError as e:
            self._send_json(502, {"error": str(e)})
        except Exception as e:
            log.exception("traducción de audio falló")
            self._send_json(500, {"error": str(e)[:200]})

    def _media_quick(self):
        """PUT|POST /v1/app/media/quick?nombre=&mime= — el archivo completo como cuerpo (Atajos de iPhone)."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            nombre = (q.get("nombre") or [""])[0] or "archivo-" + time.strftime("%Y%m%d-%H%M%S")
            mime = (q.get("mime") or [self.headers.get("Content-Type", "")])[0]
            m = app_media.quick_upload(self.rfile, n, nombre, mime, self._media_user(), (q.get("origen") or ["atajo"])[0])
            self._send_json(200, m)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            log.exception("quick upload falló")
            self._send_json(500, {"error": str(e)[:200]})

    def do_PUT(self):
        """PUT /v1/app/media/<id>/chunk?i=N — cuerpo binario de una parte, escrito directo a disco (sin tope de 1 MB)."""
        from urllib.parse import urlparse, parse_qs
        if self.path.split("?", 1)[0] == "/v1/app/translate/audio":
            if not _require_app_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            self._translate_audio()
            return
        if self.path.split("?", 1)[0] == "/v1/app/media/quick":
            if not _require_app_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            self._media_quick()
            return
        if self.path.split("?", 1)[0] == "/v1/app/voice/transcribe":
            if not _require_app_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            self._voice_transcribe()
            return
        if self.path.split("?", 1)[0] in ("/v1/app/translate/stream", "/v1/app/voice/turn"):
            if not _require_app_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            if self.path.startswith("/v1/app/translate/stream"):
                self._translate_stream()
            else:
                self._voice_turn()
            return
        if not self.path.startswith("/v1/app/media/"):
            self._send_json(404, {"error": "no encontrado"})
            return
        if not _require_app_auth(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        u = urlparse(self.path)
        parts = [x for x in u.path.split("/") if x]
        if len(parts) != 5 or parts[4] != "chunk" or not app_media.ID_RE.match(parts[3]):
            self._send_json(404, {"error": "ruta inválida"})
            return
        try:
            idx = int((parse_qs(u.query).get("i") or ["-1"])[0])
            n = int(self.headers.get("Content-Length", "0"))
            self._send_json(200, app_media.save_chunk(parts[3], idx, self.rfile, n))
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            log.exception("chunk falló")
            self._send_json(500, {"error": str(e)[:200]})

    def _chat_stream(self, body: dict):
        rid = str(body.get("rid", ""))
        user_msg, history = _split_messages(body.get("messages", []))
        adj = body.get("attachments")
        if isinstance(adj, list) and adj:
            ctx = app_media.contexto_para_chat([str(x) for x in adj])
            if ctx:
                user_msg = f"{user_msg}\n\n{ctx}"
        if not _RID_OK.match(rid) or not user_msg:
            self._send_json(400, {"error": "rid y mensaje de usuario requeridos"})
            return
        _gc_results()
        with _RESULTS_LOCK:
            if rid in _RESULTS:
                self._send_json(409, {"error": "rid repetido"})
                return
        if not _CHAT_SLOTS.acquire(blocking=False):
            self._send_json(429, {"error": "Louis está atendiendo otras consultas; reintenta en un momento"})
            return
        res = {"rid": rid, "t0": time.time(), "done": False, "client_gone": False}
        with _RESULTS_LOCK:
            _RESULTS[rid] = res
        threading.Thread(target=_chat_worker, args=(rid, user_msg, history), daemon=True).start()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
        except OSError:
            res["client_gone"] = True
        alive = not res["client_gone"]
        last = 0.0
        while alive and not res["done"]:
            time.sleep(0.25)
            if time.time() - last >= 2:
                last = time.time()
                alive = self._sse("status", {"s": int(time.time() - res["t0"])})
        if alive and res["done"]:
            if res.get("error"):
                alive = self._sse("fail", {"error": res["error"]})
            else:
                text = res["text"]
                parts = re.findall(r"\S+\s*|\s+", text)
                chunks = ["".join(parts[i:i + 3]) for i in range(0, len(parts), 3)]
                delay = min(0.03, 3.5 / max(len(chunks), 1))  # toda la animación ≤ ~3.5 s
                for c in chunks:
                    if not self._sse("delta", {"d": c}):
                        alive = False
                        break
                    time.sleep(delay)
                if alive:
                    alive = self._sse("done", {"model": res["model"]})
        if not alive:
            with _RESULTS_LOCK:
                res["client_gone"] = True
                finished = res["done"]
            if finished:
                _push_answer(res)

    def _login(self):
        """POST /v1/app/login {user, password} → {token, expires, user}. Sin autenticación previa, con límite de intentos."""
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"error": "JSON inválido"})
            return
        res = app_auth.login(str(body.get("user", "")), str(body.get("password", "")),
                             _client_ip(self), self.headers.get("User-Agent", ""))
        if res.get("ok"):
            log.info("login ok: %s desde %s", res["user"], _client_ip(self))
            self._send_json(200, {"ok": True, "token": res["token"], "expires": res["expires"], "user": res["user"]})
            return
        time.sleep(0.6)  # frena la adivinanza en línea (el límite de intentos hace el resto)
        log.warning("login FALLIDO desde %s (%s)", _client_ip(self), res.get("error"))
        if res.get("retry_after"):
            self._send_json(429, {"error": res["error"], "retry_after": res["retry_after"]},
                            headers={"Retry-After": res["retry_after"]})
        else:
            self._send_json(401, {"error": res["error"]})

    def do_POST(self):
        if self.path == "/v1/doc-chat":
            self._doc_chat()
            return

        if self.path == "/v1/app/login":
            self._login()
            return

        # /v1/app/* acepta token maestro o sesión de usuario; todo lo demás (tools, agentes, completions) SOLO el maestro.
        if not (_require_app_auth(self) if self.path.startswith("/v1/app/") else _require_auth(self)):
            self._send_json(401, {"error": "unauthorized"})
            return

        if self.path.split("?", 1)[0] == "/v1/app/media/quick":      # cuerpo binario, no JSON
            self._media_quick()
            return

        try:
            body = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": f"JSON inválido: {e}"})
            return

        if self.path.startswith("/v1/app/media/") and self._media_post(self.path.split("?", 1)[0], body):
            return
        if self.path.startswith("/v1/app/translate") and self._translate_post(self.path.split("?", 1)[0], body):
            return
        if (self.path.startswith("/v1/app/voice/") or self.path in ("/v1/app/speak", "/v1/app/clientlog")) \
                and self._voice_post(self.path.split("?", 1)[0], body):
            return

        # App móvil: chat en streaming (SSE)
        if self.path == "/v1/app/chat/stream":
            self._chat_stream(body)
            return

        # App móvil: cerrar sesión (esta o todas las del usuario)
        if self.path == "/v1/app/logout":
            kind, ses = self._auth
            self._send_json(200, {"ok": bool(kind == "sesion" and app_auth.revoke(_bearer(self)))})
            return
        if self.path == "/v1/app/sessions/revoke-all":
            kind, ses = self._auth
            n = app_auth.revoke_all((ses or {}).get("user") if kind == "sesion" else None)
            self._send_json(200, {"ok": True, "cerradas": n})
            return

        # App móvil: bandeja de notificaciones (marcar leídas / ejecutar un botón de confirmación)
        if self.path == "/v1/app/notifs/read":
            import louis_push
            ids = body.get("ids")
            n = louis_push.inbox_mark_read(None if body.get("all") else set(map(str, ids or [])))
            self._send_json(200, {"ok": True, "cambiadas": n, "unread": louis_push.unread_count()})
            return
        if self.path == "/v1/app/notifs/action":
            try:
                code, out = _run_notif_action(str(body.get("id", "")), str(body.get("action", "")))
            except Exception as e:
                log.exception("acción de bandeja falló")
                code, out = 500, {"error": str(e)[:200]}
            self._send_json(code, out)
            return

        # App móvil: notificaciones push
        if self.path.startswith("/v1/app/push/"):
            import louis_push
            op = self.path[len("/v1/app/push/"):]
            if op == "subscribe":
                ok = louis_push.add_sub(body.get("subscription") or {}, str(body.get("label", "")))
                self._send_json(200 if ok else 400, {"ok": ok, "subs": louis_push.count()} if ok
                                else {"error": "suscripción inválida"})
            elif op == "unsubscribe":
                louis_push.remove_sub(str(body.get("endpoint", "")))
                self._send_json(200, {"ok": True, "subs": louis_push.count()})
            elif op == "test":
                n = louis_push.notify(core.AGENT_NAME, "Notificación de prueba ✅ — todo funciona.", tab="chat",
                                      tag="prueba", ttl=300)
                self._send_json(200, {"ok": n > 0, "enviadas": n, "subs": louis_push.count()})
            else:
                self._send_json(404, {"error": "no encontrado"})
            return

        # App móvil: marcar un pendiente de la AGENDA como hecho (misma tool que usa Louis).
        if self.path == "/v1/app/agenda/hecho":
            texto = str(body.get("texto", "")).strip()
            if not texto:
                self._send_json(400, {"error": "texto requerido"})
                return
            try:
                self._send_json(200, {"ok": True, "result": core.execute_tool("completar_pendiente", {"texto": texto})})
            except Exception as e:
                log.exception("completar_pendiente falló")
                self._send_json(500, {"error": str(e)[:200]})
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
    try:
        n = app_media.resume_pending()
        if n:
            log.info("Adjuntos pendientes retomados: %d", n)
    except Exception:
        log.exception("no pude retomar adjuntos pendientes")
    log.info("Tools cargados: %d", len(core.TOOLS_DEFINITION))
    log.info("Memory files: %s", ", ".join(core.MEMORY_FILES))
    log.info("Auth API (Bearer): %s", "activa" if TOKEN else ("DESACTIVADA (OPENCLAW_ALLOW_NO_AUTH=1)" if ALLOW_NO_AUTH else "sin token → todo rechazado"))
    log.info("Auth chat de documentos: %s", "activa" if DOC_TOKEN else ("DESACTIVADA" if ALLOW_NO_AUTH else "sin token → rechazado"))
    log.info("Auth tablero (Basic): %s", "activa" if (DASH_USER and DASH_PASS) else ("DESACTIVADA" if ALLOW_NO_AUTH else "sin usuario/contraseña → rechazado"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("SIGINT — apagando")
        srv.shutdown()


if __name__ == "__main__":
    main()
