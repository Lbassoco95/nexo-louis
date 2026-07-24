#!/usr/bin/env python3
"""
Telegram bridge para Louis (Nexo) — versión Hetzner.

Long polling de Telegram → louis_core (routing Ollama/Claude + tools) →
respuesta formateada para Telegram Markdown legacy.

Vive como systemd unit (telegram-bridge.service).
Logs en /opt/openclaw/logs/telegram-bridge.log.

Toda la lógica de routing, tools y M365 vive en louis_core.py
(compartido con slack-bridge.py).
"""

import os
import sys
import re
import json
import time
import signal
import hashlib
import subprocess
import tempfile
import shutil
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

# Importa la lógica común
sys.path.insert(0, str(Path(__file__).parent))
import louis_core as core

# Frases con las que Louis "promete" producir/entregar un documento. Si aparecen en
# su respuesta pero NO encoló ningún archivo, la red de seguridad lo genera de verdad
# (vía el flujo directo) para que nunca quede en "voy a generar" sin entregar.
_PROMESA_DOC_RE = re.compile(
    r"\b(voy\s+a|te\s+(lo|los|las)|lo|los|las|ya)\s+"
    r"(gener\w*|prepar\w*|elabor\w*|arm\w*|redact\w*|dej\w*\s+list\w*|trabaj\w*)\b"
    r"[^.!?\n]{0,60}\b"
    r"(documento|perfil(es)?\s+de\s+puesto|perfil(es)?|pdf|informe|reporte|dictamen|"
    r"presentaci[oó]n|propuesta|machote|formato|plantilla|acta)\b",
    re.IGNORECASE,
)


def _promete_documento(text: str) -> bool:
    """True si la respuesta de Louis promete producir/entregar un documento pero
    (probablemente) no lo adjuntó. Conservador: exige verbo de acción + sustantivo
    documental cercano, para no disparar generaciones (costosas) por falsos positivos."""
    if not text:
        return False
    return bool(_PROMESA_DOC_RE.search(text))


# ===== Configuración local del bridge =====
# Mismo patrón que louis_core: /opt/openclaw en Hetzner, ~/.openclaw en dev.
HOME = Path.home()
if Path("/opt/openclaw").exists():
    HOME_OC = Path("/opt/openclaw")
else:
    HOME_OC = HOME / ".openclaw"
SPACE = HOME_OC / "spaces" / "general"
CREDS_TELEGRAM = HOME_OC / "credentials" / "telegram.env"
OFFSET_FILE = HOME_OC / "credentials" / "telegram-bridge-offset.txt"
HISTORY_FILE = SPACE / "telegram-history.jsonl"
LOG_DIR = HOME_OC / "logs"
# Protege HISTORY_FILE contra escrituras concurrentes (main thread + audio bg thread)
_history_lock = threading.Lock()
# Thread pool para transcripción de audio: max 2 jobs simultáneos
_audio_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-transcribe")
LOG_FILE = LOG_DIR / "telegram-bridge.log"

WHISPER_MODEL = HOME_OC / "whisper-models" / "ggml-medium.bin"
WHISPER_CANDIDATES = [
    "/usr/local/bin/whisper-cli",
    "/usr/local/bin/whisper-cpp",
    "/opt/homebrew/bin/whisper-cli",
    "/opt/homebrew/bin/whisper-cpp",
]
FFMPEG_CANDIDATES = [
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
]

LONG_POLL_TIMEOUT = 25

# ===== Logging =====
# systemd ya redirige stdout → LOG_FILE (StandardOutput=append:…).
# Solo necesitamos StreamHandler; agregar FileHandler también causaría duplicados.
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("telegram-bridge")
core.log = log  # comparte logger con el core


# ===== Helpers locales =====
def find_binary(candidates):
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def load_credentials():
    creds = core.load_env_file(CREDS_TELEGRAM)
    token = creds.get("TELEGRAM_BOT_TOKEN")
    chat_id = creds.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error(f"Faltan credenciales en {CREDS_TELEGRAM}")
        sys.exit(1)
    return token, chat_id


def get_offset() -> int:
    if not OFFSET_FILE.exists():
        return 0
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def set_offset(val: int):
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(val))


def http_get(url: str, timeout: int = 30):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ===== Telegram API =====
def telegram_get_updates(token: str, offset: int):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {
        "offset": offset,
        "timeout": LONG_POLL_TIMEOUT,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    full = f"{url}?{urlencode(params)}"
    try:
        raw = http_get(full, timeout=LONG_POLL_TIMEOUT + 5)
        return json.loads(raw)
    except Exception as e:
        log.warning(f"getUpdates falló: {e}")
        return None


def telegram_send_message(token: str, chat_id: str, text: str, parse_mode: str = "Markdown"):
    """
    Manda mensaje a Telegram. Aplica format_for_telegram() para convertir el
    Markdown de Claude a HTML de Telegram (más robusto que Markdown legacy).

    Si el render con HTML falla, reintenta sin formato (tags removidos).
    """
    import html as _html
    use_format = bool(parse_mode)
    formatted = core.format_for_telegram(text) if use_format else text
    tg_parse = "HTML" if use_format else None
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = []
    remaining = formatted
    while remaining:
        chunks.append(remaining[:4000])
        remaining = remaining[4000:]
    for chunk in chunks:
        body = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if tg_parse:
            body["parse_mode"] = tg_parse
        try:
            core.http_post_json(url, headers={}, body=body, timeout=15)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if tg_parse and ("parse" in err_body.lower() or "entity" in err_body.lower() or e.code == 400):
                log.warning(f"HTML falló ({err_body[:200]}), reintentando sin formato")
                # Quitar tags HTML y desescapar entidades → texto plano legible
                raw = re.sub(r"<[^>]+>", "", chunk)
                raw = _html.unescape(raw)
                body.pop("parse_mode", None)
                body["text"] = raw
                try:
                    core.http_post_json(url, headers={}, body=body, timeout=15)
                except Exception as e2:
                    log.error(f"sendMessage retry sin parse falló: {e2}")
            else:
                log.error(f"sendMessage falló: {e} {err_body[:200]}")
        except Exception as e:
            log.error(f"sendMessage falló: {e}")


def _is_html_response(text: str) -> bool:
    """True si la respuesta es HTML completo (no fragmento inline)."""
    t = text.strip()
    return t.startswith(("<!DOCTYPE", "<!doctype", "<html", "<HTML"))


def _extract_html_from_fence(text: str) -> str | None:
    """Extrae HTML de un bloque ```html ... ``` si está presente."""
    import re
    m = re.search(r"```(?:html)?\s*\n(<!DOCTYPE[\s\S]+?)\n```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def telegram_send_document(token: str, chat_id: str, content: bytes, filename: str, caption: str = ""):
    """Envía bytes como archivo adjunto (HTML, PDF, TXT…) usando multipart/form-data."""
    boundary = b"----LouisBridge0xDEAD"
    def field(name: str, value: str) -> bytes:
        return (
            b"--" + boundary + b"\r\n"
            + f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode()
            + b"\r\n"
        )
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    mime_map = {"html": "text/html", "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown"}
    mime = mime_map.get(ext, "application/octet-stream")
    file_part = (
        b"--" + boundary + b"\r\n"
        + f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
        + f"Content-Type: {mime}\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts = [field("chat_id", str(chat_id))]
    if caption:
        parts.append(field("caption", caption[:1024]))
    parts.append(file_part)
    parts.append(b"--" + boundary + b"--\r\n")
    body = b"".join(parts)
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                raise RuntimeError(result)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        log.error(f"sendDocument falló: {e.code} {err[:300]}")
        raise


def telegram_send_file(token: str, chat_id: str, text: str, filename: str, caption: str = ""):
    """Convierte texto/HTML a archivo y lo envía por Telegram."""
    content = text.encode("utf-8")
    try:
        telegram_send_document(token, chat_id, content, filename, caption=caption)
        log.info(f"→ documento enviado: {filename} ({len(content)} bytes)")
    except Exception as e:
        log.warning(f"sendDocument falló ({e}), enviando como texto")
        telegram_send_message(token, chat_id, text[:4000], parse_mode=None)


def telegram_get_file_path(token: str, file_id: str):
    url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
    raw = http_get(url)
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(f"getFile: {data}")
    return data["result"]["file_path"]


def telegram_download_file(token: str, file_path: str, dest: Path):
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


# ===== Whisper transcripción =====
def transcribe_audio(audio_ogg: Path, duration_s: int = 0) -> str:
    whisper_bin = find_binary(WHISPER_CANDIDATES)
    ffmpeg_bin = find_binary(FFMPEG_CANDIDATES)
    if not whisper_bin:
        return "(error: whisper no instalado)"
    if not ffmpeg_bin:
        return "(error: ffmpeg no instalado)"
    if not WHISPER_MODEL.exists():
        return f"(error: modelo whisper no encontrado en {WHISPER_MODEL})"

    # Timeout por modelo: cada modelo de fallback tiene su propio multiplicador
    # decreciente. medium = 1.5x+30 (max 6min), small = 1.2x+30 (max 4min),
    # base = 0.8x+30 (max 3min). Total máx para 4-min audio ≈ 13min vs los 33min anteriores.
    def _scaled(dur: int, multiplier: float, cap: int) -> int:
        if dur <= 0:
            return cap // 2
        return min(cap, max(cap // 3, int(dur * multiplier) + 30))

    # Modelos por orden de preferencia: medium (mejor precisión) → small → base
    # (más rápidos). Si medium se tarda demasiado en CPU, degradamos en vez de fallar.
    whisper_dir = WHISPER_MODEL.parent
    modelos = [(n, whisper_dir / f"ggml-{n}.bin", _scaled(duration_s, mult, cap))
               for n, mult, cap in (("medium", 1.5, 360), ("small", 1.2, 240), ("base", 0.8, 180))
               if (whisper_dir / f"ggml-{n}.bin").exists()]
    if not modelos:
        return f"(error: no encontré ningún modelo whisper en {whisper_dir})"
    threads = str(max(4, os.cpu_count() or 4))  # usa todos los cores → mucho más rápido

    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "audio.wav"
        r = subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(audio_ogg), "-ar", "16000", "-ac", "1", str(wav_path)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error(f"ffmpeg falló: {r.stderr}")
            return "(error convirtiendo audio)"

        def _run_whisper(cmd, tout):
            """Ejecuta whisper en su propio process group para poder matarlo con killpg
            incluso si está en D-state (cargando modelo de disco)."""
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
                preexec_fn=os.setsid,  # nuevo process group → killpg los mata a todos
            )
            try:
                stdout, stderr = proc.communicate(timeout=tout)
                return proc.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise

        ultimo = ""
        for nombre, modelo, tout in modelos:
            of = Path(td) / f"transcript_{nombre}"
            cmd = [whisper_bin, "-m", str(modelo), "-f", str(wav_path),
                   "-l", "es", "-t", threads, "-otxt", "-of", str(of)]
            try:
                rc, stdout, stderr = _run_whisper(cmd, tout)
            except subprocess.TimeoutExpired:
                log.warning(f"whisper '{nombre}' excedió {tout}s (matado via killpg); pruebo modelo más ligero")
                ultimo = f"timeout {tout}s ({nombre})"
                continue
            if rc != 0:
                log.error(f"whisper '{nombre}' falló rc={rc}: {stderr[:300]}")
                ultimo = (stderr or "")[:200]
                continue
            txt_path = of.with_suffix(".txt")
            texto = txt_path.read_text().strip() if txt_path.exists() else stdout.strip()
            if texto:
                if nombre != modelos[0][0]:
                    log.info(f"Transcrito con modelo de respaldo '{nombre}'")
                return texto
            ultimo = "transcripción vacía"
        log.error(f"todos los modelos whisper fallaron: {ultimo}")
        return ("(no pude transcribir el audio — el servidor está saturado o el audio "
                "viene dañado; intenta de nuevo en un momento)")


# ===== Main loop =====
# ═══════════════════════════════════════════════════════════════════════════
# PANEL DE PENDIENTES CON BOTONES (callback_query) — control directo sin LLM.
# Un clic = marca '- [x]' en SEGUIMIENTOS.md (operación a archivo, 0 tokens del modelo).
# ═══════════════════════════════════════════════════════════════════════════
import html as _htmlmod

_SEGUIMIENTOS = core.SPACE / "SEGUIMIENTOS.md"


def _agenda_open_items(limit=24):
    """[(hash8, texto)] de los pendientes abiertos '- [ ]' de SEGUIMIENTOS."""
    if not _SEGUIMIENTOS.exists():
        return []
    out = []
    for line in _SEGUIMIENTOS.read_text().splitlines():
        m = re.match(r"^\s*-\s*\[\s*\]\s+(.+)", line)
        if m:
            out.append((hashlib.md5(line.encode("utf-8")).hexdigest()[:8], m.group(1).strip()))
            if len(out) >= limit:
                break
    return out


def _agenda_mark_done(h8):
    """Marca '- [x]' la línea cuyo md5[:8] coincide. Devuelve el texto cerrado o None."""
    if not _SEGUIMIENTOS.exists():
        return None
    lines = _SEGUIMIENTOS.read_text().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*\[\s*\]\s+", line) and hashlib.md5(line.encode("utf-8")).hexdigest()[:8] == h8:
            lines[i] = re.sub(r"\[\s*\]", "[x]", line, count=1)
            _SEGUIMIENTOS.write_text("\n".join(lines) + "\n")
            return re.sub(r"^\s*-\s*\[x\]\s+", "", lines[i]).strip()
    return None


_META_RE = re.compile(r"\s*·\s*\[(?:auto|capturado)[^\]]*\]")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _fmt_item(txt: str, maxlen: int = 150) -> str:
    """Limpia un pendiente para mostrarlo bonito en Telegram (parse_mode=HTML):
    quita el metadato de captura (· [auto …]), recorta, escapa HTML y convierte la
    negrita Markdown (**texto**) en <b>texto</b> para que NO salgan los asteriscos."""
    t = _META_RE.sub("", txt).strip()
    if len(t) > maxlen:
        t = t[:maxlen].rstrip() + "…"
    t = _htmlmod.escape(t)
    t = _MD_BOLD_RE.sub(r"<b>\1</b>", t)
    return t


def _agenda_panel():
    """Devuelve (texto_html, reply_markup) con los pendientes y un botón por cada uno."""
    items = _agenda_open_items()
    if not items:
        return "✅ <b>Seguimientos</b> — sin pendientes abiertos. ¡Vas al día!", None
    lines = ["📋 <b>Pendientes abiertos</b> — toca el número para cerrarlo:\n"]
    row, keyboard = [], []
    for i, (h, txt) in enumerate(items, 1):
        lines.append(f"<b>{i}.</b> {_fmt_item(txt)}")
        row.append({"text": f"✅ {i}", "callback_data": f"done:{h}"})
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"text": "🔄 Actualizar", "callback_data": "panel"}])
    return "\n".join(lines), {"inline_keyboard": keyboard}


def telegram_send_panel(token, chat_id, text, reply_markup=None):
    body = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML",
            "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        core.http_post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                            headers={}, body=body, timeout=15)
    except Exception as e:
        log.error(f"send_panel falló: {e}")


def telegram_answer_callback(token, cbq_id, text=""):
    try:
        core.http_post_json(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                            headers={}, body={"callback_query_id": cbq_id, "text": text[:200]}, timeout=10)
    except Exception as e:
        log.warning(f"answerCallback falló: {e}")


def telegram_edit(token, chat_id, message_id, text, reply_markup=None):
    body = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        core.http_post_json(f"https://api.telegram.org/bot{token}/editMessageText",
                            headers={}, body=body, timeout=15)
    except Exception as e:
        log.warning(f"editMessageText falló: {e}")


def handle_callback(cbq, token, chat_id):
    """Maneja los clics de botones SIN pasar por el modelo (operación directa)."""
    data = cbq.get("data") or ""
    cbq_id = cbq.get("id")
    m = cbq.get("message") or {}
    mid = m.get("message_id")
    cid = str((m.get("chat") or {}).get("id") or "")
    if cid != str(chat_id):
        telegram_answer_callback(token, cbq_id, "No autorizado")
        return
    if data == "panel":
        txt, mk = _agenda_panel()
        telegram_edit(token, chat_id, mid, txt, mk)
        telegram_answer_callback(token, cbq_id, "Actualizado")
        return
    if data.startswith("done:"):
        cerrado = _agenda_mark_done(data.split(":", 1)[1])
        # Toast efímero (texto plano, sin markdown) + confirmación persistente bien
        # formateada con el texto COMPLETO de la tarea cerrada.
        if cerrado:
            plano = _MD_BOLD_RE.sub(r"\1", _META_RE.sub("", cerrado)).strip()
            telegram_answer_callback(token, cbq_id, f"✅ Hecho: {plano[:180]}")
            telegram_send_panel(token, chat_id, f"✅ <b>Cerrado:</b> {_fmt_item(cerrado, maxlen=300)}")
        else:
            telegram_answer_callback(token, cbq_id, "Ya estaba cerrado o cambió")
        txt, mk = _agenda_panel()
        telegram_edit(token, chat_id, mid, txt, mk)
        return
    telegram_answer_callback(token, cbq_id, "")


def _finish_user_input(telegram_token, chat_id, api_key, system_prompt, user_input):
    """Runs the full LLM → response flow for a resolved user_input.
    Called from both the main thread (text) and the audio background thread.
    Thread-safe: uses _history_lock around all history reads/writes."""
    import datetime as _dt_mod

    if not user_input or not user_input.strip():
        return

    if user_input.strip().lower().lstrip("/") in ("nuevo", "reset", "limpia", "limpiar", "borra historial"):
        with _history_lock:
            try:
                if HISTORY_FILE.exists():
                    HISTORY_FILE.unlink()
            except Exception as e:
                log.warning(f"No pude borrar historial: {e}")
        telegram_send_message(telegram_token, chat_id,
                              "🧹 Listo, empecé de cero. El historial anterior se borró.", parse_mode=None)
        return

    if core.is_status_command(user_input):
        response = core._verificar_conexiones(incluir_m365=False)
        with _history_lock:
            core.append_history(HISTORY_FILE, "user", user_input)
            core.append_history(HISTORY_FILE, "assistant", response)
        log.info(f"← status ({len(response)} chars)")
        telegram_send_message(telegram_token, chat_id, response, parse_mode=None)
        return

    if core.is_agents_list_command(user_input):
        response = core.format_agents_list_compact()
        with _history_lock:
            core.append_history(HISTORY_FILE, "user", user_input)
            core.append_history(HISTORY_FILE, "assistant", response)
        log.info(f"← agents-list ({len(response)} chars)")
        telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")
        return

    with _history_lock:
        core.append_history(HISTORY_FILE, "user", user_input)
        history = core.load_history(HISTORY_FILE, max_turns=28)[:-1]

    if core.needs_doc_sonnet(user_input):
        log.info("→ Documento directo (Sonnet escribe contenido → PDF/PPTX/XLSX)")
        response, model_used = core.generar_documento_directo(api_key, system_prompt, history, user_input)
        with _history_lock:
            core.append_history(HISTORY_FILE, "assistant", response)
        telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")
        for content, fname, cap in core.get_pending_files():
            try:
                telegram_send_document(telegram_token, chat_id, content, fname, caption=cap)
                log.info(f"📎 documento enviado: {fname} ({len(content)} bytes)")
            except Exception as e:
                log.error(f"envío de documento {fname} falló: {e}")
                telegram_send_message(telegram_token, chat_id, f"⚠️ No pude enviarte {fname}: {e}", parse_mode=None)
        return

    response, model_used = core.call_llm(
        api_key, system_prompt, history, user_input, history_file=HISTORY_FILE
    )
    log.info(f"← {model_used} respondió ({len(response)} chars)")
    with _history_lock:
        core.append_history(HISTORY_FILE, "assistant", response)

    pending_files = core.get_pending_files()

    if not pending_files and core.needs_doc_sonnet(user_input) and len(response) > 1200:
        try:
            titulo = (user_input or "documento")[:70].strip().rstrip(".?!")
            pdf = core._generar_pdf(titulo, response, "Louis")
            if pdf:
                fname = f"{titulo[:40].replace(' ', '_')}_{_dt_mod.datetime.now().strftime('%H%M%S')}.pdf"
                pending_files = [(pdf, fname, f"📄 {titulo[:60]}")]
                log.info(f"📎 PDF generado por red de seguridad: {fname} ({len(pdf)} bytes)")
        except Exception as e:
            log.warning(f"Red de seguridad PDF falló: {e}")
    elif not pending_files and _promete_documento(response):
        try:
            log.info("→ Louis prometió documento sin entregarlo; generando vía flujo directo")
            doc_resp, _m = core.generar_documento_directo(api_key, system_prompt, history, user_input)
            telegram_send_message(telegram_token, chat_id, doc_resp, parse_mode="Markdown")
            with _history_lock:
                core.append_history(HISTORY_FILE, "assistant", doc_resp)
            pending_files = core.get_pending_files()
        except Exception as e:
            log.warning(f"Red de seguridad (promesa de documento) falló: {e}")

    html_body = None
    if _is_html_response(response):
        html_body = response.strip()
    else:
        html_body = _extract_html_from_fence(response)

    if html_body:
        fname = f"louis_{_dt_mod.datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        caption = "📄 Análisis listo — abre en Safari → Compartir → Imprimir → PDF"
        telegram_send_file(telegram_token, chat_id, html_body, fname, caption=caption)
    elif pending_files:
        short = response.strip()
        if short:
            telegram_send_message(telegram_token, chat_id, short[:1500], parse_mode="Markdown")
    else:
        telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")

    for content, fname, cap in pending_files:
        try:
            telegram_send_document(telegram_token, chat_id, content, fname, caption=cap)
            log.info(f"📎 archivo enviado: {fname} ({len(content)} bytes)")
        except Exception as e:
            log.error(f"envío de archivo {fname} falló: {e}")
            telegram_send_message(telegram_token, chat_id, f"⚠️ No pude enviarte {fname}: {e}", parse_mode=None)


def _audio_bg_worker(telegram_token, chat_id, file_id, dur, api_key, system_prompt):
    """Background thread: download → transcribe → transcript → LLM → respond.
    Runs in _audio_pool so the main bridge loop stays responsive."""
    # Directorio persistente: si el servicio se reinicia durante la transcripción
    # el archivo queda en disco y puede recuperarse manualmente.
    audio_dir = HOME_OC / "state" / "audio-pending"
    audio_dir.mkdir(parents=True, exist_ok=True)
    saved_path = audio_dir / f"audio_{int(time.time())}_{file_id[:16]}.ogg"
    try:
        # 1. Descargar y guardar en ubicación persistente
        file_path = telegram_get_file_path(telegram_token, file_id)
        telegram_download_file(telegram_token, file_path, saved_path)
        log.info(f"Audio guardado en {saved_path} ({dur}s)")

        # 2. Transcribir — lanzar timer de progreso cada 5 min mientras corre
        _stop_progress = threading.Event()
        def _progress_ping():
            for i in range(1, 6):  # máx 5 pings = 25 min antes de desistir
                if _stop_progress.wait(timeout=300):
                    return
                elapsed = i * 5
                telegram_send_message(telegram_token, chat_id,
                    f"⏳ Aún transcribiendo el audio ({elapsed} min transcurridos)...",
                    parse_mode=None)
        _progress_thread = threading.Thread(target=_progress_ping, daemon=True)
        _progress_thread.start()
        try:
            transcript = transcribe_audio(saved_path, duration_s=dur)
        finally:
            _stop_progress.set()
        log.info(f"Transcripción (bg): {transcript[:200]}")

        # 3. Detectar fallo de Whisper — NO pasar un mensaje de error al LLM
        _FAIL = ("(no pude transcribir", "(error:", "(error convirtiendo", "(error: whisper", "(error: ffmpeg")
        if any(transcript.lower().startswith(m.lower()) for m in _FAIL) or not transcript.strip():
            log.error(f"Transcripción fallida para {saved_path}: {transcript[:120]}")
            telegram_send_message(
                telegram_token, chat_id,
                f"❌ No pude transcribir el audio ({dur // 60}:{dur % 60:02d} min). "
                f"El servidor de voz falló o el audio llegó dañado.\n"
                f"🔁 Vuelve a mandarlo — corre en segundo plano y no bloquea el chat.",
                parse_mode=None,
            )
            saved_path.unlink(missing_ok=True)
            return

        # 4. Confirmar transcript al usuario
        telegram_send_message(
            telegram_token, chat_id,
            f"📝 Te escuché:\n«{transcript}»",
            parse_mode=None,
        )

        # 5. Para grabaciones largas (≥2 min): extracción estructurada — CONFIRMAR antes de crear
        es_grabacion = dur >= 120
        if es_grabacion:
            min_s = f"{dur // 60} min {dur % 60}s"
            user_input = (
                f"[GRABACIÓN DE REUNIÓN/CONVERSACIÓN — {min_s}]\n\n"
                f"TRANSCRIPCIÓN COMPLETA:\n{transcript}\n\n"
                f"INSTRUCCIÓN (en este orden ESTRICTO):\n"
                f"1. Resume en 3-5 bullets: qué se trató, quiénes participaron "
                f"(si se mencionan), decisiones tomadas.\n"
                f"2. Lista las tareas/compromisos identificados con: qué, responsable "
                f"(si no se menciona asumir Polo), deadline (si no hay, sin fecha), "
                f"proyecto/empresa.\n"
                f"3. IMPORTANTE: NO crees las tareas todavía. Termina con: "
                f"'¿Creo estas N tareas en Kawiil Central? Responde *sí* para confirmar "
                f"o dime qué cambiar.' — y espera confirmación explícita de Polo "
                f"ANTES de llamar kawiil_central_crear_tarea."
            )
        else:
            user_input = transcript

        # 6. Responder con LLM — audio ya procesado, borrar archivo persistente
        saved_path.unlink(missing_ok=True)
        _finish_user_input(telegram_token, chat_id, api_key, system_prompt, user_input)

    except Exception as e:
        log.exception(f"Error en audio background worker (file_id={file_id})")
        telegram_send_message(telegram_token, chat_id,
                              f"❌ Error procesando audio: {e}\n"
                              f"🔁 Vuelve a mandarlo.", parse_mode=None)
        saved_path.unlink(missing_ok=True)


def process_update(update, telegram_token, chat_id, api_key, system_prompt):
    msg = update.get("message")
    if not msg:
        return

    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        log.warning(f"Mensaje de chat distinto ({msg.get('chat', {}).get('id')}), ignorando.")
        return

    text = msg.get("text")
    # Comando directo (sin LLM): panel de pendientes con botones.
    if text and text.strip().lower() in ("/agenda", "/pendientes", "pendientes", "/tareas", "/hoy"):
        ptxt, pmk = _agenda_panel()
        telegram_send_panel(telegram_token, chat_id, ptxt, pmk)
        return
    voice = msg.get("voice")
    audio = msg.get("audio")
    caption = msg.get("caption")
    photo = msg.get("photo")
    document = msg.get("document")
    video = msg.get("video")
    animation = msg.get("animation")

    user_input = None

    if text:
        user_input = text
        log.info(f"Texto recibido: {text[:100]}")
    elif photo:
        # FOTOS → multimodal con Claude Sonnet vision
        try:
            telegram_send_message(telegram_token, chat_id, "🖼️ Analizando imagen...", parse_mode=None)
            # photo es array de tamaños; tomamos la más grande (último elemento)
            largest = photo[-1]
            file_id = largest["file_id"]
            file_path = telegram_get_file_path(telegram_token, file_id)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            telegram_download_file(telegram_token, file_path, tmp_path)
            import base64
            img_b64 = base64.b64encode(tmp_path.read_bytes()).decode("ascii")
            tmp_path.unlink(missing_ok=True)
            media_type = "image/jpeg"  # Telegram siempre re-encodea a JPG
            sys_prompt = core.load_system_prompt(channel="telegram")
            log.info(f"Vision call (caption: {(caption or '')[:80]})")
            response = core.call_claude_with_image(api_key, sys_prompt, img_b64, media_type, caption or "")
            core.append_history(HISTORY_FILE, "user", f"[foto] {caption or '(sin caption)'}")
            # Guardar resumen corto para no re-disparar análisis en el siguiente mensaje de texto
            resumen = response[:300] + "…" if len(response) > 300 else response
            core.append_history(HISTORY_FILE, "assistant", f"[análisis de imagen] {resumen}")
            telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")
        except Exception as e:
            log.exception("Error procesando foto")
            telegram_send_message(telegram_token, chat_id, f"❌ Error analizando imagen: {e}", parse_mode=None)
        return
    elif document and caption:
        # Documento con caption — procesa el caption (todavía no leemos el doc)
        log.info(f"Documento con caption: {caption[:100]}")
        telegram_send_message(
            telegram_token, chat_id,
            "📄 Recibí un documento. Por ahora trabajo con el caption — para analizar contenido de PDFs/Word mándalo por correo o cópiame el texto.",
            parse_mode=None,
        )
        user_input = caption
    elif (video or animation) and caption:
        kind = "video" if video else "GIF"
        log.info(f"{kind} con caption: {caption[:100]}")
        user_input = caption
    elif document or video or animation:
        kind = "documento" if document else ("video" if video else "GIF")
        telegram_send_message(
            telegram_token, chat_id,
            f"📎 Recibí un {kind} sin caption — agrégale texto explicando qué quieres que haga.",
            parse_mode=None,
        )
        return
    elif voice or audio:
        a = voice or audio
        file_id = a["file_id"]
        dur = int(a.get("duration") or 0)
        log.info(f"Audio recibido (file_id={file_id}, dur={dur}s)")
        # Aviso de progreso proporcional al largo: en audios largos la transcripción
        # tarda, así que avisamos para que no parezca colgado.
        es_grabacion = dur >= 120  # 2+ min = grabación de reunión, no nota rápida
        if dur >= 90:
            nota_extra = (" Al terminar extraeré las tareas y compromisos." if es_grabacion else "")
            aviso = (f"🎙️ Audio de ~{dur // 60}:{dur % 60:02d} min — transcribiendo, "
                     f"puede tardar un poco.{nota_extra} Te aviso al terminar…")
        else:
            aviso = "🎙️ Transcribiendo audio..."
        telegram_send_message(telegram_token, chat_id, aviso, parse_mode=None)
        # Transcripción en background: el main loop SIGUE procesando mensajes mientras
        # Whisper trabaja. Sin esto, un audio de 4 min podía bloquear el bridge ~20 min.
        _audio_pool.submit(
            _audio_bg_worker,
            telegram_token, chat_id, file_id, dur, api_key, system_prompt
        )
        return  # main loop libre para seguir procesando
    else:
        log.info(f"Mensaje sin texto/audio, ignorando: {msg}")
        return

    _finish_user_input(telegram_token, chat_id, api_key, system_prompt, user_input)


def _cleanup_stranded_audio(telegram_token, chat_id):
    """Al arrancar: notifica audios que quedaron pendientes de sesiones anteriores."""
    audio_dir = HOME_OC / "state" / "audio-pending"
    if not audio_dir.exists():
        return
    stale = list(audio_dir.glob("*.ogg")) + list(audio_dir.glob("*.oga"))
    if not stale:
        return
    for f in stale:
        try:
            f.unlink()
        except Exception:
            pass
    n = len(stale)
    telegram_send_message(
        telegram_token, chat_id,
        f"⚠️ El servicio se reinició mientras procesaba {n} audio(s) — la transcripción se interrumpió.\n"
        f"🔁 Vuelve a mandar el/los audio(s) para procesarlos.",
        parse_mode=None,
    )
    log.info(f"Limpiados {n} audio(s) pendientes de sesión anterior")


def main():
    log.info("=== Telegram bridge v3 arrancando (louis_core + Markdown fix) ===")
    telegram_token, chat_id = load_credentials()
    api_key = core.load_anthropic_key()
    log.info(f"Ollama: {core.OLLAMA_BASE} ({core.OLLAMA_MODEL})  |  Claude: {core.CLAUDE_MODEL}")
    log.info("Credenciales cargadas. Entrando a long polling.")

    _cleanup_stranded_audio(telegram_token, chat_id)

    offset = get_offset()
    system_prompt = core.load_system_prompt(channel="telegram")
    msgs_since_reload = 0

    while True:
        try:
            resp = telegram_get_updates(telegram_token, offset)
            if resp is None:
                time.sleep(3)
                continue
            if not resp.get("ok"):
                log.warning(f"getUpdates no-ok: {resp}")
                time.sleep(5)
                continue
            for update in resp.get("result", []):
                update_id = update["update_id"]
                offset = update_id + 1
                set_offset(offset)
                # Clic de botón → manejo directo, sin modelo ni tokens.
                if update.get("callback_query"):
                    try:
                        handle_callback(update["callback_query"], telegram_token, chat_id)
                    except Exception:
                        log.exception("Error en callback")
                    continue
                if msgs_since_reload >= 5:
                    system_prompt = core.load_system_prompt(channel="telegram")
                    msgs_since_reload = 0
                msgs_since_reload += 1
                try:
                    process_update(update, telegram_token, chat_id, api_key, system_prompt)
                except Exception as e:
                    log.exception("Error procesando update")
                    telegram_send_message(telegram_token, chat_id, f"❌ Error interno: {e}", parse_mode=None)
        except KeyboardInterrupt:
            log.info("Interrumpido, saliendo.")
            break
        except Exception as e:
            log.exception(f"Loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
