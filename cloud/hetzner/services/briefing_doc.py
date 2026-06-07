#!/usr/bin/env python3
"""briefing_doc.py — Briefing/agenda DETERMINÍSTICO como documento HTML a Telegram.

Por qué existe: el briefing del LLM "razonaba" la agenda sobre la memoria y
revivía juntas viejas (ej. inventaba una junta que ya había pasado). Aquí la
agenda sale SOLO del calendario M365 EN VIVO + los pendientes abiertos de
AGENDA.md. Cero interpretación, cero invención. Y como documento HTML, se ve
en tabla (no la lista fea de Telegram).

Uso:
  briefing_doc.py [hoy|manana]   (default: hoy)

Fuentes de verdad:
  • Calendario: m365.py calendario <tenant> <hoy|manana>  (kawiil + yoltik)
  • Pendientes: líneas '- [ ]' de AGENDA.md
"""
import datetime as dt, html, json, os, re, subprocess, sys, urllib.request, uuid
from pathlib import Path

HOME_OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))
SPACE = HOME_OC / "spaces" / "general"
AGENDA = SPACE / "AGENDA.md"
CREDS = os.environ.get("TELEGRAM_CREDS", str(HOME_OC / "credentials" / "telegram.env"))
M365 = HOME_OC / "scripts" / "m365" / "m365.py"
if not M365.exists():
    M365 = HOME_OC / "scripts" / "m365.py"
TENANTS = [t.strip() for t in os.environ.get("BRIEFING_TENANTS", "kawiil,yoltik").split(",") if t.strip()]

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
TZ = dt.timezone(dt.timedelta(hours=-6))  # CDMX


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
    if not AGENDA.exists():
        return []
    items = []
    for ln in AGENDA.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*-\s*\[\s*\]\s+", ln):
            txt = re.sub(r"^\s*-\s*\[\s*\]\s+", "", ln).strip()
            if txt:
                items.append(txt)
    return items[:max_items]


def build_html(eventos, pend, fecha_obj, rango, err):
    fl = f"{DIAS[fecha_obj.weekday()]} {fecha_obj.day} de {MES[fecha_obj.month]} de {fecha_obj.year}"
    gen = dt.datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    # tabla de calendario
    if eventos:
        filas = []
        for e in eventos:
            tag = '<span class="on">En línea</span> ' if e.get("online") else ""
            extra = " · ".join(x for x in (e.get("lugar", ""), e.get("asistentes", "")) if x)
            extra_html = f'<div class="sub">{esc(extra)}</div>' if extra else ""
            ini, fin, asunto, tenant = esc(e["inicio"]), esc(e["fin"]), esc(e["asunto"]), esc(e["tenant"])
            filas.append(
                f'<tr><td class="h">{ini}–{fin}</td>'
                f'<td>{tag}<b>{asunto}</b>{extra_html}</td>'
                f'<td class="t">{tenant}</td></tr>')
        cal = f'<table><thead><tr><th>Hora</th><th>Evento</th><th>Cuenta</th></tr></thead><tbody>{"".join(filas)}</tbody></table>'
    else:
        cal = '<p class="vacio">Sin eventos en el calendario para este día. ✅ Día libre de juntas.</p>'
    # pendientes
    if pend:
        lis = "".join(f"<li>{esc(p)}</li>" for p in pend)
        pend_html = f'<h2>📌 Pendientes abiertos ({len(pend)})</h2><ul class="pend">{lis}</ul>'
    else:
        pend_html = '<h2>📌 Pendientes</h2><p class="vacio">Sin pendientes abiertos en AGENDA.</p>'
    nota = f'<p class="warn">⚠️ No pude leer parte del calendario: {esc(err)}</p>' if err else ""
    doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agenda — {esc(fl)}</title><style>
body{{font-family:'Georgia',serif;max-width:860px;margin:32px auto;padding:0 22px;color:#1a1a1a;line-height:1.55}}
h1{{font-size:1.5em;border-bottom:3px solid #1f4e79;padding-bottom:8px;color:#1f4e79}}
h2{{font-size:1.1em;color:#2c3e50;margin-top:1.5em;border-bottom:1px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:.5em 0}}
th{{text-align:left;font-size:.78em;text-transform:uppercase;letter-spacing:.04em;color:#888;border-bottom:2px solid #1f4e79;padding:6px 9px}}
td{{border-bottom:1px solid #eee;padding:9px;vertical-align:top}}
td.h{{white-space:nowrap;font-weight:bold;color:#1f4e79;width:96px;font-size:.92em}}
td.t{{font-size:.72em;color:#999;text-transform:capitalize;width:64px}}
.sub{{font-size:.8em;color:#777;margin-top:2px}}
.on{{font-size:.66em;font-weight:bold;background:#1f4e79;color:#fff;padding:1px 6px;border-radius:4px}}
.pend li{{margin-bottom:5px}} .pend{{font-size:.95em}}
.vacio{{color:#888;font-style:italic}} .warn{{color:#b35900;font-size:.85em}}
.hd{{display:flex;justify-content:space-between;margin-bottom:1em;padding:12px 15px;background:#eef3f8;border-radius:6px;font-size:.84em;color:#666}}
.ft{{margin-top:2.5em;padding-top:1em;border-top:1px solid #ddd;font-size:.78em;color:#999;text-align:center}}
</style></head><body>
<div class="hd"><span><strong>Louis · Kawiil</strong> — Agenda del día</span><span>Generado: {gen} CDMX</span></div>
<h1>🗓️ Agenda — {esc(fl)}</h1>
{nota}
<h2>⏰ Calendario ({len(eventos)} eventos)</h2>
{cal}
{pend_html}
<div class="ft">Fuente: Calendario M365 en vivo ({", ".join(TENANTS)}) + AGENDA.md · Dato duro, sin interpretación · Louis (Kawiil)</div>
</body></html>"""
    return doc.encode("utf-8")


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
    dlabel = "mañana" if rango == "manana" else "hoy"
    caption = (f"🗓️ <b>Agenda de {dlabel}</b> — {DIAS[fecha_obj.weekday()]} {fecha_obj.day}/{fecha_obj.month}\n"
               f"<b>{len(eventos)}</b> eventos en calendario · <b>{len(pend)}</b> pendientes. Detalle visual en el adjunto.")
    fname = f"Agenda_{fecha_obj.isoformat().replace('-', '')}.html"
    ok = send_doc(build_html(eventos, pend, fecha_obj, rango, err), fname, caption)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
