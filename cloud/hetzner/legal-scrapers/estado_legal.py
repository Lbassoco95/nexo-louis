#!/usr/bin/env python3
"""estado_legal.py — reporte SEMANAL del estado de las descargas legales (SJF + DOF).

Dice, con dato duro de las BDs: si vamos al dia, cuanto bajamos esta semana, y
como avanza el backfill historico. Se manda a Telegram como mensaje conciso.
"""
import datetime as dt, json, os, sqlite3, sys, urllib.request
from pathlib import Path

SJF_DB = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
DOF_DB = os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof/biblioteca_dof.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
SJF_UNIVERSO = 262016  # tesis totales conocidas del SJF (todas las epocas)


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
    return f"{n:,}".replace(",", ",") if isinstance(n, int) else str(n)


def main():
    cutoff = (dt.datetime.now() - dt.timedelta(days=7)).isoformat()
    hoy = dt.date.today().strftime("%d/%m/%Y")
    L = [f"📊 <b>Estado de descargas legales</b> — semana al {hoy}\n"]

    # ── SJF ──────────────────────────────────────────────────────────
    if Path(SJF_DB).exists():
        total = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis") or 0
        universo = _q1(SJF_DB, "SELECT value FROM progress WHERE key='universe_total'") or SJF_UNIVERSO
        try:
            universo = int(universo)
        except Exception:
            universo = SJF_UNIVERSO
        pct = (total / universo * 100) if universo else 0
        ult = _q1(SJF_DB, "SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis") or "—"
        dias = _dias(ult)
        sem = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE fetched_at >= ?", (cutoff,)) or 0
        cur = _q1(SJF_DB, "SELECT value FROM progress WHERE key='backfill_cursor'")
        # al dia: el SJF publica semanal (jueves); <=10 dias = al dia
        ok = "✅ al día" if (dias is not None and dias <= 10) else f"⚠️ atrasado {dias}d" if dias is not None else "—"
        L.append("⚖️ <b>SJF (Semanario Judicial)</b>")
        L.append(f"• Acervo: <b>{_miles(total)}</b> / {_miles(universo)} ({pct:.1f}%)")
        L.append(f"• Última publicación: <b>{ult}</b> — {ok}")
        L.append(f"• Descargadas esta semana: <b>{_miles(sem)}</b>")
        L.append(f"• Backfill histórico: cursor en <b>{cur or '—'}</b> (rellenando épocas anteriores)\n")
    else:
        L.append("⚖️ <b>SJF</b>: BD no encontrada\n")

    # ── DOF ──────────────────────────────────────────────────────────
    if Path(DOF_DB).exists():
        total = _q1(DOF_DB, "SELECT COUNT(*) FROM notas") or 0
        ult = _q1(DOF_DB, "SELECT MAX(fecha) FROM notas WHERE fecha <= date('now')") or "—"
        dias = _dias(ult)
        sem = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fetched_at >= ?", (cutoff,)) or 0
        # al dia: publica diario habil; <=3 dias (cubre findes) = al dia
        ok = "✅ al día" if (dias is not None and dias <= 3) else f"⚠️ atrasado {dias}d" if dias is not None else "—"
        L.append("📰 <b>DOF (Diario Oficial)</b>")
        L.append(f"• Acervo: <b>{_miles(total)}</b> notas")
        L.append(f"• Última edición: <b>{ult}</b> — {ok}")
        L.append(f"• Descargadas esta semana: <b>{_miles(sem)}</b>\n")
    else:
        L.append("📰 <b>DOF</b>: BD no encontrada\n")

    L.append("<i>Mantengo ambas al día y sigo llenando lo histórico en automático.</i>")
    ok = send_msg("\n".join(L))
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
