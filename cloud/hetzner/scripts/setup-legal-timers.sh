#!/usr/bin/env bash
# setup-legal-timers.sh — instala/activa la automatización legal de Donna en el server.
# Ejecutar con: sudo bash setup-legal-timers.sh
#
# Hace:
#   1) (re)escribe /opt/openclaw/legal/sjf/sjf_weekly_summary.py corregido.
#   2) escribe units systemd: sjf-update (diario 13:30) + sjf-weekly (lunes 8:00).
#   3) daemon-reload + enable --now de ambos timers.
#
# Idempotente: se puede correr varias veces.
set -euo pipefail

USER_OC="polo"
SJF_DIR="/opt/openclaw/legal/sjf"
LOGS="/opt/openclaw/logs"
mkdir -p "$SJF_DIR" "$LOGS"

echo "[1/3] Escribiendo sjf_weekly_summary.py corregido…"
cat > "$SJF_DIR/sjf_weekly_summary.py" <<'PYEOF'
#!/usr/bin/env python3
"""Resumen semanal de tesis del SJF para Polo (lunes vía systemd timer).
Toma la edición semanal más reciente en la BD y la manda a Telegram (HTML,
agrupado por instancia). Solo reporta lo que está en la BD (datos reales)."""
from __future__ import annotations
import argparse, html, json, os, sqlite3, sys, urllib.request
from pathlib import Path

DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
DETALLE_URL = "https://sjf2.scjn.gob.mx/detalle/tesis/{}"
MAX_LEN = 3900


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
    body = json.dumps({"chat_id": chat_id, "text": text[:MAX_LEN],
                       "parse_mode": "HTML", "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print(f"ERROR enviando a Telegram: {e}", file=sys.stderr)
        return False


def _esc(s: str) -> str:
    return html.escape((s or "").strip())


_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_es(iso: str) -> str:
    try:
        y, m, d = iso[:10].split("-")
        return f"{int(d)} de {_MESES[int(m)]} de {y}"
    except Exception:
        return iso


def build_summary(dias):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if dias:
        rows = conn.execute(
            "SELECT registro_digital, rubro, instancia, materias, fecha_publicacion "
            "FROM tesis WHERE date(substr(fecha_publicacion,1,10)) >= date('now', ?) "
            "ORDER BY fecha_publicacion DESC, registro_digital ASC",
            (f"-{int(dias)} days",)).fetchall()
        etiqueta = f"últimos {dias} días"
    else:
        last = conn.execute("SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis").fetchone()[0]
        if not last:
            conn.close()
            return None
        rows = conn.execute(
            "SELECT registro_digital, rubro, instancia, materias, fecha_publicacion "
            "FROM tesis WHERE substr(fecha_publicacion,1,10)=? "
            "ORDER BY registro_digital ASC", (last,)).fetchall()
        etiqueta = last
    conn.close()

    if not rows:
        return (f"⚖️ <b>Semanario Judicial de la Federación</b>\n"
                f"Sin tesis nuevas en {_fecha_es(etiqueta)}. La biblioteca está al día.")

    orden = ["Pleno de la Suprema Corte", "Primera Sala", "Segunda Sala",
             "Plenos Regionales", "Tribunales Colegiados", "Tribunal Colegiado de Apelación"]

    def _rank(inst):
        for i, k in enumerate(orden):
            if k.lower() in (inst or "").lower():
                return i
        return len(orden)

    grupos = {}
    for r in rows:
        grupos.setdefault((r["instancia"] or "Otras").strip(), []).append(r)

    lineas = [f"⚖️ <b>Semanario Judicial de la Federación</b>",
              f"📅 Edición del <b>{_fecha_es(etiqueta)}</b> · <b>{len(rows)}</b> tesis y jurisprudencias\n"]
    for inst in sorted(grupos, key=_rank):
        items = grupos[inst]
        lineas.append(f"<b>{_esc(inst)}</b> ({len(items)})")
        for r in items:
            rubro = _esc(r["rubro"]).rstrip(". ")[:150]
            reg = r["registro_digital"]
            linea = f"• {rubro} — <a href=\"{DETALLE_URL.format(reg)}\">{reg}</a>"
            if sum(len(x) for x in lineas) + len(linea) > MAX_LEN - 90:
                lineas.append("\n… (resumen recortado por longitud; revisa el SJF para el resto)")
                return "\n".join(lineas)
            lineas.append(linea)
        lineas.append("")
    return "\n".join(lineas).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=None)
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
PYEOF
chmod 0755 "$SJF_DIR/sjf_weekly_summary.py"
chown "$USER_OC:$USER_OC" "$SJF_DIR/sjf_weekly_summary.py"
python3 -m py_compile "$SJF_DIR/sjf_weekly_summary.py" && echo "  ✓ sjf_weekly_summary.py OK"

echo "[2/3] Escribiendo units systemd…"
cat > /etc/systemd/system/sjf-update.service <<EOF
[Unit]
Description=Donna SJF harvester — actualiza tesis del Semanario Judicial
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$USER_OC
Group=$USER_OC
WorkingDirectory=$SJF_DIR
ExecStart=/usr/bin/python3 $SJF_DIR/sjf_harvest.py
StandardOutput=append:$LOGS/sjf-update.log
StandardError=append:$LOGS/sjf-update.log
TimeoutStartSec=2400
MemoryMax=512M
EOF

cat > /etc/systemd/system/sjf-update.timer <<EOF
[Unit]
Description=Dispara el harvester SJF de Donna (diario)
[Timer]
OnCalendar=*-*-* 13:30:00
RandomizedDelaySec=900
Persistent=true
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/sjf-weekly.service <<EOF
[Unit]
Description=Donna SJF — resumen semanal (Telegram)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$USER_OC
Group=$USER_OC
WorkingDirectory=$SJF_DIR
ExecStart=/usr/bin/python3 $SJF_DIR/sjf_weekly_summary.py
StandardOutput=append:$LOGS/sjf-weekly.log
StandardError=append:$LOGS/sjf-weekly.log
EOF

cat > /etc/systemd/system/sjf-weekly.timer <<EOF
[Unit]
Description=Resumen semanal SJF de Donna (lunes)
[Timer]
OnCalendar=Mon *-*-* 08:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

echo "[3/3] Activando timers…"
systemctl daemon-reload
systemctl enable --now sjf-update.timer sjf-weekly.timer
chown "$USER_OC:$USER_OC" "$LOGS"/sjf-*.log 2>/dev/null || true
echo ""
echo "✓ Listo. Timers activos:"
systemctl list-timers 'sjf-*' --no-pager || true
