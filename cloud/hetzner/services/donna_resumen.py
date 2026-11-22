#!/usr/bin/env python3
"""donna_resumen.py — resúmenes del día de Donna como DATOS (base de datos), no como mensajes largos.

El scheduler arma el resumen (mediodía, cierre del día), lo guarda estructurado en donna_db.resumenes y la notificación es solo un aviso corto que apunta
a él («3 para hoy · 12 vencidas · 2 entregables»). En la app se abre completo, ordenado por secciones, con botones por pendiente.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import donna_db

TZ = ZoneInfo("America/Mexico_City")
TITULOS = {"tarde": "Seguimiento de mediodía", "cierre": "Cierre del día", "semanal": "Review semanal"}
ICONOS = {"tarde": "🔔", "cierre": "🌆", "semanal": "📊"}


def armar(core, slot: str, hoy=None) -> dict | None:
    """Datos estructurados del resumen. None si no hay nada accionable (no se avisa de la nada)."""
    hoy = hoy or datetime.now(TZ).date()
    agenda = core._read_space_file("AGENDA.md")
    g = core.agenda_por_fechas(agenda, hoy)
    ents = core._entregables_lista_tablero()
    listos = [e for e in ents if e.get("estado") == "listo"]
    briefs = [e for e in ents if e.get("estado") == "brief"]
    secciones = []
    if g["hoy"]:
        secciones.append({"id": "hoy", "titulo": "Para hoy", "items": [{"texto": i["texto"], "cuando": i.get("cuando", "")} for i in g["hoy"][:12]]})
    if g["manana"] and slot == "cierre":
        secciones.append({"id": "manana", "titulo": "Mañana", "items": [{"texto": i["texto"], "cuando": i.get("cuando", "")} for i in g["manana"][:8]]})
    if g["vencidas"]:
        secciones.append({"id": "vencidas", "titulo": "Vencidas (sin cerrar)", "items": [{"texto": i["texto"], "cuando": i.get("cuando", "")} for i in g["vencidas"][:8]],
                          "total": len(g["vencidas"])})
    if listos:
        secciones.append({"id": "entregables", "titulo": "Entregables esperando tu Vo.Bo.",
                          "items": [{"texto": e["titulo"] + (" (%s)" % e["cliente"] if e.get("cliente") else ""), "cuando": ""} for e in listos[:6]], "total": len(listos)})
    if briefs:
        secciones.append({"id": "briefs", "titulo": "Briefs por despachar", "items": [{"texto": e["titulo"], "cuando": ""} for e in briefs[:5]], "total": len(briefs)})
    if not (g["hoy"] or listos or briefs):
        return None
    conteos = {"hoy": len(g["hoy"]), "manana": len(g["manana"]), "vencidas": len(g["vencidas"]), "entregables": len(listos), "briefs": len(briefs)}
    return {"slot": slot, "conteos": conteos, "secciones": secciones}


def linea_corta(datos: dict) -> str:
    c = datos["conteos"]
    p = []
    if c["hoy"]:
        p.append("%d para hoy" % c["hoy"])
    if c["vencidas"]:
        p.append("%d vencidas" % c["vencidas"])
    if c["entregables"]:
        p.append("%d entregable(s) por aprobar" % c["entregables"])
    if c["briefs"]:
        p.append("%d brief(s)" % c["briefs"])
    return " · ".join(p) or "Sin pendientes"


def publicar(core, slot: str, notificar=None, hoy=None) -> str | None:
    """Guarda el resumen en la base y manda UN aviso corto que apunta a él. Devuelve el id (o None si no había nada)."""
    datos = armar(core, slot, hoy)
    if not datos:
        return None
    fecha = (hoy or datetime.now(TZ).date()).isoformat()
    titulo = "%s %s" % (ICONOS.get(slot, "🔔"), TITULOS.get(slot, "Resumen"))
    rid = donna_db.resumen_add(slot, fecha, titulo, datos)
    if notificar is None:
        import louis_push
        notificar = louis_push.notify
    notificar(titulo, linea_corta(datos), tab="notifs", kind="resumen", link="resumen:" + rid,
              full_body="**%s**\n\nToca «Ver resumen» para abrirlo completo y marcar lo que ya hiciste." % linea_corta(datos))
    return rid


def publicar_texto(tipo: str, titulo: str, texto: str, resumen_corto: str, notificar=None, hoy=None) -> str:
    """Para resúmenes que ya vienen redactados (review semanal): se guardan completos y el aviso es corto."""
    fecha = (hoy or datetime.now(TZ).date()).isoformat()
    rid = donna_db.resumen_add(tipo, fecha, titulo, {"slot": tipo}, texto)
    if notificar is None:
        import louis_push
        notificar = louis_push.notify
    notificar(titulo, resumen_corto, tab="notifs", kind="resumen", link="resumen:" + rid, full_body=resumen_corto)
    return rid
