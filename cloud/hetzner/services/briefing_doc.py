#!/usr/bin/env python3
"""briefing_doc.py — Briefing matutino HTML interactivo como documento a Telegram.

Fuentes de verdad:
  • Calendario: m365.py calendario <tenant> <hoy|manana>  (kawiil + yoltik)
  • Pendientes: líneas '- [ ]' de SEGUIMIENTOS.md
  • Avances detectados: /opt/openclaw/state/advances_delta.json (generado a las 06:30)

El HTML usa donna_html.py (render_page + kpi_cards) con chat widget embebido.
Se guarda en /opt/openclaw/state/briefing_latest.html para servir por URL.

Uso:
  briefing_doc.py [hoy|manana]   (default: hoy)
"""
import base64, datetime as dt, html, json, os, re, subprocess, sys, urllib.request, uuid
from pathlib import Path

HOME_OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))
SPACE = HOME_OC / "spaces" / "general"
SEGUIMIENTOS = SPACE / "SEGUIMIENTOS.md"
CREDS = os.environ.get("TELEGRAM_CREDS", str(HOME_OC / "credentials" / "telegram.env"))
M365 = HOME_OC / "scripts" / "m365" / "m365.py"
if not M365.exists():
    M365 = HOME_OC / "scripts" / "m365.py"
TENANTS = [t.strip() for t in os.environ.get("BRIEFING_TENANTS", "kawiil,yoltik").split(",") if t.strip()]

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
TZ = dt.timezone(dt.timedelta(hours=-6))  # CDMX

CHAT_URL = os.environ.get("CHAT_ENDPOINT", "https://louis.kawiil.mx/v1/chat/completions")
CHAT_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")

# ── Marca Kawiil ──────────────────────────────────────────────────────────
BRAND_DIR = Path(os.environ.get("BRAND_DIR", str(HOME_OC / "assets" / "brand")))
KAWIIL_AZUL = "#1a6ef5"   # azul brillante (wordmark)
KAWIIL_MARINO = "#0a1a8c"  # azul marino (isotipo)


def logo_data_uri(brand="kawiil", archivo="usos_kawiil_1.png"):
    """Devuelve el logo como data URI base64 para incrustar en el HTML. '' si no está."""
    p = BRAND_DIR / brand / archivo
    try:
        if p.exists():
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:image/png;base64,{b64}"
    except Exception:
        pass
    return ""


def esc(s):
    return html.escape((s or "").strip())


def md_inline(s: str) -> str:
    """Convierte **bold** y *italic* a HTML, escapando el resto."""
    s = html.escape((s or "").strip())
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
    return s


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


def fetch_eventos(rango):
    """Corre m365 calendario por tenant y parsea los eventos. Lista de dicts."""
    eventos = []
    if not M365.exists():
        return eventos, f"no encontré m365.py en {M365}"
    err = ""
    for tenant in TENANTS:
        try:
            r = subprocess.run(["/usr/bin/python3", str(M365), "calendario", tenant, rango],
                               capture_output=True, text=True, timeout=60)
            out = r.stdout or ""
            if r.returncode != 0 and not out:
                err += f"[{tenant}: {(r.stderr or '').strip()[:120]}] "
                continue
            eventos += parse_m365(out, tenant)
        except Exception as e:
            err += f"[{tenant}: {e}] "
    # dedup: el mismo evento puede estar en ambos calendarios (ej. "Comida")
    vistos, unicos = set(), []
    for e in eventos:
        clave = (e.get("inicio", ""), e.get("fin", ""), re.sub(r"\s+", " ", e.get("asunto", "").lower()).strip())
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(e)
    eventos = unicos
    # ordenar por hora de inicio
    eventos.sort(key=lambda e: e.get("inicio", "9999"))
    return eventos, err.strip()


def parse_m365(text, tenant):
    """Parsea la salida de m365.py calendario.

    Formato por evento:
      [2026-06-08 09:00 → 10:00] [En línea] Asunto
         📍 Lugar
         👤 Organizador
         👥 Nombres...
    """
    eventos = []
    cur = None
    for raw in (text or "").splitlines():
        ln = raw.rstrip()
        # El formato real trae un emoji de estatus al inicio (👑/✓/etc.) ANTES del [fecha].
        m = re.match(r"^.*?\[(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*(?:→|->)\s*(\d{2}:\d{2})\]\s*(.*)$", ln)
        if m:
            if cur:
                eventos.append(cur)
            fecha, ini, fin, resto = m.group(1), m.group(2), m.group(3), m.group(4)
            resto = resto.strip()
            mo = re.match(r"^\[([^\]]+)\]\s*(.*)$", resto)  # tag opcional tipo [En línea]
            online = False
            if mo:
                online = any(k in mo.group(1).lower() for k in ("línea", "online", "teams"))
                resto = mo.group(2).strip()
            if "🎥" in resto:                       # 🎥 = junta en línea (Teams)
                online = True
                resto = resto.replace("🎥", "").strip()
            cur = {"fecha": fecha, "inicio": ini, "fin": fin, "asunto": resto,
                   "lugar": "", "org": "", "asistentes": "", "online": online, "tenant": tenant}
        elif cur:
            s = ln.strip()
            if s.startswith("📍"):
                cur["lugar"] = s[1:].strip()
                if "teams" in cur["lugar"].lower():
                    cur["online"] = True
            elif s.startswith("👤"):
                cur["org"] = s[1:].strip()
            elif s.startswith("👥"):
                cur["asistentes"] = s[1:].strip()
            # 📨 (mi respuesta) e id=... se ignoran
    if cur:
        eventos.append(cur)
    return eventos


def pendientes_abiertos(max_items=25):
    if not SEGUIMIENTOS.exists():
        return []
    items = []
    for ln in SEGUIMIENTOS.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*-\s*\[\s*\]\s+", ln):
            txt = re.sub(r"^\s*-\s*\[\s*\]\s+", "", ln).strip()
            if txt:
                items.append(txt)
    return items[:max_items]


def _build_agenda_table(eventos, err) -> str:
    """Genera la tabla de agenda (solo el HTML interior, sin sección wrapper)."""
    nota = f'<p style="color:#b35900;font-size:.85em">⚠️ No pude leer parte del calendario: {esc(err)}</p>' if err else ""
    if not eventos:
        return nota + '<p style="color:#888;font-style:italic">Sin eventos en el calendario para este día. ✅ Día libre de juntas.</p>'
    filas = []
    for e in eventos:
        tag = '<span style="font-size:.66em;font-weight:bold;background:#1a6ef5;color:#fff;padding:1px 6px;border-radius:4px">En línea</span> ' if e.get("online") else ""
        extra = " · ".join(x for x in (e.get("lugar", ""), e.get("asistentes", "")) if x)
        extra_html = f'<div style="font-size:.8em;color:#777;margin-top:2px">{esc(extra)}</div>' if extra else ""
        ini, fin, asunto, tenant = esc(e["inicio"]), esc(e["fin"]), esc(e["asunto"]), esc(e["tenant"])
        filas.append(
            f'<tr><td style="white-space:nowrap;font-weight:bold;color:#1a6ef5;width:96px;font-size:.92em;padding:9px;border-bottom:1px solid #eee">{ini}–{fin}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #eee;vertical-align:top">{tag}<b>{asunto}</b>{extra_html}</td>'
            f'<td style="font-size:.72em;color:#999;text-transform:capitalize;width:64px;padding:9px;border-bottom:1px solid #eee">{tenant}</td></tr>')
    return (nota +
            '<table style="border-collapse:collapse;width:100%;margin:.5em 0">'
            '<thead><tr>'
            '<th style="text-align:left;font-size:.78em;text-transform:uppercase;letter-spacing:.04em;color:#888;border-bottom:2px solid #1a6ef5;padding:6px 9px">Hora</th>'
            '<th style="text-align:left;font-size:.78em;text-transform:uppercase;letter-spacing:.04em;color:#888;border-bottom:2px solid #1a6ef5;padding:6px 9px">Evento</th>'
            '<th style="text-align:left;font-size:.78em;text-transform:uppercase;letter-spacing:.04em;color:#888;border-bottom:2px solid #1a6ef5;padding:6px 9px">Cuenta</th>'
            '</tr></thead>'
            f'<tbody>{"".join(filas)}</tbody></table>')


def _load_advances(max_age_h: int = 3) -> list:
    """Lee advances_delta.json si tiene menos de max_age_h horas."""
    p = Path(os.environ.get("STATE_DIR", "/opt/openclaw/state")) / "advances_delta.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = dt.datetime.fromisoformat(data.get("ts", "2000-01-01T00:00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=TZ)
        age_h = (dt.datetime.now(TZ) - ts).total_seconds() / 3600
        if age_h > max_age_h:
            return []
        return data.get("bullets", [])
    except Exception:
        return []


def build_html(eventos, pend, fecha_obj, rango, err, avances=None):
    """Genera el HTML del briefing — layout de agenda personal."""
    if avances is None:
        avances = _load_advances()

    fl = f"{DIAS[fecha_obj.weekday()]} {fecha_obj.day} de {MES[fecha_obj.month]} de {fecha_obj.year}"
    dlabel = "mañana" if rango == "manana" else "hoy"
    hora_gen = dt.datetime.now(TZ).strftime("%H:%M")
    hora_int = int(hora_gen.split(":")[0])
    saludo = "Buenos días" if hora_int < 12 else ("Buenas tardes" if hora_int < 19 else "Buenas noches")

    # Separar urgentes de pendientes regulares (solo hoy, no cualquier fecha)
    hoy_iso = fecha_obj.strftime("%Y-%m-%d")
    _URGENTE_RE = re.compile(
        rf'\bHOY\b|VENCE HOY|urgente|URGENTE|{re.escape(hoy_iso)}',
        re.IGNORECASE
    )
    urgentes = [p for p in pend if _URGENTE_RE.search(p)]
    no_urgentes = [p for p in pend if not _URGENTE_RE.search(p)]

    # Logo
    logo_uri = logo_data_uri()
    logo_html = (f'<img src="{logo_uri}" style="height:26px;margin-bottom:8px;opacity:.9" alt="Kawiil">'
                 if logo_uri else "")

    # KPI strip
    kpi_defs = [
        ("🗓", str(len(eventos)), "juntas hoy", ""),
        ("📌", str(len(pend)), "pendientes", ""),
        ("🔴", str(len(urgentes)), "urgentes",
         "background:rgba(220,50,47,.18);color:#ff7070" if urgentes else ""),
        ("✅", str(len(avances)), "avanzaron", ""),
    ]
    kpi_cards = ""
    for icon, val, lbl, extra_style in kpi_defs:
        style = f"flex:1;min-width:68px;background:rgba(255,255,255,.13);border-radius:10px;padding:11px 10px;text-align:center;{extra_style}"
        kpi_cards += (f'<div style="{style}">'
                      f'<div style="font-size:1.35rem;font-weight:700;color:#fff">{icon} {val}</div>'
                      f'<div style="font-size:.68rem;color:rgba(255,255,255,.72);text-transform:uppercase;letter-spacing:.06em;margin-top:3px">{lbl}</div>'
                      f'</div>')

    # Agenda rows
    if eventos:
        rows = ""
        for e in eventos:
            tag = (f'<span style="font-size:.63em;background:{KAWIIL_AZUL};color:#fff;padding:1px 5px;'
                   f'border-radius:3px;font-weight:700;margin-right:4px">Online</span>'
                   if e.get("online") else "")
            extra = " · ".join(x for x in (e.get("lugar", ""), e.get("asistentes", "")) if x)
            extra_html = f'<div style="font-size:.78em;color:var(--muted);margin-top:2px">{esc(extra)}</div>' if extra else ""
            ini_raw, fin_raw = e["inicio"], e["fin"]
            all_day = ini_raw in ("00:00", "") and fin_raw in ("00:00", "")
            hora_lbl = "Todo el día" if all_day else f"{esc(ini_raw)}–{esc(fin_raw)}"
            hora_style = ("min-width:92px;font-size:.82em;color:var(--muted);font-style:italic;padding-top:1px"
                          if all_day else
                          f"min-width:92px;font-weight:600;color:{KAWIIL_AZUL};font-size:.87em;padding-top:1px")
            asunto = esc(e["asunto"])
            rows += (f'<div style="display:flex;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border)">'
                     f'<div style="{hora_style}">{hora_lbl}</div>'
                     f'<div style="flex:1">{tag}<span style="font-weight:500">{asunto}</span>{extra_html}</div>'
                     f'</div>')
        agenda_inner = rows
        if err:
            agenda_inner += f'<p style="color:#b35900;font-size:.82em;margin-top:8px">⚠️ {esc(err)}</p>'
    else:
        agenda_inner = '<p style="color:var(--muted);font-style:italic">Sin eventos. ✅ Día libre de juntas.</p>'
        if err:
            agenda_inner += f'<p style="color:#b35900;font-size:.82em;margin-top:6px">⚠️ {esc(err)}</p>'

    # Urgentes section
    urgentes_html = ""
    if urgentes:
        items = "".join(
            f'<li style="list-style:none;margin-bottom:10px">'
            f'<div style="display:flex;align-items:flex-start;gap:8px">'
            f'<button onclick="marcar(this,{json.dumps(u)})" '
            f'style="flex-shrink:0;margin-top:2px;min-width:18px;height:18px;border-radius:3px;'
            f'border:2px solid #e74c3c;background:transparent;cursor:pointer;color:#27ae60;font-size:11px;padding:0">'
            f'</button>'
            f'<span onclick="toggleNota(\'u{i}\')" style="cursor:pointer;flex:1">{md_inline(u)}</span>'
            f'</div>'
            f'<div id="nota_u{i}" style="display:none;margin-top:6px;padding-left:26px">'
            f'<textarea id="ntxt_u{i}" placeholder="Nota o pregunta sobre esta tarea…" '
            f'style="width:100%;padding:6px 8px;border:1px solid var(--border);border-radius:6px;'
            f'font-size:.84em;background:var(--bg);color:var(--text);resize:none;font-family:inherit" rows="2"></textarea>'
            f'<div style="display:flex;gap:6px;margin-top:4px;flex-wrap:wrap">'
            f'<button onclick="enviarNota(\'u{i}\',{json.dumps(u)})" '
            f'style="padding:5px 11px;background:{KAWIIL_AZUL};color:#fff;border:0;border-radius:5px;font-size:.8em;cursor:pointer">Enviar nota</button>'
            f'<button onclick="preguntarOrigen(\'u{i}\',{json.dumps(u)})" '
            f'style="padding:5px 11px;background:transparent;border:1px solid var(--border);border-radius:5px;font-size:.8em;cursor:pointer;color:var(--text)">❓ ¿De dónde viene?</button>'
            f'</div>'
            f'<div id="nresp_u{i}" style="font-size:.82em;color:var(--muted);margin-top:5px;line-height:1.45"></div>'
            f'</div>'
            f'</li>'
            for i, u in enumerate(urgentes)
        )
        urgentes_html = (
            f'<div style="background:rgba(220,50,47,.07);border-left:3px solid #e74c3c;'
            f'border-radius:0 8px 8px 0;padding:13px 15px;margin-bottom:14px">'
            f'<div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;'
            f'color:#e74c3c;margin-bottom:7px">🔴 Tareas urgentes — solo hoy</div>'
            f'<ul style="margin:0;padding:0;list-style:none;font-size:.9em;color:var(--text)">{items}</ul>'
            f'</div>'
        )

    # Avances section
    avances_html = ""
    if avances:
        items = "".join(f'<li style="margin-bottom:5px">{md_inline(a)}</li>' for a in avances)
        avances_html = (
            f'<div style="background:rgba(39,174,96,.07);border-left:3px solid #27ae60;'
            f'border-radius:0 8px 8px 0;padding:13px 15px;margin-bottom:14px">'
            f'<div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;'
            f'color:#27ae60;margin-bottom:7px">✅ Avanzaron ayer</div>'
            f'<ul style="margin:0;padding-left:17px;font-size:.9em;color:var(--text)">{items}</ul>'
            f'</div>'
        )

    # Pendientes section
    pend_html = ""
    if no_urgentes:
        items = "".join(
            f'<li style="list-style:none;margin-bottom:10px">'
            f'<div style="display:flex;align-items:flex-start;gap:8px">'
            f'<button onclick="marcar(this,{json.dumps(p)})" '
            f'style="flex-shrink:0;margin-top:2px;min-width:18px;height:18px;border-radius:3px;'
            f'border:2px solid var(--muted);background:transparent;cursor:pointer;color:#27ae60;font-size:11px;padding:0">'
            f'</button>'
            f'<span onclick="toggleNota(\'p{i}\')" style="cursor:pointer;flex:1">{md_inline(p)}</span>'
            f'</div>'
            f'<div id="nota_p{i}" style="display:none;margin-top:6px;padding-left:26px">'
            f'<textarea id="ntxt_p{i}" placeholder="Nota o pregunta sobre esta tarea…" '
            f'style="width:100%;padding:6px 8px;border:1px solid var(--border);border-radius:6px;'
            f'font-size:.84em;background:var(--bg);color:var(--text);resize:none;font-family:inherit" rows="2"></textarea>'
            f'<div style="display:flex;gap:6px;margin-top:4px;flex-wrap:wrap">'
            f'<button onclick="enviarNota(\'p{i}\',{json.dumps(p)})" '
            f'style="padding:5px 11px;background:{KAWIIL_AZUL};color:#fff;border:0;border-radius:5px;font-size:.8em;cursor:pointer">Enviar nota</button>'
            f'<button onclick="preguntarOrigen(\'p{i}\',{json.dumps(p)})" '
            f'style="padding:5px 11px;background:transparent;border:1px solid var(--border);border-radius:5px;font-size:.8em;cursor:pointer;color:var(--text)">❓ ¿De dónde viene?</button>'
            f'</div>'
            f'<div id="nresp_p{i}" style="font-size:.82em;color:var(--muted);margin-top:5px;line-height:1.45"></div>'
            f'</div>'
            f'</li>'
            for i, p in enumerate(no_urgentes)
        )
        pend_html = (
            f'<div style="background:var(--card);border-radius:12px;padding:15px;'
            f'margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)">'
            f'<div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;'
            f'color:var(--muted);margin-bottom:9px">📌 Pendientes en proceso ({len(no_urgentes)})</div>'
            f'<ul style="margin:0;padding:0;list-style:none;font-size:.9em;color:var(--text)">{items}</ul>'
            f'</div>'
        )

    # Context for chat (plain text summary)
    import json as _json
    ctx_lines = [f"Briefing de {dlabel} — {fl}", f"Generado: {hora_gen} CDMX"]
    if urgentes:
        ctx_lines.append(f"\nURGENTES ({len(urgentes)}):")
        ctx_lines += [f"- {u}" for u in urgentes]
    if no_urgentes:
        ctx_lines.append(f"\nPENDIENTES ({len(no_urgentes)}):")
        ctx_lines += [f"- {p}" for p in no_urgentes[:15]]
    if eventos:
        ctx_lines.append(f"\nAGENDA ({len(eventos)} eventos):")
        ctx_lines += [f"- {e['inicio']}: {e['asunto']}" for e in eventos]
    if avances:
        ctx_lines.append(f"\nAVANCES RECIENTES ({len(avances)}):")
        ctx_lines += [f"- {a}" for a in avances[:8]]
    ctx_js = _json.dumps("\n".join(ctx_lines))
    chat_token_js = _json.dumps(CHAT_TOKEN)
    chat_url_js = _json.dumps(CHAT_URL)

    content = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Briefing — {esc(fl)}</title>
<style>
:root{{--bg:#f4f6fb;--card:#fff;--text:#1a1a2e;--muted:#8892a4;--border:#e6eaf2;--acc:{KAWIIL_AZUL}}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0e1118;--card:#181d2c;--text:#dde3f0;--muted:#5a6278;--border:#252a3a}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:220px}}
.hdr{{background:linear-gradient(135deg,{KAWIIL_MARINO} 0%,{KAWIIL_AZUL} 100%);color:#fff;padding:22px 18px 18px}}
.hdr-meta{{font-size:.69rem;opacity:.68;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}}
.hdr-title{{font-size:1.28rem;font-weight:700;margin-bottom:3px}}
.hdr-sub{{font-size:.82rem;opacity:.78;margin-bottom:14px}}
.kpi-row{{display:flex;gap:7px;flex-wrap:wrap}}
.main{{padding:15px;max-width:680px;margin:0 auto}}
.card{{background:var(--card);border-radius:12px;padding:15px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.sec-lbl{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:9px}}
.foot{{text-align:center;font-size:.7rem;color:var(--muted);padding:18px 16px}}
.chat-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:10px 14px;box-shadow:0 -2px 12px rgba(0,0,0,.1);z-index:100}}
.chat-bar .inner{{max-width:680px;margin:0 auto}}
#conv{{max-height:200px;overflow-y:auto;margin-bottom:8px}}
.cm{{padding:8px 12px;border-radius:10px;margin:4px 0;font-size:.87em;line-height:1.5}}
.cm.user{{background:var(--acc);color:#fff;margin-left:18%}}
.cm.bot{{background:var(--border);color:var(--text);margin-right:18%}}
.cm.bot p{{margin:.3em 0}}.cm.bot ul,.cm.bot ol{{margin:.3em 0 .3em 16px}}
.cin{{display:flex;gap:8px;align-items:flex-end}}
.cin textarea{{flex:1;padding:9px;border:1px solid var(--border);border-radius:8px;font-size:.9em;resize:none;background:var(--bg);color:var(--text);font-family:inherit}}
.cin button{{padding:9px 14px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer;font-size:.88em;white-space:nowrap}}
.chat-note{{font-size:.66rem;color:var(--muted);margin-top:4px;text-align:center}}
</style>
</head>
<body>
<div class="hdr">
{logo_html}
<div class="hdr-meta">{saludo} · {hora_gen} CDMX</div>
<div class="hdr-title">{esc(fl)}</div>
<div class="hdr-sub">Agenda de {dlabel}</div>
<div class="kpi-row">{kpi_cards}</div>
</div>
<div class="main">
<div id="jsbanner" onclick="this.remove()" style="background:#fffbe6;border:1px solid #f5c518;border-radius:10px;padding:10px 14px;margin-bottom:12px;font-size:.84em;color:#7a5c00;cursor:pointer">
📱 <b>Abre en Safari o Chrome</b> para interactuar — toca cualquier tarea para ver opciones, agregar notas o preguntar a Donna. El visor de Telegram bloquea JS. <span style="opacity:.6">(Toca aquí para cerrar)</span>
</div>
{urgentes_html}{avances_html}
<div class="card">
<div class="sec-lbl">🗓 Calendario — reuniones y compromisos ({len(eventos)})</div>
{agenda_inner}
</div>
{pend_html}
</div>
<div class="foot">Donna · Nexo Kawiil · {esc(fl)}</div>
<div class="chat-bar">
<div class="inner">
<div id="conv"></div>
<div class="cin">
<textarea id="cq" rows="2" placeholder="Pregunta o actualiza… (ej: 'marca el REPUVE como hecho', 'agrega pendiente: llamar a Erika')"></textarea>
<button onclick="preg()">Enviar</button>
</div>
<p class="chat-note">Abre en Safari/Chrome para que el chat funcione — el visor de Telegram bloquea JS.</p>
</div>
</div>
<script>
const CHAT_URL={chat_url_js},CHAT_TOKEN={chat_token_js},CTX={ctx_js};
function inl(s){{s=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');s=s.replace(/[*][*](.+?)[*][*]/g,'<strong>$1</strong>');s=s.replace(/[*](.+?)[*]/g,'<em>$1</em>');s=s.replace(/`(.+?)`/g,'<code>$1</code>');return s;}}
function md(t){{var lines=t.split('\\n'),out=[],i=0;while(i<lines.length){{var l=lines[i];if(/^[ ]*[-*] /.test(l)){{var it=[];while(i<lines.length&&/^[ ]*[-*] /.test(lines[i])){{it.push('<li>'+inl(lines[i].replace(/^[ ]*[-*] /,''))+'</li>');i++;}}out.push('<ul>'+it.join('')+'</ul>');continue;}}if(l.trim()){{out.push('<p>'+inl(l)+'</p>');}}i++;}}return out.join('');}}
function addMsg(role,html){{var d=document.createElement('div');d.className='cm '+role;d.innerHTML=html;var c=document.getElementById('conv');c.appendChild(d);c.scrollTop=c.scrollHeight;return d;}}
async function marcar(btn,txt){{btn.textContent='✓';btn.style.cssText+='background:#27ae60;border-color:#27ae60;color:#fff';var sp=btn.nextElementSibling;sp.style.textDecoration='line-through';sp.style.opacity='.45';btn.disabled=true;var msgs=CTX?[{{role:'user',content:'Contexto:\\n'+CTX}},{{role:'assistant',content:'ok'}},{{role:'user',content:'marca como hecho: '+txt}}]:[{{role:'user',content:'marca como hecho: '+txt}}];try{{var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||'';if(ans){{var note=document.createElement('div');note.style.cssText='font-size:.72em;color:#27ae60;margin-top:2px;padding-left:26px';note.textContent='✓ '+ans.slice(0,120);btn.parentElement.appendChild(note);}}}}catch(e){{console.warn(e);}}}}
function toggleNota(id){{var d=document.getElementById('nota_'+id);d.style.display=d.style.display==='none'?'block':'none';if(d.style.display==='block')document.getElementById('ntxt_'+id).focus();}}
async function _callLouis(msgs,respId){{var rd=document.getElementById('nresp_'+respId);rd.textContent='pensando…';var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;try{{var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||'(sin respuesta)';rd.innerHTML=md(ans);}}catch(e){{rd.textContent='Error: '+e;}}}}
async function enviarNota(id,task){{var txt=(document.getElementById('ntxt_'+id).value||'').trim();if(!txt)return;var msgs=CTX?[{{role:'user',content:'Contexto:\\n'+CTX}},{{role:'assistant',content:'ok'}},{{role:'user',content:'agrega nota a la tarea "'+task+'": '+txt}}]:[{{role:'user',content:'agrega nota a la tarea "'+task+'": '+txt}}];await _callLouis(msgs,id);}}
async function preguntarOrigen(id,task){{document.getElementById('nota_'+id).style.display='block';var msgs=CTX?[{{role:'user',content:'Contexto:\\n'+CTX}},{{role:'assistant',content:'ok'}},{{role:'user',content:'¿De dónde viene esta tarea en SEGUIMIENTOS.md y cuándo se creó: "'+task+'"?'}}]:[{{role:'user',content:'¿De dónde viene esta tarea: "'+task+'"?'}}];await _callLouis(msgs,id);}}
var _hist=[];
async function preg(){{var inp=document.getElementById('cq');var q=(inp.value||'').trim();if(!q)return;inp.value='';addMsg('user',inl(q));var bot=addMsg('bot','<em>pensando…</em>');try{{var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;var msgs=[];if(CTX){{msgs.push({{role:'user',content:'Contexto:\\n'+CTX}});msgs.push({{role:'assistant',content:'Contexto cargado.'}});}}msgs=msgs.concat(_hist);msgs.push({{role:'user',content:q}});var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||j.error||'(sin respuesta)';_hist.push({{role:'user',content:q}});_hist.push({{role:'assistant',content:ans}});bot.innerHTML=md(ans);}}catch(e){{bot.innerHTML='<em>Error al conectar ('+e+'). Abre este HTML en un navegador real.</em>';}}}}
document.getElementById('cq').addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();preg();}}}});
</script>
</body>
</html>'''.encode("utf-8")

    # Guardar copia para /briefing/latest
    try:
        latest = Path(os.environ.get("STATE_DIR", "/opt/openclaw/state")) / "briefing_latest.html"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(content)
    except Exception:
        pass

    return content


def build_cierre_html(fecha_obj, pendientes, kc_stalled, avances_bullets):
    """Genera el HTML del cierre del día — resumen nocturno equivalente al briefing matutino."""
    import json as _json
    fl = f"{DIAS[fecha_obj.weekday()]} {fecha_obj.day} de {MES[fecha_obj.month]} de {fecha_obj.year}"
    hora_gen = dt.datetime.now(TZ).strftime("%H:%M")
    logo_uri = logo_data_uri()
    logo_html = (f'<img src="{logo_uri}" style="height:26px;margin-bottom:8px;opacity:.9" alt="Kawiil">'
                 if logo_uri else "")

    n_pend = len(pendientes)
    n_stalled = len(kc_stalled)
    n_avances = len(avances_bullets)

    kpi_defs = [
        ("📌", str(n_pend), "pendientes abiertos", ""),
        ("🔴", str(n_stalled), "kawiil sin avance",
         "background:rgba(220,50,47,.18);color:#ff7070" if n_stalled else ""),
        ("✅", str(n_avances), "avances hoy", ""),
    ]
    kpi_cards = ""
    for icon, val, lbl, extra_style in kpi_defs:
        style = (f"flex:1;min-width:68px;background:rgba(255,255,255,.13);"
                 f"border-radius:10px;padding:11px 10px;text-align:center;{extra_style}")
        kpi_cards += (f'<div style="{style}">'
                      f'<div style="font-size:1.35rem;font-weight:700;color:#fff">{icon} {val}</div>'
                      f'<div style="font-size:.68rem;color:rgba(255,255,255,.72);text-transform:uppercase;'
                      f'letter-spacing:.06em;margin-top:3px">{lbl}</div></div>')

    if pendientes:
        pend_items = "".join(
            f'<li style="list-style:none;padding:7px 0;border-bottom:1px solid var(--border);font-size:.9em">'
            f'{md_inline(p)}</li>'
            for p in pendientes[:15]
        )
        extra = (f'<p style="font-size:.78em;color:var(--muted);margin-top:8px">...y {n_pend - 15} más</p>'
                 if n_pend > 15 else "")
        pend_section = (f'<div class="card"><div class="sec-lbl">📌 Pendientes que siguen abiertos ({n_pend})</div>'
                        f'<ul style="padding:0;margin:0">{pend_items}</ul>{extra}</div>')
    else:
        pend_section = (f'<div class="card"><div class="sec-lbl">📌 Pendientes</div>'
                        f'<p style="color:var(--muted);font-style:italic">Sin pendientes abiertos. ✅</p></div>')

    if kc_stalled:
        stalled_items = "".join(
            f'<li style="list-style:none;padding:7px 0;border-bottom:1px solid var(--border);'
            f'font-size:.9em;color:#e74c3c">{esc(s)}</li>'
            for s in kc_stalled[:8]
        )
        stalled_section = (f'<div class="card" style="border-left:3px solid #e74c3c">'
                           f'<div class="sec-lbl">🔴 Tareas kawiil.central sin avance ({n_stalled})</div>'
                           f'<ul style="padding:0;margin:0">{stalled_items}</ul>'
                           f'<p style="font-size:.78em;color:var(--muted);margin-top:8px">'
                           f"Di 'actualizar [tarea]' para agregar avance o cierre.</p></div>")
    else:
        stalled_section = ""

    if avances_bullets:
        av_items = "".join(
            f'<li style="list-style:none;padding:6px 0;border-bottom:1px solid var(--border);font-size:.9em">'
            f'{md_inline(a)}</li>'
            for a in avances_bullets[:10]
        )
        avances_section = (f'<div class="card"><div class="sec-lbl">✅ Avances detectados hoy ({n_avances})</div>'
                           f'<ul style="padding:0;margin:0">{av_items}</ul></div>')
    else:
        avances_section = (f'<div class="card"><div class="sec-lbl">✅ Avances detectados hoy</div>'
                           f'<p style="color:var(--muted);font-style:italic">No se detectaron avances nuevos.</p></div>')

    ctx_js = _json.dumps(
        f"Cierre del día — {fl} ({hora_gen} CDMX)\n"
        f"Pendientes abiertos: {n_pend}, Kawiil sin avance: {n_stalled}, Avances: {n_avances}"
    )
    chat_url_js = _json.dumps(CHAT_URL)
    chat_token_js = _json.dumps(CHAT_TOKEN)

    content = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cierre — {esc(fl)}</title>
<style>
:root{{--bg:#f4f6fb;--card:#fff;--text:#1a1a2e;--muted:#8892a4;--border:#e6eaf2;--acc:{KAWIIL_AZUL}}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0e1118;--card:#181d2c;--text:#dde3f0;--muted:#5a6278;--border:#252a3a}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:220px}}
.hdr{{background:linear-gradient(135deg,#1a3a6e 0%,#4a2a8c 100%);color:#fff;padding:22px 18px 18px}}
.hdr-meta{{font-size:.69rem;opacity:.68;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}}
.hdr-title{{font-size:1.28rem;font-weight:700;margin-bottom:3px}}
.hdr-sub{{font-size:.82rem;opacity:.78;margin-bottom:14px}}
.kpi-row{{display:flex;gap:7px;flex-wrap:wrap}}
.main{{padding:15px;max-width:680px;margin:0 auto}}
.card{{background:var(--card);border-radius:12px;padding:15px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.sec-lbl{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:9px}}
.foot{{text-align:center;font-size:.7rem;color:var(--muted);padding:18px 16px}}
.chat-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:10px 14px;box-shadow:0 -2px 12px rgba(0,0,0,.1);z-index:100}}
.chat-bar .inner{{max-width:680px;margin:0 auto}}
#conv{{max-height:200px;overflow-y:auto;margin-bottom:8px}}
.cm{{padding:8px 12px;border-radius:10px;margin:4px 0;font-size:.87em;line-height:1.5}}
.cm.user{{background:var(--acc);color:#fff;margin-left:18%}}
.cm.bot{{background:var(--border);color:var(--text);margin-right:18%}}
.cm.bot p{{margin:.3em 0}}
.cin{{display:flex;gap:8px;align-items:flex-end}}
.cin textarea{{flex:1;padding:9px;border:1px solid var(--border);border-radius:8px;font-size:.9em;resize:none;background:var(--bg);color:var(--text);font-family:inherit}}
.cin button{{padding:9px 14px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer;font-size:.88em;white-space:nowrap}}
.chat-note{{font-size:.66rem;color:var(--muted);margin-top:4px;text-align:center}}
</style>
</head>
<body>
<div class="hdr">
{logo_html}
<div class="hdr-meta">🌆 Cierre del día · {hora_gen} CDMX</div>
<div class="hdr-title">{esc(fl)}</div>
<div class="hdr-sub">Resumen del día</div>
<div class="kpi-row">{kpi_cards}</div>
</div>
<div class="main">
{stalled_section}{avances_section}{pend_section}
</div>
<div class="foot">Donna · Nexo Kawiil · Cierre {esc(fl)}</div>
<div class="chat-bar">
<div class="inner">
<div id="conv"></div>
<div class="cin">
<textarea id="cq" rows="2" placeholder="Pregunta o actualiza… (ej: 'actualizar tarea X', '¿qué quedó pendiente?')"></textarea>
<button onclick="preg()">Enviar</button>
</div>
<p class="chat-note">Abre en Safari/Chrome para que el chat funcione — el visor de Telegram bloquea JS.</p>
</div>
</div>
<script>
const CHAT_URL={chat_url_js},CHAT_TOKEN={chat_token_js},CTX={ctx_js};
function inl(s){{s=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');s=s.replace(/[*][*](.+?)[*][*]/g,'<strong>$1</strong>');s=s.replace(/[*](.+?)[*]/g,'<em>$1</em>');s=s.replace(/`(.+?)`/g,'<code>$1</code>');return s;}}
function md(t){{var lines=t.split('\\n'),out=[],i=0;while(i<lines.length){{var l=lines[i];if(/^[ ]*[-*] /.test(l)){{var it=[];while(i<lines.length&&/^[ ]*[-*] /.test(lines[i])){{it.push('<li>'+inl(lines[i].replace(/^[ ]*[-*] /,''))+'</li>');i++;}}out.push('<ul>'+it.join('')+'</ul>');continue;}}if(l.trim()){{out.push('<p>'+inl(l)+'</p>');}}i++;}}return out.join('');}}
function addMsg(role,html){{var d=document.createElement('div');d.className='cm '+role;d.innerHTML=html;var c=document.getElementById('conv');c.appendChild(d);c.scrollTop=c.scrollHeight;return d;}}
var _hist=[];
async function preg(){{var inp=document.getElementById('cq');var q=(inp.value||'').trim();if(!q)return;inp.value='';addMsg('user',inl(q));var bot=addMsg('bot','<em>pensando…</em>');try{{var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;var msgs=[];if(CTX){{msgs.push({{role:'user',content:'Contexto:\\n'+CTX}});msgs.push({{role:'assistant',content:'Contexto cargado.'}});}}msgs=msgs.concat(_hist);msgs.push({{role:'user',content:q}});var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||j.error||'(sin respuesta)';_hist.push({{role:'user',content:q}});_hist.push({{role:'assistant',content:ans}});bot.innerHTML=md(ans);}}catch(e){{bot.innerHTML='<em>Error al conectar ('+e+'). Abre este HTML en un navegador real.</em>';}}}}
document.getElementById('cq').addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();preg();}}}});
</script>
</body>
</html>'''.encode("utf-8")

    try:
        latest = Path(os.environ.get("STATE_DIR", "/opt/openclaw/state")) / "cierre_latest.html"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(content)
    except Exception:
        pass

    return content


def build_system_health_html(fecha_str, modulos, analisis_agentes):
    """Genera HTML del informe de salud del sistema cada 2 días.
    modulos: list of (icon, nombre, estado_str, ok: bool)
    analisis_agentes: dict con keys 'sistema', 'ia', 'aprendizaje', 'evolucion'
                      (cada uno es el JSON devuelto por el agente revisor-*)
    """
    import json as _json
    logo_uri = logo_data_uri()
    logo_html = (f'<img src="{logo_uri}" style="height:26px;margin-bottom:8px;opacity:.9" alt="Kawiil">'
                 if logo_uri else "")
    hora_gen = dt.datetime.now(TZ).strftime("%H:%M")

    n_ok = sum(1 for _, _, _, ok in modulos if ok)
    n_total = len(modulos)

    # Extraer datos clave de agentes para KPIs
    ag_sis = analisis_agentes.get("sistema", {})
    ag_ia = analisis_agentes.get("ia", {})
    ag_apr = analisis_agentes.get("aprendizaje", {})
    ag_evo = analisis_agentes.get("evolucion", {})

    ia_calidad = ag_ia.get("calidad", "sin datos")
    evo_progreso = ag_evo.get("progreso", "media")
    n_issues = len(ag_sis.get("errores", [])) + len(ag_ia.get("problemas", []))

    kpi_color_ok = "" if n_ok == n_total else "background:rgba(220,50,47,.18);color:#ff7070"
    kpi_color_ia = ("background:rgba(220,50,47,.18);color:#ff7070"
                    if ia_calidad == "baja" else "")
    kpi_color_evo = ("background:rgba(39,174,96,.18);color:#2ecc71"
                     if evo_progreso == "alta" else "")

    kpi_defs = [
        ("✅", f"{n_ok}/{n_total}", "módulos ok", kpi_color_ok),
        ("🤖", ia_calidad, "calidad IA", kpi_color_ia),
        ("🚀", evo_progreso, "progreso", kpi_color_evo),
    ]
    kpi_cards = ""
    for icon, val, lbl, extra in kpi_defs:
        style = f"flex:1;min-width:68px;background:rgba(255,255,255,.13);border-radius:10px;padding:11px 10px;text-align:center;{extra}"
        kpi_cards += (f'<div style="{style}">'
                      f'<div style="font-size:1.1rem;font-weight:700;color:#fff">{icon} {val}</div>'
                      f'<div style="font-size:.68rem;color:rgba(255,255,255,.72);text-transform:uppercase;'
                      f'letter-spacing:.06em;margin-top:3px">{lbl}</div></div>')

    # Módulos table
    mod_rows = "".join(
        f'<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">'
        f'<span style="font-size:1.1rem;margin-right:10px">{icon}</span>'
        f'<div style="flex:1"><div style="font-size:.9em;font-weight:500">{esc(nombre)}</div>'
        f'<div style="font-size:.78em;color:var(--muted)">{esc(estado)}</div></div>'
        f'<span style="font-size:.85rem">{"🟢" if ok else "🔴"}</span></div>'
        for icon, nombre, estado, ok in modulos
    )

    def _bullets(items, color="#27ae60"):
        if not items:
            return '<p style="color:var(--muted);font-size:.88em;font-style:italic">Sin datos</p>'
        return "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:.88em">'
            f'<span style="color:{color};margin-right:6px">•</span>{esc(str(i))}</div>'
            for i in items
        )

    def _agent_resumen(ag_data):
        r = ag_data.get("resumen", "")
        if not r:
            return ""
        return (f'<p style="font-size:.82em;color:var(--muted);margin-bottom:8px;'
                f'font-style:italic">{esc(r)}</p>')

    # Sección revisor-sistema
    sis_html = (
        _agent_resumen(ag_sis)
        + "<b style='font-size:.8em;color:var(--muted)'>ERRORES DETECTADOS</b>"
        + _bullets(ag_sis.get("errores", []), "#e74c3c")
        + "<b style='font-size:.8em;color:var(--muted);display:block;margin-top:8px'>RECOMENDACIONES TÉCNICAS</b>"
        + _bullets(ag_sis.get("recomendaciones", []), KAWIIL_AZUL)
    )

    # Sección revisor-ia
    ia_probs = ag_ia.get("problemas", [])
    ia_sugs = ag_ia.get("sugerencias", [])
    ia_pats = ag_ia.get("patrones_positivos", [])
    ia_html = (
        _agent_resumen(ag_ia)
        + ("<b style='font-size:.8em;color:var(--muted)'>PATRONES POSITIVOS</b>"
           + _bullets(ia_pats, "#27ae60") if ia_pats else "")
        + "<b style='font-size:.8em;color:var(--muted);display:block;margin-top:8px'>PROBLEMAS DE COMPORTAMIENTO</b>"
        + _bullets(ia_probs, "#e74c3c")
        + "<b style='font-size:.8em;color:var(--muted);display:block;margin-top:8px'>AJUSTES SUGERIDOS</b>"
        + _bullets(ia_sugs, KAWIIL_AZUL)
    )

    # Sección revisor-aprendizaje
    apr_n_new = ag_apr.get("learnings_nuevos_recientes", 0)
    apr_patron = ag_apr.get("patron_principal", "")
    apr_areas_ok = ag_apr.get("areas_con_datos", [])
    apr_areas_no = ag_apr.get("areas_sin_datos", [])
    apr_sugs = ag_apr.get("sugerencias", [])
    apr_html = (
        _agent_resumen(ag_apr)
        + (f'<div style="font-size:.88em;padding:6px 0;border-bottom:1px solid var(--border)">'
           f'<b>{apr_n_new}</b> learnings nuevos esta semana</div>' if isinstance(apr_n_new, int) else "")
        + (f'<div style="font-size:.88em;padding:6px 0;border-bottom:1px solid var(--border);color:var(--muted)">'
           f'<em>{esc(apr_patron)}</em></div>' if apr_patron else "")
        + (f'<div style="font-size:.78em;color:#27ae60;padding:4px 0">✓ Con datos: {esc(", ".join(apr_areas_ok))}</div>'
           if apr_areas_ok else "")
        + (f'<div style="font-size:.78em;color:#e74c3c;padding:4px 0">✗ Sin datos: {esc(", ".join(apr_areas_no))}</div>'
           if apr_areas_no else "")
        + "<b style='font-size:.8em;color:var(--muted);display:block;margin-top:8px'>SUGERENCIAS PARA POLO</b>"
        + _bullets(apr_sugs, KAWIIL_AZUL)
    )

    # Sección revisor-evolucion
    evo_features = ag_evo.get("features_deployadas", [])
    evo_pending = ag_evo.get("pendientes_detectados", [])
    evo_next = ag_evo.get("siguiente_prioridad", "")
    evo_commits = ag_evo.get("commits_recientes", "?")
    evo_html = (
        _agent_resumen(ag_evo)
        + (f'<div style="font-size:.88em;padding:6px 0;border-bottom:1px solid var(--border)">'
           f'<b>{evo_commits}</b> commits en los últimos 14 días</div>' if evo_commits != "?" else "")
        + "<b style='font-size:.8em;color:var(--muted)'>FEATURES DEPLOYADAS</b>"
        + _bullets(evo_features, "#27ae60")
        + "<b style='font-size:.8em;color:var(--muted);display:block;margin-top:8px'>PENDIENTES DETECTADOS</b>"
        + _bullets(evo_pending, "#e74c3c")
        + (f'<div style="margin-top:10px;padding:10px;background:rgba(26,110,245,.08);border-radius:8px;'
           f'border-left:3px solid {KAWIIL_AZUL};font-size:.88em">'
           f'<b style="color:{KAWIIL_AZUL}">🎯 Siguiente prioridad:</b> {esc(evo_next)}</div>'
           if evo_next else "")
    )

    # CTA para Claude Code
    code_url = "https://claude.ai/code"
    cta_html = (
        f'<div class="card" style="background:rgba(26,110,245,.08);border:1px solid rgba(26,110,245,.25)">'
        f'<div class="sec-lbl">🔧 Implementar mejoras en Claude Code</div>'
        f'<p style="font-size:.87em;line-height:1.6">'
        f'1. Abre <a href="{code_url}" style="color:{KAWIIL_AZUL}">{code_url}</a><br>'
        f'2. Conecta el repo <b>lbassoco95/nexo-louis</b><br>'
        f'3. Di: <em>"implementa las mejoras del último informe de sistema"</em></p></div>'
    )

    ctx_js = _json.dumps(f"Informe de sistema — {fecha_str} ({hora_gen} CDMX)\n"
                         f"Módulos OK: {n_ok}/{n_total}")
    chat_url_js = _json.dumps(CHAT_URL)
    chat_token_js = _json.dumps(CHAT_TOKEN)

    content = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Informe de sistema — {esc(fecha_str)}</title>
<style>
:root{{--bg:#f4f6fb;--card:#fff;--text:#1a1a2e;--muted:#8892a4;--border:#e6eaf2;--acc:{KAWIIL_AZUL}}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0e1118;--card:#181d2c;--text:#dde3f0;--muted:#5a6278;--border:#252a3a}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:220px}}
.hdr{{background:linear-gradient(135deg,#1a2a3e 0%,#2a1a6e 100%);color:#fff;padding:22px 18px 18px}}
.hdr-meta{{font-size:.69rem;opacity:.68;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}}
.hdr-title{{font-size:1.28rem;font-weight:700;margin-bottom:3px}}
.hdr-sub{{font-size:.82rem;opacity:.78;margin-bottom:14px}}
.kpi-row{{display:flex;gap:7px;flex-wrap:wrap}}
.main{{padding:15px;max-width:680px;margin:0 auto}}
.card{{background:var(--card);border-radius:12px;padding:15px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.sec-lbl{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:9px}}
.foot{{text-align:center;font-size:.7rem;color:var(--muted);padding:18px 16px}}
.chat-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:10px 14px;box-shadow:0 -2px 12px rgba(0,0,0,.1);z-index:100}}
.chat-bar .inner{{max-width:680px;margin:0 auto}}
#conv{{max-height:200px;overflow-y:auto;margin-bottom:8px}}
.cm{{padding:8px 12px;border-radius:10px;margin:4px 0;font-size:.87em;line-height:1.5}}
.cm.user{{background:var(--acc);color:#fff;margin-left:18%}}
.cm.bot{{background:var(--border);color:var(--text);margin-right:18%}}
.cm.bot p{{margin:.3em 0}}
.cin{{display:flex;gap:8px;align-items:flex-end}}
.cin textarea{{flex:1;padding:9px;border:1px solid var(--border);border-radius:8px;font-size:.9em;resize:none;background:var(--bg);color:var(--text);font-family:inherit}}
.cin button{{padding:9px 14px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer;font-size:.88em;white-space:nowrap}}
.chat-note{{font-size:.66rem;color:var(--muted);margin-top:4px;text-align:center}}
</style>
</head>
<body>
<div class="hdr">
{logo_html}
<div class="hdr-meta">🔧 Informe de sistema · {hora_gen} CDMX</div>
<div class="hdr-title">Revisión — {esc(fecha_str)}</div>
<div class="hdr-sub">Cada 2 días · Donna Kawiil</div>
<div class="kpi-row">{kpi_cards}</div>
</div>
<div class="main">
<div class="card"><div class="sec-lbl">🗂 Estado de módulos</div>{mod_rows}</div>
<div class="card"><div class="sec-lbl">⚙️ Revisor de sistema</div>{sis_html}</div>
<div class="card"><div class="sec-lbl">🤖 Revisor de IA</div>{ia_html}</div>
<div class="card"><div class="sec-lbl">📚 Revisor de aprendizaje</div>{apr_html}</div>
<div class="card"><div class="sec-lbl">🚀 Revisor de evolución</div>{evo_html}</div>
{cta_html}
</div>
<div class="foot">Donna · Nexo Kawiil · Informe {esc(fecha_str)}</div>
<div class="chat-bar">
<div class="inner">
<div id="conv"></div>
<div class="cin">
<textarea id="cq" rows="2" placeholder="Pregunta sobre el sistema… (ej: '¿por qué no llega el cierre?', 'explica el módulo de coaching')"></textarea>
<button onclick="preg()">Enviar</button>
</div>
<p class="chat-note">Abre en Safari/Chrome para que el chat funcione.</p>
</div>
</div>
<script>
const CHAT_URL={chat_url_js},CHAT_TOKEN={chat_token_js},CTX={ctx_js};
function inl(s){{s=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');s=s.replace(/[*][*](.+?)[*][*]/g,'<strong>$1</strong>');s=s.replace(/[*](.+?)[*]/g,'<em>$1</em>');return s;}}
function md(t){{var lines=t.split('\\n'),out=[],i=0;while(i<lines.length){{var l=lines[i];if(/^[ ]*[-*] /.test(l)){{var it=[];while(i<lines.length&&/^[ ]*[-*] /.test(lines[i])){{it.push('<li>'+inl(lines[i].replace(/^[ ]*[-*] /,''))+'</li>');i++;}}out.push('<ul>'+it.join('')+'</ul>');continue;}}if(l.trim()){{out.push('<p>'+inl(l)+'</p>');}}i++;}}return out.join('');}}
function addMsg(role,html){{var d=document.createElement('div');d.className='cm '+role;d.innerHTML=html;var c=document.getElementById('conv');c.appendChild(d);c.scrollTop=c.scrollHeight;return d;}}
var _hist=[];
async function preg(){{var inp=document.getElementById('cq');var q=(inp.value||'').trim();if(!q)return;inp.value='';addMsg('user',inl(q));var bot=addMsg('bot','<em>pensando…</em>');try{{var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;var msgs=[];if(CTX){{msgs.push({{role:'user',content:'Contexto:\\n'+CTX}});msgs.push({{role:'assistant',content:'Contexto cargado.'}});}}msgs=msgs.concat(_hist);msgs.push({{role:'user',content:q}});var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||j.error||'(sin respuesta)';_hist.push({{role:'user',content:q}});_hist.push({{role:'assistant',content:ans}});bot.innerHTML=md(ans);}}catch(e){{bot.innerHTML='<em>Error: '+e+'</em>';}}}}
document.getElementById('cq').addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();preg();}}}});
</script>
</body>
</html>'''.encode("utf-8")

    try:
        latest = Path(os.environ.get("STATE_DIR", "/opt/openclaw/state")) / "system_review_latest.html"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(content)
    except Exception:
        pass

    return content


def send_doc(content, fname, caption, reply_markup=None):
    token, chat = creds()
    if not token or not chat:
        print("ERROR: faltan credenciales Telegram", file=sys.stderr)
        return False
    b = "----L" + uuid.uuid4().hex
    parts = []
    fields = [("chat_id", str(chat)), ("caption", caption), ("parse_mode", "HTML")]
    if reply_markup:
        fields.append(("reply_markup", reply_markup))
    for n, v in fields:
        parts += [f"--{b}".encode(), f'Content-Disposition: form-data; name="{n}"'.encode(),
                  b"", v.encode("utf-8")]
    parts += [f"--{b}".encode(),
              f'Content-Disposition: form-data; name="document"; filename="{fname}"'.encode(),
              b"Content-Type: text/html; charset=utf-8", b"", content, f"--{b}--".encode(), b""]
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument",
                                 data=b"\r\n".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except Exception as e:
        print(f"ERROR sendDocument: {e}", file=sys.stderr)
        return False


def main():
    rango = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("hoy", "manana", "mañana") else "hoy"
    rango = "manana" if rango in ("manana", "mañana") else "hoy"
    fecha_obj = dt.datetime.now(TZ).date() + (dt.timedelta(days=1) if rango == "manana" else dt.timedelta())
    eventos, err = fetch_eventos(rango)
    pend = pendientes_abiertos()
    avances = _load_advances()
    dlabel = "mañana" if rango == "manana" else "hoy"
    av_txt = f" · {len(avances)} avances detectados" if avances else ""
    caption = (f"☀️ <b>Briefing de {dlabel}</b> — {DIAS[fecha_obj.weekday()]} {fecha_obj.day}/{fecha_obj.month}\n"
               f"<b>{len(eventos)}</b> eventos · <b>{len(pend)}</b> pendientes{av_txt}")
    import json as _json
    safari_btn = _json.dumps({"inline_keyboard": [[
        {"text": "📱 Abrir dashboard interactivo →", "url": "https://louis.kawiil.mx/briefing"}
    ]]})
    fname = f"Briefing_{fecha_obj.isoformat().replace('-', '')}.html"
    ok = send_doc(build_html(eventos, pend, fecha_obj, rango, err, avances), fname, caption, reply_markup=safari_btn)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


def build_weekly_review_html(fecha_str, cerrados, abiertos, deadlines, estancados,
                             n_dup, entregables_snap):
    """Genera HTML de la review semanal (lunes 08:00 CDMX).
    deadlines: list of str
    estancados: list of (edad_dias: int, texto: str), ya ordenados descendente
    """
    import json as _json
    logo_uri = logo_data_uri()
    logo_html = (f'<img src="{logo_uri}" style="height:26px;margin-bottom:8px;opacity:.9" alt="Kawiil">'
                 if logo_uri else "")
    hora_gen = dt.datetime.now(TZ).strftime("%H:%M")

    n_estancados = len(estancados)
    n_deadlines = len(deadlines)

    kpi_defs = [
        ("✅", str(cerrados), "cerrados", "background:rgba(39,174,96,.2);color:#2ecc71"),
        ("🟢", str(abiertos), "abiertos",
         "background:rgba(220,50,47,.18);color:#ff7070" if abiertos > 50 else ""),
        ("🐌", str(n_estancados), "estancados",
         "background:rgba(230,126,34,.18);color:#e67e22" if n_estancados > 0 else ""),
    ]
    kpi_cards = ""
    for icon, val, lbl, extra in kpi_defs:
        style = (f"flex:1;min-width:68px;background:rgba(255,255,255,.13);border-radius:10px;"
                 f"padding:11px 10px;text-align:center;{extra}")
        kpi_cards += (f'<div style="{style}">'
                      f'<div style="font-size:1.3rem;font-weight:700;color:#fff">{icon} {val}</div>'
                      f'<div style="font-size:.68rem;color:rgba(255,255,255,.72);text-transform:uppercase;'
                      f'letter-spacing:.06em;margin-top:3px">{lbl}</div></div>')

    def _row(txt, color="var(--text)"):
        return (f'<div style="padding:7px 0;border-bottom:1px solid var(--border);'
                f'font-size:.88em;color:{color}">{esc(txt)}</div>')

    deadline_rows = "".join(_row(d) for d in deadlines[:10]) if deadlines else (
        '<p style="color:var(--muted);font-size:.88em;font-style:italic">Sin vencimientos próximos</p>')

    estancado_rows = "".join(
        _row(f"({e}d) {t}", "#e67e22" if e >= 14 else "var(--text)")
        for e, t in estancados[:10]
    ) if estancados else (
        '<p style="color:var(--muted);font-size:.88em;font-style:italic">Todo en movimiento ✓</p>')

    ent_html = ""
    if entregables_snap:
        ent_html = (f'<div class="card"><div class="sec-lbl">📦 Entregables</div>'
                    f'<div style="font-size:.88em">{esc(entregables_snap.splitlines()[0])}</div></div>')

    dup_html = ""
    if n_dup:
        dup_html = (f'<div class="card" style="background:rgba(155,89,182,.08);border:1px solid rgba(155,89,182,.2)">'
                    f'<div class="sec-lbl">🧹 Duplicados detectados</div>'
                    f'<div style="font-size:.88em">{n_dup} ítem(s) duplicado(s) en AGENDA — '
                    f'di <em>«limpia la agenda»</em> para quitarlos.</div></div>')

    cta_html = (f'<div class="card" style="background:rgba(26,110,245,.06);border:1px solid rgba(26,110,245,.18)">'
                f'<div class="sec-lbl">🎯 Enfoque de la semana</div>'
                f'<p style="font-size:.87em;line-height:1.6">'
                f'Cierra primero lo que vence. Los estancados de 14+ días, '
                f'¿siguen vivos?<br><em>Di «ya hice X», «quita X» o «sigue pendiente X».</em></p></div>')

    ctx_js = _json.dumps(f"Review semanal — {fecha_str} ({hora_gen} CDMX)\n"
                         f"Cerrados: {cerrados} · Abiertos: {abiertos} · Estancados: {n_estancados}")
    chat_url_js = _json.dumps(CHAT_URL)
    chat_token_js = _json.dumps(CHAT_TOKEN)

    content = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review semanal — {esc(fecha_str)}</title>
<style>
:root{{--bg:#f4f6fb;--card:#fff;--text:#1a1a2e;--muted:#8892a4;--border:#e6eaf2;--acc:{KAWIIL_AZUL}}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0e1118;--card:#181d2c;--text:#dde3f0;--muted:#5a6278;--border:#252a3a}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:220px}}
.hdr{{background:linear-gradient(135deg,#0f4c35 0%,#1a6e45 60%,#0e3060 100%);color:#fff;padding:22px 18px 18px}}
.hdr-meta{{font-size:.69rem;opacity:.68;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}}
.hdr-title{{font-size:1.28rem;font-weight:700;margin-bottom:3px}}
.hdr-sub{{font-size:.82rem;opacity:.78;margin-bottom:14px}}
.kpi-row{{display:flex;gap:7px;flex-wrap:wrap}}
.main{{padding:15px;max-width:680px;margin:0 auto}}
.card{{background:var(--card);border-radius:12px;padding:15px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.sec-lbl{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:9px}}
.foot{{text-align:center;font-size:.7rem;color:var(--muted);padding:18px 16px}}
.chat-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:10px 14px;box-shadow:0 -2px 12px rgba(0,0,0,.1);z-index:100}}
.chat-bar .inner{{max-width:680px;margin:0 auto}}
#conv{{max-height:200px;overflow-y:auto;margin-bottom:8px}}
.cm{{padding:8px 12px;border-radius:10px;margin:4px 0;font-size:.87em;line-height:1.5}}
.cm.user{{background:var(--acc);color:#fff;margin-left:18%}}
.cm.bot{{background:var(--border);color:var(--text);margin-right:18%}}
.cm.bot p{{margin:.3em 0}}
.cin{{display:flex;gap:8px;align-items:flex-end}}
.cin textarea{{flex:1;padding:9px;border:1px solid var(--border);border-radius:8px;font-size:.9em;resize:none;background:var(--bg);color:var(--text);font-family:inherit}}
.cin button{{padding:9px 14px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer;font-size:.88em;white-space:nowrap}}
.chat-note{{font-size:.66rem;color:var(--muted);margin-top:4px;text-align:center}}
</style>
</head>
<body>
<div class="hdr">
{logo_html}
<div class="hdr-meta">📊 Review semanal · {hora_gen} CDMX</div>
<div class="hdr-title">{esc(fecha_str)}</div>
<div class="hdr-sub">{n_deadlines} con vencimiento · {n_estancados} estancados</div>
<div class="kpi-row">{kpi_cards}</div>
</div>
<div class="main">
<div class="card">
  <div class="sec-lbl">⏰ A cerrar esta semana ({n_deadlines})</div>
  {deadline_rows}
</div>
<div class="card">
  <div class="sec-lbl">🐌 Estancados 7+ días ({n_estancados})</div>
  {estancado_rows}
</div>
{ent_html}{dup_html}{cta_html}
</div>
<div class="foot">Donna · Nexo Kawiil · Review {esc(fecha_str)}</div>
<div class="chat-bar">
<div class="inner">
<div id="conv"></div>
<div class="cin">
<textarea id="cq" rows="2" placeholder="'cierra tarea X', 'quita Y', '¿qué vence hoy?'"></textarea>
<button onclick="preg()">Enviar</button>
</div>
<p class="chat-note">Abre en Safari/Chrome para que el chat funcione.</p>
</div>
</div>
<script>
const CHAT_URL={chat_url_js},CHAT_TOKEN={chat_token_js},CTX={ctx_js};
function inl(s){{s=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');s=s.replace(/[*][*](.+?)[*][*]/g,'<strong>$1</strong>');s=s.replace(/[*](.+?)[*]/g,'<em>$1</em>');return s;}}
function md(t){{var lines=t.split('\\n'),out=[],i=0;while(i<lines.length){{var l=lines[i];if(/^[ ]*[-*] /.test(l)){{var it=[];while(i<lines.length&&/^[ ]*[-*] /.test(lines[i])){{it.push('<li>'+inl(lines[i].replace(/^[ ]*[-*] /,''))+'</li>');i++;}}out.push('<ul>'+it.join('')+'</ul>');continue;}}if(l.trim()){{out.push('<p>'+inl(l)+'</p>');}}i++;}}return out.join('');}}
function addMsg(role,html){{var d=document.createElement('div');d.className='cm '+role;d.innerHTML=html;var c=document.getElementById('conv');c.appendChild(d);c.scrollTop=c.scrollHeight;return d;}}
var _hist=[];
async function preg(){{var inp=document.getElementById('cq');var q=(inp.value||'').trim();if(!q)return;inp.value='';addMsg('user',inl(q));var bot=addMsg('bot','<em>pensando…</em>');try{{var h={{'Content-Type':'application/json'}};if(CHAT_TOKEN)h['Authorization']='Bearer '+CHAT_TOKEN;var msgs=[];if(CTX){{msgs.push({{role:'user',content:'Contexto:\\n'+CTX}});msgs.push({{role:'assistant',content:'Contexto cargado.'}});}}msgs=msgs.concat(_hist);msgs.push({{role:'user',content:q}});var r=await fetch(CHAT_URL,{{method:'POST',headers:h,body:JSON.stringify({{messages:msgs}})}});var j=await r.json();var ans=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||j.error||'(sin respuesta)';_hist.push({{role:'user',content:q}});_hist.push({{role:'assistant',content:ans}});bot.innerHTML=md(ans);}}catch(e){{bot.innerHTML='<em>Error: '+e+'</em>';}}}}
document.getElementById('cq').addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();preg();}}}});
</script>
</body>
</html>'''.encode("utf-8")

    try:
        latest = Path(os.environ.get("STATE_DIR", "/opt/openclaw/state")) / "review_semanal_latest.html"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(content)
    except Exception:
        pass

    return content


if __name__ == "__main__":
    sys.exit(main())
