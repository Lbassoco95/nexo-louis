#!/usr/bin/env python3
"""Boletin DIARIO del DOF -> documento HTML adjunto a Telegram, CURADO POR IMPACTO.

Destaca los documentos que importan (leyes, decretos, reglamentos, acuerdos,
circulares, lineamientos, NOMs, reglas, resoluciones, convenios, modificaciones,
reformas… = los que cambian normas/tramites/administracion publica), agrupados
por dependencia con su tipo y link. El "mar" (avisos judiciales, edictos,
convocatorias, balances) NO se detalla: se IDENTIFICA y se cuenta por categoria.

Clasificacion = coincidencia de patron sobre la etiqueta oficial del documento
(el DOF nombra cada nota por su tipo al inicio del titulo) + la dependencia.
Dato duro; cero interpretacion de contenido.
"""
import datetime as dt, html, json, os, re, sqlite3, sys, urllib.request, uuid
from pathlib import Path

DB = os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof/biblioteca_dof.db")
CREDS = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
URL = "https://www.dof.gob.mx/nota_detalle.php?codigo={cod}&fecha={f}"
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
EDIC = {"MAT": "Matutina", "VES": "Vespertina", "EXT": "Extraordinaria"}

# Tipos de documento RELEVANTES (impactan norma/tramite/administracion/ciudadania).
# Se evalua contra el inicio del titulo (el DOF estandariza el tipo en mayusculas).
RELEVANTE_RE = re.compile(
    r"^\s*(LEY|C[OÓ]DIGO|DECRETO|REGLAMENTO|ACUERDO|CIRCULAR|LINEAMIENTOS?|"
    r"NORMA|REGLAS|MANUAL|ESTATUTO|RESOLUCI[OÓ]N|DISPOSICIONES?|CONVENIO|"
    r"MODIFICACI[OÓ]N|REFORMA|ANEXO|POL[IÍ]TICA|PROGRAMA|PLAN|DECLARATORIA|"
    r"ESTRATEGIA|BASES|TARIFA|ESTÁNDAR|ESTANDAR|CONDICIONES GENERALES)\b", re.I)
# Dependencias/secciones que son "el mar" (no se detalla, solo se cuenta).
DEP_RUIDO = ("AVISOS JUDICIALES", "AVISOS GENERALES", "PARTICULARES")


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


def clasifica(r):
    """Devuelve ('relevante', None) o ('resto', categoria)."""
    dep = (r["nombre_cod_orga_uno"] or "").upper()
    tit = (r["titulo"] or "").strip()
    up = tit.upper()
    if any(j in dep for j in DEP_RUIDO):
        return "resto", "Avisos judiciales y generales"
    if RELEVANTE_RE.match(tit):
        return "relevante", None
    if up.startswith("EDICTO"):
        return "resto", "Edictos"
    if up.startswith(("CONVOCATORIA", "LICITACI", "FALLO", "FE DE ERRATAS")):
        return "resto", "Convocatorias / Licitaciones / Fe de erratas"
    if up.startswith(("BALANCE", "ESTADO DE", "ESTADOS FINANC", "ESTADO FINANC")):
        return "resto", "Estados financieros / Balances"
    if up.startswith("AVISO"):
        return "resto", "Avisos"
    return "resto", "Otros documentos"


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


def build_html(relevantes, resto_counts, total, fecha):
    fl = fecha_larga(fecha)
    ddmm = fecha_ddmmyyyy(fecha)
    hoy = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    grupos = {}
    for r in relevantes:
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
    body = "\n".join(secc) or '<p class="vacio">Sin documentos normativos relevantes en esta edición.</p>'

    n_rel = len(relevantes)
    n_resto = sum(resto_counts.values())
    resto_li = "".join(f"<li>{esc(cat)}: <strong>{n}</strong></li>"
                       for cat, n in sorted(resto_counts.items(), key=lambda x: -x[1]))
    resto_block = (f'<h2 class="resto">Resto identificado (no detallado) — {n_resto}</h2>'
                   f'<ul class="rl">{resto_li}</ul>') if n_resto else ""

    doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DOF — {esc(fl)}</title><style>
body{{font-family:'Georgia',serif;max-width:900px;margin:40px auto;padding:0 24px;color:#1a1a1a;line-height:1.6}}
h1{{font-size:1.55em;border-bottom:3px solid #0b5d2e;padding-bottom:8px;color:#0b5d2e}}
h2{{font-size:1.1em;color:#2c3e50;margin-top:1.6em;border-bottom:1px solid #ddd;padding-bottom:4px}}
h2.resto{{color:#888;border-bottom:1px dashed #ccc;margin-top:2.2em}}
.c{{color:#999;font-weight:normal;font-size:.82em}}
table{{border-collapse:collapse;width:100%;margin:.4em 0}}
td{{border-bottom:1px solid #eee;padding:6px 9px;vertical-align:top}}
td.cod{{width:78px;font-size:.85em;white-space:nowrap}}
td.cod a{{color:#0b5d2e;text-decoration:none;font-weight:bold}}
.t{{font-size:.93em}}
.tag{{font-size:.68em;font-weight:bold;background:#dff0e4;color:#0b5d2e;padding:1px 6px;border-radius:4px;margin-right:4px}}
.ed{{font-size:.68em;font-weight:bold;background:#0b5d2e;color:#fff;padding:1px 6px;border-radius:4px;margin-right:4px}}
.rl{{color:#777;font-size:.9em;columns:2}} .rl li{{margin-bottom:3px}}
.hd{{display:flex;justify-content:space-between;margin-bottom:1.2em;padding:14px 16px;background:#f4f7f5;border-radius:6px;font-size:.85em;color:#666}}
.resumen{{background:#f4f7f5;border-left:4px solid #0b5d2e;padding:10px 14px;margin:1em 0;font-size:.95em}}
.vacio{{color:#888;font-style:italic}}
.ft{{margin-top:3em;padding-top:1em;border-top:1px solid #ddd;font-size:.8em;color:#999;text-align:center}}
</style></head><body>
<div class="hd"><span>Elaborado por: <strong>Louis · Kawiil</strong> — Diario Oficial de la Federación</span><span>Generado: {hoy}</span></div>
<h1>📰 Diario Oficial — {esc(fl)}</h1>
<div class="resumen"><strong>{n_rel}</strong> documentos normativos relevantes (leyes, decretos, acuerdos, reglamentos, circulares, lineamientos…) de un total de <strong>{total}</strong> publicaciones. El resto ({n_resto}) son avisos/edictos/convocatorias — identificados abajo. Da clic en el código para abrir la nota en el DOF.</div>
{body}
{resto_block}
<div class="ft">Documento generado por Louis (Kawiil) · {hoy} · Fuente: Diario Oficial de la Federación (SEGOB) · Clasificación por tipo oficial del documento.</div>
</body></html>"""
    return doc.encode("utf-8")


def main():
    # El DOF no publica sábado/domingo: el boletín DIARIO descansa esos días.
    # (El backfill histórico es otro proceso y sigue corriendo.) --force lo ignora.
    if dt.datetime.now().weekday() >= 5 and "--force" not in sys.argv:
        print("Fin de semana: el DOF no publica; boletín diario omitido.")
        return 0
    if not Path(DB).exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    # La edición del DÍA HÁBIL ANTERIOR (fecha < hoy): el lunes reporta el viernes,
    # el martes el lunes, etc. Cada edición se reporta a la mañana siguiente.
    fecha = conn.execute("SELECT MAX(fecha) FROM notas WHERE fecha < date('now')").fetchone()[0]
    if not fecha:
        print("Sin fechas validas", file=sys.stderr)
        return 1
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
        return 0

    relevantes, resto_counts = [], {}
    for r in rows:
        clase, cat = clasifica(r)
        if clase == "relevante":
            relevantes.append(r)
        else:
            resto_counts[cat] = resto_counts.get(cat, 0) + 1

    fl = fecha_larga(fecha)
    caption = (f"📰 <b>Diario Oficial</b> — {esc(fl)}\n"
               f"<b>{len(relevantes)}</b> documentos relevantes (leyes/decretos/acuerdos/circulares…) "
               f"de {len(rows)} publicaciones. Detalle por dependencia en el adjunto.")
    fname = f"DOF_{fecha.replace('-', '')}.html"
    ok = send_doc(build_html(relevantes, resto_counts, len(rows), fecha), fname, caption)
    if ok:
        try:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(fecha)
        except Exception as e:
            print(f"WARN: no guardé estado: {e}", file=sys.stderr)
        notificar_kawiil_central(
            titulo=f"DOF — {fl}",
            cuerpo=(f"{len(relevantes)} documentos normativos relevantes "
                    f"(leyes/decretos/acuerdos/circulares…) de {len(rows)} publicaciones. "
                    f"El detalle por dependencia llegó al Telegram de Louis."),
            tipo="dof_resumen")
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
