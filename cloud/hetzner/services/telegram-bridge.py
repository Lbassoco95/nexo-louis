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
import json
import time
import subprocess
import tempfile
import shutil
import logging
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

# Importa la lógica común
sys.path.insert(0, str(Path(__file__).parent))
import louis_core as core

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
        "allowed_updates": json.dumps(["message"]),
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
    Manda mensaje a Telegram. Aplica format_for_telegram() para convertir
    `**bold**` → `*bold*` (Markdown legacy de Telegram).

    Si el render con Markdown falla (caracteres inválidos), reintenta sin parse_mode.
    """
    formatted = core.format_for_telegram(text) if parse_mode else text
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = []
    remaining = formatted
    while remaining:
        chunks.append(remaining[:4000])
        remaining = remaining[4000:]
    for chunk in chunks:
        body = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if parse_mode:
            body["parse_mode"] = parse_mode
        try:
            core.http_post_json(url, headers={}, body=body, timeout=15)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if parse_mode and ("parse" in err_body.lower() or "entity" in err_body.lower() or e.code == 400):
                log.warning(f"Markdown falló ({err_body[:200]}), reintentando sin parse_mode")
                body.pop("parse_mode", None)
                body["text"] = chunk  # texto raw, no el formatted
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
def transcribe_audio(audio_ogg: Path) -> str:
    whisper_bin = find_binary(WHISPER_CANDIDATES)
    ffmpeg_bin = find_binary(FFMPEG_CANDIDATES)
    if not whisper_bin:
        return "(error: whisper no instalado)"
    if not ffmpeg_bin:
        return "(error: ffmpeg no instalado)"
    if not WHISPER_MODEL.exists():
        return f"(error: modelo whisper no encontrado en {WHISPER_MODEL})"

    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "audio.wav"
        r = subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(audio_ogg), "-ar", "16000", "-ac", "1", str(wav_path)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error(f"ffmpeg falló: {r.stderr}")
            return "(error convirtiendo audio)"

        r = subprocess.run(
            [
                whisper_bin,
                "-m", str(WHISPER_MODEL),
                "-f", str(wav_path),
                "-l", "es",
                "-otxt",
                "-of", str(Path(td) / "transcript"),
                "--no-prints",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            log.error(f"whisper falló: {r.stderr}")
            return "(error transcribiendo)"

        txt_path = Path(td) / "transcript.txt"
        if txt_path.exists():
            return txt_path.read_text().strip()
        return r.stdout.strip()


# ===== Main loop =====
def process_update(update, telegram_token, chat_id, api_key, system_prompt):
    msg = update.get("message")
    if not msg:
        return

    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        log.warning(f"Mensaje de chat distinto ({msg.get('chat', {}).get('id')}), ignorando.")
        return

    text = msg.get("text")
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
            core.append_history(HISTORY_FILE, "assistant", response)
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
        log.info(f"Audio recibido (file_id={file_id})")
        telegram_send_message(telegram_token, chat_id, "🎙️ Transcribiendo audio...", parse_mode=None)
        try:
            file_path = telegram_get_file_path(telegram_token, file_id)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            telegram_download_file(telegram_token, file_path, tmp_path)
            transcript = transcribe_audio(tmp_path)
            tmp_path.unlink(missing_ok=True)
            log.info(f"Transcripción: {transcript[:200]}")
            telegram_send_message(
                telegram_token, chat_id,
                f"📝 Te escuché:\n«{transcript}»",
                parse_mode=None,
            )
            user_input = transcript
        except Exception as e:
            log.exception("Error procesando audio")
            telegram_send_message(telegram_token, chat_id, f"❌ Error procesando audio: {e}", parse_mode=None)
            return
    else:
        log.info(f"Mensaje sin texto/audio, ignorando: {msg}")
        return

    if not user_input or not user_input.strip():
        return

    if core.is_status_command(user_input):
        response = core._verificar_conexiones(incluir_m365=False)
        model_used = "status"
        core.append_history(HISTORY_FILE, "user", user_input)
        core.append_history(HISTORY_FILE, "assistant", response)
        log.info(f"← {model_used} ({len(response)} chars)")
        telegram_send_message(telegram_token, chat_id, response, parse_mode=None)
        return

    if core.is_agents_list_command(user_input):
        response = core.format_agents_list_compact()
        model_used = "agents-list"
        core.append_history(HISTORY_FILE, "user", user_input)
        core.append_history(HISTORY_FILE, "assistant", response)
        log.info(f"← {model_used} ({len(response)} chars)")
        telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")
        return

    core.append_history(HISTORY_FILE, "user", user_input)
    history = core.load_history(HISTORY_FILE, max_turns=16)[:-1]
    # Pasamos el mensaje CRUDO: call_llm detecta /sonnet, /oss, /haiku, /llama
    # y limpia el prefijo internamente antes de llamar al modelo.
    response, model_used = core.call_llm(
        api_key, system_prompt, history, user_input, history_file=HISTORY_FILE
    )
    log.info(f"← {model_used} respondió ({len(response)} chars)")
    core.append_history(HISTORY_FILE, "assistant", response)

    # Detecta HTML completo (análisis de agentes, documentos) → envía como archivo.
    # Acepta tanto respuesta directa <html>…</html> como bloque ```html … ```.
    html_body = None
    if _is_html_response(response):
        html_body = response.strip()
    else:
        html_body = _extract_html_from_fence(response)

    import datetime as _dt_mod
    if html_body:
        fname = f"louis_{_dt_mod.datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        caption = "📄 Análisis listo (ábrelo en el navegador para mejor lectura)"
        telegram_send_file(telegram_token, chat_id, html_body, fname, caption=caption)
    elif len(response) > 2200:
        # Respuesta larga (plan, informe, dictamen) → archivo HTML en vez de texto cortado
        titulo = (user_input or "Documento")[:80].strip().rstrip(".?!")
        html_bytes = core._md_to_html(titulo, "Louis", response)
        fname = f"louis_{_dt_mod.datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        try:
            telegram_send_document(telegram_token, chat_id, html_bytes, fname,
                                   caption="📄 Te lo mando como archivo para que lo puedas compartir con el equipo (ábrelo en Safari → Compartir → Guardar como PDF)")
            log.info(f"📎 respuesta larga enviada como archivo: {fname} ({len(html_bytes)} bytes)")
        except Exception as e:
            log.warning(f"No pude enviar como archivo ({e}), mandando texto")
            telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")
    else:
        telegram_send_message(telegram_token, chat_id, response, parse_mode="Markdown")

    # Drena archivos encolados por tools (dropbox_enviar, dof_pdf) y los manda.
    for content, fname, cap in core.get_pending_files():
        try:
            telegram_send_document(telegram_token, chat_id, content, fname, caption=cap)
            log.info(f"📎 archivo enviado: {fname} ({len(content)} bytes)")
        except Exception as e:
            log.error(f"envío de archivo {fname} falló: {e}")
            telegram_send_message(telegram_token, chat_id, f"⚠️ No pude enviarte {fname}: {e}", parse_mode=None)


def main():
    log.info("=== Telegram bridge v3 arrancando (louis_core + Markdown fix) ===")
    telegram_token, chat_id = load_credentials()
    api_key = core.load_anthropic_key()
    log.info(f"Ollama: {core.OLLAMA_BASE} ({core.OLLAMA_MODEL})  |  Claude: {core.CLAUDE_MODEL}")
    log.info("Credenciales cargadas. Entrando a long polling.")

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
