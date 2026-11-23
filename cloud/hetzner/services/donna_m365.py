#!/usr/bin/env python3
"""donna_m365.py — correo y calendario de Outlook (Kawiil y Yoltik) como FUENTES del radar proactivo. Solo lectura.

• Evidencia para cerrar pendientes: correos ENVIADOS por Polo («ya se mandó la carta»), respuestas RECIBIDAS y reuniones que ya ocurrieron.
• Fechas para el radar: reuniones de hoy y mañana, y las invitaciones que aún no ha respondido.
Privacidad: de cada correo solo se toma asunto, destinatarios y los primeros ~300 caracteres; nada se guarda salvo el fragmento de evidencia que
Donna cita al proponer un cierre. El texto de correos y eventos es DATO: el juez nunca lo trata como instrucciones.
Usa m365.py (mismos tokens que ya usa Donna). Sin token de un tenant, lo omite y lo reporta; nunca tumba al radar.
"""
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

TENANTS = ("kawiil", "yoltik")
TZ_CDMX = timezone(timedelta(hours=-6))
_TAGS = re.compile(r"<[^>]+>")


def _m365():
    import m365                       # mismo directorio que este módulo (/opt/openclaw/scripts)
    return m365


def tenants_disponibles(m=None) -> list:
    """Tenants con token guardado (los que se pueden leer)."""
    m = m or _m365()
    out = []
    for t in TENANTS:
        try:
            if (m.CREDS_DIR / f"m365-{t}-tokens.json").exists():
                out.append(t)
        except Exception:
            pass
    return out


def _limpia(txt: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", txt or "")).strip()[:n]


def _fecha(dt_txt: str) -> str:
    """Los eventos y correos llegan ya en hora de CDMX (m365.py pide esa zona) → 'YYYY-MM-DD'."""
    return (dt_txt or "")[:10]


def _iso_utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def correo_evidencias(dias: int = 10, m=None, ahora: datetime = None) -> list:
    m = m or _m365()
    ahora = ahora or datetime.now(TZ_CDMX)
    desde = _iso_utc(ahora - timedelta(days=dias))
    out, errores = [], []
    for t in tenants_disponibles(m):
        try:
            tok = m.get_access_token(t)
            q = urllib.parse.urlencode({"$top": "60", "$orderby": "sentDateTime desc", "$filter": f"sentDateTime ge {desde}",
                                        "$select": "id,subject,toRecipients,sentDateTime,bodyPreview"})
            for e in m.graph_get(tok, f"/me/mailFolders/sentitems/messages?{q}").get("value", []):
                para = ", ".join((r.get("emailAddress") or {}).get("name") or (r.get("emailAddress") or {}).get("address", "") for r in (e.get("toRecipients") or [])[:3])
                out.append({"fuente": "correo_enviado", "texto": "Polo envió a %s: «%s» — %s" % (para or "(sin destinatario)", _limpia(e.get("subject"), 120), _limpia(e.get("bodyPreview"))),
                            "fecha": _fecha(e.get("sentDateTime")), "ref": "m365:" + t + ":" + str(e.get("id", ""))[-24:]})
            q = urllib.parse.urlencode({"$top": "60", "$orderby": "receivedDateTime desc", "$filter": f"receivedDateTime ge {desde}",
                                        "$select": "id,subject,from,receivedDateTime,bodyPreview"})
            for e in m.graph_get(tok, f"/me/mailFolders/inbox/messages?{q}").get("value", []):
                de = (e.get("from") or {}).get("emailAddress") or {}
                out.append({"fuente": "correo_recibido", "texto": "Correo de %s: «%s» — %s" % (de.get("name") or de.get("address", "?"), _limpia(e.get("subject"), 120), _limpia(e.get("bodyPreview"))),
                            "fecha": _fecha(e.get("receivedDateTime")), "ref": "m365:" + t + ":" + str(e.get("id", ""))[-24:]})
        except Exception as ex:
            errores.append("%s: %s" % (t, str(ex)[:80]))
    if errores and not out:
        raise RuntimeError("; ".join(errores))
    return out


def _eventos(m, tenant: str, desde: datetime, hasta: datetime) -> list:
    tok = m.get_access_token(tenant)
    q = urllib.parse.urlencode({"startDateTime": _iso_utc(desde), "endDateTime": _iso_utc(hasta), "$orderby": "start/dateTime", "$top": "60",
                                "$select": "id,subject,start,end,attendees,isCancelled,responseStatus,isOrganizer,location"})
    return [e for e in m.graph_get(tok, f"/me/calendarview?{q}").get("value", []) if not e.get("isCancelled")]


def calendario_evidencias(dias: int = 10, m=None, ahora: datetime = None) -> list:
    """Reuniones que YA ocurrieron (con quién) — evidencia de «tener la junta con X»."""
    m = m or _m365()
    ahora = ahora or datetime.now(TZ_CDMX)
    out, errores = [], []
    for t in tenants_disponibles(m):
        try:
            for e in _eventos(m, t, ahora - timedelta(days=dias), ahora):
                if (e.get("responseStatus") or {}).get("response") in ("declined",):
                    continue
                gente = ", ".join(((a.get("emailAddress") or {}).get("name") or "") for a in (e.get("attendees") or [])[:6] if (a.get("emailAddress") or {}).get("name"))
                out.append({"fuente": "calendario", "texto": "Reunión realizada «%s»%s" % (_limpia(e.get("subject"), 120), (" con " + gente) if gente else ""),
                            "fecha": _fecha((e.get("start") or {}).get("dateTime")), "ref": "m365:" + t + ":" + str(e.get("id", ""))[-24:]})
        except Exception as ex:
            errores.append("%s: %s" % (t, str(ex)[:80]))
    if errores and not out:
        raise RuntimeError("; ".join(errores))
    return out


def calendario_abiertos(hoy, m=None, dias: int = 7) -> list:
    """Reuniones próximas e invitaciones sin responder, como elementos con fecha para el radar. Marcados `cerrable: False` (no se «cierran»)."""
    m = m or _m365()
    ahora = datetime.now(TZ_CDMX)
    ini = datetime(hoy.year, hoy.month, hoy.day, tzinfo=TZ_CDMX)
    out, errores = [], []
    for t in tenants_disponibles(m):
        try:
            for e in _eventos(m, t, ini, ini + timedelta(days=dias + 1)):
                resp = (e.get("responseStatus") or {}).get("response", "none")
                if resp == "declined":
                    continue
                st = (e.get("start") or {}).get("dateTime", "")
                hora = st[11:16]
                titulo = _limpia(e.get("subject"), 120) or "(sin título)"
                if resp == "notResponded" and not e.get("isOrganizer"):
                    titulo = "Responder invitación: " + titulo
                out.append({"tipo": "calendario", "ref": "m365:" + t + ":" + str(e.get("id", ""))[-24:], "titulo": titulo, "fecha": _fecha(st), "hora": hora, "cerrable": False,
                            "sin_responder": resp == "notResponded" and not e.get("isOrganizer")})
        except Exception as ex:
            errores.append("%s: %s" % (t, str(ex)[:80]))
    if errores and not out:
        raise RuntimeError("; ".join(errores))
    return out


def buscar_correo(consulta: str, m=None, limite: int = 5) -> str:
    """Búsqueda de texto en el correo (solo lectura) para el asistente de la junta: «De X (fecha): «asunto» — fragmento»."""
    m = m or _m365()
    q = (consulta or "").replace('"', " ").strip()[:120]
    if len(q) < 3:
        return "(escribe qué buscar en el correo)"
    out = []
    for t in tenants_disponibles(m):
        try:
            tok = m.get_access_token(t)
            p = urllib.parse.urlencode({"$search": '"%s"' % q, "$top": str(limite), "$select": "subject,from,receivedDateTime,bodyPreview"}, quote_via=urllib.parse.quote)
            for e in m.graph_get(tok, f"/me/messages?{p}").get("value", []):
                de = (e.get("from") or {}).get("emailAddress") or {}
                out.append("De %s (%s, %s): «%s» — %s" % (de.get("name") or de.get("address", "?"), _fecha(e.get("receivedDateTime")), t, _limpia(e.get("subject"), 120), _limpia(e.get("bodyPreview"))))
        except Exception as ex:
            out.append("(no pude buscar en %s: %s)" % (t, str(ex)[:60]))
    return "\n".join(out) or "(sin correos con eso)"


def fuentes(m=None) -> dict:
    """Se mezcla con las demás en donna_radar.fuentes_por_defecto."""
    return {"abiertos": {"calendario": lambda hoy: calendario_abiertos(hoy, m)},
            "evidencias": {"correo": lambda dias: correo_evidencias(dias, m), "calendario": lambda dias: calendario_evidencias(dias, m)}}
