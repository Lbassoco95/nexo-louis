#!/usr/bin/env python3
"""briefing_doc.py — Briefing matutino HTML interactivo como documento a Telegram.

Fuentes de verdad:
  • Calendario: m365.py calendario <tenant> <hoy|manana>  (kawiil + yoltik)
  • Pendientes: líneas '- [ ]' de SEGUIMIENTOS.md
  • Avances detectados: /opt/openclaw/state/advances_delta.json (generado a las 06:30)

El HTML usa louis_html.py (render_page + kpi_cards) con chat widget embebido.
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
    """Genera el HTML del briefing usando louis_html para el shell y tabla inline para la agenda."""
    # Importar el engine de HTML desde el mismo directorio
    import importlib.util, sys as _sys
    _this_dir = Path(__file__).parent
    _html_path = _this_dir / "louis_html.py"
    try:
        spec = importlib.util.spec_from_file_location("louis_html", str(_html_path))
        louis_html = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(louis_html)
    except Exception:
        louis_html = None

    if avances is None:
        avances = _load_advances()

    fl = f"{DIAS[fecha_obj.weekday()]} {fecha_obj.day} de {MES[fecha_obj.month]} de {fecha_obj.year}"

    if louis_html:
        kpi_row = louis_html.kpi_cards([
            {"value": str(len(eventos)), "label": "juntas hoy"},
            {"value": str(len(pend)), "label": "pendientes"},
            {"value": str(len(avances)), "label": "avanzaron ayer"},
        ])

        avances_html = ""
        if avances:
            items = "".join(f"<li style='margin-bottom:4px'>{esc(b)}</li>" for b in avances)
            avances_html = (f"<details class='sec' open><summary>☀️ Lo que avanzó</summary>"
                            f"<ul style='font-size:.95em'>{items}</ul></details>")

        agenda_html = (f"<details class='sec' open><summary>⏰ Agenda ({len(eventos)} eventos)</summary>"
                       f"{_build_agenda_table(eventos, err)}</details>")

        if pend:
            pend_items = "".join(f"<li style='margin-bottom:5px'>{esc(p)}</li>" for p in pend)
            pend_section = (f"<details class='sec' open><summary>📌 Pendientes abiertos ({len(pend)})</summary>"
                            f"<ul style='font-size:.95em'>{pend_items}</ul></details>")
        else:
            pend_section = ("<details class='sec'><summary>📌 Pendientes</summary>"
                            "<p style='color:#888;font-style:italic'>Sin pendientes abiertos en SEGUIMIENTOS.</p></details>")

        ctx_md = "\n".join(f"- {p}" for p in pend)
        content = louis_html.render_page(
            titulo=f"Briefing — {fl}",
            agente="Louis",
            body_html=kpi_row + avances_html + agenda_html + pend_section,
            ctx_md=ctx_md,
            con_chat=True,
            resumen=f"{len(eventos)} juntas · {len(pend)} pendientes · {len(avances)} avances detectados",
            fuente=f"M365 en vivo ({', '.join(TENANTS)}) + SEGUIMIENTOS.md",
        )
    else:
        # Fallback: HTML estático simple (sin louis_html)
        gen = dt.datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
        agenda_tbl = _build_agenda_table(eventos, err)
        if pend:
            lis = "".join(f"<li>{esc(p)}</li>" for p in pend)
            pend_html = f'<h2>📌 Pendientes abiertos ({len(pend)})</h2><ul>{lis}</ul>'
        else:
            pend_html = '<h2>📌 Pendientes</h2><p style="color:#888">Sin pendientes abiertos.</p>'
        avances_fb = ""
        if avances:
            items = "".join(f"<li>{esc(b)}</li>" for b in avances)
            avances_fb = f"<h2>☀️ Lo que avanzó</h2><ul>{items}</ul>"
        content = (f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">'
                   f'<title>Briefing — {esc(fl)}</title></head><body>'
                   f'<h1>🗓️ Briefing — {esc(fl)}</h1>'
                   f'{avances_fb}{agenda_tbl}{pend_html}'
                   f'<p style="font-size:.78em;color:#999">Generado: {gen} CDMX</p>'
                   f'</body></html>').encode("utf-8")

    # Guardar copia para /briefing/latest
    try:
        latest = Path(os.environ.get("STATE_DIR", "/opt/openclaw/state")) / "briefing_latest.html"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(content)
    except Exception:
        pass

    return content


def send_doc(content, fname, caption):
    token, chat = creds()
    if not token or not chat:
        print("ERROR: faltan credenciales Telegram", file=sys.stderr)
        return False
    b = "----L" + uuid.uuid4().hex
    parts = []
    for n, v in (("chat_id", str(chat)), ("caption", caption), ("parse_mode", "HTML")):
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
               f"<b>{len(eventos)}</b> eventos en calendario · <b>{len(pend)}</b> pendientes{av_txt}. "
               f"Abre el adjunto HTML en tu navegador para el dashboard interactivo.")
    fname = f"Briefing_{fecha_obj.isoformat().replace('-', '')}.html"
    ok = send_doc(build_html(eventos, pend, fecha_obj, rango, err, avances), fname, caption)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
