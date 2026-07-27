#!/usr/bin/env python3
"""Boletin semanal SJF -> documento HTML adjunto a Telegram (estilo DOF).
Agrupado POR MATERIA; cada tesis etiquetada como Jurisprudencia o Tesis."""
import datetime as dt, html, json, os, sqlite3, sys, urllib.request, uuid
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


def send_msg(text):
    token, chat = creds()
    if not token or not chat:
        return False
    body = json.dumps({"chat_id": chat, "text": text[:3900], "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False


def notificar_kawiil_central(titulo, cuerpo, tipo):
    """Best-effort: avisa en el app de Kawiil Central (solo a Polo por defecto).
    No rompe el boletín si falla (import o BD)."""
    try:
        if "/opt/openclaw/scripts" not in sys.path:
            sys.path.insert(0, "/opt/openclaw/scripts")
        import louis_core as L
        r = L._kawiil_central_notificar(titulo=titulo, cuerpo=cuerpo, para="", tipo=tipo)
        print(f"Kawiil Central: {r}")
    except Exception as e:
        print(f"WARN: no notifiqué a Kawiil Central: {e}", file=sys.stderr)


def mrank(m):
    ml = m.lower()
    for i, k in enumerate(MORD):
        if k == ml:
            return (0, i, ml)
    return (1, 0, ml)  # las no listadas, alfabeticas al final


def build_html(rows, etiqueta):
    # Motor HTML interactivo ÚNICO (mismo look que el análisis legal y el DOF).
    if "/opt/openclaw/scripts" not in sys.path:
        sys.path.insert(0, "/opt/openclaw/scripts")
    import louis_html as LH
    fx = fecha_es(etiqueta)
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
            f'{esc(r["rubro"]).rstrip(". ")}</td></tr>'
            for r in it)
        secc.append(
            f'<details class="sec" open><summary>{esc(mat)} '
            f'<span class="c">({len(it)} · {nj} jur / {len(it) - nj} tesis)</span></summary>'
            f'<div class="sec-body"><table><tbody>{filas}</tbody></table></div></details>')
    body = "\n".join(secc) or '<p style="color:#888;font-style:italic">Sin publicaciones nuevas.</p>'

    # Tarjetas KPI arriba (dashboard, no lista)
    kpis = LH.kpi_cards([
        {"value": len(rows), "label": "Publicaciones de la semana"},
        {"value": n_jur, "label": "Jurisprudencias", "sub": "criterio obligatorio"},
        {"value": n_tes, "label": "Tesis aisladas", "sub": "criterio orientador"},
        {"value": len(grupos), "label": "Materias"},
    ])
    body = kpis + body

    resumen = (f"<strong>{len(rows)}</strong> publicaciones: <strong>{n_jur}</strong> "
               f"jurisprudencias · <strong>{n_tes}</strong> tesis aisladas. "
               f"Organizadas por materia. Da clic en el registro para el detalle en el SJF.")
    ctx = (f"Semanario Judicial, semana del {fx}: {len(rows)} publicaciones "
           f"({n_jur} jurisprudencias, {n_tes} tesis).\n"
           + "\n".join(f"- [{'Jurisprudencia' if es_juris(r) else 'Tesis'}] "
                       f"{(r['rubro'] or '').strip()}" for r in rows[:120]))
    return LH.render_page(
        f"⚖️ Semanario Judicial — {fx}", "Semanario Judicial de la Federación",
        body, ctx_md=ctx, con_chat=True, resumen=resumen,
        chat_titulo="💬 Pregúntale a Louis sobre estas tesis",
        fuente="Fuente: SCJN — Semanario Judicial de la Federación")



def main():
    if not Path(DB).exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    # Ventana de la SEMANA PASADA: lo PUBLICADO en los últimos 7 días.
    # (El backfill histórico mete tesis con fecha_publicacion vieja → no entran aquí.)
    hasta = dt.date.today()
    desde = hasta - dt.timedelta(days=7)
    rows = conn.execute(
        "SELECT registro_digital,rubro,ta_tj,tipo_tesis,materias,fecha_publicacion "
        "FROM tesis WHERE substr(fecha_publicacion,1,10) >= ? AND substr(fecha_publicacion,1,10) <= ? "
        "ORDER BY fecha_publicacion DESC, registro_digital ASC",
        (desde.isoformat(), hasta.isoformat())).fetchall()
    # Etiqueta de rango legible: "1 al 5 de junio de 2026"
    if desde.month == hasta.month:
        etiqueta = f"{desde.day} al {hasta.day} de {MES[hasta.month]} de {hasta.year}"
    else:
        etiqueta = f"{desde.day} de {MES[desde.month]} al {hasta.day} de {MES[hasta.month]} de {hasta.year}"
    if not rows:
        # Diagnóstico: última tesis registrada en la BD (para saber si el harvester corrió)
        ultima = conn.execute(
            "SELECT fecha_publicacion FROM tesis ORDER BY fecha_publicacion DESC LIMIT 1"
        ).fetchone()
        conn.close()
        ultima_str = (f" · última en BD: <b>{ultima['fecha_publicacion'][:10]}</b>"
                      if ultima else " · BD sin registros")
        send_msg(f"⚖️ <b>Semanario Judicial</b> — sin nuevas tesis registradas "
                 f"del {esc(etiqueta)}{ultima_str}.")
        print("Sin publicaciones esta semana")
        return 0
    conn.close()
    n_jur = sum(1 for r in rows if es_juris(r))
    caption = (f"⚖️ <b>Semanario Judicial</b> — semana del {esc(etiqueta)}\n"
               f"{len(rows)} publicaciones ({n_jur} jurisprudencias · {len(rows) - n_jur} tesis), por materia. Detalle en el adjunto.")
    fname = f"Semanario_SJF_{desde.isoformat().replace('-', '')}_{hasta.isoformat().replace('-', '')}.html"
    ok = send_doc(build_html(rows, etiqueta), fname, caption)
    if ok:
        notificar_kawiil_central(
            titulo=f"Semanario Judicial — semana del {etiqueta}",
            cuerpo=(f"{len(rows)} publicaciones ({n_jur} jurisprudencias · "
                    f"{len(rows) - n_jur} tesis), organizadas por materia. "
                    f"El detalle llegó al Telegram de Louis."),
            tipo="sjf_semanal")
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
