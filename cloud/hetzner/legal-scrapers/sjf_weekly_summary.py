#!/usr/bin/env python3
"""
sjf_weekly_summary.py — resumen semanal de tesis del Semanario Judicial para Polo.

Cada lunes (vía systemd timer) consulta la BD del SJF, toma la edición más reciente
del Semanario (la fecha_publicacion máxima — el SJF publica un lote por semana, los
jueves) y manda a Telegram un resumen con el conteo y los rubros de esas tesis.

Solo reporta lo que está EN la BD (datos legales reales — nunca inventa). Reusa las
credenciales de Telegram que ya usa el scheduler de Louis.

Uso:
    python3 sjf_weekly_summary.py            # manda el resumen de la última edición
    python3 sjf_weekly_summary.py --dias 7   # resumen de lo publicado en los últimos 7 días

Variables de entorno:
    SJF_DB_PATH        ruta a la BD (default /opt/openclaw/legal/sjf/biblioteca.db)
    TELEGRAM_CREDS     ruta a telegram.env (default /opt/openclaw/credentials/telegram.env)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
DETALLE_URL = "https://sjf2.scjn.gob.mx/detalle/tesis/{}"
MAX_LEN = 3900  # margen bajo el límite de 4096 de Telegram


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


def send_telegram(text: str) -> bool:
    creds = load_creds(CREDS)
    token = creds.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = creds.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: faltan credenciales Telegram", file=sys.stderr)
        return False
    body = json.dumps({
        "chat_id": chat_id, "text": text[:MAX_LEN],
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print(f"ERROR enviando a Telegram: {e}", file=sys.stderr)
        return False


def _esc(s: str) -> str:
    return html.escape((s or "").strip())


def build_summary(dias: int | None) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if dias:
        # Publicado en los últimos N días (según fecha_publicacion)
        rows = conn.execute(
            "SELECT registro_digital, rubro, instancia, materias, fecha_publicacion "
            "FROM tesis WHERE date(substr(fecha_publicacion,1,10)) >= date('now', ?) "
            "ORDER BY fecha_publicacion DESC, registro_digital ASC",
            (f"-{int(dias)} days",),
        ).fetchall()
        etiqueta = f"últimos {dias} días"
    else:
        # La edición semanal más reciente = la fecha_publicacion máxima
        last = conn.execute(
            "SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis").fetchone()[0]
        if not last:
            conn.close()
            return None
        rows = conn.execute(
            "SELECT registro_digital, rubro, instancia, materias, fecha_publicacion "
            "FROM tesis WHERE substr(fecha_publicacion,1,10)=? "
            "ORDER BY registro_digital ASC", (last,),
        ).fetchall()
        etiqueta = last
    conn.close()

    if not rows:
        return (f"⚖️ <b>Semanario Judicial</b>\nSin tesis nuevas en {etiqueta}. "
                f"La biblioteca está al día.")

    lineas = [f"⚖️ <b>Semanario Judicial — {etiqueta}</b>",
              f"<b>{len(rows)}</b> tesis publicadas.\n"]
    for r in rows:
        rubro = _esc(r["rubro"])[:130]
        reg = r["registro_digital"]
        url = DETALLE_URL.format(reg)
        linea = f"• <a href=\"{url}\">{reg}</a> — {rubro}"
        # corta si nos pasamos del límite
        if sum(len(x) for x in lineas) + len(linea) > MAX_LEN - 80:
            faltan = len(rows) - (len(lineas) - 2)
            lineas.append(f"\n… y {faltan} más. Búscalas en el SJF.")
            break
        lineas.append(linea)
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=None,
                    help="Resumen de lo publicado en los últimos N días (default: última edición)")
    args = ap.parse_args()

    if not Path(DB_PATH).exists():
        print(f"ERROR: no existe la BD {DB_PATH}", file=sys.stderr)
        return 1
    texto = build_summary(args.dias)
    if not texto:
        print("Sin datos para resumir", file=sys.stderr)
        return 1
    ok = send_telegram(texto)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
