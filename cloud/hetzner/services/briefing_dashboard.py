#!/usr/bin/env python3
"""briefing_dashboard.py — Briefing diario como DASHBOARD HTML interactivo.

En vez de mandar por Telegram una lista larguísima de pendientes, el briefing sale como:
  • un mensaje CORTO (conteos + próxima junta), y
  • un documento HTML interactivo (motor louis_html: tarjetas KPI, secciones
    colapsables, buscador, chat) con el detalle.

Datos duros, sin interpretación: calendario M365 en vivo (briefing_doc.fetch_eventos)
+ pendientes abiertos de AGENDA.md. Los pendientes se agrupan por tema y los que
traen una fecha ya pasada ("HOY 15-oct", "Mañana 24-jun") se separan en
"Con fecha vencida" para que sea obvio qué hay que cerrar o reprogramar.

API:  build(rango="hoy") -> (texto_corto, html_bytes, nombre_archivo)
"""
import datetime as dt
import html as _html
import re

import louis_html as LH

TZ = dt.timezone(dt.timedelta(hours=-6))  # CDMX
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
       "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MES_ABR = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
           "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}

_PREFIJO_FECHA = re.compile(
    r"^\s*(HOY|MA[ÑN]ANA)\s+(\d{1,2})[- ](ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\b[^·—:-]*[·—:-]?\s*",
    re.IGNORECASE)
_META = re.compile(r"\s*·?\s*\[(auto|visto)\s+(\d{4}-\d{2}-\d{2})\]\s*", re.IGNORECASE)
_TEMA = re.compile(r"^([^:·]{2,24}?)\s*[:·]\s+\S")

esc = _html.escape


def _fecha_vencida(txt: str, hoy: dt.date):
    """Fecha explícita al inicio ('HOY 15-oct', 'Mañana 24-jun'). Devuelve date o None."""
    m = _PREFIJO_FECHA.match(txt)
    if not m:
        return None
    mes = MES_ABR[m.group(3).lower()]
    dia = int(m.group(2))
    try:
        f = dt.date(hoy.year, mes, dia)
    except ValueError:
        return None
    return f


def _limpia(txt: str):
    """Quita las etiquetas [auto …]/[visto …] y devuelve (texto, meta)."""
    metas = [f"{m.group(1).lower()} {m.group(2)}" for m in _META.finditer(txt)]
    limpio = _META.sub(" ", txt).strip(" ·")
    return re.sub(r"\s{2,}", " ", limpio), (metas[-1] if metas else "")


def _tema(txt: str) -> str:
    resto = _PREFIJO_FECHA.sub("", txt, count=1)
    m = _TEMA.match(resto)
    if m:
        t = m.group(1).strip()
        if t and not t.upper().startswith(("HOY", "MAÑANA", "MANANA")):
            return t
    return "Otros"


def clasifica(pend: list, hoy: dt.date):
    """→ (vencidos, por_tema). vencidos: [(texto, meta, fecha)], por_tema: {tema: [(texto, meta)]}."""
    vencidos, por_tema = [], {}
    for raw in pend:
        texto, meta = _limpia(raw)
        f = _fecha_vencida(texto, hoy)
        if f is not None and f < hoy:
            vencidos.append((texto, meta, f))
        else:
            por_tema.setdefault(_tema(texto), []).append((texto, meta))
    vencidos.sort(key=lambda x: x[2])
    return vencidos, por_tema


def _lista(items) -> str:
    lis = []
    for it in items:
        texto, meta = it[0], it[1]
        m = f' <span style="color:#999;font-size:.75em">· {esc(meta)}</span>' if meta else ""
        lis.append(f"<li>{esc(texto)}{m}</li>")
    return f'<ul>{"".join(lis)}</ul>'


def _seccion(titulo: str, cuerpo: str, abierta: bool = False) -> str:
    return (f'<details class="sec"{" open" if abierta else ""}><summary>{titulo}</summary>'
            f'<div class="sec-body">{cuerpo}</div></details>')


def _tabla_eventos(eventos: list) -> str:
    filas = []
    for e in eventos:
        tag = "🎥 " if e.get("online") else ""
        extra = " · ".join(x for x in (e.get("lugar", ""), e.get("asistentes", "")) if x)
        extra_html = f'<div style="font-size:.8em;color:#777">{esc(extra)}</div>' if extra else ""
        filas.append(
            f'<tr><td style="white-space:nowrap;font-weight:bold">{esc(e["inicio"])}–{esc(e["fin"])}</td>'
            f'<td>{tag}<b>{esc(e["asunto"])}</b>{extra_html}</td>'
            f'<td style="color:#999;font-size:.8em">{esc(e.get("tenant", ""))}</td></tr>')
    return ('<table><thead><tr><th>Hora</th><th>Evento</th><th>Cuenta</th></tr></thead>'
            f'<tbody>{"".join(filas)}</tbody></table>')


def _proxima(eventos: list, ahora: dt.datetime):
    hhmm = ahora.strftime("%H:%M")
    for e in eventos:
        if e.get("fin", "") >= hhmm:
            return e
    return None


def build(rango: str = "hoy"):
    """Arma el briefing. Lanza excepción si no hay forma de armarlo (el llamador cae al texto)."""
    ahora = dt.datetime.now(TZ)
    fecha = ahora.date() + (dt.timedelta(days=1) if rango == "manana" else dt.timedelta())
    etiqueta = f"{DIAS[fecha.weekday()]} {fecha.day} de {MES[fecha.month]} de {fecha.year}"

    import briefing_doc as BDOC
    try:
        eventos, err_cal = BDOC.fetch_eventos(rango)
    except Exception as e:  # sin calendario no se cae todo el briefing
        eventos, err_cal = [], f"calendario no disponible ({e})"
    pend = BDOC.pendientes_abiertos(max_items=500)

    vencidos, por_tema = clasifica(pend, fecha)
    n_pend = len(pend)

    kpis = LH.kpi_cards([
        {"value": len(eventos), "label": "Eventos en calendario"},
        {"value": n_pend, "label": "Pendientes abiertos"},
        {"value": len(vencidos), "label": "Con fecha vencida",
         "sub": "revisar o reprogramar" if vencidos else "al corriente"},
        {"value": len(por_tema), "label": "Temas"},
    ])

    partes = [kpis]
    if err_cal:
        partes.append(f'<p style="color:#b35900;font-size:.85em">⚠️ {esc(err_cal)}</p>')
    cal = (_tabla_eventos(eventos) if eventos
           else '<p style="color:#888;font-style:italic">Sin eventos en el calendario. ✅ Día libre de juntas.</p>')
    partes.append(_seccion(f"📅 Calendario ({len(eventos)})", cal, abierta=True))
    if vencidos:
        partes.append(_seccion(f"🔴 Con fecha vencida ({len(vencidos)})", _lista(vencidos), abierta=True))
    temas = sorted((t for t in por_tema if t != "Otros"), key=lambda t: -len(por_tema[t]))
    if "Otros" in por_tema:
        temas.append("Otros")
    for t in temas:
        partes.append(_seccion(f"📌 {esc(t)} ({len(por_tema[t])})", _lista(por_tema[t])))

    ctx = "\n".join(f"- {_limpia(p)[0]}" for p in pend)[:4000]
    html_bytes = LH.render_page(
        f"Briefing — {etiqueta}", "Briefing diario", "".join(partes),
        ctx_md=ctx, con_chat=True,
        chat_titulo="💬 Pregúntale a Louis sobre tus pendientes",
        fuente="Calendario M365 en vivo + AGENDA.md · dato duro, sin interpretación")

    prox = _proxima(eventos, ahora) if rango == "hoy" else (eventos[0] if eventos else None)
    lineas = [
        f"☀️ *Briefing {DIAS[fecha.weekday()]} {fecha.day} {MES[fecha.month][:3]}*",
        f"📅 {len(eventos)} eventos · 📌 {n_pend} pendientes · 🔴 {len(vencidos)} con fecha vencida",
    ]
    if prox:
        lineas.append(f"⏭️ Siguiente: {prox['inicio']} {prox['asunto'][:60]}")
    lineas.append("Detalle interactivo en el adjunto 👇 (ábrelo en Safari/Chrome).")
    fname = f"Briefing_{fecha.isoformat().replace('-', '')}.html"
    return "\n".join(lineas), html_bytes, fname


def send_document(content: bytes, fname: str, caption: str = "") -> bool:
    """Manda el HTML por Telegram (reusa briefing_doc.send_doc)."""
    import briefing_doc as BDOC
    return BDOC.send_doc(content, fname, caption)
