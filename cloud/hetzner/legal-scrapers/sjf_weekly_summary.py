#!/usr/bin/env python3
"""Boletin semanal SJF -> documento HTML adjunto a Telegram (estilo DOF).
Agrupado POR MATERIA; cada tesis etiquetada como Jurisprudencia o Tesis."""
import datetime as dt, html, os, sqlite3, sys, urllib.request, uuid
from pathlib import Path

DB = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
URL = "https://sjf2.scjn.gob.mx/detalle/tesis/{}"
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
# Orden preferido de materias (las demas van alfabeticas despues)
MORD = ["constitucional", "penal", "civil", "administrativa", "laboral",
        "mercantil", "fiscal", "comun", "común"]


def esc(s):
    return html.escape((s or "").strip())


def fecha_es(iso):
    try:
        y, m, d = iso[:10].split("-")
        return f"{int(d)} de {MES[int(m)]} de {y}"
    except Exception:
        return iso


def es_juris(r):
    return str(r["ta_tj"]).strip() == "1"


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


def mrank(m):
    ml = m.lower()
    for i, k in enumerate(MORD):
        if k == ml:
            return (0, i, ml)
    return (1, 0, ml)  # las no listadas, alfabeticas al final


def build_html(rows, etiqueta):
    fx = fecha_es(etiqueta)
    hoy = dt.date.today().strftime("%d/%m/%Y")
    n_jur = sum(1 for r in rows if es_juris(r))
    n_tes = len(rows) - n_jur
    # agrupar por materia (una tesis con varias materias aparece en cada una)
    grupos = {}
    for r in rows:
        mats = [x.strip() for x in (r["materias"] or "Sin materia").split(",") if x.strip()]
        for m in (mats or ["Sin materia"]):
            grupos.setdefault(m, []).append(r)
    secc = []
    for mat in sorted(grupos, key=mrank):
        it = grupos[mat]
        # jurisprudencias primero, luego tesis
        it = sorted(it, key=lambda r: (0 if es_juris(r) else 1, r["registro_digital"]))
        nj = sum(1 for r in it if es_juris(r))
        filas = "".join(
            f'<tr><td class="reg"><a href="{URL.format(r["registro_digital"])}">{r["registro_digital"]}</a></td>'
            f'<td><span class="tag {"j" if es_juris(r) else "t"}">'
            f'{"Jurisprudencia" if es_juris(r) else "Tesis"}</span> '
            f'<span class="rubro">{esc(r["rubro"]).rstrip(". ")}</span></td></tr>'
            for r in it)
        secc.append(
            f'<h2>{esc(mat)} <span class="c">({len(it)} · {nj} jur / {len(it) - nj} tesis)</span></h2>'
            f'<table><tbody>{filas}</tbody></table>')
    body = "\n".join(secc)
    doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Semanario Judicial — {esc(fx)}</title><style>
body{{font-family:'Georgia',serif;max-width:900px;margin:40px auto;padding:0 24px;color:#1a1a1a;line-height:1.65}}
h1{{font-size:1.6em;border-bottom:3px solid #8B1A2E;padding-bottom:8px;color:#8B1A2E}}
h2{{font-size:1.2em;color:#2c3e50;margin-top:1.8em;border-bottom:1px solid #ddd;padding-bottom:4px}}
.c{{color:#999;font-weight:normal;font-size:.82em}}
table{{border-collapse:collapse;width:100%;margin:.5em 0}}
td{{border-bottom:1px solid #eee;padding:7px 10px;vertical-align:top}}
td.reg{{width:88px;font-size:.9em;white-space:nowrap}}
td.reg a{{color:#8B1A2E;text-decoration:none;font-weight:bold}}
.rubro{{font-size:.95em}}
.tag{{font-size:.7em;font-weight:bold;padding:1px 6px;border-radius:4px;margin-right:4px;white-space:nowrap}}
.tag.j{{background:#8B1A2E;color:#fff}} .tag.t{{background:#e8e8e8;color:#555}}
.hd{{display:flex;justify-content:space-between;margin-bottom:1.2em;padding:14px 16px;background:#f8f9fa;border-radius:6px;font-size:.85em;color:#666}}
.resumen{{background:#f8f9fa;border-left:4px solid #8B1A2E;padding:10px 14px;margin:1em 0;font-size:.95em}}
.ft{{margin-top:3em;padding-top:1em;border-top:1px solid #ddd;font-size:.8em;color:#999;text-align:center}}
</style></head><body>
<div class="hd"><span>Elaborado por: <strong>Louis · Kawiil</strong> — Semanario Judicial de la Federación</span><span>Generado: {hoy}</span></div>
<h1>⚖️ Semanario Judicial — Edición del {esc(fx)}</h1>
<div class="resumen"><strong>{len(rows)}</strong> publicaciones: <strong>{n_jur}</strong> jurisprudencias · <strong>{n_tes}</strong> tesis aisladas. Organizadas por materia. Da clic en el registro para el detalle en el SJF.</div>
{body}
<div class="ft">Documento generado por Louis (Kawiil) · {hoy} · Fuente: SCJN · Nota: una tesis con varias materias aparece en cada una.</div>
</body></html>"""
    return doc.encode("utf-8")


def main():
    if not Path(DB).exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    last = conn.execute("SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis").fetchone()[0]
    if not last:
        print("BD vacía", file=sys.stderr)
        return 1
    rows = conn.execute(
        "SELECT registro_digital,rubro,ta_tj,tipo_tesis,materias,fecha_publicacion "
        "FROM tesis WHERE substr(fecha_publicacion,1,10)=? ORDER BY registro_digital ASC",
        (last,)).fetchall()
    conn.close()
    fx = fecha_es(last)
    n_jur = sum(1 for r in rows if es_juris(r))
    caption = (f"⚖️ <b>Semanario Judicial</b> — edición del {esc(fx)}\n"
               f"{len(rows)} publicaciones ({n_jur} jurisprudencias · {len(rows) - n_jur} tesis), por materia. Detalle en el adjunto.")
    fname = f"Semanario_SJF_{last.replace('-', '')}.html"
    ok = send_doc(build_html(rows, last), fname, caption)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
