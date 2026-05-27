#!/usr/bin/env python3
"""
Slack bridge para Louis (Nexo) — Hetzner.

Usa Socket Mode (slack_bolt) — sin webhooks públicos.
Toda la lógica de routing, tools y M365 está en louis_core.py.

Reacciona a:
  - app_mention en cualquier canal donde esté el bot
  - message.im (DM directo al bot)
  - opcionalmente: SLACK_ALLOWED_CHANNELS para filtrar

Credenciales esperadas en /opt/openclaw/credentials/slack.env:
  SLACK_BOT_TOKEN=xoxb-...
  SLACK_APP_TOKEN=xapp-...
  SLACK_SIGNING_SECRET=...   (opcional, no se usa en Socket Mode pero por completitud)
  SLACK_ALLOWED_USERS=U123,U456   (opcional, whitelist de usuarios)
"""

import os
import sys
import base64
import tempfile
import logging
import subprocess
import urllib.request
from pathlib import Path

# slack_bolt + slack_sdk — se instalan via bootstrap (pip)
try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
except ImportError:
    sys.stderr.write(
        "ERROR: slack_bolt no está instalado. Instala con:\n"
        "  pip install --break-system-packages slack-bolt slack-sdk\n"
    )
    sys.exit(1)

# Importa lógica común
sys.path.insert(0, str(Path(__file__).parent))
import louis_core as core

# ===== Paths =====
# Igual que en louis_core: usar /opt/openclaw cuando exista (Hetzner runtime).
HOME = Path.home()
if Path("/opt/openclaw").exists():
    HOME_OC = Path("/opt/openclaw")
else:
    HOME_OC = HOME / ".openclaw"
SPACE = HOME_OC / "spaces" / "general"
CREDS_SLACK = HOME_OC / "credentials" / "slack.env"
HISTORY_FILE = SPACE / "slack-history.jsonl"
LOG_DIR = HOME_OC / "logs"
LOG_FILE = LOG_DIR / "slack-bridge.log"

# ===== Logging =====
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("slack-bridge")
core.log = log


def load_credentials():
    creds = core.load_env_file(CREDS_SLACK)
    bot_token = creds.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")
    app_token = creds.get("SLACK_APP_TOKEN") or os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        log.error(
            f"Faltan SLACK_BOT_TOKEN (xoxb-...) o SLACK_APP_TOKEN (xapp-...) en {CREDS_SLACK} o env"
        )
        sys.exit(1)
    allowed_users = [
        u.strip() for u in (creds.get("SLACK_ALLOWED_USERS") or "").split(",") if u.strip()
    ]
    return bot_token, app_token, allowed_users


def main():
    log.info("=== Slack bridge v1 arrancando (Socket Mode + louis_core) ===")
    bot_token, app_token, allowed_users = load_credentials()
    api_key = core.load_anthropic_key()
    log.info(f"Ollama: {core.OLLAMA_BASE} ({core.OLLAMA_MODEL})  |  Claude: {core.CLAUDE_MODEL}")
    if allowed_users:
        log.info(f"Whitelist usuarios: {allowed_users}")
    else:
        log.info("Sin whitelist — responde a cualquier usuario en canales donde esté el bot")

    app = App(token=bot_token)
    # Cache simple del bot_user_id para ignorar self-messages
    bot_user_id = None
    try:
        auth = app.client.auth_test()
        bot_user_id = auth.get("user_id")
        log.info(f"Bot ID: {bot_user_id} ({auth.get('user')}) en team {auth.get('team')}")
    except Exception as e:
        log.warning(f"auth_test falló (continúo igual): {e}")

    def _download_slack_file(url: str, dest: Path):
        """Slack files requieren Bearer token para descargar."""
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {bot_token}")
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())

    def _process_file(slack_file: dict, prompt_text: str) -> str:
        """Procesa un archivo de Slack — imagen → vision, audio → whisper."""
        url = slack_file.get("url_private_download") or slack_file.get("url_private")
        mimetype = (slack_file.get("mimetype") or "").lower()
        if not url:
            return f"(no pude obtener URL del archivo {slack_file.get('name','?')})"
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(slack_file.get("name","f.bin")).suffix or ".bin") as tmp:
            tmp_path = Path(tmp.name)
        try:
            _download_slack_file(url, tmp_path)
            if mimetype.startswith("image/"):
                # Vision con Claude
                img_b64 = base64.b64encode(tmp_path.read_bytes()).decode("ascii")
                media_type = mimetype if mimetype in ("image/jpeg","image/png","image/gif","image/webp") else "image/jpeg"
                sys_prompt = core.load_system_prompt(channel="slack")
                return core.call_claude_with_image(api_key, sys_prompt, img_b64, media_type, prompt_text or "")
            elif mimetype.startswith("audio/") or mimetype in ("video/mp4", "video/webm"):
                # Transcribir con whisper
                whisper_bin = "/usr/local/bin/whisper-cli"
                ffmpeg_bin = "/usr/bin/ffmpeg"
                whisper_model = HOME_OC / "whisper-models" / "ggml-medium.bin"
                if not Path(whisper_bin).exists() or not whisper_model.exists():
                    return "(audio recibido pero whisper no está instalado en este servidor)"
                with tempfile.TemporaryDirectory() as td:
                    wav = Path(td) / "audio.wav"
                    subprocess.run([ffmpeg_bin,"-y","-i",str(tmp_path),"-ar","16000","-ac","1",str(wav)], capture_output=True, timeout=60)
                    r = subprocess.run(
                        [whisper_bin,"-m",str(whisper_model),"-f",str(wav),"-l","es","-otxt","-of",str(Path(td)/"t"),"--no-prints"],
                        capture_output=True, text=True, timeout=180,
                    )
                    tx_path = Path(td) / "t.txt"
                    transcript = tx_path.read_text().strip() if tx_path.exists() else r.stdout.strip()
                # Devuelve transcript + procesa como texto normal vía LLM
                if not transcript:
                    return "(no logré transcribir el audio)"
                composed = (prompt_text + "\n\n" if prompt_text else "") + f"Transcripción del audio: {transcript}"
                # Procesar con Haiku (o Claude si tiene tools)
                sys_prompt = core.load_system_prompt(channel="slack")
                resp, _ = core.call_llm(api_key, sys_prompt, [], composed)
                return f"📝 *Te escuché:* «{transcript}»\n\n{resp}"
            else:
                return f"(archivo {slack_file.get('name','?')} tipo {mimetype} aún no se procesa)"
        finally:
            tmp_path.unlink(missing_ok=True)

    def _handle(text: str, user: str, channel: str, thread_ts: str = None) -> str:
        """Procesa el texto y devuelve respuesta formateada para Slack."""
        if not text or not text.strip():
            return None
        # Quita la mención al bot del inicio si vino con @
        if bot_user_id:
            text = text.replace(f"<@{bot_user_id}>", "").strip()
        cleaned = core.strip_override_prefix(text)

        # Historia: agregamos el mensaje crudo (texto del usuario)
        core.append_history(HISTORY_FILE, "user", text)
        history = core.load_history(HISTORY_FILE)[:-1]

        system_prompt = core.load_system_prompt(channel="slack")
        # Pequeño hint para el LLM sobre el contexto Slack
        ctx_hint = (
            f"\n\n# CONTEXTO DEL MENSAJE\n"
            f"Usuario Slack: <@{user}>\n"
            f"Canal: {channel}\n"
        )
        system_prompt = system_prompt + ctx_hint

        response, model_used = core.call_llm(api_key, system_prompt, history, cleaned or text)
        log.info(f"← {model_used} respondió a {user} en {channel} ({len(response)} chars)")
        core.append_history(HISTORY_FILE, "assistant", response)
        return core.format_for_slack(response)

    def _allowed(user: str) -> bool:
        if not allowed_users:
            return True
        return user in allowed_users

    @app.event("app_mention")
    def on_mention(event, say, logger):
        user = event.get("user")
        text = event.get("text", "")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        files = event.get("files") or []
        if user == bot_user_id:
            return
        if not _allowed(user):
            log.warning(f"Usuario no autorizado {user} en {channel}, ignorando")
            return
        log.info(f"@mention de {user} en {channel}: {text[:100]} (files: {len(files)})")
        try:
            if files:
                for f in files:
                    resp = _process_file(f, text)
                    if resp:
                        say(text=core.format_for_slack(resp), thread_ts=thread_ts, mrkdwn=True)
                return
            response = _handle(text, user, channel, thread_ts)
            if response:
                say(text=response, thread_ts=thread_ts, mrkdwn=True)
        except Exception as e:
            log.exception("Error procesando app_mention")
            try:
                say(text=f":x: Error interno: {e}", thread_ts=thread_ts)
            except Exception:
                pass

    @app.event("message")
    def on_message(event, say, logger):
        # Solo procesa DMs (channel_type == "im") — los mensajes en canales públicos
        # los manejamos vía app_mention
        if event.get("channel_type") != "im":
            return
        if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
            return
        user = event.get("user")
        if not user or user == bot_user_id:
            return
        if not _allowed(user):
            log.warning(f"DM de usuario no autorizado {user}, ignorando")
            return
        text = event.get("text", "")
        channel = event.get("channel")
        files = event.get("files") or []
        log.info(f"DM de {user}: {text[:100]} (files: {len(files)})")
        try:
            if files:
                for f in files:
                    resp = _process_file(f, text)
                    if resp:
                        say(text=core.format_for_slack(resp), mrkdwn=True)
                return
            response = _handle(text, user, channel)
            if response:
                say(text=response, mrkdwn=True)
        except Exception as e:
            log.exception("Error procesando DM")
            try:
                say(text=f":x: Error interno: {e}")
            except Exception:
                pass

    # Socket Mode
    handler = SocketModeHandler(app, app_token)
    log.info("Conectando Socket Mode...")
    handler.start()


if __name__ == "__main__":
    main()
