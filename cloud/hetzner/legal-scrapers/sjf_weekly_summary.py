#!/usr/bin/env python3
"""
sjf_weekly_summary.py — boletín semanal del SJF para Polo (lunes vía systemd timer).

Genera un DOCUMENTO HTML con estilo (igual que los boletines del DOF) con la
edición semanal más reciente del Semanario, agrupado por instancia, y lo envía a
Telegram como ARCHIVO ADJUNTO (.html) vía sendDocument — no como mensaje de texto.

Solo reporta lo que está EN la BD (datos legales reales). Reusa las credenciales
de Telegram del scheduler de Louis.

Uso:
    python3 sjf_weekly_summary.py            # boletín de la última edición
    python3 sjf_weekly_summary.py --dias 7   # de lo publicado en los últimos 7 días
    python3 sjf_weekly_summary.py --texto    # forzar mensaje de texto (sin adjunto)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sqlite3
import sys
import urllib.request
import uuid
from pathlib import Path

DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
DETALLE_URL = "https://sjf2.scjn.gob.mx/detalle/tesis/{}"

_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _esc(s: str) -> str:
    return html.escape((s or "").strip())


def _fecha_es(iso: str) -> str:
    try:
        y, m, d = iso[:10].split("-")
        return f"{int(d)} de {_MESES[int(m)]} de {y}"
    except Exception:
        return iso


# ── Credenciales / envío ──────────────────────────────────────────────────────
def load_creds(path: str) -> dict:
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _token_chat():
    creds = load_creds(CREDS)
    token = creds.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = creds.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    return token, chat_id


def send_document(content: bytes, filename: str, caption: str) -> bool:
    """Envía un archivo a Telegram con sendDocument (multipart/form-data)."""
    token, chat_id = _token_chat()
    if not token or not chat_id:
        print("ERROR: faltan credenciales Telegram", file=sys.stderr)
        return False
    boundary = "----LouisSJF" + uuid.uuid4().hex
    nl = b"\r\n"
    parts = []
    for name, value in (("chat_id", str(chat_id)), ("caption", caption), ("parse_mode", "HTML")):
        parts += [f"--{boundary}".encode(), f'Content-Disposition: form-data; name="{name}"'.encode(),
                  b"", value.encode("utf-8")]
    parts += [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode(),
        b"Content-Type: text/html; charset=utf-8", b"", content,
        f"--{boundary}--".encode(), b""]
    body = nl.join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except Exception as e:
        print(f"ERROR sendDocument: {e}", file=sys.stderr)
        return False


def send_message(text: str) -> bool:
    token, chat_id = _token_chat()
    if not token or not chat_id:
        return False
    body = json.dumps({"chat_id": chat_id, "text": text[:3900], "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print(f"ERROR sendMessage: {e}", file=sys.stderr)
        return False


# ── Datos ───────────────────────────────────────────────────────────────────
def fetch_rows(dias):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if dias:
        rows = conn.execute(
            "SELECT registro_digital, rubro, texto, instancia, materias, tipo_tesis, "
            "localizacion, fecha_publicacion FROM tesis "
            "WHERE date(substr(fecha_publicacion,1,10)) >= date('now', ?) "
            "ORDER BY fecha_publicacion DESC, registro_digital ASC",
            (f"-{int(dias)} days",)).fetchall()
        etiqueta = f"últimos {dias} días"
    else:
        last = conn.execute("SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis").fetchone()[0]
        if not last:
            conn.close()
            return None, None
        rows = conn.execute(
            "SELECT registro_digital, rubro, texto, instancia, materias, tipo_tesis, "
            "localizacion, fecha_publicacion FROM tesis WHERE substr(fecha_publicacion,1,10)=? "
            "ORDER BY registro_digital ASC", (last,)).fetchall()
        etiqueta = last
    conn.close()
    return rows, etiqueta


_ORDEN = ["Pleno de la Suprema Corte", "Primera Sala", "Segunda Sala",
          "Plenos Regionales", "Tribunales Colegiados", "Tribunal Colegiado de Apelación"]


def _rank(inst: str) -> int:
    for i, k in enumerate(_ORDEN):
        if k.lower() in (inst or "").lower():
            return i
    return len(_ORDEN)


# ── HTML (mismo estilo que los boletines del DOF / Louis) ──────────────────────
def build_html(rows, etiqueta) -> bytes:
    fecha_txt = _fecha_es(etiqueta)
    hoy = dt.date.today().strftime("%d/%m/%Y")
    grupos: dict[str, list] = {}
    for r in rows:
        grupos.setdefault((r["instancia"] or "Otras").strip(), []).append(r)

    secciones = []
    for inst in sorted(grupos, key=_rank):
        items = grupos[inst]
        filas = []
        for r in items:
            rubro = _esc(r["rubro"]).rstrip(". ")
            reg = r["registro_digital"]
            tipo = _esc(r["tipo_tesis"]) or ""
            loc = _esc(r["localizacion"]) or ""
            filas.append(
                f'<tr><td class="reg"><a href="{DETALLE_URL.format(reg)}">{reg}</a></td>'
                f'<td><div class="rubro">{rubro}</div>'
                f'<div class="meta">{tipo}{" · " + loc if loc else ""}</div></td></tr>')
        secciones.append(
            f'<h2>{_esc(inst)} <span class="cuenta">({len(items)})</span></h2>'
            f'<table class="tesis"><tbody>{"".join(filas)}</tbody></table>')

    body = "\n".join(secciones)
    doc = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Semanario Judicial de la Federación — {_esc(fecha_txt)}</title>
<style>
  body {{ font-family: 'Georgia', serif; max-width: 900px; margin: 40px auto;
         padding: 0 24px; color: #1a1a1a; line-height: 1.65; }}
  h1 {{ font-size: 1.6em; border-bottom: 3px solid #8B1A2E; padding-bottom: 8px; color: #8B1A2E; }}
  h2 {{ font-size: 1.2em; color: #2c3e50; margin-top: 1.8em; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  .cuenta {{ color: #999; font-weight: normal; font-size: 0.85em; }}
  table.tesis {{ border-collapse: collapse; width: 100%; margin: 0.6em 0; }}
  table.tesis td {{ border-bottom: 1px solid #eee; padding: 8px 10px; vertical-align: top; }}
  td.reg {{ width: 92px; font-size: 0.9em; white-space: nowrap; }}
  td.reg a {{ color: #8B1A2E; text-decoration: none; font-weight: bold; }}
  .rubro {{ font-size: 0.96em; }}
  .meta {{ color: #888; font-size: 0.8em; margin-top: 2px; }}
  .header {{ display: flex; justify-content: space-between; margin-bottom: 1.5em;
             padding: 14px 16px; background: #f8f9fa; border-radius: 6px; font-size: 0.85em; color: #666; }}
  .footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #ddd;
             font-size: 0.8em; color: #999; text-align: center; }}
  @media print {{ body {{ margin: 0; padding: 20px; }} }}
</style></head>
<body>
<div class="header">
  <span>Elaborado por: <strong>Louis · Kawiil</strong> — Semanario Judicial de la Federación</span>
  <span>Generado: {hoy}</span>
</div>
<h1>⚖️ Semanario Judicial — Edición del {_esc(fecha_txt)}</h1>
<p><strong>{len(rows)}</strong> tesis y jurisprudencias publicadas. Da clic en el registro para abrir el detalle en el SJF.</p>
{body}
<div class="footer">Documento generado por Louis (Kawiil) · {hoy} · Fuente: Semanario Judicial de la Federación (SCJN)</div>
</body></html>"""
    return doc.encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=None)
    ap.add_argument("--texto", action="store_true", help="enviar como mensaje, no como documento HTML")
    args = ap.parse_args()

    if not Path(DB_PATH).exists():
        print(f"ERROR: no existe la BD {DB_PATH}", file=sys.stderr)
        return 1
    rows, etiqueta = fetch_rows(args.dias)
    if not rows:
        send_message("⚖️ <b>Semanario Judicial</b>\nSin tesis nuevas esta semana. La biblioteca está al día.")
        print("Sin filas; aviso enviado")
        return 0

    fecha_txt = _fecha_es(etiqueta)
    caption = f"⚖️ <b>Semanario Judicial</b> — edición del {_esc(fecha_txt)}\n{len(rows)} tesis y jurisprudencias. Detalle en el archivo adjunto."

    if args.texto:
        ok = send_message(caption)
    else:
        fname = f"Semanario_SJF_{etiqueta.replace('-', '')[:8] or 'edicion'}.html"
        html_bytes = build_html(rows, etiqueta)
        ok = send_document(html_bytes, fname, caption)
        if not ok:
            print("Documento falló; intento como texto…", file=sys.stderr)
            ok = send_message(caption)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
