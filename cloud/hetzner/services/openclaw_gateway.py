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
import importlib as _il  # noqa: E402


class _Faltante:
    """Marcador de un módulo OPCIONAL que no está instalado. El servidor SIGUE arrancando (login, chat y tablero funcionan) y solo falla,
    con un mensaje claro, lo que use ese módulo. Así una instalación a medias nunca deja a Polo sin poder conectarse."""
    def __init__(self, nombre, err):
        self._n, self._e = nombre, err

    def __getattr__(self, k):
        raise RuntimeError(f"falta el módulo {self._n} en /opt/openclaw/scripts (reinstala los archivos de Donna): {self._e}")

    def __bool__(self):
        return False


def _opcional(nombre):
    try:
        return _il.import_module(nombre)
    except Exception as e:
        logging.getLogger("openclaw-gateway").error("MÓDULO OPCIONAL FALTANTE: %s (%s)", nombre, e)
        return _Faltante(nombre, e)


app_media = _opcional("app_media")            # adjuntos, grabaciones y archivos de la app
app_translate = _opcional("app_translate")    # traductor español/inglés/chino
ws_lite = _opcional("ws_lite")                # WebSocket mínimo: traducción en vivo y Donna en tiempo real
donna_voz = _opcional("donna_voz")            # acciones directas por voz: guardar, reglas, recordatorios
app_chats = _opcional("app_chats")            # conversaciones guardadas: nueva / cargar / renombrar / borrar
app_trabajos = _opcional("app_trabajos")      # trabajos grandes de Donna: plan por pasos con avance visible

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


def _chat_worker(rid: str, user_msg: str, history: list, voz: bool = False, registrar: bool = True, extra: str = "", etiqueta: str = "", cid: str = "", user: str = ""):
    res = _RESULTS[rid]
    try:
        api_key = core.load_anthropic_key()
        sys_prompt = core.load_system_prompt(channel="api") + (_VOZ_BLOQUE if voz else "")
        # Mismo orden que telegram-bridge: el mensaje entra al historial ANTES de call_llm.
        if registrar:
            _hist_append("user", (etiqueta + " " if etiqueta else "") + user_msg)
        text, model = core.call_llm(api_key, sys_prompt, history, user_msg + extra, history_file=APP_HISTORY_FILE)
        if text and registrar:
            _hist_append("assistant", text)
        out = {"text": text or "(sin respuesta)", "model": model, "error": None}
        if cid and text and app_chats and not res.get("cancelled"):          # la respuesta queda en SU conversación aunque el usuario ya esté en otra
            try:
                app_chats.agregar_respuesta(user, cid, text, model)
            except Exception:
                log.warning("no pude agregar la respuesta a la conversación", exc_info=True)
    except Exception as e:
        log.exception("chat stream falló")
        out = {"text": "", "model": "", "error": str(e)[:200] or "error"}
    with _RESULTS_LOCK:
        res.update(out, done=True)
        gone = res.get("client_gone")
    _CHAT_SLOTS.release()
    if gone:
        _push_answer(res)


def _tablero_contexto(aid: str):
    """(etiqueta, contexto) para preguntas hechas DESDE un tablero abierto: título + texto plano de su contenido (sin HTML).
    El contexto va solo al modelo; en el historial/memoria queda únicamente la etiqueta corta + la pregunta."""
    try:
        import app_artifacts
        rec = app_artifacts._read(aid)
    except Exception:
        rec = None
    if not rec:
        return "", ""
    plano = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", rec.get("html", ""), flags=re.S | re.I)
    plano = re.sub(r"</(p|div|li|tr|h[1-6]|table|section)>", "\n", plano, flags=re.I)
    plano = re.sub(r"<[^>]+>", " ", plano)
    plano = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", plano)).strip()[:9000]
    et = "[Tablero «%s»]" % rec.get("titulo", "")[:80]
    ctx = ("\n\n---\nCONTEXTO: Polo tiene abierto el TABLERO «%s» (id %s, versión %s) y la pregunta es sobre él. Contenido actual del tablero:\n%s\n---\n"
           "Responde con base en ese contenido y en datos reales (consúltalos con tus herramientas si hace falta; no inventes). "
           "Si pide cambios, agregar columnas/datos o actualizarlo, usa `actualizar_artefacto` con id %s y pon [[artefacto:%s]] en tu respuesta."
           % (rec.get("titulo", ""), aid, rec.get("version", 1), plano, aid, aid))
    return et, ctx


def _ask_donna_voice(user_msg: str, history: list, registrar: bool = True, voz: bool = True):
    """Pregunta al cerebro completo de Donna en modo voz (bloquea). → (texto, modelo, error)."""
    if not _CHAT_SLOTS.acquire(blocking=False):
        return "", "", "Donna está atendiendo otras consultas; reintenta en un momento"
    rid = "v" + secrets.token_hex(6)
    res = {"rid": rid, "t0": time.time(), "done": False, "client_gone": False}
    with _RESULTS_LOCK:
        _RESULTS[rid] = res
    _chat_worker(rid, user_msg, history, voz=voz, registrar=registrar)          # libera el cupo al terminar
    with _RESULTS_LOCK:
        out = _RESULTS.pop(rid, res)
    return out.get("text", ""), out.get("model", ""), out.get("error")


_PLAN_SYS = (
    "Eres la planeadora de Donna, la asistente ejecutiva de Polo (CEO de Kawiil y Yoltik). Recibes un pedido grande o ambicioso y lo divides en un PLAN de 3 a 8 pasos "
    "concretos, en orden, que Donna ejecutará una por una con sus herramientas (Kawiil Central, Patio Yoltik, Slack, correo, adjuntos y transcripciones, memoria, tableros, agenda). "
    "Cada paso: `titulo` corto y accionable, `detalle` (qué consultar o producir, con nombres/filtros), y `tipo`: «leer» (solo consultar o analizar), «memoria» (guardar en la memoria de Donna "
    "o crear un tablero/informe) o «cambio» (MODIFICA sistemas externos: cerrar o actualizar tareas, enviar correos o mensajes, crear o mover eventos, borrar; requiere confirmación de Polo). "
    "Pon los pasos de lectura primero y los de cambio al final. El último paso suele ser un informe o tablero con lo encontrado. No inventes datos. "
    'Responde SOLO un JSON: {"titulo": "...", "pasos": [{"titulo": "...", "detalle": "...", "tipo": "leer|memoria|cambio"}]}')


def _trabajo_planear(pedido: str) -> dict:
    key = core.load_anthropic_key()
    if not key:
        raise RuntimeError("falta ANTHROPIC_API_KEY para planear")
    out = core.call_haiku(key, _PLAN_SYS, [], pedido, max_tokens=1800, timeout=60)
    if not out or str(out).lstrip().startswith(("(error", "(sin respuesta", "Error")):
        raise RuntimeError("el planeador no respondió")
    return app_translate._json_de(out)


def _trabajo_ejecutar(prompt: str):
    texto, err = "", ""
    for _ in range(6):
        texto, _m, err = _ask_donna_voice(prompt, [], registrar=False, voz=False)
        if not (err and "atendiendo otras consultas" in str(err)):
            break
        time.sleep(15)
    return texto, err


def _trabajo_resumir(job: dict) -> str:
    cuerpo = f"Trabajo: {job['titulo']}\nPedido: {job['pedido'][:500]}\n\n" + "\n\n".join(
        f"Paso {i + 1} [{p['estado']}] {p['titulo']}\n{(p['resultado'] or '')[:900]}" for i, p in enumerate(job["pasos"]))
    out = core.call_deepseek("Resume para Polo, en español y máximo 12 líneas, cómo quedó este trabajo: **Hecho** (con las cifras reales que traen los resultados), "
                             "**Hallazgos clave** y **Pendiente** (lo que falló, está en espera de su confirmación o no se hizo). Usa SOLO lo que dicen los resultados.", [], cuerpo)
    return (out or "").strip()


def _trabajo_notificar(titulo: str, cuerpo: str, link: str, completo: str) -> None:
    import louis_push
    louis_push.notify_async(titulo, cuerpo.replace("*", ""), tab="chat", tag="trab-" + link.split(":")[-1], full_body=completo, kind="trabajo", link=link)


try:
    app_trabajos.configurar(planear=_trabajo_planear, ejecutar=_trabajo_ejecutar, resumir=_trabajo_resumir, notificar=_trabajo_notificar)
except RuntimeError:
    pass                                                    # módulo no instalado: el módulo Trabajos no estará disponible, lo demás sí


def _guardar_conversacion_voz(hist: list, t0: float) -> None:
    try:
        import app_media as _M
        texto = "\n".join(("Polo: " if h["role"] == "user" else "Donna: ") + h["content"] for h in hist)
        nombre = "Conversación con Donna " + time.strftime("%Y-%m-%d %H:%M", time.localtime(t0))
        _M.register_text(nombre, texto, tipo="conversacion", resumen=_M.summarize(texto), origen="donna-voz")
    except Exception:
        log.exception("no pude guardar la conversación por voz")


def _bg_lanzar(pedido: str, notificar: bool = True, historial: list = None, tarea: bool = False) -> dict:
    """Corre el cerebro completo de Donna en un hilo, sin el tope de la conversación. Al terminar, si `notificar`, manda una notificación con el resultado."""
    bid = "b" + secrets.token_hex(4)
    rec = {"id": bid, "estado": "en curso", "pedido": pedido[:300], "t0": time.time(), "resultado": "", "error": "",
           "notificar": notificar, "evento": threading.Event()}
    with _BG_LOCK:
        for k in [k for k, v in _BG.items() if time.time() - v["t0"] > 6 * 3600]:
            _BG.pop(k, None)
        _BG[bid] = rec
    ext = pedido if not tarea else (pedido + "\n\n(Tarea pedida POR VOZ y ejecutada en segundo plano: haz el trabajo completo con tus herramientas y termina con un resumen breve y VERAZ: "
                                    "qué hiciste, cuántos elementos, y qué quedó pendiente o falló. No digas que hiciste algo que no hiciste.)")

    def correr():
        texto, err = "", ""
        for intento in range(4):
            texto, _m, err = _ask_donna_voice(ext, list(historial or []), registrar=False)
            if not (err and "atendiendo otras consultas" in str(err)):
                break
            time.sleep(20)
        with _BG_LOCK:
            rec["estado"] = "error" if err or not (texto or "").strip() else "lista"
            rec["resultado"], rec["error"] = (texto or "")[:6000], str(err or "")[:200]
            avisar = rec["notificar"]
        rec["evento"].set()
        if avisar:
            try:
                import louis_push
                ok = rec["estado"] == "lista"
                louis_push.notify_async(("✅ Tarea lista: " if ok else "⚠️ Tarea con problema: ") + rec["pedido"][:50],
                                        (rec["resultado"] or rec["error"] or "sin resultado")[:220].replace("*", ""), tab="chat",
                                        tag="bg-" + bid, full_body=(rec["resultado"] or rec["error"] or "sin resultado"), kind="tarea")
            except Exception:
                log.exception("no pude avisar que terminó la tarea en segundo plano")

    threading.Thread(target=correr, daemon=True).start()
    return rec


def _push_answer(res: dict):
    """El cliente se fue antes de recibir la respuesta → avisa por push (si hay suscripciones)."""
    if res.get("pushed") or res.get("cancelled"):
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


_RADAR_LOCK = threading.Lock()
_JUNTA_HITS: dict = {}


def _junta_rate_ok(user: str) -> bool:
    """20 consultas por minuto por usuario (cada una cuesta una llamada al modelo)."""
    now = time.time()
    q = _JUNTA_HITS.setdefault(user, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 20:
        return False
    q.append(now)
    return True


def _leer_junta(sesion_id: str):
    try:
        import donna_tareas
        donna_tareas.extraer_y_avisar(core, sesion_id)
    except Exception:
        log.exception("tareas propuestas de la junta fallaron")
_ACCIONES_BASE = ("hecho", "posponer_1h", "posponer_manana", "descartar")


def _accion_base(rec: dict, action_id: str):
    """Botones que tiene CUALQUIER aviso. Si el aviso es de un seguimiento (link «seg:<id>»), la acción también se aplica al seguimiento."""
    import donna_db, donna_seg
    nid = rec["id"]
    seg_id = rec["link"].split(":", 1)[1] if str(rec.get("link", "")).startswith("seg:") else ""
    if action_id == "descartar":
        donna_db.notif_descartar(nid)
        return 200, {"ok": True, "result": "Descartado"}
    if action_id == "hecho":
        donna_db.notif_resolver(nid, "hecho")
        if seg_id:
            try:
                donna_seg.accion(seg_id, "hecho")
            except ValueError:
                pass
        return 200, {"ok": True, "result": "Hecho ✓"}
    # posponer
    if action_id == "posponer_1h":
        hasta, txt = donna_seg.cuando_a_ts("", 1), "en 1 hora"
    else:
        from datetime import datetime, timedelta
        n = datetime.now(donna_seg.TZ)
        hasta, txt = int((n + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).timestamp()), "mañana 8:00"
    donna_db.notif_posponer(nid, hasta)
    if seg_id:
        try:
            donna_seg.accion(seg_id, "posponer", en_horas=max(1, (hasta - int(time.time())) / 3600))
        except ValueError:
            pass
    return 200, {"ok": True, "result": "Pospuesto — vuelve " + txt}


def _run_notif_action(nid: str, action_id: str):
    """(código HTTP, dict). Valida: la acción existe, el aviso la ofrece, no se usó ya y la propuesta sigue vigente."""
    import louis_push
    rec = louis_push.inbox_get(nid)
    if not rec:
        return 404, {"error": "aviso no encontrado"}
    if action_id in _ACCIONES_BASE:
        return _accion_base(rec, action_id)
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
_LIVE_SLOTS = threading.BoundedSemaphore(2)          # máximo 2 traducciones en vivo a la vez (cada minuto cuesta)
LIVE_MAX_SECONDS = int(os.environ.get("LIVE_MAX_SECONDS", "1800"))
LIVE_IDLE_SECONDS = int(os.environ.get("LIVE_IDLE_SECONDS", "90"))
LIVE_ESCUCHAR_SILENCIO = int(os.environ.get("LIVE_ESCUCHAR_SILENCIO", "3600"))      # sin oír a nadie este tiempo → pregunta «¿sigo escuchando?» (aviso + notificación)
LIVE_ESCUCHAR_REPREGUNTA = int(os.environ.get("LIVE_ESCUCHAR_REPREGUNTA", "900")) # si nadie responde, la escucha NO se detiene sola: solo se vuelve a preguntar cada tanto
LIVE_ESCUCHAR_MAX_SECONDS = int(os.environ.get("LIVE_ESCUCHAR_MAX_SECONDS", "28800"))  # modo «escuchar»: hasta 8 h por sesión (solo cuesta mientras llega audio)
# En «escuchar» el teléfono no manda audio en los silencios; con la pantalla bloqueada tampoco manda ping. La conexión NO se cierra por inactividad antes de que venza el aviso de silencio.
LIVE_ESCUCHAR_IDLE = LIVE_ESCUCHAR_SILENCIO + 900
_LIVE_LANGS = {"es": "es", "en": "en", "zh": os.environ.get("LIVE_ZH_CODE", "zh")}
_DONNA_TOOLS = [
    {"type": "function", "name": "consultar_donna",
     "description": "Pásale a Donna (tu cerebro completo: agenda, tareas, Kawiil Central, Patio Yoltik, Slack, correo, documentos, grabaciones, tableros, transcripciones, memoria) "
                    "CUALQUIER consulta o acción compleja de Polo, con el pedido completo y claro. Devuelve el resultado REAL para que lo digas. "
                    "Si el pedido CAMBIA algo, antes confirma con Polo y escribe «Polo ya confirmó» en el pedido.",
     "parameters": {"type": "object", "properties": {"pedido": {"type": "string", "description": "Lo que Polo pidió, completo y con contexto"}}, "required": ["pedido"]}},
    {"type": "function", "name": "guardar_nota",
     "description": "Guarda AHORA una nota, dato o pendiente que Polo dictó en su memoria. Úsala siempre que diga «anota», «guarda», «apunta», «recuerda que…» (un dato, no un aviso futuro). "
                    "Devuelve ✅ solo si de verdad quedó guardado.",
     "parameters": {"type": "object", "properties": {
         "texto": {"type": "string", "description": "La nota, completa y autocontenida"},
         "archivo": {"type": "string", "description": "agenda (por defecto), importante, personal, academico, familia, salud, proyectos, clientes, finanzas, viajes, bitacora"}},
         "required": ["texto"]}},
    {"type": "function", "name": "recordar_regla",
     "description": "Fija una regla o preferencia PERMANENTE de Polo («a partir de ahora…», «siempre que…», «nunca…»). Se guarda para siempre y Donna la aplica en adelante.",
     "parameters": {"type": "object", "properties": {"regla": {"type": "string"}, "contexto": {"type": "string"}}, "required": ["regla"]}},
    {"type": "function", "name": "crear_recordatorio",
     "description": "Crea un recordatorio/aviso futuro que llegará como notificación. Da `en_minutos` (p. ej. 30) o `fecha_hora` ISO 8601 en hora de Ciudad de México (p. ej. 2026-11-19T09:00).",
     "parameters": {"type": "object", "properties": {"mensaje": {"type": "string"}, "en_minutos": {"type": "integer"}, "fecha_hora": {"type": "string"}}, "required": ["mensaje"]}},
    {"type": "function", "name": "tarea_en_segundo_plano",
     "description": "Para trabajo GRANDE o ambicioso (varios pasos o fuentes, cargar o limpiar muchas cosas, revisar cientos de datos, preparar un informe): lo convierte en un TRABAJO con plan de pasos que Donna "
                    "resuelve sola, y Polo lo sigue en el módulo Trabajos (lo que hace, lo cumplido, lo pendiente); avisa por notificación al terminar. Responde de inmediato con el plan.",
     "parameters": {"type": "object", "properties": {"pedido": {"type": "string"}}, "required": ["pedido"]}},
    {"type": "function", "name": "terminar_conversacion",
     "description": "Cierra la conversación por voz cuando Polo se despide o dice que eso es todo.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
]

# Promesas de acción dichas en voz alta («ya lo guardé», «voy a agendarlo»): si no hubo herramienta, se corrige al modelo.
_PROMESA_RE = re.compile(
    r"\b(ya (lo |la |los |las |te lo |te la )?(guard|agend|cerr|envi|cre|mov|anot|registr|actualiz|program|carg|sub|borr)\w*"
    r"|(lo|la|los|las|te lo|te la) (guard|agend|cerr|envi|cre|anot|registr|actualiz|program|carg|sub|borr)\w*"
    r"|(voy a|déjame|dejame|me encargo de|te lo|ahora mismo) ?(lo |la )?(guardar|agendar|cerrar|enviar|crear|mover|anotar|registrar|actualizar|programar|cargar|subir|borrar)\w*)\b", re.I)
_CONSULTA_ESPERA = int(os.environ.get("DONNA_CONSULTA_ESPERA", "40"))     # segundos que consultar_donna espera antes de pasar a segundo plano
_BG: dict = {}                         # id -> tarea en segundo plano {estado, pedido, t0, resultado, error, notificar, evento}
_BG_LOCK = threading.Lock()
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


def _verificar_audio(sid: str, dur_s: int):
    """Tras guardar una junta: ¿el audio se puede leer y cuánto cubre? Si algo está mal se avisa (notificación) en vez de enterarse meses después."""
    try:
        import donna_audio
        v = donna_audio.verificar(sid, dur_s)
        log.info("audio de la junta %s: ok=%s cubre %d de %d s, avisos=%s", sid, v["ok"], v["cubre_s"], v["total_s"], v["avisos"])
        if v["avisos"]:
            import louis_push
            louis_push.notify_async("⚠️ Revisa el audio de la junta", v["avisos"][0][:160], tab="chat", tag="audio", kind="escuchar", link="escuchar:1")
    except Exception:
        log.exception("verificación del audio falló")


def _reintentar_voces_fallidas():
    """Al arrancar: las revisiones de voces que fallaron por el límite de 1400 s de OpenAI (ya corregido) se vuelven a lanzar solas, una vez."""
    try:
        import donna_diariza, donna_audio
        c = donna_diariza._c()
        try:
            filas = [r["sesion_id"] for r in c.execute("SELECT sesion_id FROM junta_voces WHERE estado='error' AND datos LIKE '%1400 seconds%'")]
        finally:
            c.close()
        for sid in filas:
            if donna_audio.resumen(sid)["tramos"] > 0:
                log.info("voces: reintento automático de la junta %s (falló por el límite de duración)", sid)
                _lanzar_voces(sid)
    except Exception:
        log.exception("reintento de voces falló")


_VOCES_EN_CURSO = set()


def _lanzar_voces(sid: str) -> bool:
    """Arranca la pasada de voces de una junta en segundo plano (una a la vez por junta). False si ya estaba corriendo."""
    if sid in _VOCES_EN_CURSO:
        return False
    _VOCES_EN_CURSO.add(sid)

    def _run():
        try:
            import donna_diariza
            r = donna_diariza.procesar(sid)
            log.info("voces: junta %s → %s", sid, r.get("estado"))
            if r.get("estado") == "lista" and r.get("cambios"):
                try:
                    import louis_push
                    louis_push.notify_async("🎙️ Revisión de voces lista", "%d cambios propuestos en quién habló. Revísalos en Escuchar." % len(r["cambios"]), tab="chat", tag="voces", kind="escuchar", link="escuchar:1")
                except Exception:
                    log.warning("no pude avisar de la revisión de voces", exc_info=True)
        except Exception:
            log.exception("voces falló")
        finally:
            _VOCES_EN_CURSO.discard(sid)
    threading.Thread(target=_run, daemon=True).start()
    return True


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
        if _r0 == "/v1/app/live" and "websocket" in self.headers.get("Upgrade", "").lower():
            self._live_ws()
            return
        if _r0 == "/v1/app/donna" and "websocket" in self.headers.get("Upgrade", "").lower():
            self._donna_ws()
            return
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
                    try:
                        import donna_db
                        d["resumenes"] = donna_db.resumen_list(4)                   # cierre del día, mediodía, semanal (guardados en la base)
                    except Exception:
                        d["resumenes"] = []
                    self._send_json(200, d)
                elif _r0 in ("/v1/app/personas", "/v1/app/persona", "/v1/app/aportaciones", "/v1/app/escucha/sesiones"):
                    import donna_personas
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(self.path).query)
                    u, a1 = self._media_user(), (lambda k: (qs.get(k) or [""])[0])
                    if _r0 == "/v1/app/personas":
                        self._send_json(200, {"items": donna_personas.listar(u, a1("q"))})
                    elif _r0 == "/v1/app/aportaciones":
                        self._send_json(200, {"items": donna_personas.buscar(a1("q"), a1("persona"), 30, u)})
                    elif _r0 == "/v1/app/escucha/sesiones":
                        import donna_audio
                        its = donna_personas.sesiones(u)
                        for it in its:
                            try:
                                it["audio"] = donna_audio.resumen(it["id"])
                            except Exception:
                                it["audio"] = {"tramos": 0, "bytes": 0, "vence": 0}
                        self._send_json(200, {"items": its})
                    else:
                        d = donna_personas.detalle(a1("id"))
                        if not d or d["usuario"] != u and u != "admin":
                            self._send_json(404, {"error": "persona no encontrada"})
                        else:
                            d["cruce"] = {"transcripciones": donna_personas.cruce_transcripciones(d["id"])}
                            if a1("kc"):
                                d["cruce"]["kawiil_central"] = donna_personas.cruce_kawiil_central(core, d["nombre"], d["empresa"])
                            self._send_json(200, d)
                elif _r0 in ("/v1/app/escucha/junta", "/v1/app/escucha/descargar"):            # abrir una junta guardada / descargar su minuta o su audio
                    import donna_minuta, donna_audio
                    from urllib.parse import urlparse as _up6, parse_qs as _pq6
                    _q6 = _pq6(_up6(self.path).query)
                    sid6 = (_q6.get("id") or _q6.get("sesion") or [""])[0]
                    if (_q6.get("que") or [""])[0] != "tramo" and not self._sesion_mia(sid6):
                        self._send_json(404, {"error": "junta no encontrada"})
                    elif _r0.endswith("junta"):
                        self._send_json(200, donna_minuta.datos(sid6, self._media_user()))
                    elif (_q6.get("que") or [""])[0] == "tramo":
                        aid6 = (_q6.get("aid") or [""])[0]
                        e6 = donna_audio.estado(aid6) if donna_audio.valido(aid6) else None
                        if not e6 or (e6["usuario"] != self._media_user() and self._media_user() != "admin") or e6["estado"] == "borrado":
                            self._send_json(404, {"error": "audio no encontrado"})
                        else:
                            self._enviar_archivo(donna_audio.exportar_tramo(aid6), "audio/mpeg", "audio-%s.mp3" % aid6[:8])
                    elif (_q6.get("que") or [""])[0] == "audio":
                        try:
                            mp3 = donna_audio.exportar(sid6)
                        except ValueError as e:
                            self._send_json(404, {"error": str(e)})
                        else:
                            self._enviar_archivo(mp3, "audio/mpeg", "junta-%s.mp3" % sid6[:8])
                    else:
                        nombre, texto = donna_minuta.minuta_md(sid6, self._media_user())
                        cuerpo = texto.encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/markdown; charset=utf-8")
                        self.send_header("Content-Disposition", 'attachment; filename="%s"' % nombre.encode("ascii", "replace").decode())
                        self.send_header("Content-Length", str(len(cuerpo)))
                        self.end_headers()
                        self.wfile.write(cuerpo)
                elif _r0 in ("/v1/app/escucha/audio_estado", "/v1/app/escucha/audio_huerfano"):  # ¿el audio quedó bien y cuánto cubre? / audio sin junta
                    import donna_audio, donna_personas
                    from urllib.parse import urlparse as _up7, parse_qs as _pq7
                    if _r0.endswith("huerfano"):
                        self._send_json(200, {"items": donna_audio.huerfanos(self._media_user())})
                    else:
                        sid7 = (_pq7(_up7(self.path).query).get("sesion") or [""])[0]
                        if not self._sesion_mia(sid7):
                            self._send_json(404, {"error": "junta no encontrada"})
                        else:
                            c7 = donna_personas._c()
                            try:
                                dur7 = c7.execute("SELECT duracion FROM sesiones_escucha WHERE id=?", (sid7,)).fetchone()["duracion"]
                            finally:
                                c7.close()
                            self._send_json(200, donna_audio.verificar(sid7, dur7))
                elif _r0 == "/v1/app/escucha/duplicadas":                                      # juntas guardadas varias veces
                    import donna_minuta
                    self._send_json(200, donna_minuta.duplicadas(self._media_user()))
                elif _r0 == "/v1/app/escucha/activas":                                         # escuchas activas (para detenerlas desde cualquier dispositivo)
                    import donna_remoto
                    self._send_json(200, {"items": donna_remoto.activas(self._media_user())})
                elif _r0 == "/v1/app/escucha/voces":                                           # propuesta de voces (audio + OpenAI) de una junta guardada
                    import donna_diariza
                    from urllib.parse import urlparse as _up5, parse_qs as _pq5
                    sid5 = (_pq5(_up5(self.path).query).get("sesion") or [""])[0]
                    if not self._sesion_mia(sid5):
                        self._send_json(404, {"error": "junta no encontrada"})
                    else:
                        self._send_json(200, donna_diariza.estado(sid5))
                elif _r0 in ("/v1/app/escucha/rescates", "/v1/app/escucha/rescate"):          # copia de rescate del servidor
                    import donna_rescate, donna_borrador
                    from urllib.parse import urlparse as _up3, parse_qs as _pq3
                    _u = self._media_user()
                    if _r0.endswith("rescates"):
                        con_borrador = {b["id"] for b in donna_borrador.listar(_u)}
                        self._send_json(200, {"items": [x for x in donna_rescate.listar(_u) if x["id"] not in con_borrador]})
                    else:
                        d = donna_rescate.a_borrador((_pq3(_up3(self.path).query).get("id") or [""])[0], _u)
                        self._send_json(200, d) if d else self._send_json(404, {"error": "rescate no encontrado"})
                elif _r0 == "/v1/app/escucha/revision":
                    import donna_junta
                    from urllib.parse import urlparse as _up4, parse_qs as _pq4
                    d = donna_junta.revision((_pq4(_up4(self.path).query).get("sesion") or [""])[0])
                    self._send_json(200, d) if d else self._send_json(200, {"estado": "sin_revision"})
                elif _r0 == "/v1/app/escucha/sesion":                                          # una junta guardada, lista para retomarla
                    import donna_personas
                    from urllib.parse import urlparse as _up2, parse_qs as _pq2
                    d = donna_personas.sesion_a_borrador((_pq2(_up2(self.path).query).get("id") or [""])[0], self._media_user())
                    self._send_json(200, d) if d else self._send_json(404, {"error": "junta no encontrada"})
                elif _r0 in ("/v1/app/escucha/borradores", "/v1/app/escucha/borrador"):          # juntas guardadas solas, para retomarlas
                    import donna_borrador
                    from urllib.parse import urlparse as _up, parse_qs as _pq
                    _q = _pq(_up(self.path).query)
                    if _r0.endswith("borradores"):
                        self._send_json(200, {"items": donna_borrador.listar(self._media_user())})
                    else:
                        d = donna_borrador.leer((_q.get("id") or [""])[0], self._media_user())
                        self._send_json(200, d) if d else self._send_json(404, {"error": "borrador no encontrado"})
                elif _r0 == "/v1/app/escucha/notas":
                    import donna_junta
                    from urllib.parse import urlparse, parse_qs
                    lid = (parse_qs(urlparse(self.path).query).get("live_id") or [""])[0][:40]
                    self._send_json(200, {"notas": donna_junta.notas(lid), "chat": donna_junta.chat(lid), "materiales": donna_junta.materiales(lid)})
                elif _r0 in ("/v1/app/tareas", "/v1/app/tareas/proyectos"):
                    import donna_tareas
                    from urllib.parse import urlparse, parse_qs
                    sid = (parse_qs(urlparse(self.path).query).get("sesion") or [""])[0]
                    if _r0.endswith("proyectos"):
                        try:
                            self._send_json(200, {"items": donna_tareas.proyectos_kc(core)})
                        except Exception as e:
                            self._send_json(200, {"items": [], "error": str(e)[:160]})
                    else:
                        self._send_json(200, {"extraccion": donna_tareas.estado_extraccion(sid) if sid else {"estado": "todas"}, "items": donna_tareas.listar(sid)})
                elif _r0 in ("/v1/app/aprendizaje", "/v1/app/mejoras/exportar"):
                    import donna_aprender
                    if _r0.endswith("exportar"):
                        self._send_json(200, {"texto": donna_aprender.mejoras_markdown()})
                    else:
                        self._send_json(200, {"reglas": donna_aprender.reglas_list(), "mejoras": donna_aprender.mejoras_list()})
                elif _r0 == "/v1/app/limpieza":
                    import donna_limpieza
                    self._send_json(200, donna_limpieza.escanear(core))
                elif _r0 == "/v1/app/preferencias":
                    import donna_dia
                    self._send_json(200, {"prefs": donna_dia.prefs_get()})
                elif _r0 == "/v1/app/oficio":
                    import donna_oficio, donna_aprender
                    self._send_json(200, {"competencias": donna_oficio.resumen(7), "propuestas": [r for r in donna_aprender.reglas_list(("propuesta",))], "corriendo": _RADAR_LOCK.locked()})
                elif _r0 == "/v1/app/agente":
                    import donna_agente
                    cs = donna_agente.ciclos(8)
                    for c_ in cs:
                        try:
                            c_["decision"] = json.loads(c_["decision"] or "{}")
                        except ValueError:
                            c_["decision"] = {}
                    self._send_json(200, {"proxima_ts": donna_agente.estado_get("proxima_ts", 0), "ultima_ts": donna_agente.estado_get("ultima_ts", 0), "ciclos": cs,
                                          "conocimiento": donna_agente.conocimiento_list(), "planes": donna_agente.planes(), "uso": __import__("donna_modelos").uso_resumen(7), "llamadas_hoy": donna_agente._llamadas_hoy(), "max_llamadas": donna_agente.MAX_CICLOS_DIA})
                elif _r0 == "/v1/app/radar":
                    import donna_radar
                    self._send_json(200, {"bitacora": donna_radar.bitacora_list(12), "propuestas": donna_radar.propuestas(), "autonomia": donna_radar.autonomia_todas(),
                                          "estadisticas": donna_radar.estadisticas(), "corriendo": _RADAR_LOCK.locked(),
                                          "fuentes": {"conectadas": donna_radar.fuentes_conectadas(), "pendientes": donna_radar.FUENTES_NO_CONECTADAS}})
                elif _r0 == "/v1/app/seguimientos":
                    import donna_db
                    self._send_json(200, {"items": donna_db.seg_list(usuario=self._media_user() if self._media_user() != "admin" else None)})
                elif _r0 == "/v1/app/resumenes":
                    import donna_db
                    self._send_json(200, {"items": donna_db.resumen_list(30)})
                elif _r0 == "/v1/app/resumen":
                    import donna_db
                    from urllib.parse import urlparse, parse_qs
                    r = donna_db.resumen_get((parse_qs(urlparse(self.path).query).get("id") or [""])[0])
                    self._send_json(200 if r else 404, r or {"error": "resumen no encontrado"})
                elif _r0 == "/v1/app/notifs":
                    import louis_push
                    from urllib.parse import urlparse, parse_qs
                    try:
                        lim = int((parse_qs(urlparse(self.path).query).get("limit") or ["50"])[0])
                    except ValueError:
                        lim = 50
                    self._send_json(200, {"items": louis_push.inbox_list(lim), "unread": louis_push.unread_count(),
                                          "confirmaciones": louis_push.pending_confirmations()})
                elif _r0 == "/v1/app/chats":
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(self.path).query)
                    self._send_json(200, {"items": app_chats.listar(self._media_user(), (qs.get("q") or [""])[0]) if app_chats else []})
                elif _r0 == "/v1/app/chat":
                    from urllib.parse import urlparse, parse_qs
                    try:
                        c = app_chats.obtener(self._media_user(), (parse_qs(urlparse(self.path).query).get("id") or [""])[0]) if app_chats else None
                    except ValueError:
                        c = None
                    self._send_json(200 if c else 404, c or {"error": "no existe esa conversación"})
                elif _r0 == "/v1/app/trabajos":
                    self._send_json(200, {"items": app_trabajos.listar()})
                elif _r0 == "/v1/app/trabajo":
                    from urllib.parse import urlparse, parse_qs
                    t = app_trabajos.obtener((parse_qs(urlparse(self.path).query).get("id") or [""])[0])
                    self._send_json(200 if t else 404, t or {"error": "no existe ese trabajo"})
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

    # ---- traducción EN VIVO (OpenAI gpt-realtime-translate) a través de este servidor ----
    def _ws_handshake_auth(self):
        """Apretón de manos WebSocket + autenticación por el 1.er mensaje {t:'auth',token}. Toma un cupo de _LIVE_SLOTS (quien llama lo libera).
        → (Conn, hello) o (None, None) si algo falló (ya se avisó y cerró)."""
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key or "websocket" not in self.headers.get("Upgrade", "").lower():
            self._send_json(400, {"error": "se requiere WebSocket"})
            return None, None
        try:
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws_lite.accept_key(key))
            self.end_headers()
            self.wfile.flush()
        except OSError:
            return None, None
        self.close_connection = True
        cli = ws_lite.Conn(self.connection, cliente=False)

        def fallo(msg):
            try:
                cli.send_text(json.dumps({"t": "error", "m": msg}, ensure_ascii=False))
            except OSError:
                pass
            cli.close()
            return None, None

        try:
            m = cli.recv_message(timeout=8)
            if not m or m[0] != "text":
                cli.close()
                return None, None
            try:
                hello = json.loads(m[1])
            except ValueError:
                hello = {}
            tok = str(hello.get("token", ""))
            s = None if _same(tok, TOKEN or "\0") else app_auth.check_session(tok)
            ok = bool(TOKEN and _same(tok, TOKEN)) or (s and not str(s.get("user", "")).startswith("atajo-"))
            hello["_user"] = "admin" if (TOKEN and _same(tok, TOKEN)) else str((s or {}).get("user") or "admin")
            if hello.get("t") != "auth" or not ok:
                return fallo("No autorizado")
            if not _LIVE_SLOTS.acquire(blocking=False):
                return fallo("Ya hay traducciones en vivo en curso; espera a que termine una")
            return cli, hello
        except Exception:
            cli.close()
            return None, None

    def _live_ws(self):
        """GET /v1/app/live (Upgrade: websocket). Protocolo con la app: 1.er mensaje {t:'auth',token,to}; luego audio PCM16 24 kHz como
        binarios (app→servidor) y, de vuelta, binarios 0x01+PCM16 (voz traducida) y JSON {t:'in'|'out'|'error'|'end'}.
        La llave de OpenAI nunca sale del servidor."""
        import base64 as _b64
        cli, hello = self._ws_handshake_auth()
        if cli is None:
            return

        def aviso(t, **kw):
            try:
                cli.send_text(json.dumps({"t": t, **kw}, ensure_ascii=False))
            except OSError:
                pass

        up = None
        t0 = time.time()
        try:
            import app_media as _M
            apikey = _M.openai_key()
            if not apikey:
                aviso("error", m="Falta OPENAI_API_KEY en el servidor")
                return
            url = os.environ.get("LIVE_UPSTREAM_URL", "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate")
            try:
                up = ws_lite.connect_url(url, {"Authorization": f"Bearer {apikey}"})
            except Exception as e:
                aviso("error", m="No pude conectar con el traductor en vivo: " + str(e)[:120])
                return
            escuchar = str(hello.get("modo", "")) == "escuchar"      # solo subtítulos en español: no se reenvía la voz traducida
            to = "es" if escuchar else _LIVE_LANGS.get(str(hello.get("to", "en")), "en")
            limite = LIVE_ESCUCHAR_MAX_SECONDS if escuchar else LIVE_MAX_SECONDS
            up.send_text(json.dumps({"type": "session.update", "session": {"audio": {
                "input": {"transcription": {"model": "gpt-realtime-whisper"}}, "output": {"language": to}}}}))
            stop = threading.Event()
            est = {"voz": time.time(), "pregunto": None, "pausa": False, "aviso": True}           # escuchar: última vez que se oyó a alguien / cuándo se preguntó «¿sigo?»
            import donna_rescate
            rid = str(hello.get("rescate", "")) if escuchar and donna_rescate.valido(hello.get("rescate")) else ""
            ruser = str(hello.get("_user") or "admin")
            import donna_remoto
            rev = donna_remoto.registrar(rid, ruser) if rid else None                           # para poder detenerla desde otro dispositivo
            rbuf = {"in": ["", 0, 0.0], "out": ["", 0, 0.0]}                                      # copia de rescate: [texto, ms, desde cuándo]

            def rescatar(k):                                                                       # una frase terminada → al disco (solo texto)
                b = rbuf[k]
                if rid and b[0].strip():
                    donna_rescate.agregar(rid, ruser, k, b[1], b[0])
                rbuf[k] = ["", 0, 0.0]

            def arriba():                                         # OpenAI → app
                try:
                    while not stop.is_set():
                        r = up.recv_message(timeout=1)
                        if r is None:
                            continue
                        if r[0] == "close":
                            aviso("end", m="El traductor cerró la sesión")
                            break
                        if r[0] != "text":
                            continue
                        try:
                            ev = json.loads(r[1])
                        except ValueError:
                            continue
                        tp = ev.get("type", "")
                        if tp == "session.output_audio.delta":
                            if escuchar:
                                continue
                            cli.send_binary(b"\x01" + _b64.b64decode(ev.get("delta", "")))
                        elif tp == "session.output_transcript.delta":
                            if rid:
                                b = rbuf["out"]
                                b[0] += ev.get("delta", "")
                                b[1] = int(ev.get("elapsed_ms") or b[1] or 0)
                                b[2] = b[2] or time.time()
                            aviso("out", d=ev.get("delta", ""), ms=ev.get("elapsed_ms"))
                        elif tp == "session.input_transcript.delta":
                            if ev.get("delta", "").strip():
                                est["voz"] = time.time()
                                if est["pregunto"]:
                                    est["pregunto"] = None
                                    aviso("voz")
                            if rid:
                                b = rbuf["in"]
                                b[0] += ev.get("delta", "")
                                b[1] = int(ev.get("elapsed_ms") or b[1] or 0)
                                b[2] = b[2] or time.time()
                            aviso("in", d=ev.get("delta", ""), ms=ev.get("elapsed_ms"))
                        elif tp == "error":
                            msg = (ev.get("error") or {}).get("message", "error del traductor")
                            log.warning("live upstream error: %s", msg[:200])
                            aviso("error", m=msg[:240])
                        elif tp.endswith(".done") or tp in ("session.created", "session.updated"):
                            if rid and tp.endswith("input_transcript.done"):
                                rescatar("in")
                            elif rid and tp.endswith("output_transcript.done"):
                                rescatar("out")
                            aviso("ev", type=tp)
                        for _k in ("in", "out"):                                                   # si una frase larga no cierra en 15 s, igual se guarda
                            if rid and rbuf[_k][2] and time.time() - rbuf[_k][2] > 15:
                                rescatar(_k)
                except Exception:
                    log.exception("live: OpenAI→app falló")
                finally:
                    try:
                        rescatar("in")
                        rescatar("out")
                    except Exception:
                        pass
                    stop.set()

            th = threading.Thread(target=arriba, daemon=True)
            th.start()
            aviso("listo", to=to, max=limite, modo="escuchar" if escuchar else "")
            ultimo = time.time()
            while not stop.is_set():
                if rev is not None and rev.is_set():                                           # orden de detener desde otro dispositivo: avisa a la pantalla (que guarda) y cierra
                    aviso("detener_remoto")
                    time.sleep(1.5)
                    break
                if time.time() - t0 > limite:
                    aviso("end", m="Se alcanzó el límite de la sesión (%d min)" % (limite // 60))
                    break
                if escuchar and est["aviso"] and not est["pausa"]:
                    ahora = time.time()
                    if ahora - est["voz"] > LIVE_ESCUCHAR_SILENCIO:       # 1 h sin oír a nadie: se detiene y la app guarda la junta sola (el texto ya está en el rescate y el borrador)
                        aviso("fin_silencio", seg=int(ahora - est["voz"]))
                        try:
                            import louis_push
                            louis_push.notify_async("Escucha detenida", "Pasó %d min sin voces. La junta se guardó sola; si la app estaba cerrada, quedó como borrador en Escuchar." % max(1, int((ahora - est["voz"]) // 60)),
                                                    tab="chat", tag="escuchar", kind="escuchar", link="escuchar:1")
                        except Exception:
                            log.warning("no pude avisar del fin por silencio", exc_info=True)
                        time.sleep(1.5)                                    # que la app alcance a recibir el aviso antes de cerrar
                        break
                r = cli.recv_message(timeout=1)
                if r is None:
                    if time.time() - ultimo > (LIVE_ESCUCHAR_IDLE if escuchar else LIVE_IDLE_SECONDS):
                        aviso("end", m="Sesión cerrada por inactividad")
                        break
                    continue
                if r[0] == "close":
                    break
                if r[0] == "binary":
                    ultimo = time.time()
                    up.send_text(json.dumps({"type": "session.input_audio_buffer.append",
                                             "audio": _b64.b64encode(r[1]).decode("ascii")}))
                elif r[0] == "text":
                    try:
                        j = json.loads(r[1])
                    except ValueError:
                        continue
                    if j.get("t") == "seguir":
                        est["voz"], est["pregunto"], ultimo = time.time(), None, time.time()      # «sigue escuchando»
                    elif j.get("t") == "pausa":
                        est["pausa"], ultimo = True, time.time()                                  # en pausa no se pregunta nada
                    elif j.get("t") == "continuar":
                        est["pausa"], est["voz"], est["pregunto"], ultimo = False, time.time(), None, time.time()
                    elif j.get("t") == "cfg":
                        est["aviso"] = bool(j.get("aviso", True))
                    elif j.get("t") == "ping":
                        ultimo = time.time()                              # el modo escuchar no manda audio en los silencios: esto lo mantiene vivo
                    elif j.get("t") == "lang" and not escuchar:
                        up.send_text(json.dumps({"type": "session.update", "session": {"audio": {
                            "output": {"language": _LIVE_LANGS.get(str(j.get("to", "")), "en")}}}}))
                    elif j.get("t") == "close":
                        break
            stop.set()
        except Exception:
            log.exception("live falló")
            aviso("error", m="Error interno en la traducción en vivo")
        finally:
            _LIVE_SLOTS.release()
            try:
                up and up.close()
            except Exception:
                pass
            cli.close()
            try:
                import donna_remoto as _dr
                _dr.quitar(locals().get("rid") or "")
            except Exception:
                pass
            log.info("live: sesión de %.0f s", time.time() - t0)

    # ---- DONNA EN TIEMPO REAL (voz de OpenAI gpt-realtime-2.1 + el cerebro completo de Donna como herramienta) ----
    def _donna_ws(self):
        """GET /v1/app/donna (Upgrade: websocket). Misma mecánica que el traductor en vivo. El modelo de voz habla y escucha; todo dato o acción
        pasa por `consultar_donna` (core.call_llm con sus herramientas y protecciones). Mensajes a la app: binario 0x01+PCM (voz), JSON
        {t: listo|speech|in|in_done|out|out_done|tool|tool_done|turn_done|error|end}. De la app: PCM binario, {t:'interrupt'|'text'|'close'}."""
        import base64 as _b64
        cli, hello = self._ws_handshake_auth()
        if cli is None:
            return

        def aviso(t, **kw):
            try:
                cli.send_text(json.dumps({"t": t, **kw}, ensure_ascii=False))
            except OSError:
                pass

        up, t0, hist = None, time.time(), []
        try:
            import app_media as _M
            apikey = _M.openai_key()
            if not apikey:
                aviso("error", m="Falta OPENAI_API_KEY en el servidor")
                return
            url = os.environ.get("LIVE_DONNA_URL") or ("wss://api.openai.com/v1/realtime?model=" + os.environ.get("LIVE_DONNA_MODEL", "gpt-realtime-2.1"))
            try:
                up = ws_lite.connect_url(url, {"Authorization": f"Bearer {apikey}"})
            except Exception as e:
                aviso("error", m="No pude conectar con la voz en tiempo real: " + str(e)[:120])
                return
            up.send_text(json.dumps({"type": "session.update", "session": {
                "type": "realtime", "instructions": core.build_realtime_brief(),
                "audio": {"input": {"transcription": {"model": "gpt-4o-mini-transcribe"},
                                    "turn_detection": {"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300, "silence_duration_ms": 650,
                                                       "create_response": True, "interrupt_response": True}},
                          "output": {"voice": os.environ.get("LIVE_DONNA_VOICE", "marin")}},
                "tools": _DONNA_TOOLS, "tool_choice": "auto"}}, ensure_ascii=False))
            stop, hl = threading.Event(), threading.Lock()
            estado = {"cerrar": False, "accion_t": 0.0, "tool_resp": False, "texto_resp": "", "corregido": False}

            def herramienta(call_id, name, args_json):
                aviso("tool", name=name)
                try:
                    a = json.loads(args_json or "{}")
                except ValueError:
                    a = {}
                try:
                    if name == "consultar_donna":
                        pedido = str(a.get("pedido", "")).strip()
                        with hl:
                            h = list(hist[-8:])
                        bg = _bg_lanzar(pedido, notificar=False, historial=h)
                        if not bg["evento"].wait(_CONSULTA_ESPERA):
                            with _BG_LOCK:
                                if bg["estado"] == "en curso":
                                    bg["notificar"] = True              # sigue solo y avisa por notificación al terminar
                            salida = ("Sigue en proceso en segundo plano (tarea %s). Dile a Polo que le avisarás por notificación en cuanto termine y que puede seguir "
                                      "con otra cosa." % bg["id"]) if bg["estado"] == "en curso" else bg["resultado"]
                        else:
                            salida = (("No pude consultarlo: " + bg["error"]) if bg["estado"] == "error" else bg["resultado"]) or "(sin respuesta)"
                    elif name == "guardar_nota":
                        salida = donna_voz.guardar_nota(a.get("texto", ""), a.get("archivo", "agenda"))
                    elif name == "recordar_regla":
                        salida = donna_voz.recordar_regla(a.get("regla", ""), a.get("contexto", ""))
                    elif name == "crear_recordatorio":
                        salida = donna_voz.crear_recordatorio(a.get("mensaje", ""), a.get("fecha_hora", ""), a.get("en_minutos", 0))
                    elif name == "tarea_en_segundo_plano":
                        pedido = str(a.get("pedido", "")).strip()
                        if len(pedido) < 8:
                            salida = "❌ Falta el pedido del trabajo."
                        else:
                            try:
                                t = app_trabajos.crear(pedido, usuario="voz", esperar_plan=25)
                                aviso("trabajo", id=t["id"], titulo=t["titulo"])
                                pasos = "; ".join(f"{i + 1}) {p['titulo']}" for i, p in enumerate(t["pasos"][:6])) or "(armando el plan)"
                                salida = (f"✅ Es un trabajo grande: lo armé como TRABAJO «{t['titulo']}» con un plan de {len(t['pasos'])} paso(s): {pasos}. "
                                          "Ya lo está resolviendo por su cuenta y Polo lo ve en el módulo Trabajos (qué hace, qué cumplió y qué queda pendiente); "
                                          "los pasos que cambian cosas esperan su confirmación. Dile eso, breve, con tus palabras.")
                            except ValueError as e:
                                salida = "❌ No pude crear el trabajo: " + str(e)
                    elif name == "terminar_conversacion":
                        estado["cerrar"] = True
                        salida = "Listo. Despídete en una frase corta."
                    else:
                        salida = "❌ herramienta desconocida"
                except Exception as e:
                    log.exception("donna live: herramienta %s falló", name)
                    salida = "❌ La herramienta falló: " + str(e)[:150]
                estado["accion_t"] = time.time()
                try:
                    up.send_text(json.dumps({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": call_id, "output": str(salida)[:7000]}}, ensure_ascii=False))
                    up.send_text(json.dumps({"type": "response.create"}))
                except OSError:
                    pass
                aviso("tool_done", name=name)

            def arriba():                                         # OpenAI → app
                try:
                    while not stop.is_set():
                        r = up.recv_message(timeout=1)
                        if r is None:
                            continue
                        if r[0] == "close":
                            aviso("end", m="La voz en tiempo real cerró la sesión")
                            break
                        if r[0] != "text":
                            continue
                        try:
                            ev = json.loads(r[1])
                        except ValueError:
                            continue
                        tp = ev.get("type", "")
                        if tp == "response.created":
                            estado["tool_resp"], estado["texto_resp"] = False, ""
                        elif tp == "response.output_audio.delta":
                            cli.send_binary(b"\x01" + _b64.b64decode(ev.get("delta", "")))
                        elif tp == "response.output_audio_transcript.delta":
                            aviso("out", d=ev.get("delta", ""))
                        elif tp == "response.output_audio_transcript.done":
                            txt = ev.get("transcript", "")
                            estado["texto_resp"] += " " + txt
                            aviso("out_done", text=txt)
                            if txt.strip():
                                with hl:
                                    hist.append({"role": "assistant", "content": txt})
                                _hist_append("assistant", txt)
                        elif tp == "conversation.item.input_audio_transcription.delta":
                            aviso("in", d=ev.get("delta", ""))
                        elif tp == "conversation.item.input_audio_transcription.completed":
                            txt = ev.get("transcript", "")
                            estado["corregido"] = False
                            aviso("in_done", text=txt)
                            if txt.strip():
                                with hl:
                                    hist.append({"role": "user", "content": txt})
                                _hist_append("user", txt)
                        elif tp == "input_audio_buffer.speech_started":
                            aviso("speech")
                        elif tp == "response.function_call_arguments.done":
                            estado["tool_resp"] = True
                            threading.Thread(target=herramienta, args=(ev.get("call_id"), ev.get("name"), ev.get("arguments")), daemon=True).start()
                        elif tp == "response.done":
                            aviso("turn_done")
                            llamo = estado["tool_resp"] or any((o or {}).get("type") == "function_call" for o in (ev.get("response", {}).get("output") or []))
                            estado["tool_resp"] = False                      # la siguiente respuesta empieza limpia aunque no llegue response.created
                            if estado["cerrar"] and not llamo:
                                aviso("end", m="Conversación terminada")
                                break
                            # VIGILANTE DE PROMESAS: si dijo que hizo o hará algo y no llamó ninguna herramienta, no ha pasado nada → se le corrige
                            if (not llamo and not estado["corregido"] and time.time() - estado["accion_t"] > 25
                                    and _PROMESA_RE.search(estado["texto_resp"] or "")):
                                estado["corregido"] = True
                                log.warning("donna live: promesa sin herramienta: %s", estado["texto_resp"][:120])
                                aviso("warn", m="Donna dijo que haría algo sin ejecutarlo; la corregí para que lo haga de verdad")
                                try:
                                    up.send_text(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "system", "content": [{"type": "input_text", "text":
                                        "CORRECCIÓN DEL SISTEMA: acabas de decir que hiciste o harás una acción, pero NO llamaste ninguna herramienta, así que NO ha ocurrido nada. "
                                        "Llama ahora la herramienta que corresponde (guardar_nota, recordar_regla, crear_recordatorio, tarea_en_segundo_plano o consultar_donna) y SOLO después "
                                        "di el resultado real. Si no puedes hacerlo, dile a Polo con claridad que no se hizo."}]}}, ensure_ascii=False))
                                    up.send_text(json.dumps({"type": "response.create"}))
                                except OSError:
                                    pass
                        elif tp == "error":
                            e = ev.get("error") or {}
                            if "cancel" in str(e.get("code", "")) + str(e.get("message", "")).lower():
                                continue                          # cancelar cuando no hay respuesta en curso: inofensivo
                            log.warning("donna live upstream error: %s", str(e.get("message", ""))[:200])
                            aviso("error", m=str(e.get("message", "error de la voz en tiempo real"))[:240])
                except Exception:
                    log.exception("donna live: OpenAI→app falló")
                finally:
                    stop.set()

            threading.Thread(target=arriba, daemon=True).start()
            aviso("listo", max=LIVE_MAX_SECONDS)
            ultimo = time.time()
            while not stop.is_set():
                if time.time() - t0 > LIVE_MAX_SECONDS:
                    aviso("end", m="Se alcanzó el límite de la sesión (%d min)" % (LIVE_MAX_SECONDS // 60))
                    break
                r = cli.recv_message(timeout=1)
                if r is None:
                    if time.time() - ultimo > 2 * LIVE_IDLE_SECONDS:
                        aviso("end", m="Sesión cerrada por inactividad")
                        break
                    continue
                if r[0] == "close":
                    break
                if r[0] == "binary":
                    ultimo = time.time()
                    up.send_text(json.dumps({"type": "input_audio_buffer.append", "audio": _b64.b64encode(r[1]).decode("ascii")}))
                elif r[0] == "text":
                    try:
                        j = json.loads(r[1])
                    except ValueError:
                        continue
                    if j.get("t") == "interrupt":
                        up.send_text(json.dumps({"type": "response.cancel"}))
                    elif j.get("t") == "text" and str(j.get("text", "")).strip():
                        ultimo = time.time()
                        up.send_text(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
                            {"type": "input_text", "text": str(j["text"])[:2000]}]}}, ensure_ascii=False))
                        up.send_text(json.dumps({"type": "response.create"}))
                    elif j.get("t") == "close":
                        break
            stop.set()
        except Exception:
            log.exception("donna live falló")
            aviso("error", m="Error interno en la voz en tiempo real")
        finally:
            _LIVE_SLOTS.release()
            try:
                up and up.close()
            except Exception:
                pass
            cli.close()
            log.info("donna live: sesión de %.0f s", time.time() - t0)
            if len(hist) >= 2:                                  # lo conversado queda guardado, con resumen, y pasa a la base de Donna
                threading.Thread(target=_guardar_conversacion_voz, args=(list(hist), t0), daemon=True).start()

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
        return _ask_donna_voice(user_msg, history)

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
    def _enviar_archivo(self, ruta, mime: str, nombre: str):
        """Entrega un archivo grande por bloques (descarga autenticada)."""
        try:
            size = ruta.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % nombre)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(ruta, "rb") as f:
                while True:
                    blk = f.read(1024 * 1024)
                    if not blk:
                        break
                    self.wfile.write(blk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _sesion_mia(self, sid: str) -> bool:
        """La junta existe y es del usuario (o es el administrador)."""
        try:
            import donna_personas
            donna_personas.asegurar_esquema()
            c = donna_personas._c()
            try:
                r = c.execute("SELECT usuario FROM sesiones_escucha WHERE id=?", (str(sid),)).fetchone()
            finally:
                c.close()
            u = self._media_user()
            return bool(r) and (r["usuario"] == u or u == "admin")
        except Exception:
            return False

    def _escucha_guardar(self, body: dict):
        """POST /v1/app/escucha/guardar — la transcripción de «Escuchar» queda como entregable del usuario (con voces y horas) y como
        documento buscable por Donna. Solo texto: el audio nunca se guarda."""
        import app_media as _M
        from datetime import datetime
        import donna_remoto
        if donna_remoto.ya_guardada(str(body.get("borrador_id", ""))):
            raise ValueError("Esta junta ya se guardó desde otra pantalla. Ábrela en Escuchar → Juntas guardadas; no la guardo dos veces.")
        lineas = body.get("lineas")
        if not isinstance(lineas, list) or not lineas or len(lineas) > 4000:
            raise ValueError("no hay transcripción que guardar")
        voces = body.get("voces") if isinstance(body.get("voces"), list) else []
        ahora = datetime.now(core.TZ_CDMX)
        dur = max(0, min(int(body.get("duracion") or 0), 86400))
        L, md_lines = [], []
        for l in lineas:
            if not isinstance(l, dict):
                continue
            es, orig = str(l.get("es", ""))[:3000], str(l.get("orig", ""))[:3000]
            if not (es.strip() or orig.strip()):
                continue
            quien = (str(l.get("nombre") or "") or ("Voz %s" % l.get("voz") if l.get("voz") else "Voz por identificar"))[:40]
            tono = str(l.get("tono") or "")[:10]
            hora = str(l.get("t", ""))[:8]
            L.append("[%s] %s%s: %s%s" % (hora, quien, " (voz %s)" % tono if tono else "", es, ("\n   (%s) %s" % ("中文" if l.get("idioma") == "zh" else "EN", orig)) if orig.strip() else ""))
            md_lines.append("**%s** · %s%s — %s%s" % (hora, quien, " · voz %s" % tono if tono else "", es.replace("\n", " "),
                                                     ("  \n<sub>%s</sub>" % orig.replace("\n", " ")) if orig.strip() else "") + ("  _(audio débil: revisar)_" if l.get("bajo") else ""))
        if not L:
            raise ValueError("no hay transcripción que guardar")
        quienes = ["- Voz %s — %s%s" % (v.get("n"), str(v.get("nombre", ""))[:40], " (voz %s)" % str(v.get("tono", ""))[:10] if v.get("tono") else "")
                   for v in voces if isinstance(v, dict)]
        titulo = "Transcripción Escuchar " + ahora.strftime("%Y-%m-%d %H:%M")
        t_propio = " ".join(str(body.get("titulo", "")).split())[:100]
        if t_propio:
            titulo = t_propio                                       # p. ej. una junta rescatada del chat con Donna
        contenido = ("Transcripción en español de una escucha en vivo (solo texto; no se guardó audio).\n\n- Fecha: %s\n- Duración: %d min %02d s\n\n## Voces\n%s\n\n## Transcripción\n\n%s\n"
                     % (ahora.strftime("%Y-%m-%d %H:%M"), dur // 60, dur % 60, "\n".join(quienes) or "- (sin identificar)", "\n\n".join(md_lines)))
        origen_nota = " ".join(str(body.get("origen_nota", "")).split())[:400]
        if origen_nota:
            contenido = "> **Nota de origen:** " + origen_nota + "\n\n" + contenido
        live_id = str(body.get("live_id", ""))[:40]
        if live_id:
            try:
                import donna_junta
                contenido += donna_junta.texto_para_entregable(live_id)          # notas y consultas a Donna hechas durante la junta
            except Exception:
                log.exception("junta: no pude agregar notas al entregable")
        r = core._entregable_registrar(titulo, "", contenido, tipo="transcripcion", estado="aprobado", preparado_por="Donna · Escuchar")
        if r.startswith("ERROR"):
            raise ValueError(r)
        texto = "\n".join(L)
        resumen = ""
        try:
            extra = ""
            if live_id:                                         # lo que se llevó a la junta (documentos, imágenes) también cuenta para el resumen
                try:
                    import donna_junta
                    extra = donna_junta.texto_para_entregable(live_id)[:12000]
                except Exception:
                    pass
            resumen = _M.summarize("Conversación escuchada en vivo (voces y horas):\n" + texto + (("\n\nMateriales y notas de la junta:" + extra) if extra else "")) if len(texto) > 200 else ""
        except Exception:
            pass
        m = _M.register_text(titulo, texto, tipo="conversacion", resumen=resumen, origen="escuchar")
        out = {"ok": True, "nombre": titulo, "archivo": r.split(":", 1)[-1].strip().split(" ")[0], "mid": m.get("id", ""), "resumen": resumen, "lineas": len(L)}
        try:                                                    # ya quedó guardada de verdad: el borrador y la copia de rescate sobran
            import donna_borrador, donna_rescate
            donna_borrador.descartar(str(body.get("borrador_id", "")), self._media_user())
            donna_rescate.descartar(str(body.get("borrador_id", "")), self._media_user())
            donna_remoto.marcar_guardada(str(body.get("borrador_id", "")), out.get("mid", ""))
            donna_remoto.limpiar(str(body.get("borrador_id", "")))
        except Exception:
            log.exception("borrador: no pude limpiarlo")
        try:                                                    # base de personas: quién dijo qué (si falla, la transcripción ya quedó guardada)
            import donna_personas
            ps = donna_personas.guardar_sesion(titulo, dur, [v for v in voces if isinstance(v, dict)], [l for l in lineas if isinstance(l, dict)],
                                               mid=out["mid"], archivo=out["archivo"], resumen=resumen, usuario=self._media_user())
            if live_id:
                import donna_junta
                donna_junta.remapear(live_id, ps["sesion_id"])
            viejo = re.sub(r"[^0-9a-zA-Z_-]", "", str(body.get("reemplaza", "")))[:40]
            if viejo and donna_personas.reemplazar_sesion(viejo, ps["sesion_id"], self._media_user()):
                out["reemplazo"] = viejo                          # la junta retomada sustituye a la guardada antes
                try:
                    import donna_audio
                    donna_audio.relinkear(viejo, ps["sesion_id"])    # su audio pasa a la junta nueva
                except Exception:
                    log.exception("audio: no pude pasar el audio de la junta reemplazada")
            try:                                                   # audio de esta grabación → ligado a la junta (empieza a correr su plazo) y pasada de voces con OpenAI
                import donna_audio
                if donna_audio.ligar(str(body.get("borrador_id", "")), ps["sesion_id"]) or donna_audio.tramos(ps["sesion_id"]):
                    out["audio"] = donna_audio.resumen(ps["sesion_id"])
                    threading.Timer(20, lambda s=ps["sesion_id"]: _lanzar_voces(s)).start()
                    threading.Timer(8, lambda s=ps["sesion_id"], d=dur: _verificar_audio(s, d)).start()
            except Exception:
                log.exception("audio: no pude ligar el audio a la junta")
            out.update(sesion_id=ps["sesion_id"], personas=ps["personas"], sugeridos=ps["sugeridos"], extraccion="leyendo")
            import donna_tareas
            threading.Thread(target=lambda sid=ps["sesion_id"]: _leer_junta(sid), daemon=True).start()   # tareas propuestas, sin hacer esperar al guardado

            def _revisar(sid=ps["sesion_id"], u=self._media_user()):                                    # revisión final: se verifica de nuevo todo lo de la junta (después de leer las tareas)
                try:
                    time.sleep(25)
                    import donna_junta
                    donna_junta.revision_final(core, sid, u)
                except Exception:
                    log.exception("revisión final falló")
            threading.Thread(target=_revisar, daemon=True).start()
        except Exception:
            log.exception("personas: no pude indexar la sesión")
        return out

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
            if path == "/v1/app/translate/log":                       # la traducción en vivo manda aquí sus turnos al terminar
                sid, turnos = str(body.get("sid", "")), body.get("turnos") or []
                if not isinstance(turnos, list) or len(turnos) > 300:
                    raise ValueError("turnos inválidos")
                n = 0
                for t in turnos:
                    if not isinstance(t, dict):
                        continue
                    o, tr = str(t.get("original", ""))[:app_translate.MAX_TEXT], str(t.get("traduccion", ""))[:app_translate.MAX_TEXT]
                    if not (o.strip() or tr.strip()):
                        continue
                    app_translate.registrar_turno(sid, {"origen": t.get("origen") if t.get("origen") in app_translate.LANGS else "",
                                                        "destino": t.get("destino") if t.get("destino") in app_translate.LANGS else "es",
                                                        "original": o, "traduccion": tr, "pinyin": ""}, "vivo")
                    n += 1
                self._send_json(200, {"ok": True, "turnos": n})
                return True
            if path == "/v1/app/translate/resumen":                   # «¿de qué están hablando?» sobre lo escuchado hasta ahora
                import app_media as _M
                texto = str(body.get("texto", ""))[-9000:]
                if len(texto.strip()) < 20:
                    raise ValueError("aún no hay suficiente conversación")
                if not app_translate._rate_ok(20):
                    raise ValueError("demasiadas solicitudes seguidas; espera un momento")
                r = _M.summarize("Fragmento de una conversación escuchada EN VIVO (cada bloque trae el original y su traducción al español). "
                                 "Dime en español, breve: de qué están hablando, puntos clave y, si los hay, acuerdos o pendientes (quién, qué, cuándo). "
                                 "No inventes nada que no esté en el texto.\n\n" + texto)
                self._send_json(200, {"resumen": r or "(sin resumen todavía)"})
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
        if self.path.split("?", 1)[0] == "/v1/app/escucha/audio/chunk":          # audio de la junta, por partes mientras se graba
            if not _require_app_auth(self):
                self._send_json(401, {"error": "unauthorized"})
                return
            import donna_audio
            q = parse_qs(urlparse(self.path).query)
            g1 = lambda k, d="": (q.get(k) or [d])[0]
            try:
                self._send_json(200, donna_audio.guardar_parte(g1("aid"), g1("rid"), self._media_user(), int(g1("i", "-1")), self.rfile, int(self.headers.get("Content-Length", "0")),
                                                               int(g1("off", "0") or 0), g1("mime")))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                log.exception("audio de la junta falló")
                self._send_json(500, {"error": str(e)[:200]})
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
        _et, _cx = _tablero_contexto(str(body.get("tablero", ""))) if re.match(r"^a[0-9a-f]{10}$", str(body.get("tablero", ""))) else ("", "")
        _cid = str(body.get("cid", ""))
        _cid = _cid if (app_chats and re.match(r"^c[0-9a-f]{10}$", _cid)) else ""
        threading.Thread(target=_chat_worker, args=(rid, user_msg, history), kwargs={"extra": _cx, "etiqueta": _et, "cid": _cid, "user": self._media_user()}, daemon=True).start()
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
        if self.path in ("/v1/app/chats/guardar", "/v1/app/chats/renombrar", "/v1/app/chats/borrar", "/v1/app/chats/nuevo"):
            if not app_chats:
                self._send_json(503, {"error": "conversaciones guardadas no disponibles en este servidor"})
                return
            try:
                u, cid = self._media_user(), str(body.get("id", ""))
                if self.path.endswith("nuevo"):
                    self._send_json(200, {"id": app_chats.nuevo_id()})
                elif self.path.endswith("guardar"):
                    self._send_json(200, app_chats.guardar(u, cid, body.get("msgs"), str(body.get("titulo", ""))))
                elif self.path.endswith("renombrar"):
                    self._send_json(200, app_chats.renombrar(u, cid, str(body.get("titulo", ""))))
                else:
                    self._send_json(200, {"ok": app_chats.borrar(u, cid)})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path in ("/v1/app/trabajos/crear", "/v1/app/trabajo/accion"):
            try:
                if self.path.endswith("crear"):
                    self._send_json(200, app_trabajos.crear(str(body.get("pedido", "")), str(body.get("titulo", "")), self._media_user(), esperar_plan=float(body.get("esperar") or 0)))
                else:
                    self._send_json(200, app_trabajos.accion(str(body.get("id", "")), str(body.get("accion", "")), body.get("paso")))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/navegador":                          # navegador que Polo ve y maneja para iniciar sesión (browser_live.py)
            import browser_live
            self._send_json(200, browser_live.operar(body))
            return
        if self.path in ("/v1/app/persona", "/v1/app/escucha/asignar"):
            import donna_personas
            try:
                u = self._media_user()
                if self.path.endswith("asignar"):
                    self._send_json(200, {"ok": True, **donna_personas.asignar(str(body.get("sesion_id", "")), int(body.get("voz") or 0), str(body.get("nombre", "")), u)})
                else:
                    op = body.get("op")
                    if op == "crear":
                        pid = donna_personas.resolver(str(body.get("nombre", "")), u, True)
                        if not pid:
                            raise ValueError("Falta el nombre.")
                        self._send_json(200, {"ok": True, "item": donna_personas.editar(pid, **{k: body.get(k) for k in ("empresa", "rol", "notas") if body.get(k)}) if any(body.get(k) for k in ("empresa", "rol", "notas")) else donna_personas.detalle(pid, 0)})
                    elif op == "editar":
                        self._send_json(200, {"ok": True, "item": donna_personas.editar(str(body.get("id", "")), **{k: body.get(k) for k in ("nombre", "empresa", "rol", "kc_user", "notas", "alias")})})
                    elif op == "fusionar":
                        self._send_json(200, {"ok": True, "item": donna_personas.fusionar(str(body.get("id", "")), str(body.get("destino", "")))})
                    elif op == "eliminar":
                        self._send_json(200, {"ok": donna_personas.eliminar(str(body.get("id", "")))})
                    else:
                        raise ValueError("operación no válida")
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/escucha/material":                  # un adjunto subido durante la junta queda ligado a ella
            import donna_junta
            try:
                self._send_json(200, donna_junta.material_add(str(body.get("live_id", ""))[:40], str(body.get("mid", "")), str(body.get("hora", "")), str(body.get("comentario", ""))))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path in ("/v1/app/escucha/asistente", "/v1/app/escucha/nota"):
            import donna_junta
            try:
                lid = str(body.get("live_id", ""))[:40]
                if self.path.endswith("nota"):
                    self._send_json(200, donna_junta.nota_add(lid, str(body.get("texto", "")), str(body.get("hora", "")), "polo"))
                else:
                    if not _junta_rate_ok(self._media_user()):
                        self._send_json(429, {"error": "Muchas preguntas seguidas; espera un momento."})
                        return
                    self._send_json(200, donna_junta.preguntar(core, lid, str(body.get("pregunta", "")), body.get("lineas") if isinstance(body.get("lineas"), list) else [], self._media_user(),
                                                              voces=body.get("voces") if isinstance(body.get("voces"), list) else None))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                log.exception("asistente de junta falló")
                self._send_json(502, {"error": "No pude consultar a Donna ahora: " + str(e)[:120]})
            return
        if self.path in ("/v1/app/aprendizaje/regla", "/v1/app/aprendizaje/mejora"):
            import donna_aprender
            try:
                if self.path.endswith("regla"):
                    if body.get("accion") == "crear":
                        self._send_json(200, donna_aprender.regla_agregar(str(body.get("cuando", "")), str(body.get("regla", "")), "", "Escrita por Polo en la app"))
                    else:
                        self._send_json(200, donna_aprender.regla_accion(str(body.get("id", "")), str(body.get("accion", "")), str(body.get("regla", "")), str(body.get("cuando", ""))))
                else:
                    self._send_json(200, donna_aprender.mejora_estado(str(body.get("id", "")), str(body.get("accion", "")), str(body.get("nota", ""))))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path in ("/v1/app/escucha/audio", "/v1/app/escucha/voces"):
            import donna_audio, donna_diariza
            u = self._media_user()
            acc = body.get("accion")
            try:
                if self.path.endswith("audio"):                                              # el tramo de audio terminó (o se borra)
                    if acc == "ligar":
                        if not self._sesion_mia(str(body.get("sesion_id", ""))):
                            raise ValueError("junta no encontrada")
                        if not donna_audio.ligar_tramo(str(body.get("aid", "")), str(body.get("sesion_id", "")), u):
                            raise ValueError("audio no encontrado")
                        self._send_json(200, {"ok": True})
                    elif acc == "borrar":
                        self._send_json(200, {"ok": True, "borrados": donna_audio.borrar(str(body.get("id", "")), u)})
                    else:
                        self._send_json(200, donna_audio.terminar(str(body.get("aid", "")), u))
                    return
                sid = str(body.get("sesion_id", ""))
                if not self._sesion_mia(sid):
                    self._send_json(404, {"error": "junta no encontrada"})
                elif acc == "aplicar":
                    self._send_json(200, {"ok": True, **donna_diariza.aplicar(sid, body.get("ids") if isinstance(body.get("ids"), list) else "todas", u)})
                elif acc == "borrar_audio":
                    self._send_json(200, {"ok": True, "borrados": donna_audio.borrar(sid, u)})
                elif acc == "procesar":
                    if not _lanzar_voces(sid):
                        self._send_json(200, {"ok": True, "ya": True})
                    else:
                        self._send_json(200, {"ok": True})
                else:
                    self._send_json(400, {"error": "acción no válida"})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/escucha/duplicadas":                # «Eliminar duplicadas»: conserva la que tiene audio (o la más reciente) y nunca borra una con audio
            import donna_minuta
            n = donna_minuta.limpiar_duplicadas(self._media_user())
            log.info("escucha: %d junta(s) duplicadas eliminadas", n)
            self._send_json(200, {"ok": True, "eliminadas": n})
            return
        if self.path == "/v1/app/escucha/eliminar":                  # borrar una junta guardada (p. ej. una copia duplicada); el audio solo si se pide
            import donna_personas, donna_audio
            sid = str(body.get("sesion_id", ""))
            try:
                if not self._sesion_mia(sid):
                    raise ValueError("junta no encontrada")
                con_audio = donna_audio.resumen(sid)["tramos"] > 0
                if con_audio and not body.get("borrar_audio"):
                    raise ValueError("Esta junta tiene audio guardado. Confirma que también quieres borrar el audio.")
                r = donna_personas.eliminar_sesion(sid, self._media_user())
                if con_audio:
                    donna_audio.borrar(sid, self._media_user())
                log.info("escucha: junta %s eliminada (%d intervenciones%s)", sid, r["intervenciones"], ", con audio" if con_audio else "")
                self._send_json(200, {"ok": True, **r, "audio_borrado": con_audio})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/escucha/detener":                   # detener y guardar la(s) escucha(s) activa(s) desde cualquier dispositivo
            import donna_remoto
            ids = donna_remoto.ordenar(self._media_user(), str(body.get("excepto", ""))[:30])
            log.info("escucha: orden de detener a %d escucha(s)", len(ids))
            self._send_json(200, {"ok": True, "ordenes": len(ids), "ids": ids})
            return
        if self.path == "/v1/app/escucha/borrador":                  # autoguardado de la junta en curso / descartar
            import donna_borrador
            try:
                if body.get("accion") == "descartar":
                    import donna_rescate
                    rid_d = str(body.get("id", ""))
                    self._send_json(200, {"ok": True, "borrados": donna_borrador.descartar(rid_d, self._media_user()) + donna_rescate.descartar(rid_d, self._media_user())})
                else:
                    import donna_remoto
                    if donna_remoto.ya_guardada(str(body.get("id", ""))):                  # esa junta ya se guardó: un autoguardado tardío no debe resucitar el borrador
                        self._send_json(200, {"ok": True, "guardada": True, "orden": ""})
                    else:
                        self._send_json(200, {"ok": True, **donna_borrador.guardar(str(body.get("id", "")), self._media_user(), body), "orden": donna_remoto.orden_de(str(body.get("id", "")))})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/limpieza":                          # limpieza guiada de la AGENDA: cerrar · seguimiento · conservar · deshacer
            import donna_limpieza as _L
            ids = [str(x) for x in (body.get("ids") or [])][:300]
            acc = body.get("accion")
            try:
                if acc == "cerrar":
                    r = _L.cerrar(core, ids)
                elif acc == "conservar":
                    r = _L.conservar(ids)
                elif acc == "seguimiento":
                    r = _L.a_seguimiento(core, ids, str(body.get("cuando") or ""), float(body.get("en_horas") or 0), usuario=self._media_user())
                elif acc == "deshacer":
                    r = _L.deshacer(core, ids)
                else:
                    self._send_json(400, {"error": "acción no válida"})
                    return
                self._send_json(200, {"ok": True, **r})
            except Exception as e:
                log.exception("limpieza falló")
                self._send_json(500, {"error": str(e)[:200]})
            return
        if self.path in ("/v1/app/preferencias", "/v1/app/dia/enviar"):
            import donna_dia
            try:
                if self.path.endswith("enviar"):                          # «Enviarme el resumen ahora» (usa los canales configurados)
                    tipo = "cierre" if body.get("tipo") == "cierre" else "manana"
                    r = donna_dia.entregar(core, tipo, donna_dia.prefs_get()[tipo]["canales"], usuarios=[self._media_user()] if self._media_user() != "admin" else None)
                    self._send_json(200, {"ok": True, "canales": r["canales"]})
                else:
                    self._send_json(200, {"ok": True, "prefs": donna_dia.prefs_set(body.get("prefs") or {})})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/oficio/revisar":                    # «Autoevaluarse ahora»
            import donna_oficio
            if not _RADAR_LOCK.acquire(blocking=False):
                self._send_json(200, {"ok": True, "ya": True})
                return

            def _ofi():
                try:
                    donna_oficio.revisar_reciente(core)
                except Exception:
                    log.exception("autoevaluación falló")
                finally:
                    _RADAR_LOCK.release()
            threading.Thread(target=_ofi, daemon=True).start()
            self._send_json(200, {"ok": True})
            return
        if self.path == "/v1/app/agente/plan":
            import donna_agente
            try:
                self._send_json(200, donna_agente.plan_accion(str(body.get("id", "")), str(body.get("accion", ""))))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/agente/conocimiento":
            import donna_agente
            try:
                self._send_json(200, donna_agente.corregir(str(body.get("id", "")), str(body.get("accion", "")), str(body.get("hecho", ""))))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path in ("/v1/app/tareas/accion", "/v1/app/tareas/extraer"):
            import donna_tareas
            try:
                if self.path.endswith("extraer"):
                    sid = str(body.get("sesion_id", ""))
                    donna_tareas.transcripcion(sid)                      # valida que exista
                    threading.Thread(target=lambda: _leer_junta(sid), daemon=True).start()
                    self._send_json(200, {"ok": True})
                elif body.get("accion") == "rechazar":
                    self._send_json(200, donna_tareas.rechazar(str(body.get("id", "")), str(body.get("motivo", ""))))
                else:
                    self._send_json(200, donna_tareas.aprobar(core, str(body.get("id", "")), str(body.get("destino", "")), body.get("titulo"), body.get("responsable"),
                                                              body.get("fecha"), str(body.get("proyecto_id", ""))))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path in ("/v1/app/radar/cierre", "/v1/app/radar/autonomia", "/v1/app/radar/ejecutar"):
            import donna_radar
            try:
                if self.path.endswith("cierre"):
                    self._send_json(200, donna_radar.resolver(core, str(body.get("id", "")), str(body.get("decision", "")), bool(body.get("autorizar"))))
                elif self.path.endswith("autonomia"):
                    donna_radar.autonomia_set(str(body.get("clase", "")), str(body.get("nivel", "")))
                    self._send_json(200, {"ok": True, "autonomia": donna_radar.autonomia_todas()})
                else:                                                   # «Revisar ahora»: corre el radar en segundo plano; la app consulta /v1/app/radar
                    que = str(body.get("que", "cierres"))
                    if que not in ("cierres", "fechas", "agente"):
                        raise ValueError("operación no válida")
                    if not _RADAR_LOCK.acquire(blocking=False):
                        self._send_json(200, {"ok": True, "ya": True})
                        return

                    def _ya():
                        try:
                            f = donna_radar.fuentes_por_defecto(core)
                            if que == "agente":                                # «Revisar ahora»: un ciclo completo del agente (observa, decide, actúa, aprende)
                                import donna_agente
                                donna_agente.ciclo(core, "Polo pidió que revisara ahora", f)
                            elif que == "fechas":
                                donna_radar.radar_fechas(f)
                            else:
                                donna_radar.procesar_cierres(core, f, donna_radar.juez_haiku(core))
                        except Exception:
                            log.exception("radar manual falló")
                        finally:
                            _RADAR_LOCK.release()
                    threading.Thread(target=_ya, daemon=True).start()
                    self._send_json(200, {"ok": True})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/seguimiento":
            import donna_seg
            try:
                if body.get("op") == "crear":
                    r = donna_seg.crear(str(body.get("titulo", "")), str(body.get("fecha_hora", "")), body.get("en_horas", 0), str(body.get("nota", "")),
                                        origen=str(body.get("origen", "manual")), usuario=self._media_user())
                else:
                    r = donna_seg.accion(str(body.get("id", "")), str(body.get("accion", "")), str(body.get("fecha_hora", "")), body.get("en_horas", 0), body.get("nota"))
                self._send_json(200, {"ok": True, "item": r})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path == "/v1/app/chat/cancel":                        # «actualizar el mensaje anterior»: la respuesta en curso se descarta
            rid = str(body.get("rid", ""))
            with _RESULTS_LOCK:
                r = _RESULTS.get(rid)
                if r is not None:
                    r["cancelled"] = True
            self._send_json(200, {"ok": r is not None})
            return
        if self.path == "/v1/app/escucha/guardar":
            try:
                self._send_json(200, self._escucha_guardar(body))
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                log.exception("escucha/guardar falló")
                self._send_json(500, {"error": str(e)[:200]})
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
    threading.Timer(90, lambda: _reintentar_voces_fallidas()).start()
    log.info("Arrancando openclaw-gateway en %s:%d", HOST, PORT)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.started_at = int(time.time())
    try:
        n = app_trabajos.reanudar_pendientes()
        if n:
            log.info("Trabajos retomados tras el reinicio: %d", n)
    except Exception:
        log.exception("no pude retomar trabajos")
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
