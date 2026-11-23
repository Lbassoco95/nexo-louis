#!/usr/bin/env python3
"""donna_dia.py — «Tu día»: el resumen de la mañana y el cierre del día, y los avisos de lo que tiene hora, CON LAS PREFERENCIAS QUE POLO CONFIGURA.

Polo decide: a qué hora llega su día, por dónde (notificación, el chat de Donna, Telegram, o varios), qué incluye, cuántos minutos antes le recuerda lo que tiene hora y cuándo
NO molestar. Todo se guarda en la base (`preferencias`) y se cambia en Ajustes → «Tu día y tus avisos». Es una agenda que él pone; el agente de vigilancia sigue decidiendo
por su cuenta qué revisar y cuándo.
"""
import json
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import donna_db

TZ = ZoneInfo("America/Mexico_City")
CANALES = ("push", "chat", "telegram")
DEFAULTS = {
    "manana": {"activo": True, "hora": "08:00", "canales": ["push", "chat"]},
    "cierre": {"activo": False, "hora": "18:30", "canales": ["push"]},
    "recordar": {"activo": True, "minutos": 30, "canales": ["push"]},
    "no_molestar": {"activo": True, "desde": "22:00", "hasta": "07:00"},
    "incluye": {"reuniones": True, "tareas": True, "vencidas": True, "seguimientos": True, "urgente": True, "propuestas": True},
}
SCHEMA = "CREATE TABLE IF NOT EXISTS preferencias (k TEXT PRIMARY KEY, v TEXT NOT NULL, actualizado INTEGER NOT NULL);"
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


def prefs_get() -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    c = _c()
    try:
        for r in c.execute("SELECT k, v FROM preferencias"):
            if r["k"] in out:
                try:
                    out[r["k"]].update(json.loads(r["v"]))
                except ValueError:
                    pass
    finally:
        c.close()
    return out


def prefs_set(patch: dict) -> dict:
    """Valida y guarda SOLO lo que cambió. Lanza ValueError con un mensaje para Polo si algo no tiene sentido."""
    actual = prefs_get()
    for sec, vals in (patch or {}).items():
        if sec not in DEFAULTS or not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            if k not in DEFAULTS[sec]:
                continue
            base = DEFAULTS[sec][k]
            if k in ("hora", "desde", "hasta"):
                if not isinstance(v, str) or not _HORA.match(v):
                    raise ValueError("La hora debe ser como 08:00.")
            elif k == "canales":
                v = [x for x in (v if isinstance(v, list) else str(v).split("+")) if x in CANALES]
                if not v:
                    raise ValueError("Elige por lo menos un canal (notificación, chat o Telegram).")
            elif k == "minutos":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise ValueError("Los minutos deben ser un número.")
                if not 5 <= v <= 240:
                    raise ValueError("Los minutos de anticipación van de 5 a 240.")
            elif isinstance(base, bool):
                v = bool(v)
            actual[sec][k] = v
    c = _c()
    try:
        now = int(time.time())
        for sec in DEFAULTS:
            c.execute("INSERT INTO preferencias(k,v,actualizado) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, actualizado=excluded.actualizado", (sec, json.dumps(actual[sec]), now))
        c.commit()
    finally:
        c.close()
    return actual


def _min(h: str) -> int:
    return int(h[:2]) * 60 + int(h[3:5])


def en_silencio(ahora: datetime, p: dict) -> bool:
    nm = p["no_molestar"]
    if not nm["activo"]:
        return False
    m, d, h = ahora.hour * 60 + ahora.minute, _min(nm["desde"]), _min(nm["hasta"])
    return (d <= m < h) if d < h else (m >= d or m < h)               # el rango puede cruzar la medianoche (22:00 → 07:00)


# ------------------------------------------------------------------ armar el día
def armar(core, ahora: datetime, p: dict = None, calendario=None) -> dict:
    p = p or prefs_get()
    inc = p["incluye"]
    hoy = ahora.date()
    d = {"fecha": hoy.isoformat(), "reuniones": [], "tareas": [], "sin_hora": [], "vencidas": 0, "seguimientos": [], "urgentes": [], "propuestas": {}}
    try:
        g = core.agenda_por_fechas(core._read_space_file("AGENDA.md"), hoy)
    except Exception:
        g = {"hoy": [], "vencidas": []}
    if inc["tareas"]:
        for it in g.get("hoy", []):
            (d["tareas"] if it.get("hora") else d["sin_hora"]).append({"texto": _limpia(it["texto"]), "hora": it.get("hora", "")})
    if inc["vencidas"]:
        d["vencidas"] = len(g.get("vencidas", []))
    if inc["reuniones"]:
        try:
            d["reuniones"] = sorted(({"titulo": e["titulo"], "hora": e.get("hora", ""), "sin_responder": e.get("sin_responder", False)} for e in _calendario_hoy(hoy, calendario) if e.get("fecha") == hoy.isoformat()), key=lambda e: e["hora"])
        except Exception:
            d["reuniones"] = []
    if inc["seguimientos"]:
        fin = int(datetime(hoy.year, hoy.month, hoy.day, 23, 59, tzinfo=TZ).timestamp())
        d["seguimientos"] = [s["titulo"] for s in donna_db.seg_list(("activo",), 0) if 0 < s["proxima_revision"] <= fin][:5]
    if inc["urgente"]:
        try:
            import donna_agente
            d["urgentes"] = [x["titulo"] for x in donna_agente.planes(("propuesto",)) if x["gravedad"] == "urgente"][:3]
        except Exception:
            pass
    if inc["propuestas"]:
        try:
            import donna_radar, donna_tareas
            d["propuestas"] = {"cierres": len(donna_radar.propuestas()), "tareas": len(donna_tareas.listar())}
        except Exception:
            pass
    return d


_CAL = {"t": 0.0, "fecha": "", "items": []}


def _calendario_hoy(hoy, calendario=None) -> list:
    """Reuniones de hoy. El tick corre cada minuto, así que el calendario real se consulta como mucho cada 10 min."""
    if calendario is not None:
        return calendario(hoy)
    if _CAL["fecha"] == hoy.isoformat() and time.time() - _CAL["t"] < 600:
        return _CAL["items"]
    import donna_m365
    items = donna_m365.calendario_abiertos(hoy)
    _CAL.update(t=time.time(), fecha=hoy.isoformat(), items=items)
    return items


def _limpia(t: str) -> str:
    t = re.sub(r"\[[^\]]*\]", " ", t or "")
    t = re.sub(r"\(de la junta[^)]*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ·—-")[:140]


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def texto(d: dict, tipo: str, ahora: datetime, nombre: str = "Polo") -> str:
    """Como lo diría una asistente: lo más importante primero, corto, y una pregunta de cierre."""
    fecha = "%s %d de %s" % (_DIAS[ahora.weekday()], ahora.day, ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"][ahora.month - 1])
    saludo = ("☀️ Buenos días, %s. Esto es lo de hoy, %s:" % (nombre, fecha)) if tipo == "manana" else ("🌙 Cierre del día, %s (%s):" % (nombre, fecha))
    L = [saludo, ""]
    primero = None
    if d["urgentes"]:
        L.append("🚨 **Urgente:** " + "; ".join(d["urgentes"]))
        primero = "lo urgente"
    if d["reuniones"]:
        L.append("**Reuniones:**")
        L += ["• %s — %s%s" % (r["hora"] or "sin hora", r["titulo"], " (falta responder la invitación)" if r["sin_responder"] else "") for r in d["reuniones"][:6]]
        primero = primero or "la primera reunión"
    if d["tareas"] or d["sin_hora"]:
        L.append("**Tareas de hoy:**")
        L += ["• %s — %s" % (t["hora"], t["texto"]) for t in d["tareas"][:4]] + ["• " + t["texto"] for t in d["sin_hora"][:6 - min(len(d["tareas"]), 4)]]
        extra = len(d["tareas"]) + len(d["sin_hora"]) - 6
        if extra > 0:
            L.append("• …y %d más (están en Hoy)" % extra)
        primero = primero or "la primera tarea"
    if d["seguimientos"]:
        L.append("**Seguimientos que toca revisar:** " + "; ".join(d["seguimientos"][:3]))
    p = d["propuestas"]
    if p and (p.get("cierres") or p.get("tareas")):
        L.append("**Por decidir:** %s." % " y ".join(x for x in ("%d propuesta(s) de cierre" % p["cierres"] if p.get("cierres") else "", "%d tarea(s) propuestas de tus juntas" % p["tareas"] if p.get("tareas") else "") if x))
    if d["vencidas"]:
        L.append("Tienes **%d vencidas** acumuladas; ¿les damos una pasada hoy?" % d["vencidas"])
    if len(L) == 2:
        L.append("No veo nada con fecha para hoy. Día libre para avanzar lo importante: ¿qué quieres empujar?")
    elif primero:
        L += ["", "¿Empiezo por %s?" % primero]
    return "\n".join(L)


# ------------------------------------------------------------------ entrega por los canales que Polo eligió
def entregar(core, tipo: str, canales: list, ahora: datetime = None, notificar=None, usuarios=None, calendario=None) -> dict:
    ahora = ahora or datetime.now(TZ)
    p = prefs_get()
    d = armar(core, ahora, p, calendario)
    tx = texto(d, tipo, ahora)
    titulo = "☀️ Tu día" if tipo == "manana" else "🌙 Cierre del día"
    rid = donna_db.resumen_add(tipo, d["fecha"], titulo, {"slot": tipo, "dia": d, "secciones": []}, tx)
    hecho = []
    if "push" in canales:
        if notificar is None:
            import louis_push
            notificar = louis_push.notify
        n_t, n_r = len(d["tareas"]) + len(d["sin_hora"]), len(d["reuniones"])
        corta = " · ".join(x for x in ("%d reunión(es)" % n_r if n_r else "", "%d tarea(s)" % n_t if n_t else "", "%d vencidas" % d["vencidas"] if d["vencidas"] else "") if x) or "Sin pendientes con fecha"
        notificar(titulo, corta, tab="notifs", kind="resumen", link="resumen:" + rid, full_body=tx)
        hecho.append("push")
    if "chat" in canales:
        import app_chats
        for u in (usuarios if usuarios is not None else usuarios_app()):
            cid = app_chats.nuevo_id()
            app_chats.guardar(u, cid, [{"r": "a", "t": tx, "ts": int(time.time() * 1000)}], "%s · %d-%s" % (titulo, ahora.day, _MESES[ahora.month - 1]))
        hecho.append("chat")
    if "telegram" in canales:
        try:
            core._encolar_notificacion(tx[:3800], canal="telegram")
            hecho.append("telegram")
        except Exception:
            pass
    return {"resumen": rid, "canales": hecho, "texto": tx}


def usuarios_app() -> list:
    try:
        import app_auth
        return [u for u in app_auth.list_users() if not str(u).startswith("atajo-")]
    except Exception:
        return ["admin"]


# ------------------------------------------------------------------ el reloj de la entrega (cada minuto, lo llama el scheduler)
def _ya(clave: str) -> bool:
    import donna_radar
    return donna_radar.ya_corrio(clave, datetime.now(TZ).strftime("%Y-%m-%d"))


def _marca(clave: str):
    import donna_radar
    donna_radar.marcar_corrida(clave, datetime.now(TZ).strftime("%Y-%m-%d"))


def tick(core, ahora: datetime = None, notificar=None, usuarios=None, calendario=None) -> list:
    """Envía lo que toca según las preferencias de Polo: resumen de la mañana, cierre del día y recordatorios de lo que tiene hora. Respeta «no molestar»
    (si la hora cae dentro, sale al terminar el silencio). Una sola vez por día y por tipo."""
    ahora = ahora or datetime.now(TZ)
    p = prefs_get()
    enviados = []
    if en_silencio(ahora, p):
        return enviados
    m = ahora.hour * 60 + ahora.minute
    for tipo in ("manana", "cierre"):
        cfg = p[tipo]
        if cfg["activo"] and m >= _min(cfg["hora"]) and not _ya("dia:" + tipo):
            _marca("dia:" + tipo)
            try:
                r = entregar(core, tipo, cfg["canales"], ahora, notificar, usuarios, calendario)
                enviados.append(tipo)
            except Exception:
                import logging
                logging.getLogger("dia").exception("no pude armar «%s»", tipo)
    rc = p["recordar"]
    if rc["activo"]:
        enviados += _recordatorios(core, ahora, rc, notificar, calendario)
    return enviados


def _recordatorios(core, ahora: datetime, rc: dict, notificar=None, calendario=None) -> list:
    """«⏰ En 30 min: …» para lo que tiene hora hoy (tareas de la agenda y reuniones). Una vez por cada uno."""
    hoy = ahora.date()
    items = []
    try:
        g = core.agenda_por_fechas(core._read_space_file("AGENDA.md"), hoy)
        items += [(i["hora"], _limpia(i["texto"]), "tarea") for i in g.get("hoy", []) if i.get("hora")]
    except Exception:
        pass
    try:
        items += [(e["hora"], e["titulo"], "reunión") for e in _calendario_hoy(hoy, calendario) if e.get("fecha") == hoy.isoformat() and e.get("hora")]
    except Exception:
        pass
    out = []
    m = ahora.hour * 60 + ahora.minute
    for hora, titulo, cual in items:
        falta = _min(hora) - m
        clave = "rec:%s:%s" % (hora, re.sub(r"\W+", "", titulo.lower())[:30])
        if 0 <= falta <= rc["minutos"] and not _ya(clave):
            _marca(clave)
            if notificar is None:
                import louis_push
                notificar = louis_push.notify
            notificar("⏰ %s a las %s" % (cual.capitalize(), hora), titulo, tab="notifs", kind="recordatorio", full_body="En %d min: **%s** (%s)." % (max(falta, 1), titulo, hora))
            out.append(clave)
    return out
