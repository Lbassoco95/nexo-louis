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
SJF_UNIVERSO = 0  # fallback obsoleto; se calcula dinámicamente desde la BD


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
    return f"{n:,}" if isinstance(n, int) else str(n)


def _pct(n, d):
    return (n / d * 100) if d else 0


def main():
    cutoff = (dt.datetime.now() - dt.timedelta(days=7)).isoformat()
    semana_ini = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    hoy = dt.date.today().strftime("%d/%m/%Y")
    L = [f"📊 <b>Estado de descargas legales</b> — semana al {hoy}\n"]
    sjf_txt = dof_txt = 0.0  # para el bloque de deep learning

    # ── SJF ──────────────────────────────────────────────────────────
    if Path(SJF_DB).exists():
        total = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis") or 0
        universo_raw = _q1(SJF_DB, "SELECT value FROM progress WHERE key='universe_total'")
        try:
            universo = int(universo_raw) if universo_raw else total
        except Exception:
            universo = total
        faltan = max(universo - total, 0)
        ult = _q1(SJF_DB, "SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis WHERE fecha_publicacion!=''") or "—"
        dias = _dias(ult)
        nuevas = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE substr(fecha_publicacion,1,10) >= ?", (semana_ini,)) or 0
        bajadas = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE fetched_at >= ?", (cutoff,)) or 0
        backfill_n = max(bajadas - nuevas, 0)
        cur = _q1(SJF_DB, "SELECT value FROM progress WHERE key='backfill_cursor'")
        epoca_old = _q1(SJF_DB, "SELECT epoca FROM tesis WHERE epoca IS NOT NULL AND epoca!='' ORDER BY registro_digital ASC LIMIT 1") or "—"
        con_texto = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE texto IS NOT NULL AND texto!=''") or 0
        con_pdf = _q1(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE pdf_generated IN (1,'1')") or 0
        sjf_txt = _pct(con_texto, total)
        ok = "✅ al día" if (dias is not None and dias <= 10) else (f"⚠️ atrasado {dias}d" if dias is not None else "—")
        L.append("⚖️ <b>SJF (Semanario Judicial)</b>")
        L.append(f"• Acervo: <b>{_miles(total)}</b> / {_miles(universo)} ({_pct(total, universo):.1f}%) — faltan <b>{_miles(faltan)}</b> hacia atrás")
        L.append(f"• Al día: última publicación <b>{ult}</b> {ok} · {nuevas} nuevas esta semana")
        L.append(f"• Histórico (backfill): <b>{_miles(backfill_n)}</b> esta semana · frontera registro {cur or '—'} (época más antigua: {epoca_old})")
        L.append(f"• Indexación: <b>{sjf_txt:.0f}%</b> con texto · <b>{_pct(con_pdf, total):.0f}%</b> con PDF\n")
    else:
        L.append("⚖️ <b>SJF</b>: BD no encontrada\n")

    # ── DOF ──────────────────────────────────────────────────────────
    if Path(DOF_DB).exists():
        total = _q1(DOF_DB, "SELECT COUNT(*) FROM notas") or 0
        validas = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha BETWEEN '1900-01-01' AND date('now')") or 0
        invalidas = max(total - validas, 0)
        ult = _q1(DOF_DB, "SELECT MAX(fecha) FROM notas WHERE fecha BETWEEN '1900-01-01' AND date('now')") or "—"
        n_ult = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha=?", (ult,)) or 0
        dias = _dias(ult)
        early = _q1(DOF_DB, "SELECT MIN(fecha) FROM notas WHERE fecha >= '1900-01-01'") or "—"
        nuevas = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha BETWEEN ? AND date('now')", (semana_ini,)) or 0
        bajadas = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fetched_at >= ?", (cutoff,)) or 0
        backfill_n = max(bajadas - nuevas, 0)
        con_texto = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE texto_plano IS NOT NULL AND texto_plano!=''") or 0
        con_pdf = _q1(DOF_DB, "SELECT COUNT(*) FROM notas WHERE pdf_path IS NOT NULL AND pdf_path!=''") or 0
        dof_txt = _pct(con_texto, validas)
        ok = "✅ al día" if (dias is not None and dias <= 4) else (f"⚠️ atrasado {dias}d" if dias is not None else "—")
        L.append("📰 <b>DOF (Diario Oficial)</b>")
        sucio = f" · <i>{invalidas} con fecha inválida (a depurar)</i>" if invalidas else ""
        L.append(f"• Acervo: <b>{_miles(validas)}</b> notas válidas{sucio}")
        L.append(f"• Cobertura temporal: {early} → {ult}")
        L.append(f"• Al día: última edición <b>{ult}</b> ({n_ult} notas) {ok} · {nuevas} esta semana")
        L.append(f"• Histórico (backfill): <b>{_miles(backfill_n)}</b> esta semana")
        L.append(f"• Indexación: <b>{dof_txt:.0f}%</b> con texto · <b>{_pct(con_pdf, validas):.0f}%</b> con PDF\n")
    else:
        L.append("📰 <b>DOF</b>: BD no encontrada\n")

    # ── Rumbo al deep learning ───────────────────────────────────────
    L.append("🧠 <b>Indexación / análisis (rumbo a deep learning)</b>")
    L.append(f"• SJF: {'✅ texto e índices casi completos' if sjf_txt >= 90 else f'⚠️ {sjf_txt:.0f}% con texto'}")
    L.append(f"• DOF: {'✅ listo' if dof_txt >= 90 else f'⚠️ solo {dof_txt:.0f}% con texto — falta extraer el histórico'}")
    L.append("\n<i>Al día con lo nuevo; sigo bajando y almacenando el histórico en automático.</i>")
    ok = send_msg("\n".join(L))
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
