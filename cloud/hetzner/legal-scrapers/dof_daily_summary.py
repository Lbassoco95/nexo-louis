#!/usr/bin/env python3
"""Boletin DIARIO del DOF -> documento HTML adjunto a Telegram (estilo boletin).
Toma la edicion publicada mas reciente (fecha valida <= hoy), agrupa por
dependencia (nombre_cod_orga_uno) y lista cada nota con su tipo y link al DOF.
Solo dato duro de la BD; cero interpretacion."""
import datetime as dt, html, os, sqlite3, sys, urllib.request, uuid
from pathlib import Path

DB = os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof/biblioteca_dof.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
# Detalle de la nota en el DOF: requiere codigo + fecha dd/mm/yyyy
URL = "https://www.dof.gob.mx/nota_detalle.php?codigo={cod}&fecha={f}"
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
EDIC = {"MAT": "Matutina", "VES": "Vespertina", "EXT": "Extraordinaria"}


def esc(s):
    return html.escape((s or "").strip())


def fecha_larga(iso):
    try:
        d = dt.date.fromisoformat(iso[:10])
        return f"{DIAS[d.weekday()]} {d.day} de {MES[d.month]} de {d.year}"
    except Exception:
        return iso


def fecha_ddmmyyyy(iso):
    try:
        y, m, d = iso[:10].split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return iso


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


def send_msg(text):
    token, chat = creds()
    if not token or not chat:
        return False
    import json
    body = json.dumps({"chat_id": chat, "text": text[:3900], "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False


def build_html(rows, fecha):
    fl = fecha_larga(fecha)
    ddmm = fecha_ddmmyyyy(fecha)
    hoy = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    # agrupar por dependencia (orden: por # de notas desc, luego alfabetico)
    grupos = {}
    for r in rows:
        dep = (r["nombre_cod_orga_uno"] or r["seccion"] or "Otros").strip() or "Otros"
        grupos.setdefault(dep, []).append(r)
    orden = sorted(grupos, key=lambda d: (-len(grupos[d]), d.lower()))
    secc = []
    for dep in orden:
        it = grupos[dep]
        filas = []
        for r in it:
            cod = r["cod_nota"]
            tipo = esc(r["tipo_nota_raw"])
            ed = r["edicion"] or ""
            etag = "" if ed in ("", "MAT") else f'<span class="ed">{esc(EDIC.get(ed, ed))}</span> '
            ttag = f'<span class="tag">{tipo}</span> ' if tipo else ""
            link = URL.format(cod=cod, f=ddmm)
            filas.append(
                f'<tr><td class="cod"><a href="{link}">{cod}</a></td>'
                f'<td>{etag}{ttag}<span class="t">{esc(r["titulo"]).rstrip(". ")}</span></td></tr>')
        secc.append(f'<h2>{esc(dep)} <span class="c">({len(it)})</span></h2>'
                    f'<table><tbody>{"".join(filas)}</tbody></table>')
    body = "\n".join(secc)
    doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DOF — {esc(fl)}</title><style>
body{{font-family:'Georgia',serif;max-width:900px;margin:40px auto;padding:0 24px;color:#1a1a1a;line-height:1.6}}
h1{{font-size:1.55em;border-bottom:3px solid #0b5d2e;padding-bottom:8px;color:#0b5d2e}}
h2{{font-size:1.1em;color:#2c3e50;margin-top:1.6em;border-bottom:1px solid #ddd;padding-bottom:4px}}
.c{{color:#999;font-weight:normal;font-size:.82em}}
table{{border-collapse:collapse;width:100%;margin:.4em 0}}
td{{border-bottom:1px solid #eee;padding:6px 9px;vertical-align:top}}
td.cod{{width:78px;font-size:.85em;white-space:nowrap}}
td.cod a{{color:#0b5d2e;text-decoration:none;font-weight:bold}}
.t{{font-size:.93em}}
.tag{{font-size:.68em;font-weight:bold;background:#e8e8e8;color:#555;padding:1px 6px;border-radius:4px;margin-right:4px}}
.ed{{font-size:.68em;font-weight:bold;background:#0b5d2e;color:#fff;padding:1px 6px;border-radius:4px;margin-right:4px}}
.hd{{display:flex;justify-content:space-between;margin-bottom:1.2em;padding:14px 16px;background:#f4f7f5;border-radius:6px;font-size:.85em;color:#666}}
.resumen{{background:#f4f7f5;border-left:4px solid #0b5d2e;padding:10px 14px;margin:1em 0;font-size:.95em}}
.ft{{margin-top:3em;padding-top:1em;border-top:1px solid #ddd;font-size:.8em;color:#999;text-align:center}}
</style></head><body>
<div class="hd"><span>Elaborado por: <strong>Louis · Kawiil</strong> — Diario Oficial de la Federación</span><span>Generado: {hoy}</span></div>
<h1>📰 Diario Oficial — {esc(fl)}</h1>
<div class="resumen"><strong>{len(rows)}</strong> publicaciones en <strong>{len(grupos)}</strong> dependencias. Da clic en el código para abrir la nota en el DOF.</div>
{body}
<div class="ft">Documento generado por Louis (Kawiil) · {hoy} · Fuente: Diario Oficial de la Federación (SEGOB)</div>
</body></html>"""
    return doc.encode("utf-8")


def main():
    if not Path(DB).exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    # fecha valida mas reciente (descarta fechas futuras mal parseadas)
    fecha = conn.execute(
        "SELECT MAX(fecha) FROM notas WHERE fecha <= date('now')").fetchone()[0]
    if not fecha:
        print("Sin fechas validas", file=sys.stderr)
        return 1
    # dedup: no reenviar la misma edicion si ya se mando (timer diario; DOF no publica findes)
    state = Path(os.environ.get("DOF_STATE", "/opt/openclaw/state/dof_last_sent.txt"))
    last_sent = state.read_text().strip() if state.exists() else ""
    if fecha == last_sent and "--force" not in sys.argv:
        print(f"DOF {fecha} ya enviado; sin edicion nueva, skip")
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT cod_nota, edicion, seccion, nombre_cod_orga_uno, tipo_nota_raw, titulo "
        "FROM notas WHERE fecha=? ORDER BY seccion, nombre_cod_orga_uno, cod_nota", (fecha,)).fetchall()
    conn.close()
    if not rows:
        send_msg(f"📰 <b>DOF</b> — sin notas para {fecha_larga(fecha)}.")
        print("Sin notas")
        return 0
    fl = fecha_larga(fecha)
    deps = len({(r["nombre_cod_orga_uno"] or r["seccion"] or "Otros") for r in rows})
    caption = (f"📰 <b>Diario Oficial</b> — {esc(fl)}\n"
               f"{len(rows)} publicaciones en {deps} dependencias. Detalle por dependencia en el adjunto.")
    fname = f"DOF_{fecha.replace('-', '')}.html"
    ok = send_doc(build_html(rows, fecha), fname, caption)
    if ok:
        try:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(fecha)
        except Exception as e:
            print(f"WARN: no guardé estado: {e}", file=sys.stderr)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
