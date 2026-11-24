#!/usr/bin/env python3
"""donna_minuta.py — la minuta de una junta guardada, lista para DESCARGAR (Markdown): resumen, notas, materiales, tareas propuestas y la transcripción
con horas y quién habló. Se arma al momento desde lo que hay en la base (no depende del documento de Entregables)."""
import re
import time

import donna_junta
import donna_personas
import donna_tareas


def _mmss(h) -> str:
    return str(h or "")


def datos(sesion_id: str, usuario: str = "admin") -> dict:
    """Todo lo de una junta guardada, para abrirla en la app y para armar la minuta. Lanza ValueError si no existe o no es del usuario."""
    donna_personas.asegurar_esquema()
    c = donna_personas._c()
    try:
        s = c.execute("SELECT * FROM sesiones_escucha WHERE id=?", (sesion_id,)).fetchone()
        if not s or (s["usuario"] != usuario and usuario != "admin"):
            raise ValueError("junta no encontrada")
        filas = [dict(r) for r in c.execute(
            "SELECT i.hora, i.voz_n, i.texto, i.original, i.idioma, p.nombre FROM intervenciones i LEFT JOIN personas p ON p.id=i.persona_id WHERE i.sesion_id=? ORDER BY i.id", (sesion_id,))]
    finally:
        c.close()
    j = donna_junta._c()
    try:
        notas = [dict(r) for r in j.execute("SELECT hora, texto, origen FROM junta_notas WHERE sesion_id=? ORDER BY n", (sesion_id,))]
        mats = [dict(r) for r in j.execute("SELECT hora, nombre, tipo, comentario FROM junta_materiales WHERE sesion_id=? ORDER BY n", (sesion_id,))]
    finally:
        j.close()
    try:
        tareas = [{"titulo": t["titulo"], "responsable": t.get("responsable") or "", "fecha_limite": t.get("fecha_limite") or ""} for t in donna_tareas.listar(sesion_id, ("propuesta", "aceptada"))]
    except Exception:
        tareas = []
    lineas, quienes = [], {}
    for f in filas:
        q = f["nombre"] or ("Voz %d" % f["voz_n"] if f["voz_n"] else "Voz sin identificar")
        quienes[q] = quienes.get(q, 0) + 1
        lineas.append({"hora": f["hora"] or "", "quien": q, "texto": f["texto"] or "", "original": f["original"] or ""})
    try:
        import donna_audio
        audio = donna_audio.resumen(sesion_id)
    except Exception:
        audio = {"tramos": 0, "bytes": 0, "vence": 0}
    return {"id": sesion_id, "titulo": s["titulo"], "fecha": s["fecha"], "duracion": int(s["duracion"] or 0), "resumen": s["resumen"] or "", "notas": notas, "materiales": mats,
            "tareas": tareas, "lineas": lineas, "voces": [{"nombre": k, "n": v} for k, v in sorted(quienes.items(), key=lambda kv: -kv[1])], "audio": audio}


def minuta_md(sesion_id: str, usuario: str = "admin"):
    """→ (nombre_de_archivo, texto). Lanza ValueError si la junta no existe o no es del usuario."""
    d = datos(sesion_id, usuario)
    dur = d["duracion"]
    L = ["# %s" % d["titulo"], "",
         "- **Fecha:** %s" % d["fecha"],
         ("- **Duración:** %d h %02d min" % (dur // 3600, dur % 3600 // 60)) if dur >= 3600 else "- **Duración:** %d min" % (dur // 60),
         "- **Intervenciones:** %d" % len(d["lineas"]),
         "- **Voces:** " + (", ".join("%s (%d)" % (v["nombre"], v["n"]) for v in d["voces"]) or "—"), ""]
    if d["resumen"]:
        L += ["## Resumen", "", d["resumen"].strip(), ""]
    if d["notas"]:
        L += ["## Notas tomadas durante la junta", ""] + ["- %s%s%s" % (("[%s] " % n["hora"]) if n["hora"] else "", n["texto"], " _(Donna)_" if n["origen"] == "donna" else "") for n in d["notas"]] + [""]
    if d["materiales"]:
        L += ["## Materiales de la junta", ""] + ["- %s«%s» (%s)%s" % (("[%s] " % m["hora"]) if m["hora"] else "", m["nombre"], m["tipo"], (" — " + m["comentario"]) if m["comentario"] else "") for m in d["materiales"]] + [""]
    if d["tareas"]:
        L += ["## Tareas propuestas de la junta", ""] + ["- %s%s%s" % (t["titulo"], (" — resp.: " + t["responsable"]) if t["responsable"] else "", (" · vence " + t["fecha_limite"]) if t["fecha_limite"] else "") for t in d["tareas"]] + [""]
    L += ["## Transcripción", ""]
    for f in d["lineas"]:
        L.append("**[%s] %s:** %s" % (f["hora"], f["quien"], f["texto"].replace("\n", " ")))
        if f["original"] and f["original"] != f["texto"]:
            L.append("<sub>%s</sub>" % f["original"].replace("\n", " "))
        L.append("")
    L.append("_Generado por Donna el %s. El audio de la junta se descarga aparte._" % time.strftime("%Y-%m-%d %H:%M"))
    nombre = re.sub(r"[^\w.-]+", "-", "%s-%s" % (d["titulo"].replace("Transcripción Escuchar ", "junta "), d["fecha"]), flags=re.UNICODE).strip("-")[:80] or "junta"
    return nombre + ".md", "\n".join(L) + "\n"
