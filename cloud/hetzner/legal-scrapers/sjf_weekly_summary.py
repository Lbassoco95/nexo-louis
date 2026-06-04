#!/usr/bin/env python3
"""Boletin semanal SJF -> documento HTML adjunto a Telegram (estilo DOF)."""
import datetime as dt, html, os, sqlite3, sys, urllib.request, uuid
from pathlib import Path

DB = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
URL = "https://sjf2.scjn.gob.mx/detalle/tesis/{}"
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
ORD = ["pleno de la suprema", "primera sala", "segunda sala", "plenos regionales",
       "tribunales colegiados", "tribunal colegiado de apelaci"]


def esc(s):
    return html.escape((s or "").strip())


def fecha_es(iso):
    try:
        y, m, d = iso[:10].split("-")
        return f"{int(d)} de {MES[int(m)]} de {y}"
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


def rank(inst):
    for i, k in enumerate(ORD):
        if k in (inst or "").lower():
            return i
    return len(ORD)


def build_html(rows, etiqueta):
    fx = fecha_es(etiqueta)
    hoy = dt.date.today().strftime("%d/%m/%Y")
    grupos = {}
    for r in rows:
        grupos.setdefault((r["instancia"] or "Otras").strip(), []).append(r)
    secc = []
    for inst in sorted(grupos, key=rank):
        it = grupos[inst]
        filas = "".join(
            f'<tr><td class="reg"><a href="{URL.format(r["registro_digital"])}">{r["registro_digital"]}</a></td>'
            f'<td><div class="rubro">{esc(r["rubro"]).rstrip(". ")}</div>'
            f'<div class="meta">{esc(r["tipo_tesis"])}{(" · " + esc(r["localizacion"])) if r["localizacion"] else ""}</div></td></tr>'
            for r in it)
        secc.append(f'<h2>{esc(inst)} <span class="c">({len(it)})</span></h2><table><tbody>{filas}</tbody></table>')
    body = "\n".join(secc)
    doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Semanario Judicial — {esc(fx)}</title><style>
body{{font-family:'Georgia',serif;max-width:900px;margin:40px auto;padding:0 24px;color:#1a1a1a;line-height:1.65}}
h1{{font-size:1.6em;border-bottom:3px solid #8B1A2E;padding-bottom:8px;color:#8B1A2E}}
h2{{font-size:1.2em;color:#2c3e50;margin-top:1.8em;border-bottom:1px solid #ddd;padding-bottom:4px}}
.c{{color:#999;font-weight:normal;font-size:.85em}}
table{{border-collapse:collapse;width:100%;margin:.6em 0}}
td{{border-bottom:1px solid #eee;padding:8px 10px;vertical-align:top}}
td.reg{{width:92px;font-size:.9em;white-space:nowrap}}
td.reg a{{color:#8B1A2E;text-decoration:none;font-weight:bold}}
.rubro{{font-size:.96em}} .meta{{color:#888;font-size:.8em;margin-top:2px}}
.hd{{display:flex;justify-content:space-between;margin-bottom:1.5em;padding:14px 16px;background:#f8f9fa;border-radius:6px;font-size:.85em;color:#666}}
.ft{{margin-top:3em;padding-top:1em;border-top:1px solid #ddd;font-size:.8em;color:#999;text-align:center}}
</style></head><body>
<div class="hd"><span>Elaborado por: <strong>Louis · Kawiil</strong> — Semanario Judicial de la Federación</span><span>Generado: {hoy}</span></div>
<h1>⚖️ Semanario Judicial — Edición del {esc(fx)}</h1>
<p><strong>{len(rows)}</strong> tesis y jurisprudencias. Da clic en el registro para abrir el detalle en el SJF.</p>
{body}
<div class="ft">Documento generado por Louis (Kawiil) · {hoy} · Fuente: SCJN</div>
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
        "SELECT registro_digital,rubro,instancia,tipo_tesis,localizacion,fecha_publicacion "
        "FROM tesis WHERE substr(fecha_publicacion,1,10)=? ORDER BY registro_digital ASC",
        (last,)).fetchall()
    conn.close()
    fx = fecha_es(last)
    caption = f"⚖️ <b>Semanario Judicial</b> — edición del {esc(fx)}\n{len(rows)} tesis y jurisprudencias. Detalle en el adjunto."
    fname = f"Semanario_SJF_{last.replace('-', '')}.html"
    ok = send_doc(build_html(rows, last), fname, caption)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
