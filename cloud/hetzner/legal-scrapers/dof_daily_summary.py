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
DOCS_DIR = Path(os.environ.get("DOF_DOCS_DIR", "/opt/openclaw/docs/dof"))
DONNA_DOMAIN = os.environ.get("DONNA_DOMAIN", "")
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


def save_html_public(content: bytes, fname: str) -> str:
    """Guarda el HTML en DOCS_DIR y devuelve la URL pública. '' si no hay dominio configurado."""
    try:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (DOCS_DIR / fname).write_bytes(content)
    except Exception as e:
        print(f"WARN: no guardé HTML en disco: {e}", file=sys.stderr)
        return ""
    if DONNA_DOMAIN:
        return f"https://{DONNA_DOMAIN}/docs/dof/{fname}"
    return ""


def indexar_normativas_dof(relevantes, fecha):
    """Best-effort: encola las normativas relevantes del día para indexación prioritaria."""
    if not relevantes:
        return
    try:
        if "/opt/openclaw/scripts" not in sys.path:
            sys.path.insert(0, "/opt/openclaw/scripts")
        import donna_core as L
        km = getattr(L, "KAWIIL_KNOWLEDGE_MAP", {})
        encolados = 0
        for r in relevantes:
            texto = f"{(r['titulo'] or '').lower()} {(r['nombre_cod_orga_uno'] or '').lower()}"
            agentes_match = [ag for ag, cfg in km.items()
                             if any(k.lower() in texto for k in cfg.get("dof", []))]
            if not agentes_match:
                agentes_match = ["kawiil-nelli"]
            for ag in agentes_match[:2]:
                L._legal_enqueue_priority(
                    ag,
                    f"DOF {fecha}: {r['titulo'][:120]} [{r['tipo_nota_raw'] or 'doc'}]",
                    "DOF diario — normativa publicada hoy")
                encolados += 1
        if encolados:
            print(f"Encoladas {encolados} entradas DOF para indexación prioritaria")
    except Exception as e:
        print(f"WARN: no encolé normativas DOF: {e}", file=sys.stderr)


def notificar_kawiil_central(titulo, cuerpo, tipo):
    """Best-effort: avisa en el app de Kawiil Central (solo a Polo por defecto).
    No rompe el boletín si falla (import o BD)."""
    try:
        if "/opt/openclaw/scripts" not in sys.path:
            sys.path.insert(0, "/opt/openclaw/scripts")
        import donna_core as L
        r = L._kawiil_central_notificar(titulo=titulo, cuerpo=cuerpo, para="", tipo=tipo)
        print(f"Kawiil Central: {r}")
    except Exception as e:
        print(f"WARN: no notifiqué a Kawiil Central: {e}", file=sys.stderr)


def build_html(relevantes, resto_counts, total, fecha, rows=None, ed_label="", resto_rows=None):
    # Motor HTML interactivo ÚNICO (mismo look que el análisis legal y el SJF).
    if "/opt/openclaw/scripts" not in sys.path:
        sys.path.insert(0, "/opt/openclaw/scripts")
    import donna_html as LH
    fl = fecha_larga(fecha)
    ddmm = fecha_ddmmyyyy(fecha)
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
                f'<td>{etag}{ttag}{esc(r["titulo"]).rstrip(". ")}</td></tr>')
        secc.append(
            f'<details class="sec" open><summary>{esc(dep)} '
            f'<span class="c">({len(it)})</span></summary><div class="sec-body">'
            f'<table><tbody>{"".join(filas)}</tbody></table></div></details>')
    body = "\n".join(secc) or '<p style="color:#888;font-style:italic">Sin documentos normativos relevantes en esta edición.</p>'

    n_rel = len(relevantes)
    n_resto = sum(resto_counts.values())
    n_deps = len(grupos)
    # Categorías que son puro ruido — solo mostrar conteo, sin títulos individuales
    CATS_SOLO_CUENTA = {"Avisos judiciales y generales", "Edictos"}
    if n_resto:
        resto_secc = []
        ruido_items = []
        for cat, n in sorted(resto_counts.items(), key=lambda x: -x[1]):
            cat_rows = (resto_rows or {}).get(cat, [])
            if cat_rows and cat not in CATS_SOLO_CUENTA:
                MAX_SHOW = 40
                filas = []
                for r in cat_rows[:MAX_SHOW]:
                    cod = r["cod_nota"]
                    tipo = esc(r["tipo_nota_raw"] or "")
                    ttag = f'<span class="tag">{tipo}</span> ' if tipo else ""
                    link = URL.format(cod=cod, f=ddmm)
                    filas.append(
                        f'<tr><td class="cod"><a href="{link}">{cod}</a></td>'
                        f'<td>{ttag}{esc(r["titulo"]).rstrip(". ")}</td></tr>')
                mas = (f'<p style="color:#888;font-size:.85em;margin:6px 0 0">'
                       f'+ {len(cat_rows) - MAX_SHOW} más</p>'
                       if len(cat_rows) > MAX_SHOW else "")
                resto_secc.append(
                    f'<details class="sec"><summary>{esc(cat)} '
                    f'<span class="c">({n})</span></summary><div class="sec-body">'
                    f'<table><tbody>{"".join(filas)}</tbody></table>{mas}</div></details>')
            else:
                ruido_items.append(f"<li>{esc(cat)}: <strong>{n}</strong></li>")
        if ruido_items:
            resto_secc.append(
                f'<details class="sec"><summary>Avisos / Edictos '
                f'<span class="c">({sum(v for k, v in resto_counts.items() if k in CATS_SOLO_CUENTA)})</span>'
                f'</summary><div class="sec-body"><ul>{"".join(ruido_items)}</ul></div></details>')
        body += (f'<details class="sec"><summary>Resto identificado '
                 f'<span class="c">({n_resto})</span></summary><div class="sec-body">'
                 + "".join(resto_secc) + '</div></details>')

    # Tarjetas KPI arriba (dashboard, no lista)
    kpis = LH.kpi_cards([
        {"value": total, "label": "Publicaciones del día"},
        {"value": n_rel, "label": "Normativas relevantes", "sub": "leyes/decretos/acuerdos…"},
        {"value": n_resto, "label": "Avisos / edictos", "sub": "el 'mar', no detallado"},
        {"value": n_deps, "label": "Dependencias", "sub": "con documento relevante"},
    ])
    body = kpis + body

    resumen = (f"<strong>{n_rel}</strong> documentos normativos relevantes "
               f"(leyes, decretos, acuerdos, reglamentos, circulares, lineamientos…) "
               f"de un total de <strong>{total}</strong> publicaciones. "
               f"El resto ({n_resto}) son avisos/edictos/convocatorias. "
               f"Da clic en el código para abrir la nota en el DOF.")
    # Contexto para el chat embebido: TODAS las publicaciones del día (relevantes +
    # resto con su título), para que el chat pueda responder qué se publicó/descargó.
    fuente_ctx = rows if rows is not None else relevantes
    ctx = (f"Boletín DOF del {fl}: {n_rel} documentos normativos relevantes de "
           f"{total} publicaciones totales (las {total} incluyen relevantes + "
           f"avisos/edictos/convocatorias). Lista completa de publicaciones del día:\n"
           + "\n".join(f"- [{(r['tipo_nota_raw'] or '').strip() or 'doc'}] "
                       f"{(r['titulo'] or '').strip()} (cód {r['cod_nota']})"
                       for r in fuente_ctx[:200]))
    titulo_ed = f"{fl} · Edición {ed_label}" if ed_label else fl
    return LH.render_page(
        f"📰 Diario Oficial — {titulo_ed}", "Diario Oficial de la Federación",
        body, ctx_md=ctx, con_chat=True, resumen=resumen,
        chat_titulo="💬 Pregúntale a Donna sobre el DOF de hoy",
        fuente="Fuente: Diario Oficial de la Federación (SEGOB)")



def main():
    # Flags:
    #   --ves / --vespertina  → reporta la edición VESPERTINA (default: MATUTINA)
    #   --ayer                → edición del día hábil anterior (compat con el flujo viejo)
    #   --force               → ignora fin de semana y el "ya enviado"
    # Por default reporta la edición del DÍA EN CURSO (la hora del server es CDMX).
    args = [a.lower() for a in sys.argv[1:]]
    force = "--force" in args
    usar_ayer = "--ayer" in args
    edicion = "VES" if ("--ves" in args or "--vespertina" in args) else "MAT"
    eds = ("VES",) if edicion == "VES" else ("MAT", "EXT")  # MAT arrastra las extraordinarias
    ed_label = EDIC.get(edicion, edicion)

    # El DOF no publica sábado/domingo: el boletín descansa esos días.
    if dt.datetime.now().weekday() >= 5 and not force:
        print("Fin de semana: el DOF no publica; boletín omitido.")
        return 0
    if not Path(DB).exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    if usar_ayer:
        fecha = conn.execute(
            "SELECT MAX(fecha) FROM notas WHERE fecha < date('now','localtime')").fetchone()[0]
    else:
        fecha = dt.date.today().isoformat()  # HOY (server en CDMX)
    if not fecha:
        print("Sin fechas válidas", file=sys.stderr)
        conn.close()
        return 1

    # Estado por (fecha, edición): MAT y VES se mandan por separado, una vez cada una.
    state = Path(os.environ.get("DOF_STATE", "/opt/openclaw/state/dof_last_sent.txt"))
    state_key = f"{fecha}_{edicion}"
    enviados = state.read_text().split() if state.exists() else []
    if state_key in enviados and not force:
        print(f"DOF {state_key} ya enviado; skip")
        conn.close()
        return 0

    ph = ",".join("?" * len(eds))
    rows = conn.execute(
        f"SELECT cod_nota, edicion, seccion, nombre_cod_orga_uno, tipo_nota_raw, titulo "
        f"FROM notas WHERE fecha=? AND edicion IN ({ph}) "
        f"ORDER BY seccion, nombre_cod_orga_uno, cod_nota", (fecha, *eds)).fetchall()
    conn.close()
    if not rows:
        # Sin esa edición todavía (la VESPERTINA no sale todos los días; la MATUTINA
        # puede no estar sincronizada aún). No mandamos nada para no hacer ruido.
        print(f"Sin notas para {fecha} edición {edicion}; no envío.")
        return 0

    relevantes, resto_counts, resto_rows = [], {}, {}
    for r in rows:
        clase, cat = clasifica(r)
        if clase == "relevante":
            relevantes.append(r)
        else:
            resto_counts[cat] = resto_counts.get(cat, 0) + 1
            resto_rows.setdefault(cat, []).append(r)

    fl = fecha_larga(fecha)
    fname = f"DOF_{fecha.replace('-', '')}_{edicion}.html"
    html_content = build_html(relevantes, resto_counts, len(rows), fecha, rows, ed_label, resto_rows)
    public_url = save_html_public(html_content, fname)
    url_line = f'\n🔗 <a href="{public_url}">Ver en navegador</a>' if public_url else ""
    caption = (f"📰 <b>Diario Oficial</b> — {esc(fl)} · <b>Edición {ed_label}</b>\n"
               f"<b>{len(relevantes)}</b> documentos relevantes (leyes/decretos/acuerdos/circulares…) "
               f"de {len(rows)} publicaciones. Detalle por dependencia en el adjunto.{url_line}")
    ok = send_doc(html_content, fname, caption)
    if ok:
        try:
            state.parent.mkdir(parents=True, exist_ok=True)
            # Conserva solo las marcas recientes (últimas ~30) para no crecer sin fin.
            nuevos = [x for x in enviados if x != state_key][-30:] + [state_key]
            state.write_text(" ".join(nuevos))
        except Exception as e:
            print(f"WARN: no guardé estado: {e}", file=sys.stderr)
        notificar_kawiil_central(
            titulo=f"DOF {ed_label} — {fl}",
            cuerpo=(f"{len(relevantes)} documentos normativos relevantes "
                    f"(leyes/decretos/acuerdos/circulares…) de {len(rows)} publicaciones. "
                    f"El detalle por dependencia llegó al Telegram de Donna."),
            tipo="dof_resumen")
        indexar_normativas_dof(relevantes, fecha)
    print("Enviado" if ok else "Falló el envío")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
