#!/usr/bin/env python3
"""donna_seg.py — Seguimiento de Donna: lo que ella le da seguimiento por Polo (tareas, su día, una compra, lo que haga falta).

Es independiente de las notificaciones: un seguimiento vive en la base (donna_db.seguimientos) con su estado, nota y próxima revisión; la
notificación es solo UNA forma de avisar cuando le toca revisión. Cada aviso trae botones (Hecho · Posponer · Pausar) y se puede resolver desde la app.
"""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import donna_db

TZ = ZoneInfo("America/Mexico_City")
ESTADOS_TXT = {"activo": "activo", "pausado": "en pausa", "hecho": "hecho", "cancelado": "cancelado"}


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def proxima_por_defecto(ahora: datetime = None) -> int:
    """Sin fecha explícita: mañana a las 9:00 (hora de CDMX)."""
    n = ahora or datetime.now(TZ)
    return _ts((n + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0))


def cuando_a_ts(fecha_hora: str = "", en_horas: float = 0, ahora: datetime = None) -> int:
    """`en_horas` (p. ej. 3) o `fecha_hora` ISO (se interpreta en hora de CDMX si no trae zona). Vacío → mañana 9:00."""
    n = ahora or datetime.now(TZ)
    try:
        en_horas = float(en_horas or 0)
    except (TypeError, ValueError):
        en_horas = 0
    if en_horas > 0:
        return _ts(n + timedelta(hours=min(en_horas, 24 * 365)))
    fh = (fecha_hora or "").strip()
    if fh:
        try:
            d = datetime.fromisoformat(fh.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("No entendí la fecha. Usa formato 2026-11-23T09:00 o «en X horas».")
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)
        return _ts(d)
    return proxima_por_defecto(n)


def _cuando_txt(ts: int) -> str:
    if not ts:
        return "sin fecha"
    d = datetime.fromtimestamp(ts, TZ)
    hoy = datetime.now(TZ).date()
    dd = (d.date() - hoy).days
    pref = {0: "hoy", 1: "mañana", -1: "ayer"}.get(dd) or d.strftime("%d-%m")
    return "%s %s" % (pref, d.strftime("%H:%M"))


def crear(titulo: str, fecha_hora: str = "", en_horas: float = 0, nota: str = "", origen: str = "manual", ref: str = "",
          cadencia_horas: int = 24, prioridad: int = 0, usuario: str = "admin") -> dict:
    return donna_db.seg_crear(titulo, cuando_a_ts(fecha_hora, en_horas), nota, origen, ref, cadencia_horas, prioridad, usuario)


def accion(sid: str, que: str, fecha_hora: str = "", en_horas: float = 0, nota: str = None) -> dict:
    """que: hecho · pausar · reanudar · posponer · cancelar · nota."""
    s = donna_db.seg_get(sid)
    if not s:
        raise ValueError("seguimiento no encontrado")
    if que == "hecho":
        return donna_db.seg_actualizar(sid, estado="hecho", proxima_revision=0, nota=nota, evento="hecho")
    if que == "cancelar":
        return donna_db.seg_actualizar(sid, estado="cancelado", proxima_revision=0, nota=nota, evento="cancelado")
    if que == "pausar":
        return donna_db.seg_actualizar(sid, estado="pausado", nota=nota, evento="pausado")
    if que == "reanudar":
        return donna_db.seg_actualizar(sid, estado="activo", proxima_revision=cuando_a_ts(fecha_hora, en_horas), nota=nota, evento="reanudado")
    if que == "posponer":
        return donna_db.seg_actualizar(sid, proxima_revision=cuando_a_ts(fecha_hora, en_horas or 3), nota=nota, evento="pospuesto")
    if que == "nota":
        if not (nota or "").strip():
            raise ValueError("Falta la nota.")
        return donna_db.seg_actualizar(sid, nota=nota, evento="nota", detalle=nota)
    raise ValueError("acción no válida")


def listar_texto(usuario: str = None) -> str:
    """Para que Donna lo lea con una herramienta."""
    L = donna_db.seg_list(usuario=usuario)
    if not L:
        return "No estoy dándole seguimiento a nada por ahora."
    out = []
    for s in L:
        out.append("• [%s] %s — %s · revisar %s%s" % (s["id"], s["titulo"], ESTADOS_TXT.get(s["estado"], s["estado"]), _cuando_txt(s["proxima_revision"]),
                                                     (" · " + s["nota"]) if s["nota"] else ""))
    return "\n".join(out)


def tick(notificar=None) -> int:
    """A los seguimientos activos que ya les toca revisión se les avisa UNA vez (con botones) y su próxima revisión se corre por su cadencia,
    para no repetir el aviso cada minuto. Devuelve cuántos avisó."""
    if notificar is None:
        import louis_push
        notificar = louis_push.notify
    n = 0
    for s in donna_db.seg_a_revisar():
        nota = (s["nota"] or "").strip()
        cuerpo = ("Toca revisar: **%s**" % s["titulo"]) + ("\n\n" + nota if nota else "") + "\n\n_Dime cómo va, o usa los botones._"
        try:
            notificar("🔎 Seguimiento: " + s["titulo"][:70], (nota or "Toca revisar esto.")[:200], tab="notifs", kind="seguimiento",
                      link="seg:" + s["id"], full_body=cuerpo)
        except Exception:
            continue
        donna_db.seg_marcar_aviso(s["id"], int(time.time()) + int(s["cadencia_horas"]) * 3600)
        n += 1
    return n
