#!/usr/bin/env python3
"""estado_legal.py — reporte ADAPTATIVO del estado de las descargas legales (SJF + DOF).

Dice, con dato duro de las BDs: si vamos al dia, cuanto bajamos esta semana, y
como avanza el backfill historico. Se manda a Telegram como mensaje conciso.

Schedule adaptativo (se autogestiona):
  - Semanas 1-2:   diario (cada 24h)
  - Días 15-35:    cada 3 días
  - Día 36+:       semanal
El timer corre cada 4h pero el script decide si es hora de enviar.
"""
import datetime as dt, importlib.util, json, os, sqlite3, sys, urllib.request
from pathlib import Path

HOME_OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))

SJF_DB = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
DOF_DB = os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof/biblioteca_dof.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
SCHEDULE_STATE = Path(os.environ.get("LEGAL_ESTADO_STATE",
                                     "/opt/openclaw/state/legal_estado_schedule.json"))
SJF_UNIVERSO = 0  # fallback obsoleto; se calcula dinámicamente desde la BD

# ── Schedule adaptativo ───────────────────────────────────────────────────────
_PHASES = [
    (14,  24, "diario"),          # días 0-13: cada 24h
    (35,  72, "cada 3 días"),     # días 14-34: cada 72h
    (None, 168, "semanal"),       # día 35+: cada 168h (semanal)
]

def _phase_info(days_running: int):
    for max_d, hours, label in _PHASES:
        if max_d is None or days_running < max_d:
            return hours, label
    return 168, "semanal"

def _should_send():
    """Devuelve (bool, estado) según el schedule adaptativo."""
    now = dt.datetime.now()
    state = {}
    if SCHEDULE_STATE.exists():
        try:
            state = json.loads(SCHEDULE_STATE.read_text())
        except Exception:
            pass

    first_run = state.get("first_run")
    last_sent = state.get("last_sent")

    if not first_run:
        return True, state  # primera vez

    days_running = (now - dt.datetime.fromisoformat(first_run)).days
    interval_h, _ = _phase_info(days_running)

    if not last_sent:
        return True, state

    hours_since = (now - dt.datetime.fromisoformat(last_sent)).total_seconds() / 3600
    return hours_since >= interval_h, state

def _save_state(state: dict):
    SCHEDULE_STATE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def creds():
    out = {}
    p = Path(CREDS)
    if p.exists():
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return (out.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN"),
            out.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID"))


def send_msg(text):
    token, chat = creds()
    if not token or not chat:
        print("ERROR: faltan credenciales Telegram", file=sys.stderr)
        return False
    body = json.dumps({"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print(f"ERROR sendMessage: {e}", file=sys.stderr)
        return False


def _load_louis_html():
    p = HOME_OC / "scripts" / "louis_html.py"
    if not p.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("louis_html", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def send_doc(content: bytes, fname: str, caption: str) -> bool:
    """Envía documento HTML por Telegram (sendDocument multipart)."""
    import uuid as _uuid
    token, chat = creds()
    if not token or not chat:
        print("ERROR: faltan credenciales Telegram", file=sys.stderr)
        return False
    b = "----L" + _uuid.uuid4().hex
    parts = []
    for n, v in (("chat_id", str(chat)), ("caption", caption), ("parse_mode", "HTML")):
        parts += [f"--{b}".encode(),
                  f'Content-Disposition: form-data; name="{n}"'.encode(),
                  b"", v.encode("utf-8")]
    parts += [f"--{b}".encode(),
              f'Content-Disposition: form-data; name="document"; filename="{fname}"'.encode(),
              b"Content-Type: text/html; charset=utf-8", b"", content, f"--{b}--".encode(), b""]
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=b"\r\n".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={b}"},
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except Exception as e:
        print(f"ERROR sendDocument: {e}", file=sys.stderr)
        return False


def build_html_doc(stats: dict, fecha: str) -> bytes:
    """Genera HTML interactivo con barras de progreso SJF/DOF."""
    lh = _load_louis_html()
    sjf_pct = stats.get("sjf_pct", 0.0)
    dof_html_pct = stats.get("dof_html_pct", 0.0)
    sjf_total = stats.get("sjf_total", 0)
    sjf_universo = stats.get("sjf_universo", 0)
    dof_validas = stats.get("dof_validas", 0)
    backfill_sjf = stats.get("backfill_sjf", 0)
    backfill_dof = stats.get("backfill_dof", 0)

    bar = lambda pct, color: (
        f'<div style="background:#e0e0e0;border-radius:6px;height:12px;margin:8px 0">'
        f'<div style="background:{color};border-radius:6px;height:12px;'
        f'width:{min(pct,100):.1f}%"></div></div>'
    )

    if lh:
        cards = [
            {"label": "SJF descargadas", "value": f"{sjf_total:,}", "icon": "⚖️"},
            {"label": "Cobertura SJF", "value": f"{sjf_pct:.1f}%", "icon": "📊"},
            {"label": "DOF notas", "value": f"{dof_validas:,}", "icon": "📰"},
            {"label": "DOF HTML", "value": f"{dof_html_pct:.0f}%", "icon": "🧠"},
        ]
        body = (
            lh.kpi_cards(cards) +
            f"<h3>⚖️ SJF — Semanario Judicial</h3>"
            f"{bar(sjf_pct, '#1a73e8')}"
            f"<p style='font-size:13px;color:#555'>"
            f"<b>{sjf_total:,}</b> de {sjf_universo:,} tesis ({sjf_pct:.1f}%) — faltan {max(sjf_universo-sjf_total,0):,}<br>"
            f"Backfill esta semana: <b>{backfill_sjf:,}</b></p>"
            f"<h3>📰 DOF — Diario Oficial</h3>"
            f"{bar(dof_html_pct, '#34a853')}"
            f"<p style='font-size:13px;color:#555'>"
            f"<b>{dof_validas:,}</b> notas válidas · <b>{dof_html_pct:.0f}%</b> con HTML indexado<br>"
            f"Backfill esta semana: <b>{backfill_dof:,}</b></p>"
        )
        return lh.render_page(
            f"Estado Legal — {fecha}", "kawiil-data", body,
            ctx_md="", con_chat=False,
            resumen=f"SJF {sjf_pct:.1f}% · DOF HTML {dof_html_pct:.0f}%",
            fuente="estado_legal",
        )

    # Fallback: HTML simple sin louis_html
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Estado Legal {fecha}</title>'
        f'<style>body{{font-family:-apple-system,sans-serif;padding:16px;max-width:600px;margin:0 auto}}'
        f'.bar{{background:#e0e0e0;border-radius:4px;height:10px;margin:4px 0}}'
        f'.fb{{background:#1a73e8;border-radius:4px;height:10px}}'
        f'.fg{{background:#34a853;border-radius:4px;height:10px}}</style></head><body>'
        f'<h2>📊 Estado Legal — {fecha}</h2>'
        f'<h3>⚖️ SJF</h3>'
        f'<div class="bar"><div class="fb" style="width:{min(sjf_pct,100):.1f}%"></div></div>'
        f'<p>{sjf_total:,} / {sjf_universo:,} ({sjf_pct:.1f}%) · backfill esta semana: {backfill_sjf:,}</p>'
        f'<h3>📰 DOF</h3>'
        f'<div class="bar"><div class="fg" style="width:{min(dof_html_pct,100):.0f}%"></div></div>'
        f'<p>{dof_validas:,} notas · {dof_html_pct:.0f}% con HTML · backfill esta semana: {backfill_dof:,}</p>'
        f'</body></html>'
    ).encode("utf-8")


def _q1(db, sql, params=()):
    try:
        conn = sqlite3.connect(db)
        r = conn.execute(sql, params).fetchone()
        conn.close()
        return r[0] if r else None
    except Exception:
        return None


def _dias(iso):
    try:
        return (dt.date.today() - dt.date.fromisoformat(iso[:10])).days
    except Exception:
        return None


def _miles(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def _pct(n, d):
    return (n / d * 100) if d else 0


def main():
    # Verificar si es hora de enviar según schedule adaptativo
    send, state = _should_send()
    if not send:
        now = dt.datetime.now()
        first_run = state.get("first_run", now.isoformat())
        days_running = (now - dt.datetime.fromisoformat(first_run)).days
        _, phase_label = _phase_info(days_running)
        print(f"[schedule] No es hora ({phase_label}) — omitiendo")
        return 0

    now = dt.datetime.now()
    first_run = state.get("first_run") or now.isoformat()
    days_running = (now - dt.datetime.fromisoformat(first_run)).days
    _, phase_label = _phase_info(days_running)

    cutoff = (now - dt.timedelta(days=7)).isoformat()
    semana_ini = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    hoy = dt.date.today().strftime("%d/%m/%Y")
    L = [f"📊 <b>Estado de descargas legales</b> — {hoy} <i>({phase_label})</i>\n"]
    sjf_txt = dof_txt = 0.0  # para el bloque de deep learning
    stats_data: dict = {}

    # ── SJF ──────────────────────────────────────────────────────────
    if Path(SJF_DB).exists():
        total = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis") or 0
        universo_raw = _q1(SJF_DB, "SELECT value FROM progress WHERE key='universe_total'")
        try:
            universo = int(universo_raw) if universo_raw else total
        except Exception:
            universo = total
        faltan = max(universo - total, 0)
        ult = _q1(SJF_DB, "SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis WHERE fecha_publicacion!=''") or "—"
        dias = _dias(ult)
        nuevas = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE substr(fecha_publicacion,1,10) >= ?", (semana_ini,)) or 0
        bajadas = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE fetched_at >= ?", (cutoff,)) or 0
        backfill_n = max(bajadas - nuevas, 0)
        cur = _q1(SJF_DB, "SELECT value FROM progress WHERE key='backfill_cursor'")
        epoca_old = _q1(SJF_DB, "SELECT epoca FROM tesis WHERE epoca IS NOT NULL AND epoca!='' ORDER BY registro_digital ASC LIMIT 1") or "—"
        con_texto = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE texto IS NOT NULL AND texto!=''") or 0
        con_pdf = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE pdf_generated IN (1,'1')") or 0
        sjf_txt = _pct(con_texto, total)
        ok = "✅ al día" if (dias is not None and dias <= 10) else (f"⚠️ atrasado {dias}d" if dias is not None else "—")
        L.append("⚖️ <b>SJF (Semanario Judicial)</b>")
        L.append(f"• Acervo: <b>{_miles(total)}</b> / {_miles(universo)} ({_pct(total, universo):.1f}%) — faltan <b>{_miles(faltan)}</b> hacia atrás")
        L.append(f"• Al día: última publicación <b>{ult}</b> {ok} · {nuevas} nuevas esta semana")
        L.append(f"• Histórico (backfill): <b>{_miles(backfill_n)}</b> esta semana · frontera registro {cur or '—'} (época más antigua: {epoca_old})")
        L.append(f"• Indexación: <b>{sjf_txt:.0f}%</b> con texto · <b>{_pct(con_pdf, total):.0f}%</b> con PDF\n")
        stats_data.update({"sjf_total": total, "sjf_universo": universo,
                           "sjf_pct": _pct(total, universo), "backfill_sjf": backfill_n})
    else:
        L.append("⚖️ <b>SJF</b>: BD no encontrada\n")

    # ── DOF ──────────────────────────────────────────────────────────
    if Path(DOF_DB).exists():
        total = _q1(DOF_DB, "SELECT COUNT(*) FROM notas") or 0
        validas = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha BETWEEN '1900-01-01' AND date('now')") or 0
        invalidas = max(total - validas, 0)
        ult = _q1(DOF_DB, "SELECT MAX(fecha) FROM notas WHERE fecha BETWEEN '1900-01-01' AND date('now')") or "—"
        n_ult = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha=?", (ult,)) or 0
        dias = _dias(ult)
        early = _q1(DOF_DB, "SELECT MIN(fecha) FROM notas WHERE fecha >= '1900-01-01'") or "—"
        nuevas = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha BETWEEN ? AND date('now')", (semana_ini,)) or 0
        bajadas = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fetched_at >= ?", (cutoff,)) or 0
        backfill_n = max(bajadas - nuevas, 0)
        con_texto = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE texto_plano IS NOT NULL AND texto_plano!=''") or 0
        con_pdf = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE pdf_path IS NOT NULL AND pdf_path!=''") or 0
        # notas_html: notas que SÍ tienen HTML descargable; el resto son PDFs escaneados sin texto
        notas_html = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE incluido=1 AND existe_html=1") or 0
        pendientes_html = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE incluido=1 AND existe_html=1 AND content_downloaded_at IS NULL") or 0
        dof_txt = _pct(con_texto, notas_html) if notas_html else 0.0
        ok = "✅ al día" if (dias is not None and dias <= 4) else (f"⚠️ atrasado {dias}d" if dias is not None else "—")
        L.append("📰 <b>DOF (Diario Oficial)</b>")
        sucio = f" · <i>{invalidas} con fecha inválida (a depurar)</i>" if invalidas else ""
        L.append(f"• Acervo: <b>{_miles(validas)}</b> notas válidas{sucio}")
        L.append(f"• Cobertura temporal: {early} → {ult}")
        L.append(f"• Al día: última edición <b>{ult}</b> ({n_ult} notas) {ok} · {nuevas} esta semana")
        L.append(f"• Histórico (backfill): <b>{_miles(backfill_n)}</b> esta semana")
        html_status = "✅ completo" if pendientes_html == 0 else f"⏳ {_miles(pendientes_html)} pendientes"
        L.append(f"• Indexación HTML: <b>{dof_txt:.0f}%</b> de {_miles(notas_html)} notas con HTML ({html_status}) · {_pct(con_pdf, validas):.0f}% con PDF")
        L.append(f"  <i>(El {100 - round(notas_html / validas * 100) if validas else 0}% restante del histórico son PDFs escaneados sin texto disponible)</i>\n")
        stats_data.update({"dof_validas": validas, "dof_html_pct": dof_txt, "backfill_dof": backfill_n})
    else:
        L.append("📰 <b>DOF</b>: BD no encontrada\n")

    # ── Rumbo al deep learning ───────────────────────────────────────
    L.append("🧠 <b>Indexación / análisis (rumbo a deep learning)</b>")
    L.append(f"• SJF: {'✅ texto e índices casi completos' if sjf_txt >= 90 else f'⚠️ {sjf_txt:.0f}% con texto'}")
    # dof_txt es % sobre notas con HTML; el histórico en PDF escaneado no es extraíble
    L.append(f"• DOF: {'✅ indexación HTML completa' if dof_txt >= 90 else f'⏳ {dof_txt:.0f}% de notas HTML indexadas'}")

    # Siguiente reporte
    next_interval_h, _ = _phase_info(days_running)
    next_dt = (now + dt.timedelta(hours=next_interval_h)).strftime("%d/%m %H:%M")
    L.append(f"\n<i>Próximo informe: {next_dt} ({phase_label}). Al día con lo nuevo; histórico en automático.</i>")

    caption = (f"📊 <b>Estado Legal — {hoy}</b> <i>({phase_label})</i>\n"
               f"SJF {stats_data.get('sjf_pct', 0):.1f}% · "
               f"DOF HTML {stats_data.get('dof_html_pct', 0):.0f}%")
    html_bytes = build_html_doc(stats_data, hoy)
    ok = send_doc(html_bytes, f"EstadoLegal_{dt.date.today().strftime('%Y%m%d')}.html", caption)
    print("Enviado" if ok else "Falló el envío")
    if ok:
        _save_state({
            "first_run": first_run,
            "last_sent": now.isoformat(),
            "phase": phase_label,
        })
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
