#!/usr/bin/env python3
"""
louis-scheduler — proactividad de Louis.

Cada 60s revisa /opt/openclaw/reminders/queue.jsonl. Para cada entry con
`fire_at` <= now, manda el mensaje al canal (Telegram default), opcionalmente
lo "enriquece" pasando por Claude Haiku (para tono natural), y mueve la entry
a sent.jsonl.

También maneja recordatorios recurrentes (daily/weekly) re-encolándolos.

Formato de cada entry (una por línea):
{
  "id": "uuid",
  "fire_at": "2026-05-26T08:00:00-06:00",
  "message": "Briefing matutino: ¿qué tengo hoy?",
  "channel": "telegram",         # o "slack"
  "mode": "raw" | "enrich",      # raw = manda literal, enrich = pasa por Haiku
  "recurrence": null | "daily" | "weekly" | "monthly",
  "created_at": "2026-05-25T19:00:00-06:00",
  "source": "user" | "system"
}
"""

import os
import re
import sys
import json
import time
import uuid
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Importa lógica común
sys.path.insert(0, str(Path(__file__).parent))
import louis_core as core

# ===== Paths =====
HOME = Path.home()
if Path("/opt/openclaw").exists():
    HOME_OC = Path("/opt/openclaw")
else:
    HOME_OC = HOME / ".openclaw"

REMINDERS_DIR = HOME_OC / "reminders"
QUEUE_FILE = REMINDERS_DIR / "queue.jsonl"
SENT_FILE = REMINDERS_DIR / "sent.jsonl"
LOG_DIR = HOME_OC / "logs"
LOG_FILE = LOG_DIR / "scheduler.log"

CREDS_TELEGRAM = HOME_OC / "credentials" / "telegram.env"
CREDS_SLACK = HOME_OC / "credentials" / "slack.env"

# CDMX timezone
TZ_CDMX = timezone(timedelta(hours=-6))

# Tick cada 60s — fino suficiente para recordatorios al minuto
TICK_SECONDS = 60

# ===== Logging =====
LOG_DIR.mkdir(parents=True, exist_ok=True)
REMINDERS_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE.touch(exist_ok=True)
SENT_FILE.touch(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scheduler")


# ===== Send helpers =====
def send_telegram(text: str):
    creds = core.load_env_file(CREDS_TELEGRAM)
    token = creds.get("TELEGRAM_BOT_TOKEN")
    chat_id = creds.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("Faltan credenciales Telegram")
        return False
    import urllib.request
    import urllib.error
    import html as _html
    # format_for_telegram produce HTML → hay que enviar con parse_mode HTML (antes
    # decía 'Markdown', por eso el briefing salía con ** y ### en crudo).
    formatted = core.format_for_telegram(text)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat_id, "text": formatted[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        core.http_post_json(url, headers={}, body=body, timeout=15)
        return True
    except urllib.error.HTTPError:
        # Fallback: quitar tags HTML y desescapar → texto plano legible (NO el markdown crudo).
        raw = _html.unescape(re.sub(r"<[^>]+>", "", formatted))
        body.pop("parse_mode", None)
        body["text"] = raw[:4000]
        try:
            core.http_post_json(url, headers={}, body=body, timeout=15)
            return True
        except Exception as e:
            log.error(f"Telegram send falló: {e}")
            return False
    except Exception as e:
        log.error(f"Telegram send falló: {e}")
        return False


def send_slack(text: str, channel: str = None):
    """Manda al DM del bot conmigo — necesita SLACK_DEFAULT_DM_USER en slack.env o env"""
    creds = core.load_env_file(CREDS_SLACK)
    token = creds.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")
    user_id = channel or creds.get("SLACK_DEFAULT_DM_USER") or os.environ.get("SLACK_DEFAULT_DM_USER")
    if not token or not user_id:
        log.error("Faltan SLACK_BOT_TOKEN o SLACK_DEFAULT_DM_USER")
        return False
    formatted = core.format_for_slack(text)
    try:
        core.http_post_json(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            body={"channel": user_id, "text": formatted[:4000], "mrkdwn": True},
            timeout=15,
        )
        return True
    except Exception as e:
        log.error(f"Slack send falló: {e}")
        return False


# ===== Enrich con Ollama (sin Anthropic) =====
# Patrones de RECHAZO/basura que Ollama a veces devuelve en vez de reformular
# (ej. "Lo siento, no tengo permiso para reformular los datos"). Si aparecen,
# se descarta el resultado y se manda el mensaje ORIGINAL verbatim.
_REFUSAL_RE = re.compile(
    r"(no tengo permiso|lo siento|no puedo (reformular|ayudar|procesar)|"
    r"as an ai|i (cannot|can't|am sorry)|i'm sorry|no me es posible|"
    r"no estoy autorizad|seré encantado de asistirte|en qué puedo ayudarte)",
    re.IGNORECASE)


def enrich_with_ollama(raw_message: str) -> str:
    """Reformula recordatorio en tono Louis vía Ollama local. Si Ollama devuelve un
    rechazo/basura (o algo demasiado distinto/largo), manda el mensaje ORIGINAL."""
    sys_prompt = (
        "Eres Louis, asistente ejecutivo de Polo. Reformula este recordatorio en tono "
        "directo y cálido, 1-2 líneas, en español. Telegram *negrita* legacy. NO inventes "
        "hechos, NO pidas permiso, NO te disculpes: SOLO devuelve el recordatorio reformulado."
    )
    user_msg = "Reformula SIN cambiar datos (devuelve solo el texto):\n" + raw_message[:2000]
    try:
        result = core.call_ollama(sys_prompt, [], user_msg, history_file=None)
        if (result and not result.startswith("⚠️")
                and not _REFUSAL_RE.search(result)
                and len(result) <= max(400, len(raw_message) * 3)):
            return result
        log.warning("enrich descartado (rechazo/basura/largo); uso mensaje raw")
    except Exception as e:
        log.warning(f"Enrich Ollama falló: {e}")
    return raw_message


MORNING_BRIEFING_MARKER = "__morning_briefing__"

# Indexación legal en background: tick cada 10 min (10 × 60s), 10 docs por agente
LEGAL_BG_TICK_INTERVAL = 10  # cada cuántos ticks de 60s correr el bg-indexer

# Chequeos intradía (Fase 3): horas CDMX en que Louis empuja seguimiento si hay algo.
INTRADAY_SLOTS = {13: "tarde", 18: "cierre"}
INTRADAY_STATE = HOME_OC / "state" / "intraday_sent.json"


def _intraday_ya(slot: str, hoy: str) -> bool:
    try:
        d = json.loads(INTRADAY_STATE.read_text())
        return d.get("date") == hoy and slot in d.get("slots", [])
    except Exception:
        return False


def _intraday_marca(slot: str, hoy: str):
    d = {"date": hoy, "slots": []}
    try:
        old = json.loads(INTRADAY_STATE.read_text())
        if old.get("date") == hoy:
            d = old
    except Exception:
        pass
    d["slots"] = sorted(set(d.get("slots", []) + [slot]))
    try:
        INTRADAY_STATE.parent.mkdir(parents=True, exist_ok=True)
        INTRADAY_STATE.write_text(json.dumps(d, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar intraday_sent.json: {e}")


# ===== Queue I/O =====
def read_queue() -> list:
    """Lee queue.jsonl. Cada línea = un reminder dict."""
    if not QUEUE_FILE.exists():
        return []
    out = []
    for line in QUEUE_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception as e:
            log.warning(f"Línea inválida en queue: {line[:80]} ({e})")
    return out


def write_queue(items: list):
    """Reescribe el queue completo (atomic-ish)."""
    tmp = QUEUE_FILE.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    tmp.replace(QUEUE_FILE)


def append_sent(entry: dict):
    with SENT_FILE.open("a") as f:
        entry = {**entry, "sent_at": datetime.now(TZ_CDMX).isoformat()}
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ===== Recurrence =====
def next_fire(current_iso: str, recurrence: str) -> str:
    """Calcula próxima fecha para recordatorios recurrentes."""
    try:
        dt = datetime.fromisoformat(current_iso)
    except Exception:
        return None
    if recurrence == "daily":
        return (dt + timedelta(days=1)).isoformat()
    if recurrence == "weekly":
        return (dt + timedelta(weeks=1)).isoformat()
    if recurrence == "monthly":
        # +30 días (aproximación simple — no calendar-aware)
        return (dt + timedelta(days=30)).isoformat()
    if recurrence == "yearly":
        try:
            return dt.replace(year=dt.year + 1).isoformat()
        except ValueError:
            # 29-feb → 28-feb del próximo año
            return dt.replace(year=dt.year + 1, day=28).isoformat()
    return None


# ===== Main tick =====
def tick():
    now = datetime.now(TZ_CDMX)
    queue = read_queue()
    if not queue:
        return
    to_keep = []
    fired = 0
    for entry in queue:
        try:
            fire_at_str = entry.get("fire_at")
            fire_at = datetime.fromisoformat(fire_at_str)
            # Si fire_at no tiene tz, asumimos CDMX
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=TZ_CDMX)
        except Exception as e:
            log.warning(f"fire_at inválido en entry {entry.get('id')}: {e}")
            continue

        if fire_at <= now:
            # Disparar
            raw = entry.get("message", "(recordatorio sin mensaje)")
            mode = entry.get("mode", "enrich")
            channel = entry.get("channel", "telegram")
            if mode == "briefing" or raw == MORNING_BRIEFING_MARKER:
                text = core.generate_morning_briefing()
                try:
                    core.save_last_briefing(text, core.build_operational_snapshot())
                except Exception as e:
                    log.warning(f"No guardé last-briefing: {e}")
            elif mode == "enrich":
                text = enrich_with_ollama(raw)
            else:
                text = raw
            # Prefijo discreto para distinguir mensaje proactivo
            prefix = "☀️ " if mode == "briefing" or raw == MORNING_BRIEFING_MARKER else "⏰ "
            text = f"{prefix}{text}"
            if channel == "slack":
                ok = send_slack(text)
            else:
                ok = send_telegram(text)
            log.info(f"Disparado {entry.get('id')} ({channel}, mode={mode}) → ok={ok}")
            append_sent({**entry, "delivered": ok})
            fired += 1

            # Recurrencia
            rec = entry.get("recurrence")
            if rec:
                next_iso = next_fire(fire_at_str, rec)
                if next_iso:
                    to_keep.append({**entry, "fire_at": next_iso, "id": str(uuid.uuid4())})
                    log.info(f"Recurrente '{rec}' → reencolado para {next_iso}")
        else:
            to_keep.append(entry)

    if fired > 0:
        write_queue(to_keep)


def main():
    log.info("=== louis-scheduler arrancando ===")
    log.info(f"Queue: {QUEUE_FILE}")
    log.info(f"Tick cada {TICK_SECONDS}s | bg-legal cada {LEGAL_BG_TICK_INTERVAL * TICK_SECONDS}s")
    tick_count = 0
    while True:
        try:
            tick()
        except Exception as e:
            log.exception(f"Tick falló: {e}")

        tick_count += 1

        # Indexación legal en background: cada LEGAL_BG_TICK_INTERVAL ticks (≈10 min)
        # Silenciosa — sin notificar a Polo salvo que encuentre docs nuevos
        if tick_count % LEGAL_BG_TICK_INTERVAL == 0:
            try:
                r = core._legal_indexar_background_tick(limite=10)
                if r and ":+" in r:  # solo loguear si hubo docs nuevos
                    log.info(f"legal bg: {r}")
            except Exception as e:
                log.warning(f"legal bg tick falló: {e}")

        # Auto-memoria nocturna: destila la conversación del día a memoria de largo
        # plazo (PEOPLE/CLIENTES/AGENDA/IMPORTANT). Idempotente por fecha — corre una
        # sola vez aunque el tick caiga muchas veces en la ventana de las 23h. Silenciosa.
        try:
            if datetime.now(TZ_CDMX).hour == 23:
                r = core.run_memory_distillation()
                if r and r.startswith("OK"):
                    log.info(f"auto-memoria: {r}")
        except Exception as e:
            log.warning(f"auto-memoria tick falló: {e}")

        # Chequeos intradía (Fase 3): a las 13:00 y 18:00 CDMX, empuja seguimiento SOLO
        # si hay algo abierto (vencimientos de hoy, entregables listo, briefs). Una vez
        # por slot/día. Silencioso si no hay nada que reportar.
        try:
            _now = datetime.now(TZ_CDMX)
            _slot = INTRADAY_SLOTS.get(_now.hour)
            _hoy = _now.strftime("%Y-%m-%d")
            if _slot and not _intraday_ya(_slot, _hoy):
                msg = core.build_intraday_nudge(_slot)
                if msg:
                    send_telegram(msg)
                    log.info(f"intraday nudge enviado ({_slot})")
                _intraday_marca(_slot, _hoy)  # marca aunque no haya nada (no recalcular cada tick)
        except Exception as e:
            log.warning(f"intraday check falló: {e}")

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
