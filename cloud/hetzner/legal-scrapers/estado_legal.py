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
    """Genera HTML interactivo con barras de progreso SJF/DOF y chat widget."""
    import json as _json
    AZUL, MARINO = "#1a6ef5", "#0a1a8c"
    chat_url = os.environ.get("CHAT_ENDPOINT", "https://louis.kawiil.mx/v1/chat/completions")
    chat_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")

    sjf_pct = stats.get("sjf_pct", 0.0)
    dof_html_pct = stats.get("dof_html_pct", 0.0)
    sjf_total = stats.get("sjf_total", 0)
    sjf_universo = stats.get("sjf_universo", 0)
    dof_validas = stats.get("dof_validas", 0)
    backfill_sjf = stats.get("backfill_sjf", 0)
    backfill_dof = stats.get("backfill_dof", 0)
    sjf_rate_day = stats.get("sjf_rate_day", 0.0)
    sjf_eta_str = stats.get("sjf_eta_str", "indeterminado")

    def bar(pct, color):
        w = min(pct, 100)
        return (f'<div style="background:#e0e0e0;border-radius:6px;height:14px;margin:8px 0 4px">'
                f'<div style="background:{color};border-radius:6px;height:14px;width:{w:.1f}%;'
                f'transition:width .6s ease"></div></div>')

    kpis = [
        ("⚖️", f"{sjf_total:,}", "SJF tesis"),
        ("📊", f"{sjf_pct:.1f}%", "cobertura SJF"),
        ("📰", f"{dof_validas:,}", "DOF notas"),
        ("🧠", f"{dof_html_pct:.0f}%", "DOF HTML"),
        ("⚡", f"{sjf_rate_day:.0f}/día", "ritmo SJF"),
        ("🎯", sjf_eta_str, "ETA completar SJF"),
    ]
    kpi_html = "".join(
        f'<div style="flex:1;min-width:80px;background:rgba(255,255,255,.14);border-radius:10px;padding:11px 10px;text-align:center">'
        f'<div style="font-size:1.25rem;font-weight:700;color:#fff">{icon} {val}</div>'
        f'<div style="font-size:.68rem;color:rgba(255,255,255,.72);text-transform:uppercase;letter-spacing:.06em;margin-top:3px">{lbl}</div>'
        f'</div>'
        for icon, val, lbl in kpis
    )

    ctx_js = _json.dumps(
        f"Estado Legal — {fecha}\n"
        f"SJF: {sjf_total:,} / {sjf_universo:,} tesis ({sjf_pct:.1f}%), "
        f"backfill esta semana: {backfill_sjf:,}, ritmo: {sjf_rate_day:.0f} tesis/día, "
        f"ETA completar: {sjf_eta_str}\n"
        f"DOF: {dof_validas:,} notas válidas, {dof_html_pct:.0f}% con HTML, "
        f"backfill esta semana: {backfill_dof:,}"
    )
    chat_url_js = _json.dumps(chat_url)
    chat_token_js = _json.dumps(chat_token)

    return f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estado Legal — {fecha}</title>
<style>
:root{{--bg:#f4f6fb;--card:#fff;--text:#1a1a2e;--muted:#8892a4;--border:#e6eaf2}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0e1118;--card:#181d2c;--text:#dde3f0;--muted:#5a6278;--border:#252a3a}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);padding-bottom:220px}}
.hdr{{background:linear-gradient(135deg,{MARINO} 0%,{AZUL} 100%);color:#fff;padding:20px 18px 18px}}
.hdr-title{{font-size:1.2rem;font-weight:700;margin-bottom:3px}}
.hdr-sub{{font-size:.8rem;opacity:.75;margin-bottom:14px}}
.kpi-row{{display:flex;gap:7px;flex-wrap:wrap}}
.main{{padding:14px;max-width:680px;margin:0 auto}}
.card{{background:var(--card);border-radius:12px;padding:15px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.sec-lbl{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px}}
.stat-note{{font-size:.84em;color:var(--muted);margin-top:4px;line-height:1.5}}
.foot{{text-align:center;font-size:.7rem;color:var(--muted);padding:16px}}
.chat-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:10px 14px;box-shadow:0 -2px 12px rgba(0,0,0,.1);z-index:100}}
.chat-bar .inner{{max-width:680px;margin:0 auto}}
#conv{{max-height:180px;overflow-y:auto;margin-bottom:8px}}
.cm{{padding:8px 12px;border-radius:10px;margin:4px 0;font-size:.87em;line-height:1.5}}
.cm.user{{background:{AZUL};color:#fff;margin-left:18%}}
.cm.bot{{background:var(--border);color:var(--text);margin-right:18%}}
.cm.bot p{{margin:.3em 0}}
.cin{{display:flex;gap:8px;align-items:flex-end}}
.cin textarea{{flex:1;padding:9px;border:1px solid var(--border);border-radius:8px;font-size:.9em;resize:none;background:var(--bg);color:var(--text);font-family:inherit}}
.cin button{{padding:9px 14px;border:0;border-radius:8px;background:{AZUL};color:#fff;cursor:pointer;font-size:.88em}}
.chat-note{{font-size:.66rem;color:var(--muted);margin-top:4px;text-align:center}}
</style>
</head>
<body>
<div class="hdr">
<div style="font-size:.68rem;opacity:.65;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px">📊 Kawiil · Descargas legales</div>
<div class="hdr-title">Estado Legal — {fecha}</div>
<div class="hdr-sub">SJF {sjf_pct:.1f}% · DOF HTML {dof_html_pct:.0f}%</div>
<div class="kpi-row">{kpi_html}</div>
</div>
<div class="main">
<div class="card">
<div class="sec-lbl">⚖️ SJF — Semanario Judicial de la Federación</div>
{bar(sjf_pct, AZUL)}
<div class="stat-note">
<b>{sjf_total:,}</b> de {sjf_universo:,} tesis ({sjf_pct:.1f}%) — faltan {max(sjf_universo-sjf_total,0):,}<br>
Backfill esta semana: <b>{backfill_sjf:,}</b> · ritmo: <b>{sjf_rate_day:.0f} tesis/día</b><br>
🎯 Pronóstico: <b>{sjf_eta_str}</b> para completar el acervo al ritmo actual
</div>
</div>
<div class="card">
<div class="sec-lbl">📰 DOF — Diario Oficial de la Federación</div>
{bar(dof_html_pct, "#34a853")}
<div class="stat-note">
<b>{dof_validas:,}</b> notas válidas · <b>{dof_html_pct:.0f}%</b> con HTML indexado<br>
Backfill esta semana: <b>{backfill_dof:,}</b>
</div>
</div>
</div>
<div class="foot">Louis · Kawiil Legal · {fecha}</div>
<div class="chat-bar">
<div class="inner">
<div id="conv"></div>
<div class="cin">
<textarea id="cq" rows="2" placeholder="Pregunta o actualiza… (ej: 'marca SJF como revisado', 'agrega pendiente: revisar contrato')"></textarea>
<button onclick="preg()">Enviar</button>
</div>
<p class="chat-note">Abre en Safari/Chrome — el visor de Telegram bloquea JS.</p>
</div>
</div>
<script>
const CHAT_URL={chat_url_js},CHAT_TOKEN={chat_token_js},CTX={ctx_js};
function inl(s){{s=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');s=s.replace(/[*][*](.+?)[*][*]/g,'<strong>$1</strong>');s=s.replace(/[*](.+?)[*]/g,'<em>$1</em>');return s;}}
function md(t){{return t.split('\\n').map(function(l){{return l.trim()?'<p>'+inl(l)+'</p>':'';}}).join('');}}
function addMsg(role,html){{var d=document.createElement('div');d.className='cm '+role;d.innerHTML=html;var c=document.getElementById('conv');c.appendChild(d);c.scrollTop=c.scrollHeight;return d;}}
var _hist=[];
async function preg(){{var inp=document.getElementById('cq');var q=(inp.value||'').trim();if(!q)return;inp.value='';addMsg('user',inl(q));var bot=addMsg('bot','<em>pensando…</em>');try{{var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;var msgs=[];if(CTX){{msgs.push({{role:'user',content:'Contexto:\\n'+CTX}});msgs.push({{role:'assistant',content:'Contexto cargado.'}});}}msgs=msgs.concat(_hist);msgs.push({{role:'user',content:q}});var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||j.error||'(sin respuesta)';_hist.push({{role:'user',content:q}});_hist.push({{role:'assistant',content:ans}});bot.innerHTML=md(ans);}}catch(e){{bot.innerHTML='<em>Error al conectar ('+e+'). Abre este HTML en un navegador real.</em>';}}}}
document.getElementById('cq').addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();preg();}}}});
</script>
</body>
</html>'''.encode("utf-8")


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
        # Pronóstico de velocidad SJF
        sjf_rate_day = backfill_n / 7 if backfill_n > 0 else 0
        sjf_eta_dias = int(faltan / sjf_rate_day) if sjf_rate_day > 0 else None
        sjf_eta_str = (
            f"~{sjf_eta_dias // 7} semanas" if sjf_eta_dias and sjf_eta_dias > 14
            else (f"~{sjf_eta_dias} días" if sjf_eta_dias else "indeterminado")
        )
        L.append("⚖️ <b>SJF (Semanario Judicial)</b>")
        L.append(f"• Acervo: <b>{_miles(total)}</b> / {_miles(universo)} ({_pct(total, universo):.1f}%) — faltan <b>{_miles(faltan)}</b> hacia atrás")
        L.append(f"• Al día: última publicación <b>{ult}</b> {ok} · {nuevas} nuevas esta semana")
        L.append(f"• Histórico (backfill): <b>{_miles(backfill_n)}</b> esta semana · frontera registro {cur or '—'} (época más antigua: {epoca_old})")
        L.append(f"• Pronóstico: <b>{sjf_eta_str}</b> al ritmo actual ({sjf_rate_day:.0f} tesis/día)")
        L.append(f"• Indexación: <b>{sjf_txt:.0f}%</b> con texto · <b>{_pct(con_pdf, total):.0f}%</b> con PDF\n")
        stats_data.update({"sjf_total": total, "sjf_universo": universo,
                           "sjf_pct": _pct(total, universo), "backfill_sjf": backfill_n,
                           "sjf_rate_day": sjf_rate_day, "sjf_eta_str": sjf_eta_str})
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
