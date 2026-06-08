#!/usr/bin/env python3
"""legal_digest.py — Digest semanal de APRENDIZAJES de los agentes kawiil-*.

Cierra la visión de Polo: que la experiencia que los agentes destilan del DOF/SJF
(cómo dispone/redacta la autoridad) NO se quede dentro de los agentes, sino que
"se vea". Genera un documento HTML interactivo con lo que cada agente aprendió en
los últimos N días (títulos agrupados por agente/área), lo manda a Telegram y
publica una notificación en Kawiil Central.

El auto-indexado ya corre continuo en el scheduler; esto solo COSECHA y reporta.

Uso: legal_digest.py [--dias 7] [--force]   (--force ignora "sin novedades")
"""
import argparse, datetime as dt, html, json, os, sys, uuid, urllib.request
from pathlib import Path

SCRIPTS = os.environ.get("LOUIS_SCRIPTS", "/opt/openclaw/scripts")
KNOWLEDGE_BASE = Path(os.environ.get(
    "LEGAL_KNOWLEDGE_BASE", "/opt/openclaw/spaces/general/agents/knowledge"))
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def esc(s):
    return html.escape((s or "").strip())


def _creds():
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
    token, chat = _creds()
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


def _parse_doc(md_path):
    """Extrae (titulo, fuente_linea) de un .md de conocimiento."""
    try:
        lines = md_path.read_text(errors="replace").splitlines()
    except Exception:
        return None, None
    titulo = ""
    fuente = ""
    for ln in lines[:4]:
        if ln.startswith("# ") and not titulo:
            titulo = ln[2:].strip()
        elif ln.startswith("**Fuente:**"):
            fuente = ln.replace("**", "").strip()
    return titulo or md_path.stem, fuente


def cosechar(dias):
    """Devuelve {agente: {'label':..., 'total':N, 'nuevos':[(titulo,fuente),...]}}."""
    corte = dt.datetime.now().timestamp() - dias * 86400
    out = {}
    if not KNOWLEDGE_BASE.exists():
        return out
    for adir in sorted(KNOWLEDGE_BASE.glob("kawiil-*")):
        docs_dir = adir / "docs"
        if not docs_dir.exists():
            continue
        idx = {}
        idxf = adir / "index.json"
        if idxf.exists():
            try:
                idx = json.loads(idxf.read_text())
            except Exception:
                idx = {}
        nuevos = []
        total = 0
        for md in docs_dir.glob("*.md"):
            total += 1
            try:
                if md.stat().st_mtime >= corte:
                    titulo, fuente = _parse_doc(md)
                    nuevos.append((titulo, fuente))
            except Exception:
                pass
        if total or nuevos:
            out[adir.name] = {"label": idx.get("label", ""), "total": total,
                              "nuevos": nuevos}
    return out


def build_html(data, dias, etiqueta):
    sys.path.insert(0, SCRIPTS)
    import louis_html as LH
    total_nuevos = sum(len(v["nuevos"]) for v in data.values())
    total_kb = sum(v["total"] for v in data.values())
    agentes_activos = sum(1 for v in data.values() if v["nuevos"])
    secc = []
    # Agentes con novedades primero, ordenados por # de aprendizajes nuevos
    for ag in sorted(data, key=lambda a: (-len(data[a]["nuevos"]), a)):
        v = data[ag]
        if not v["nuevos"]:
            continue
        filas = "".join(
            f"<tr><td>{esc(t)}</td><td class=\"cod\">{esc(f)}</td></tr>"
            for t, f in v["nuevos"][:60])
        label = f" — {esc(v['label'])}" if v["label"] else ""
        secc.append(
            f'<details class="sec" open><summary>{esc(ag)}{label} '
            f'<span class="c">({len(v["nuevos"])} nuevos · {v["total"]} total)</span></summary>'
            f'<div class="sec-body"><table><thead><tr><th>Aprendizaje (documento)</th>'
            f'<th>Fuente</th></tr></thead><tbody>{filas}</tbody></table></div></details>')
    # Agentes sin novedades, resumidos
    sin = [(a, v["total"]) for a, v in data.items() if not v["nuevos"]]
    if sin:
        li = "".join(f"<li>{esc(a)}: {n} docs (sin novedades)</li>"
                     for a, n in sorted(sin, key=lambda x: -x[1]))
        secc.append('<details class="sec"><summary>Resto de agentes (sin aprendizajes nuevos) '
                    f'<span class="c">({len(sin)})</span></summary>'
                    f'<div class="sec-body"><ul>{li}</ul></div></details>')
    body = "\n".join(secc) or '<p style="color:#888;font-style:italic">Sin aprendizajes nuevos en el periodo.</p>'
    resumen = (f"En los últimos <strong>{dias} días</strong> los agentes destilaron "
               f"<strong>{total_nuevos}</strong> aprendizajes nuevos del DOF/SJF "
               f"({agentes_activos} agentes con novedades). "
               f"Base de conocimiento total: <strong>{total_kb}</strong> documentos. "
               f"Esto es cómo la autoridad dispone y redacta — destilado para nuestros documentos.")
    ctx = (f"Digest de aprendizajes legales (últimos {dias} días): {total_nuevos} nuevos, "
           f"{total_kb} en total.\n"
           + "\n".join(f"- {ag}: " + "; ".join(t for t, _ in v["nuevos"][:20])
                       for ag, v in data.items() if v["nuevos"]))
    return LH.render_page(
        f"🧠 Aprendizajes de los agentes — {etiqueta}",
        "Conocimiento legal destilado (DOF/SJF)", body, ctx_md=ctx, con_chat=True,
        resumen=resumen, chat_titulo="💬 Pregúntale a Louis sobre lo aprendido",
        fuente="Fuente: corpus DOF/SJF indexado por los agentes kawiil-*"), total_nuevos, total_kb


def notificar(titulo, cuerpo):
    try:
        sys.path.insert(0, SCRIPTS)
        import louis_core as L
        print("Kawiil Central:", L._kawiil_central_notificar(
            titulo=titulo, cuerpo=cuerpo, para="", tipo="aprendizajes_legales"))
    except Exception as e:
        print(f"WARN: no notifiqué a Kawiil Central: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    data = cosechar(a.dias)
    hoy = dt.date.today()
    desde = hoy - dt.timedelta(days=a.dias)
    etiqueta = (f"{desde.day} al {hoy.day} de {MES[hoy.month]} de {hoy.year}"
                if desde.month == hoy.month else
                f"{desde.day} de {MES[desde.month]} al {hoy.day} de {MES[hoy.month]} de {hoy.year}")
    doc, total_nuevos, total_kb = build_html(data, a.dias, etiqueta)
    if total_nuevos == 0 and not a.force:
        print("Sin aprendizajes nuevos esta semana; digest omitido.")
        return 0
    caption = (f"🧠 <b>Aprendizajes de los agentes</b> — semana del {esc(etiqueta)}\n"
               f"{total_nuevos} aprendizajes nuevos del DOF/SJF · {total_kb} en la base. "
               f"Detalle interactivo en el adjunto.")
    fname = f"Aprendizajes_{desde.isoformat().replace('-','')}_{hoy.isoformat().replace('-','')}.html"
    ok = send_doc(doc, fname, caption)
    if ok:
        notificar(
            titulo=f"Aprendizajes legales — semana del {etiqueta}",
            cuerpo=(f"{total_nuevos} aprendizajes nuevos destilados del DOF/SJF "
                    f"({total_kb} en la base de conocimiento). El detalle llegó al Telegram de Louis."))
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
