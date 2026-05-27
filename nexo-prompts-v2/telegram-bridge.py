#!/usr/bin/env python3
"""
Telegram bridge para Louis (Nexo).

Long polling de Telegram → procesamiento (texto/audio) → Claude Sonnet → respuesta.

Vive como LaunchAgent. Logs en ~/.openclaw/logs/telegram-bridge.log.
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
from datetime import datetime
from urllib.parse import urlencode
import urllib.request
import urllib.error

# ===== Configuración =====
HOME = Path.home()
HOME_OC = HOME / ".openclaw"
SPACE = HOME_OC / "spaces" / "general"
CREDS_TELEGRAM = HOME_OC / "credentials" / "telegram.env"
OFFSET_FILE = HOME_OC / "credentials" / "telegram-bridge-offset.txt"
HISTORY_FILE = SPACE / "telegram-history.jsonl"
ANTHROPIC_ENV_FILE = HOME_OC / ".env"
LOG_DIR = HOME_OC / "logs"
LOG_FILE = LOG_DIR / "telegram-bridge.log"

WHISPER_MODEL = HOME_OC / "whisper-models" / "ggml-medium.bin"
# Auto-detectar el binario de whisper
WHISPER_CANDIDATES = [
    "/opt/homebrew/bin/whisper-cli",
    "/opt/homebrew/bin/whisper-cpp",
    "/usr/local/bin/whisper-cli",
    "/usr/local/bin/whisper-cpp",
]
FFMPEG_CANDIDATES = [
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
]

MAX_HISTORY_TURNS = 20
CLAUDE_MODEL = "claude-sonnet-4-6"  # mismo modelo que usa OpenClaw para Louis
ANTHROPIC_API_BASE = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

LONG_POLL_TIMEOUT = 25  # segundos

# ===== Setup logging =====
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bridge")


# ===== Helpers =====
def find_binary(candidates):
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def load_env_file(path: Path) -> dict:
    """Lee un archivo tipo KEY=value o export KEY=value y devuelve dict."""
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
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def load_credentials():
    creds = load_env_file(CREDS_TELEGRAM)
    token = creds.get("TELEGRAM_BOT_TOKEN")
    chat_id = creds.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error(f"Faltan credenciales en {CREDS_TELEGRAM}")
        sys.exit(1)
    return token, chat_id


def load_anthropic_key():
    env = load_env_file(ANTHROPIC_ENV_FILE)
    key = env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.error(f"No encontré ANTHROPIC_API_KEY en {ANTHROPIC_ENV_FILE} ni en env")
        sys.exit(1)
    return key


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


def http_post_json(url: str, headers: dict, body: dict, timeout: int = 120):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log.error(f"HTTP {e.code} en POST {url}: {err_body}")
        raise


# ===== Telegram API =====
def telegram_get_updates(token: str, offset: int):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": LONG_POLL_TIMEOUT, "allowed_updates": json.dumps(["message"])}
    full = f"{url}?{urlencode(params)}"
    try:
        raw = http_get(full, timeout=LONG_POLL_TIMEOUT + 5)
        return json.loads(raw)
    except Exception as e:
        log.warning(f"getUpdates falló: {e}")
        return None


def telegram_send_message(token: str, chat_id: str, text: str, parse_mode: str = None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram limita 4096 chars por mensaje. Si más, parto en chunks.
    chunks = []
    while text:
        chunks.append(text[:4000])
        text = text[4000:]
    for chunk in chunks:
        body = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            body["parse_mode"] = parse_mode
        try:
            http_post_json(url, headers={}, body=body, timeout=15)
        except Exception as e:
            log.error(f"sendMessage falló: {e}")


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
        # ogg → wav 16kHz mono
        r = subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(audio_ogg), "-ar", "16000", "-ac", "1", str(wav_path)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            log.error(f"ffmpeg falló: {r.stderr}")
            return "(error convirtiendo audio)"

        # whisper transcribe
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
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            log.error(f"whisper falló: {r.stderr}")
            return "(error transcribiendo)"

        txt_path = Path(td) / "transcript.txt"
        if txt_path.exists():
            return txt_path.read_text().strip()
        return r.stdout.strip()


# ===== Memoria estructurada =====
MEMORY_FILES = ["USER.md", "AGENDA.md", "LEARNINGS.md", "IMPORTANT.md", "PROJECTS.md", "PEOPLE.md"]


def load_system_prompt() -> str:
    """Concatena AGENTS.md + memoria estructurada."""
    parts = []
    agents = SPACE / "AGENTS.md"
    if agents.exists():
        parts.append(agents.read_text())
    parts.append("\n\n# CONTEXTO DE MEMORIA (archivos vivos)\n")
    for fname in MEMORY_FILES:
        path = SPACE / fname
        if path.exists():
            parts.append(f"\n## {fname}\n```\n{path.read_text()}\n```\n")
    parts.append(
        "\n\n# CANAL ACTUAL\nEstás respondiendo por Telegram (canal de chat libre con Polo). "
        "Las respuestas pueden ser largas y formales — Polo prefiere ese tono por defecto. "
        "Si Polo te dice que las quiere más cortas o de cierta forma, guárdalo en LEARNINGS.md "
        "via el tool save_learning. Si recibes un audio transcrito, considera que puede tener errores "
        "de transcripción (palabras técnicas como 'FIATCOIN', 'LFPIORPI', 'Kawiil', 'Yoltik' pueden "
        "venir mal escritas — corrígelas mentalmente al interpretar)."
    )
    return "\n".join(parts)


# ===== Historia de conversación =====
def load_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    msgs = []
    for line in HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except Exception:
            pass
    return msgs[-MAX_HISTORY_TURNS:]


def append_history(role: str, content):
    """content puede ser string o lista (para tool calls)."""
    entry = {
        "ts": datetime.now().isoformat(),
        "role": role,
        "content": content,
    }
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ===== Tools que Louis puede usar =====
TOOLS_DEFINITION = [
    {
        "name": "read_memory",
        "description": "Lee un archivo de memoria de Louis (AGENDA.md, USER.md, LEARNINGS.md, JOURNAL.md, IMPORTANT.md, PROJECTS.md, PEOPLE.md). Usa solo nombres permitidos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "enum": MEMORY_FILES + ["JOURNAL.md"],
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "write_memory",
        "description": "Sobrescribe completamente un archivo de memoria. Úsalo solo si vas a reemplazar todo el contenido (raro). Para agregar usa append_to_memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "enum": MEMORY_FILES + ["JOURNAL.md"]},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "append_to_memory",
        "description": "Agrega contenido al final de un archivo de memoria. Útil para agregar entradas a AGENDA, LEARNINGS, JOURNAL, PEOPLE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "enum": MEMORY_FILES + ["JOURNAL.md"]},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_reminder",
        "description": "Crea un Reminder en iCloud (sincroniza al iPhone de Polo como notificación nativa). Úsalo para alarmas programadas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "datetime": {
                    "type": "string",
                    "description": "Formato YYYY-MM-DD HH:MM en hora local de México",
                },
            },
            "required": ["text", "datetime"],
        },
    },
    {
        "name": "save_learning",
        "description": "Agrega entrada a LEARNINGS.md cuando descubres preferencia, regla o patrón de Polo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Tema breve, ej: 'estilo correos', 'horario'"},
                "rule": {"type": "string", "description": "Regla aprendida"},
                "context": {"type": "string", "description": "Qué pasó que generó esta regla"},
            },
            "required": ["topic", "rule", "context"],
        },
    },
]


def execute_tool(name: str, args: dict) -> str:
    """Ejecuta un tool y devuelve resultado como string."""
    try:
        if name == "read_memory":
            path = SPACE / args["filename"]
            if not path.exists():
                return f"(archivo {args['filename']} no existe aún)"
            return path.read_text()

        elif name == "write_memory":
            path = SPACE / args["filename"]
            path.write_text(args["content"])
            return f"OK escrito {args['filename']}"

        elif name == "append_to_memory":
            path = SPACE / args["filename"]
            with path.open("a") as f:
                f.write("\n" + args["content"] + "\n")
            return f"OK agregado a {args['filename']}"

        elif name == "create_reminder":
            script = SPACE / "scripts" / "crear-recordatorio.sh"
            r = subprocess.run(
                [str(script), args["text"], args["datetime"]],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                return f"OK recordatorio creado: {r.stdout.strip()}"
            return f"ERROR creando recordatorio: {r.stderr.strip()}"

        elif name == "save_learning":
            path = SPACE / "LEARNINGS.md"
            entry = (
                f"\n- [{datetime.now().strftime('%Y-%m-%d')}] {args['topic']}: {args['rule']}\n"
                f"  Contexto: {args['context']}\n"
            )
            with path.open("a") as f:
                f.write(entry)
            return f"OK aprendido: {args['topic']}"

        else:
            return f"ERROR: tool desconocido {name}"
    except Exception as e:
        log.exception(f"Tool {name} falló")
        return f"ERROR ejecutando {name}: {e}"


# ===== Claude call =====
def call_claude(api_key: str, system_prompt: str, history: list, user_message: str) -> str:
    """Llama a Claude con tools. Loop interno hasta que Claude termine."""

    # Convertir history del bridge a formato anthropic messages
    messages = []
    for h in history:
        # Solo turnos de usuario/asistente sencillos para historia
        if h["role"] in ("user", "assistant") and isinstance(h["content"], str):
            messages.append({"role": h["role"], "content": h["content"]})

    # Append turno actual
    messages.append({"role": "user", "content": user_message})

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }

    max_loops = 8
    final_text = ""

    for _ in range(max_loops):
        body = {
            "model": CLAUDE_MODEL,
            "max_tokens": 4096,
            "system": system_prompt,
            "tools": TOOLS_DEFINITION,
            "messages": messages,
        }

        try:
            resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=180)
        except Exception as e:
            log.exception("Anthropic API falló")
            return f"(error llamando a Claude: {e})"

        # Extraer contenido
        content = resp.get("content", [])
        stop_reason = resp.get("stop_reason")

        # Acumular texto y procesar tool calls
        assistant_blocks = []
        tool_calls = []
        for block in content:
            assistant_blocks.append(block)
            if block.get("type") == "text":
                final_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(block)

        messages.append({"role": "assistant", "content": assistant_blocks})

        if stop_reason != "tool_use" or not tool_calls:
            break

        # Ejecutar tools y agregar resultados
        tool_results = []
        for tc in tool_calls:
            result = execute_tool(tc["name"], tc.get("input", {}))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": result,
            })
        messages.append({"role": "user", "content": tool_results})
        final_text = ""  # reset, Claude va a generar texto final después

    return final_text.strip() or "(sin respuesta)"


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

    user_input = None

    if text:
        user_input = text
        log.info(f"Texto recibido: {text[:100]}")

    elif voice or audio:
        a = voice or audio
        file_id = a["file_id"]
        log.info(f"Audio recibido (file_id={file_id})")
        # Notificar a Polo que estamos procesando
        telegram_send_message(telegram_token, chat_id, "🎙️ Transcribiendo audio...")
        try:
            file_path = telegram_get_file_path(telegram_token, file_id)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            telegram_download_file(telegram_token, file_path, tmp_path)
            transcript = transcribe_audio(tmp_path)
            tmp_path.unlink(missing_ok=True)
            log.info(f"Transcripción: {transcript[:200]}")
            telegram_send_message(
                telegram_token, chat_id, f"📝 Te escuché:\n«{transcript}»"
            )
            user_input = transcript
        except Exception as e:
            log.exception("Error procesando audio")
            telegram_send_message(telegram_token, chat_id, f"❌ Error procesando audio: {e}")
            return
    else:
        log.info(f"Mensaje sin texto/audio, ignorando: {msg}")
        return

    if not user_input or not user_input.strip():
        return

    # Guardar en history y llamar a Claude
    append_history("user", user_input)
    history = load_history()[:-1]  # excluye el actual que acabamos de meter
    response = call_claude(api_key, system_prompt, history, user_input)
    append_history("assistant", response)

    telegram_send_message(telegram_token, chat_id, response)


def main():
    log.info("=== Telegram bridge arrancando ===")
    telegram_token, chat_id = load_credentials()
    api_key = load_anthropic_key()
    log.info("Credenciales cargadas. Entrando a long polling.")

    offset = get_offset()
    # System prompt se carga cada N mensajes para tomar cambios en memoria
    system_prompt = load_system_prompt()
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

            results = resp.get("result", [])
            for update in results:
                update_id = update["update_id"]
                offset = update_id + 1
                set_offset(offset)

                # Reload prompt cada 5 mensajes para tomar cambios en memoria
                if msgs_since_reload >= 5:
                    system_prompt = load_system_prompt()
                    msgs_since_reload = 0
                msgs_since_reload += 1

                try:
                    process_update(update, telegram_token, chat_id, api_key, system_prompt)
                except Exception as e:
                    log.exception("Error procesando update")
                    telegram_send_message(
                        telegram_token, chat_id, f"❌ Error interno: {e}"
                    )

        except KeyboardInterrupt:
            log.info("Interrumpido, saliendo.")
            break
        except Exception as e:
            log.exception(f"Loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
