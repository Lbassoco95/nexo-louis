#!/usr/bin/env python3
"""
louis-scheduler — proactividad de Donna.

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
import donna_core as core

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

# Solo stdout: la unit systemd ya hace StandardOutput=append:logs/scheduler.log.
# Con un FileHandler además, CADA línea del log salía DOS veces en el archivo
# (telegram-bridge.py ya tenía este arreglo; scheduler no).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scheduler")


# ===== Canal del briefing matutino =====
# Polo recibía DOS briefings a las 07:00: el texto sintetizado por Haiku
# (send_telegram) y el dashboard HTML (briefing_doc.py). Mismo contenido, doble
# trabajo. DONNA_BRIEFING elige cuál se manda:
#   html   (default) → solo el dashboard interactivo, que es el que Polo usa
#   texto            → solo el texto sintetizado de Telegram
#   ambos            → los dos (el comportamiento que duplicaba el briefing)
# El default fue 'texto' un día y estuvo mal: el dashboard ya existía y es el que
# Polo abre. Además el texto arrastraba pendientes de hace meses.
_BRIEFING_MODOS = ("texto", "html", "ambos")
_BRIEFING_DEFAULT = "html"


def briefing_modo() -> str:
    """Lee DONNA_BRIEFING del entorno. Valor inválido → default con warning."""
    m = (os.environ.get("DONNA_BRIEFING") or _BRIEFING_DEFAULT).strip().lower()
    if m not in _BRIEFING_MODOS:
        log.warning("DONNA_BRIEFING=%r inválido (usa %s) → uso %r",
                    m, "/".join(_BRIEFING_MODOS), _BRIEFING_DEFAULT)
        return _BRIEFING_DEFAULT
    return m


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
    """Reformula recordatorio en tono Donna vía Ollama local. Si Ollama devuelve un
    rechazo/basura (o algo demasiado distinto/largo), manda el mensaje ORIGINAL."""
    sys_prompt = (
        "Eres Donna, asistente ejecutivo de Polo. Reformula este recordatorio en tono "
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

# Chequeos intradía (Fase 3): horas CDMX en que Donna empuja seguimiento si hay algo.
INTRADAY_SLOTS = {13: "tarde", 18: "cierre"}
INTRADAY_STATE = HOME_OC / "state" / "intraday_sent.json"

# Digest proactivo Cerebro + agentes (Fase 5): 10:00 y 14:00 CDMX.
# Devuelve None si no hay nada nuevo → silencioso.
CEREBRO_SLOTS = {10: "cerebro", 14: "cerebro"}
CEREBRO_DIGEST_STATE = HOME_OC / "state" / "cerebro_digest_sent.json"

# Review semanal coach (Fase 4): lunes 08:00 CDMX, una vez por semana ISO.
WEEKLY_REVIEW_STATE = HOME_OC / "state" / "weekly_review_sent.json"

# Alerta Mac offline (Fase 6): solo alerta cuando hay algo realmente malo —
# batería crítica (≤15% sin AC) o ausencia muy larga (>4h).
# Ruido normal (Mac durmiendo 15-240 min) = silencio total.
MAC_OFFLINE_STATE = HOME_OC / "state" / "mac_offline_alerted.json"
MAC_OFFLINE_THRESHOLD_S = 15 * 60        # empieza a rastrear después de 15 min
MAC_ALERT_BATT_PCT      = 15             # % de batería para alerta inmediata
MAC_ALERT_LONG_OFFLINE  = 4 * 3600      # alerta si lleva >4h offline sin batería crítica
MAC_OFFLINE_COOLDOWN_S  = 4 * 3600      # mínimo 4h entre alertas repetidas

# Scan de avances nocturnos (Módulo 2): 06:30 CDMX, una vez al día.
ADVANCES_SCAN_STATE = HOME_OC / "state" / "advances_scan_sent.json"

# Seguimiento post-reunión (Pilar 1): 11h, 15h, 19h CDMX — horas que no colisionan
# con los slots existentes (10, 13, 14, 18, 23). Revisa reuniones terminadas ≤2h.
POST_MEETING_SLOTS = {11, 15, 19}
POST_MEETING_STATE = HOME_OC / "state" / "post_meeting_sent.json"

# Gap seguimientos vs kawiil.central (Pilar 3): 09:30 CDMX, una vez al día.
# Después de que la distilación nocturna (23:00) ya asentó los compromisos del día previo.
TASK_GAP_HOUR = 9
TASK_GAP_MIN = 30
TASK_GAP_STATE = HOME_OC / "state" / "task_gap_sent.json"

# Revisión semanal coaching + nutrición (Pilar coach): viernes 09:00 CDMX.
COACH_REVIEW_WEEKDAY = 4   # viernes (0=lunes)
COACH_REVIEW_HOUR = 9
COACH_REVIEW_STATE = HOME_OC / "state" / "coach_review_sent.json"

# Informe de salud del sistema cada 2 días a las 09:13 CDMX.
SYSTEM_REVIEW_INTERVAL_DAYS = 2
SYSTEM_REVIEW_HOUR = 9
SYSTEM_REVIEW_MIN = 13
SYSTEM_REVIEW_STATE = HOME_OC / "state" / "system_review_sent.json"


def _advances_ya(hoy: str) -> bool:
    try:
        return json.loads(ADVANCES_SCAN_STATE.read_text()).get("date") == hoy
    except Exception:
        return False


def _advances_marca(hoy: str):
    try:
        ADVANCES_SCAN_STATE.parent.mkdir(parents=True, exist_ok=True)
        ADVANCES_SCAN_STATE.write_text(json.dumps({"date": hoy}, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar advances_scan_sent.json: {e}")


def _weekly_ya(wk: str) -> bool:
    try:
        return json.loads(WEEKLY_REVIEW_STATE.read_text()).get("week") == wk
    except Exception:
        return False


def _weekly_marca(wk: str):
    try:
        WEEKLY_REVIEW_STATE.parent.mkdir(parents=True, exist_ok=True)
        WEEKLY_REVIEW_STATE.write_text(json.dumps({"week": wk}, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar weekly_review_sent.json: {e}")


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


def _post_meeting_ya(hora: int, hoy: str) -> bool:
    try:
        d = json.loads(POST_MEETING_STATE.read_text())
        return d.get("date") == hoy and str(hora) in d.get("horas", [])
    except Exception:
        return False


def _post_meeting_marca(hora: int, hoy: str):
    d = {"date": hoy, "horas": []}
    try:
        old = json.loads(POST_MEETING_STATE.read_text())
        if old.get("date") == hoy:
            d = old
    except Exception:
        pass
    d["horas"] = sorted(set(d.get("horas", []) + [str(hora)]))
    try:
        POST_MEETING_STATE.parent.mkdir(parents=True, exist_ok=True)
        POST_MEETING_STATE.write_text(json.dumps(d, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar post_meeting_sent.json: {e}")


def _task_gap_ya(hoy: str) -> bool:
    try:
        return json.loads(TASK_GAP_STATE.read_text()).get("date") == hoy
    except Exception:
        return False


def _task_gap_marca(hoy: str):
    try:
        TASK_GAP_STATE.parent.mkdir(parents=True, exist_ok=True)
        TASK_GAP_STATE.write_text(json.dumps({"date": hoy}, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar task_gap_sent.json: {e}")


def _coach_review_ya(semana: str) -> bool:
    try:
        return json.loads(COACH_REVIEW_STATE.read_text()).get("week") == semana
    except Exception:
        return False


def _coach_review_marca(semana: str):
    try:
        COACH_REVIEW_STATE.parent.mkdir(parents=True, exist_ok=True)
        COACH_REVIEW_STATE.write_text(json.dumps({"week": semana}, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar coach_review_sent.json: {e}")


def _system_review_ya(hoy: str) -> bool:
    """True si ya se envió el informe de sistema en los últimos SYSTEM_REVIEW_INTERVAL_DAYS."""
    try:
        data = json.loads(SYSTEM_REVIEW_STATE.read_text())
        last = data.get("date", "")
        if not last:
            return False
        from datetime import date as _date, timedelta as _td
        delta = _date.fromisoformat(hoy) - _date.fromisoformat(last)
        return delta.days < SYSTEM_REVIEW_INTERVAL_DAYS
    except Exception:
        return False


def _system_review_marca(hoy: str):
    try:
        SYSTEM_REVIEW_STATE.parent.mkdir(parents=True, exist_ok=True)
        SYSTEM_REVIEW_STATE.write_text(json.dumps({"date": hoy}, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar system_review_sent.json: {e}")


def _cerebro_digest_ya(slot: str, hoy: str) -> bool:
    try:
        d = json.loads(CEREBRO_DIGEST_STATE.read_text())
        return d.get("date") == hoy and slot in d.get("slots", [])
    except Exception:
        return False


def _cerebro_digest_marca(slot: str, hoy: str):
    d = {"date": hoy, "slots": []}
    try:
        old = json.loads(CEREBRO_DIGEST_STATE.read_text())
        if old.get("date") == hoy:
            d = old
    except Exception:
        pass
    d["slots"] = sorted(set(d.get("slots", []) + [slot]))
    try:
        CEREBRO_DIGEST_STATE.parent.mkdir(parents=True, exist_ok=True)
        CEREBRO_DIGEST_STATE.write_text(json.dumps(d, ensure_ascii=False))
    except Exception as e:
        log.warning(f"no pude guardar cerebro_digest_sent.json: {e}")


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
            _modo = briefing_modo()
            if mode == "briefing" or raw == MORNING_BRIEFING_MARKER:
                text = core.generate_morning_briefing()
                try:
                    core.save_last_briefing(text, core.build_operational_snapshot())
                except Exception as e:
                    log.warning(f"No guardé last-briefing: {e}")
                # HTML briefing dashboard — briefing_doc.py como subprocess async.
                # Solo si DONNA_BRIEFING lo pide; en 'texto' (default) NO se lanza,
                # que era la causa del briefing duplicado.
                if _modo in ("html", "ambos"):
                    try:
                        import subprocess as _subp
                        _bdoc = Path(__file__).parent / "briefing_doc.py"
                        if _bdoc.exists():
                            _subp.Popen([sys.executable, str(_bdoc), "hoy"])
                            log.info("briefing_doc HTML lanzado (async)")
                        else:
                            log.warning("briefing_doc.py no encontrado en %s", Path(__file__).parent)
                    except Exception as e_bd:
                        log.warning("briefing_doc HTML no lanzó: %s", e_bd)
            elif mode == "enrich":
                text = enrich_with_ollama(raw)
            else:
                text = raw
            # Briefing: el texto solo si el modo lo incluye (el HTML ya se lanzó arriba).
            if mode == "briefing" or raw == MORNING_BRIEFING_MARKER:
                if _modo in ("texto", "ambos"):
                    ok = send_telegram(text) if text else True
                else:
                    ok = True  # modo 'html': el dashboard es el único envío
                log.info(f"Disparado {entry.get('id')} (briefing modo={_modo}) → ok={ok}")
            else:
                prefix = "⏰ "
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


def _mac_offline_alert_check():
    """Alerta proactiva cuando la Mac lleva >15 min sin heartbeat.
    Primera alerta inmediata; repeticiones cada 2h mientras siga offline.
    Cuando vuelve a estar en línea manda notificación de vuelta."""
    hb = core.MAC_HEARTBEAT_FILE
    if not hb.exists():
        return
    try:
        data = json.loads(hb.read_text())
        ts = datetime.fromisoformat(data.get("ts", "").replace("Z", "+00:00"))
        delta_s = int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return
    if delta_s < 0:
        return  # timestamp futuro — reloj inconsistente

    prev = {}
    if MAC_OFFLINE_STATE.exists():
        try:
            prev = json.loads(MAC_OFFLINE_STATE.read_text())
        except Exception:
            pass
    was_alerted = prev.get("alerted", False)

    if delta_s < MAC_OFFLINE_THRESHOLD_S:
        # Mac está online
        if was_alerted:
            send_telegram("🟢 Tu Mac está de vuelta en línea.")
            log.info("mac volvió online — notificado a Polo")
            MAC_OFFLINE_STATE.parent.mkdir(parents=True, exist_ok=True)
            MAC_OFFLINE_STATE.write_text(json.dumps({"alerted": False}))
        return

    # Mac offline — verificar cooldown antes de re-alertar
    last_ts_str = prev.get("last_alerted_ts", "")
    if last_ts_str:
        try:
            last_ts = datetime.fromisoformat(last_ts_str)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=TZ_CDMX)
            if (datetime.now(TZ_CDMX) - last_ts).total_seconds() < MAC_OFFLINE_COOLDOWN_S:
                return
        except Exception:
            pass

    mins = delta_s // 60
    batt = data.get("battery_pct")
    on_ac = data.get("on_ac_power", False)
    batt_critica = batt is not None and not on_ac and batt <= MAC_ALERT_BATT_PCT
    offline_larga = delta_s >= MAC_ALERT_LONG_OFFLINE

    # Silencio si la Mac solo está dormida normalmente (sin batería crítica y < 4h)
    if not batt_critica and not offline_larga:
        return

    if batt_critica:
        msg = f"🔋 Batería crítica: {batt}% sin AC y Mac sin reportarse {mins} min — puede apagarse sola."
    else:
        horas = mins // 60
        msg = f"⚠️ Tu Mac lleva {horas}h sin reportarse (batería {batt if batt is not None else '?'}%{'🔌' if on_ac else ''}). ¿Está apagada?"

    send_telegram(msg)
    log.info(f"mac alerta: {msg[:120]} (batt={batt}%, ac={on_ac}, delta={delta_s}s)")

    MAC_OFFLINE_STATE.parent.mkdir(parents=True, exist_ok=True)
    MAC_OFFLINE_STATE.write_text(json.dumps({
        "alerted": True,
        "last_alerted_ts": datetime.now(TZ_CDMX).isoformat(),
        "delta_s": delta_s,
        "batt": batt,
    }, ensure_ascii=False))


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
        # plazo (PEOPLE/CLIENTES/SEGUIMIENTOS/IMPORTANT). Idempotente por fecha — corre una
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
                # "tarde" (13h) → texto; "cierre" (18h) → solo HTML, igual que briefing matutino
                if _slot != "cierre":
                    msg = core.build_intraday_nudge(_slot)
                    if msg:
                        send_telegram(msg)
                        log.info(f"intraday nudge enviado ({_slot})")
                if _slot == "cierre":
                    try:
                        cierre = core.build_cierre_html_data()
                        if cierre and cierre[0]:
                            core._telegram_send_html_doc(cierre[0], cierre[1], cierre[2],
                                                         reply_markup=cierre[3] if len(cierre) > 3 else None)
                            log.info("cierre HTML enviado")
                    except Exception as _e_cierre:
                        log.warning(f"cierre HTML falló: {_e_cierre}")
                _intraday_marca(_slot, _hoy)  # marca aunque no haya nada (no recalcular cada tick)
        except Exception as e:
            log.warning(f"intraday check falló: {e}")

        # Gap seguimientos vs kawiil.central (Pilar 3): 09:30 CDMX, una vez al día.
        # Cruza SEGUIMIENTOS.md con kawiil.central y propone formalizar compromisos sueltos.
        try:
            _now = datetime.now(TZ_CDMX)
            _hoy = _now.strftime("%Y-%m-%d")
            if (_now.hour == TASK_GAP_HOUR and _now.minute >= TASK_GAP_MIN
                    and not _task_gap_ya(_hoy)):
                msg = core.build_task_gap_analysis()
                if msg:
                    send_telegram(msg)
                    log.info("task gap analysis enviado (09:30h)")
                _task_gap_marca(_hoy)
        except Exception as e:
            log.warning(f"task gap check falló: {e}")

        # Post-reunión (Pilar 1): 11h, 15h, 19h CDMX.
        # Detecta reuniones que terminaron en las últimas 2h y propone capturar tareas.
        try:
            _now = datetime.now(TZ_CDMX)
            _hoy = _now.strftime("%Y-%m-%d")
            if (_now.hour in POST_MEETING_SLOTS
                    and not _post_meeting_ya(_now.hour, _hoy)):
                msg = core.build_post_meeting_followup()
                if msg:
                    send_telegram(msg)
                    log.info(f"post-meeting followup enviado ({_now.hour}h)")
                _post_meeting_marca(_now.hour, _hoy)
        except Exception as e:
            log.warning(f"post-meeting check falló: {e}")

        # Digest Cerebro + agentes (Fase 5): 10:00 y 14:00 CDMX.
        # También dispara el monitor ligero (email + Slack) para detectar novedades intraday.
        try:
            _now = datetime.now(TZ_CDMX)
            _cslot = CEREBRO_SLOTS.get(_now.hour)
            _hoy = _now.strftime("%Y-%m-%d")
            if _cslot and not _cerebro_digest_ya(str(_now.hour), _hoy):
                msg = core.build_cerebro_followup(slot=_cslot)
                if msg:
                    send_telegram(msg)
                    log.info(f"cerebro digest enviado (hora={_now.hour}h, slot={_cslot})")
                # Monitor ligero intraday: email + Slack → HTMLs de eventos nuevos
                try:
                    light = core.build_unified_monitor_scan(mode="light")
                    if light.get("summary"):
                        send_telegram(light["summary"])
                    # HTMLs individuales suprimidos — se consolidan en Cierre 18:00
                    log.info(f"monitor light: {len(light.get('bullets', []))} bullets, "
                             f"{len(light.get('events', []))} eventos detectados (sin envío HTML)")
                except Exception as e_light:
                    log.warning(f"monitor light falló: {e_light}")
                _cerebro_digest_marca(str(_now.hour), _hoy)
        except Exception as e:
            log.warning(f"cerebro digest falló: {e}")

        # Review semanal coach (Fase 4): lunes 08:00 CDMX, una vez por semana.
        try:
            _n = datetime.now(TZ_CDMX)
            if _n.weekday() == 0 and _n.hour == 8:
                _ic = _n.isocalendar()
                _wk = f"{_ic[0]}-W{_ic[1]:02d}"
                if not _weekly_ya(_wk):
                    rev_html = core.build_weekly_review_html_data()
                    if rev_html and rev_html[0]:
                        core._telegram_send_html_doc(rev_html[0], rev_html[1], rev_html[2],
                                                     reply_markup=rev_html[3] if len(rev_html) > 3 else None)
                        log.info(f"review semanal HTML enviada ({_wk})")
                    else:
                        rev = core.build_weekly_review()
                        if rev:
                            send_telegram(rev)
                            log.info(f"review semanal texto enviada ({_wk})")
                    _weekly_marca(_wk)
        except Exception as e:
            log.warning(f"review semanal falló: {e}")

        # Revisión semanal coaching + nutrición: viernes 09:00 CDMX, una vez por semana.
        try:
            _n = datetime.now(TZ_CDMX)
            if _n.weekday() == COACH_REVIEW_WEEKDAY and _n.hour == COACH_REVIEW_HOUR:
                _ic = _n.isocalendar()
                _wk = f"{_ic[0]}-W{_ic[1]:02d}"
                if not _coach_review_ya(_wk):
                    rev = core.build_weekly_coach_review()
                    if rev:
                        send_telegram(rev)
                        log.info(f"coach review enviado ({_wk})")
                    _coach_review_marca(_wk)
        except Exception as e:
            log.warning(f"coach review falló: {e}")

        # Informe de salud del sistema: cada 2 días a las 09:13 CDMX.
        try:
            _n = datetime.now(TZ_CDMX)
            _hoy = _n.strftime("%Y-%m-%d")
            if _n.hour == SYSTEM_REVIEW_HOUR and _n.minute >= SYSTEM_REVIEW_MIN and not _system_review_ya(_hoy):
                log.info("generando informe de salud del sistema…")
                sysrep = core.build_system_health_report_data()
                if sysrep and sysrep[0]:
                    core._telegram_send_html_doc(
                        sysrep[0], sysrep[1], sysrep[2],
                        reply_markup=sysrep[3] if len(sysrep) > 3 else None,
                    )
                    log.info(f"informe sistema enviado ({_hoy})")
                _system_review_marca(_hoy)
        except Exception as e:
            log.warning(f"informe sistema falló: {e}")

        # Monitor unificado 06:30 CDMX (full mode): email, calendario, Slack,
        # Dropbox, Cerebro — detecta avances, genera HTMLs por evento, guarda JSON.
        try:
            _now = datetime.now(TZ_CDMX)
            _hoy = _now.strftime("%Y-%m-%d")
            if _now.hour == 6 and _now.minute >= 30 and not _advances_ya(_hoy):
                result = core.build_unified_monitor_scan(mode="full")
                # No enviamos texto/HTMLs — briefing_doc.py los consolida a las 07:00
                log.info(f"unified_monitor full: {len(result.get('bullets', []))} bullets, "
                         f"{len(result.get('events', []))} eventos · datos guardados para briefing_doc")
                _advances_marca(_hoy)
        except Exception as e:
            log.warning(f"unified_monitor full scan falló: {e}")

        # Mac offline (Fase 6): cada 5 ticks (5 min). Alerta cuando lleva >15 min
        # sin heartbeat; re-alerta cada 2h; notifica cuando vuelve.
        if tick_count % 5 == 0:
            try:
                _mac_offline_alert_check()
            except Exception as e:
                log.warning(f"mac offline check falló: {e}")

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
