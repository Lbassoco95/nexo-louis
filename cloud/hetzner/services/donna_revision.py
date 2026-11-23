#!/usr/bin/env python3
"""donna_revision.py — revisar un entregable CONTRA LAS FUENTES (transcripciones literales de las juntas, correo enviado, Dropbox, Cerebro, agenda), no contra resúmenes.

Es el método que Polo pidió: «¿ya se mandó la actualización? si no, ponlo como prioritario conforme a lo último comentado en las juntas». Reúne la evidencia con código
(determinista y con citas literales: fecha, hora y quién lo dijo) y calcula el veredicto de envío comparando fechas; el modelo solo la redacta. Si no hay transcripciones de
la junta, lo dice (y no inventa). También da a Donna las herramientas para LISTAR y LEER juntas completas y para ver los agentes de Kawiil Central.
"""
import re
import time
from datetime import datetime

import donna_db
import donna_personas

STOP = set(donna_personas.norm("""word pdf docx doc versión version final limpio borrador copia nuevo nueva persona física fisica moral para como entre sobre desde esta este estos estas con sin los las del una uno
 que por hoy archivo documento entregable revisar revisa""").split())
ACCION = re.compile(r"(?i)\b(actualiz\w+|cambi\w+|ajust\w+|falta\w*|mand\w+|env[ií]\w+|revis\w+|quedamos|pendiente|corregir|agregar|quitar|hay que|tenemos que|necesitamos|firm\w+|aprob\w+)\b")


def claves(titulo: str) -> list:
    out = []
    for t in re.findall(r"[a-z0-9]{4,}", donna_personas.norm(titulo)):
        if t not in STOP and t not in out:
            out.append(t)
    return out[:6]


# ------------------------------------------------------------------ juntas: listar y leer LITERAL
def juntas_listar(consulta: str = "", limite: int = 15, usuario: str = "admin") -> list:
    """Sesiones de Escuchar guardadas (más recientes primero). Con `consulta`, solo las que mencionan esas palabras (todas las palabras, en cualquier frase de la junta)."""
    c = donna_personas._c()
    try:
        ses = [dict(r) for r in c.execute("SELECT id, fecha, titulo, lineas, duracion FROM sesiones_escucha WHERE usuario=? ORDER BY ts DESC LIMIT 200", (usuario,))]
        pal = [t for t in re.findall(r"[a-z0-9]{3,}", donna_personas.norm(consulta))]
        out = []
        for s in ses:
            if pal:
                txt = donna_personas.norm(" ".join(r[0] for r in c.execute("SELECT texto FROM intervenciones WHERE sesion_id=?", (s["id"],))))
                if not all(p in txt for p in pal):
                    continue
            s["participantes"] = [r[0] for r in c.execute("SELECT DISTINCT p.nombre FROM intervenciones i JOIN personas p ON p.id=i.persona_id WHERE i.sesion_id=?", (s["id"],))]
            out.append(s)
            if len(out) >= limite:
                break
        return out
    finally:
        c.close()


def junta_leer(sesion_id: str, desde: int = 0, limite: int = 60) -> str:
    """Transcripción LITERAL (frase por frase, con hora y quién habló), no un resumen."""
    c = donna_personas._c()
    try:
        s = c.execute("SELECT fecha, titulo, lineas FROM sesiones_escucha WHERE id=?", (sesion_id,)).fetchone()
        if not s:
            return "❌ No existe esa junta (usa juntas_listar para ver los ids)."
        desde, limite = max(0, int(desde or 0)), max(5, min(int(limite or 60), 150))
        rows = c.execute("SELECT i.hora, i.voz_n, i.texto, p.nombre FROM intervenciones i LEFT JOIN personas p ON p.id=i.persona_id WHERE i.sesion_id=? ORDER BY i.id LIMIT ? OFFSET ?", (sesion_id, limite, desde)).fetchall()
        L = ["[%s] %s: %s" % (r["hora"], r["nombre"] or ("Voz %d" % r["voz_n"] if r["voz_n"] else "Voz sin identificar"), r["texto"]) for r in rows]
        resto = max(0, s["lineas"] - desde - len(rows))
        return "«%s» (%s) — transcripción literal, frases %d-%d de %d:\n%s%s" % (s["titulo"], s["fecha"], desde + 1, desde + len(rows), s["lineas"], "\n".join(L),
                                                                           ("\n[… quedan %d frases: pídelas con desde=%d]" % (resto, desde + limite)) if resto else "")
    finally:
        c.close()


# ------------------------------------------------------------------ revisar un entregable contra las fuentes
def _entregable(core, titulo: str):
    tk = set(claves(titulo))
    mejor = []
    for it in core._entregables_lista_tablero():
        ti = set(claves(it["titulo"]))
        if not tk or not ti:
            continue
        sc = len(tk & ti) / len(tk)
        if sc >= 0.5:
            mejor.append((sc, it))
    if not mejor:
        return None
    it = max(mejor, key=lambda x: (x[0], x[1].get("fecha", "")))[1]
    meta = {}
    try:
        meta = core._cerebro_parsear_fm(core.ENTREGABLES_PATH / it["archivo"])
    except Exception:
        pass
    vobo = next((v for k, v in meta.items() if "vo" in k.lower() and "bo" in k.lower()), "")
    return {"archivo": it["archivo"], "titulo": it["titulo"], "estado": it["estado"], "cliente": it.get("cliente", ""), "actualizado": (it.get("fecha") or "")[:10], "vobo": vobo}


def _must(ent) -> list:
    """Palabras que DEBEN aparecer para que algo sea sobre ESTE asunto: el cliente del entregable (p. ej. «vizum»). Evita que «contratos»/«transmisor» traigan cualquier cosa."""
    return claves((ent or {}).get("cliente", ""))[:2]


def _juntas_sobre(ks: list, usuario="admin", must=()) -> list:
    """Sesiones que hablan del tema (≥2 palabras clave distintas, o la más rara si el título tiene pocas) con las frases LITERALES que las mencionan."""
    por_ses = {}
    for k in ks:
        for h in donna_personas.buscar(k, "", 80, usuario):
            e = por_ses.setdefault(h["sesion_id"], {"sesion_id": h["sesion_id"], "fecha": h["fecha"], "titulo": h["titulo"], "claves": set(), "frases": {}})
            e["claves"].add(k)
            e["frases"][h["id"]] = h
    minimo = 1 if len(ks) <= 2 else 2
    out = []
    for e in por_ses.values():
        if must and not (set(must) & e["claves"]):
            continue
        if len(e["claves"]) >= minimo:
            fr = sorted(e["frases"].values(), key=lambda h: (not ACCION.search(h["texto"]), h["hora"]))
            e["frases"] = fr[:8]
            e["claves"] = sorted(e["claves"])
            out.append(e)
    return sorted(out, key=lambda e: e["fecha"], reverse=True)


def _agenda_coincide(core, ks: list) -> list:
    try:
        L = [l.strip() for l in (core.SPACE / "AGENDA.md").read_text().splitlines() if re.match(r"^\s*-\s*\[\s*\]", l)]
    except Exception:
        return []
    return [l for l in L if len([k for k in ks if k in donna_personas.norm(l)]) >= min(2, len(ks))][:6]


_CRITERIO = re.compile(r"(?i)criterio|regla|nunca|siempre|\bno\s+se\s+(?:consolida|aprueba|env|mand)")
_VERSION = re.compile(r"(?i)versi[oó]n|actualizaci|aprob|vobo|enviar|mandar|firma")


def _notas_memoria(core, ks: list, must=()) -> list:
    """Líneas de la memoria viva (Cowork, JOURNAL, PROJECTS…) que hablan del tema. Son NOTAS/RESÚMENES: sirven de pista cuando no hay transcripción, y se rotulan así."""
    out = []
    archivos = list(getattr(core, "MEMORY_FILES", [])) + ["COWORK.md"]
    prioridad = {"LEARNINGS.md": 0, "IMPORTANT.md": 0, "COWORK.md": 1}                # criterios y decisiones de Polo primero, y completos
    for f in dict.fromkeys(archivos):
        if f in ("AGENDA.md", "USER.md"):
            continue
        try:
            txt = (core.SPACE / f).read_text()
        except Exception:
            continue
        for l in txt.splitlines():
            n = donna_personas.norm(l)
            hit = len([k for k in ks if k in n])
            criterio = 1 if (_CRITERIO.search(l) and _VERSION.search(l)) else 0            # «criterio/regla de Polo» sobre versiones, envíos o aprobaciones: lo primero
            del_cliente = (not must) or any(m in n for m in must)
            if len(l.strip()) > 20 and del_cliente and (hit >= min(2, len(ks)) or (criterio and must)):
                out.append((-criterio, prioridad.get(f, 2), -hit, f, l.strip()[:700 if (criterio or prioridad.get(f) == 0) else 300]))
    out.sort()
    return [{"archivo": f, "linea": l} for _c, _p, _h, f, l in out[:6]]


def _correo_reciente(ks: list, m=None, must=()) -> str:
    import donna_m365
    try:
        q = list(must) + [k for k in ks if k not in must]
        return donna_m365.buscar_correo(" ".join(q[:3]), m, 5)
    except Exception as e:
        return "(no pude buscar en el correo: %s)" % str(e)[:80]


def _donde_busque() -> str:
    c = donna_personas._c()
    try:
        n = c.execute("SELECT COUNT(*) FROM sesiones_escucha").fetchone()[0]
        f = c.execute("SELECT COUNT(*) FROM intervenciones").fetchone()[0]
    finally:
        c.close()
    return "%d junta(s) guardada(s) con Escuchar (%d frases en total)" % (n, f)


def _correos_enviados(ks: list, m=None, must=()) -> list:
    import donna_m365
    vistos, out = set(), []
    q3 = list(must) + [k for k in ks if k not in must]
    for q in (" ".join(q3[:3]), q3[0] if q3 else ""):
        for e in donna_m365.correo_enviado_buscar(q, m):
            clave = (e["fecha"], e["asunto"])
            if clave not in vistos:
                vistos.add(clave)
                out.append(e)
    return sorted(out, key=lambda x: x["fecha"], reverse=True)


def veredicto_envio(ultima_junta: str, ultimo_envio: str, ultimo_cambio: str) -> tuple:
    """(código, texto). Compara FECHAS: ¿se envió después de lo último que se habló y de la última actualización?"""
    if not ultimo_envio:
        return "sin_evidencia", "SIN EVIDENCIA DE ENVÍO: no encontré en tu correo enviado (Outlook de Kawiil y Yoltik) nada con este documento. Ojo: no veo WhatsApp, Teams ni enlaces de Dropbox; si se mandó por ahí, no lo sé."
    if ultima_junta and ultimo_envio < ultima_junta:
        return "antes_de_junta", "ENVIADO, PERO ANTES DE LA ÚLTIMA JUNTA (envío %s · junta %s): puede faltar lo que se acordó después." % (ultimo_envio, ultima_junta)
    if ultimo_cambio and ultimo_envio < ultimo_cambio:
        return "antes_de_cambio", "ENVIADO, PERO ANTES DE LA ÚLTIMA ACTUALIZACIÓN del entregable (envío %s · actualización %s): falta mandar la versión nueva." % (ultimo_envio, ultimo_cambio)
    return "enviado", "ENVIADO el %s%s." % (ultimo_envio, (" (después de la última junta del %s)" % ultima_junta) if ultima_junta else "")


def revisar_entregable(core, titulo: str, marcar_prioritario: bool = True, m365=None, dropbox=None, usuario: str = "admin") -> dict:
    ks = claves(titulo)
    if not ks:
        raise ValueError("Dime el nombre del entregable.")
    ent = _entregable(core, titulo)
    must = _must(ent)
    juntas = _juntas_sobre(ks, usuario, must)
    correos = _correos_enviados(ks, m365, must)
    rel = [e for e in correos if not e.get("error") and (len([k for k in ks if k in donna_personas.norm(e["asunto"] + " " + " ".join(e["adjuntos"]))]) >= min(2, len(ks)))]
    ultima_junta = juntas[0]["fecha"] if juntas else ""
    ultimo_envio = rel[0]["fecha"] if rel else ""
    ultimo_cambio = (ent or {}).get("actualizado", "")
    cod, txt = veredicto_envio(ultima_junta, ultimo_envio, ultimo_cambio)
    try:
        dbx = (dropbox or core._dropbox_buscar)(" ".join(ks[:3]), 8)
    except Exception as e:
        dbx = "(no pude buscar en Dropbox: %s)" % str(e)[:80]
    ag = _agenda_coincide(core, ks)
    notas = _notas_memoria(core, ks, must)
    reciente = _correo_reciente(ks, m365, must)
    r = {"notas": notas, "correo_reciente": reciente, "donde": _donde_busque(), "claves": ks, "entregable": ent, "juntas": juntas, "correos": rel, "correos_sin_filtrar": correos, "dropbox": str(dbx)[:1500], "agenda": ag, "veredicto": cod, "veredicto_txt": txt,
         "ultima_junta": ultima_junta, "ultimo_envio": ultimo_envio, "prioritario": None}
    if marcar_prioritario and cod != "enviado":
        r["prioritario"] = _marcar_prioritario(core, titulo, txt, juntas)
    r["texto"] = _texto(r)
    r["brief"] = brief_asistente(r)
    return r


def _marcar_prioritario(core, titulo: str, motivo: str, juntas: list) -> str:
    """Lo deja en la agenda como URGENTE y en seguimiento (diario), con el motivo. No duplica si ya está."""
    ks = set(claves(titulo))
    ya = [l for l in _agenda_coincide(core, list(ks)) if "URGENTE" in l.upper()]
    if ya:
        return "Ya estaba como urgente en la agenda."
    hoy = datetime.now(core.TZ_CDMX).strftime("%Y-%m-%d")
    p = core.SPACE / "AGENDA.md"
    txt = p.read_text() if p.exists() else "# AGENDA\n"
    base = ("- [ ] **URGENTE** Revisar «%s» conforme a lo último dicho en la junta del %s · [capturado %s]" % (titulo[:90], juntas[0]["fecha"], hoy)) if juntas else \
        ("- [ ] **URGENTE** Confirmar si ya se envió «%s» y qué actualización se pidió (sin junta guardada ni envío en correo) · [capturado %s]" % (titulo[:90], hoy))
    p.write_text(txt.rstrip("\n") + "\n" + base + "\n")
    try:
        import donna_seg
        donna_seg.crear("Revisar «%s» (prioritario)" % titulo[:80], "", 0, motivo, origen="tarea", cadencia_horas=24, prioridad=1)
    except Exception:
        pass
    return "Lo dejé como URGENTE en la agenda y en seguimiento diario."


_DBX = re.compile(r"^\s*(.+?)\s+→\s+(\S.*?)\s+\(modificado (\d{4}-\d{2}-\d{2})\)\s*$")


def _versiones_dropbox(txt: str, ks: list, must=()) -> list:
    """Archivos de Dropbox que parecen ser ESTE documento, la fecha más reciente primero: [(nombre, fecha)]."""
    out = []
    for l in (txt or "").splitlines():
        m = _DBX.match(l)
        n = donna_personas.norm(m.group(1)) if m else ""
        if m and re.search(r"(?i)\.(docx?|pdf|xlsx?)$", m.group(1)) and len([k for k in ks if k in n]) >= min(2, len(ks)) and (not must or any(x in n for x in must)):
            out.append((m.group(1), m.group(3)))
    return sorted(out, key=lambda x: x[1], reverse=True)


def _criterio_de_polo(r: dict) -> str:
    for n in r.get("notas", []):
        i = n["linea"].lower().find("criterio de polo")
        if i >= 0:
            frag = n["linea"][i:i + 330]
            cut = frag.rfind(". ", 120)
            return frag[:cut + 1] if cut > 0 else frag.rstrip(" ,;") + "…"
    return ""


def _f(d: str) -> str:
    """2026-09-10 → 10-sep-2026"""
    try:
        y, m, dd = d[:10].split("-")
        return "%d-%s-%s" % (int(dd), ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"][int(m) - 1], y)
    except Exception:
        return d or "sin fecha"


def brief_asistente(r: dict) -> str:
    """La respuesta como la daría una asistente ejecutiva: PRIMERO la conclusión, luego lo esencial en pocas líneas, lo que ya hizo y lo que sigue. La evidencia completa queda aparte
    (se ve escribiendo «evidencia»); nunca se vuelca cruda al chat."""
    e, ks = r["entregable"], r["claves"]
    cod, juntas = r["veredicto"], r["juntas"]
    if cod == "sin_evidencia":
        concl = "No encuentro que se haya enviado la actualización" + (", y no tengo la junta para saber qué se pidió." if not juntas else " (la última junta sobre esto fue el %s)." % _f(juntas[0]["fecha"]))
    elif cod == "antes_de_junta":
        concl = "Se envió el %s, pero **antes** de la última junta (%s): puede faltar lo que se acordó después." % (_f(r["ultimo_envio"]), _f(r["ultima_junta"]))
    elif cod == "antes_de_cambio":
        concl = "Se envió el %s, pero el entregable cambió después: falta mandar la versión nueva." % _f(r["ultimo_envio"])
    else:
        c = r["correos"][0] if r["correos"] else {}
        concl = "Sí: se envió el %s%s." % (_f(r["ultimo_envio"]), (" a " + c["para"]) if c.get("para") else "")
    L = ["**" + concl + "**", ""]
    if e:
        L.append("• En Cerebro está en **%s**%s, actualizado el %s." % (e["estado"], (" con VoBo " + e["vobo"]) if e["vobo"] else "", _f(e["actualizado"])))
    else:
        L.append("• No lo encontré por ese nombre en Cerebro.")
    vs = _versiones_dropbox(r["dropbox"], ks, _must(e))
    if vs:
        L.append("• En Dropbox, lo más reciente es **%s** (%s)%s." % (vs[0][0], _f(vs[0][1]), (", y también " + ", ".join("%s (%s)" % (n, _f(f)) for n, f in vs[1:3])) if len(vs) > 1 else ""))
    L.append("• Correo enviado con este documento: %s." % ("nada" if not r["correos"] else "; ".join("%s a %s" % (_f(c["fecha"]), c["para"] or "?") for c in r["correos"][:2])))
    if juntas:
        h = juntas[0]["frases"][0] if juntas[0]["frases"] else None
        L.append("• Junta del %s: %s" % (_f(juntas[0]["fecha"]), ("«%s» — %s [%s]" % (h["texto"][:200], h["persona"] or "voz sin nombre", h["hora"])) if h else "se habló del tema."))
    crit = _criterio_de_polo(r)
    if crit:
        L += ["", "**Ojo con tu criterio:** " + crit]
    L += ["", "**Lo que hice:** " + (r["prioritario"] or "no hacía falta marcar nada.")]
    sig = []
    if not juntas:
        sig.append("si la junta fue en Teams, Zoom o Cowork, dime dónde quedó y la sumo; de ahora en adelante, con Escuchar queda como fuente literal")
    if e and e.get("vobo", "").lower() == "pendiente":
        sig.append("el VoBo sigue pendiente: ¿lo apruebas, o va a otra revisión antes de mandarse?")
    if cod != "enviado":
        sig.append("dime qué cambio pidieron y lo dejo como tarea con fecha")
    if sig:
        L += ["", "**Qué sigue:**"] + ["• " + x[0].upper() + x[1:] + ("" if x.endswith("?") else ".") for x in sig[:3]]
    L += ["", "_Escribe «evidencia» y te muestro todo lo que revisé._"]
    return "\n".join(L)


def _texto(r: dict) -> str:
    L = ["REVISIÓN CONTRA FUENTES (los datos de abajo son la evidencia; no afirmes nada que no esté aquí)"]
    e = r["entregable"]
    L.append("\n📋 ENTREGABLE EN CEREBRO: " + ("«%s» · estado: %s · VoBo: %s · última actualización: %s%s" % (e["titulo"], e["estado"], e["vobo"] or "(sin dato)", e["actualizado"] or "(sin fecha)", (" · cliente: " + e["cliente"]) if e["cliente"] else "")
                                                if e else "no lo encontré por ese nombre en Cerebro."))
    L.append("\n🎙 JUNTAS GUARDADAS QUE HABLAN DE ESTO (transcripción literal, no resumen): %d  [busqué entre %s]" % (len(r["juntas"]), r["donde"]))
    if not r["juntas"]:
        L.append("   ⚠️ NO HAY TRANSCRIPCIONES GUARDADAS sobre este tema. Si la junta se grabó fuera de Escuchar (Teams, Zoom, Cowork), Donna todavía no puede leerla: dile a Polo dónde está y regístralo con pedir_mejora.")
    if r["notas"]:
        L.append("   Pistas en tus NOTAS/MEMORIA (son resúmenes, NO la transcripción; úsalas solo como pista y dilo así):")
        L += ["   · [%s] %s" % (n["archivo"], n["linea"]) for n in r["notas"]]
    for j in r["juntas"][:4]:
        L.append("   • %s — «%s» (id %s)" % (j["fecha"], j["titulo"], j["sesion_id"]))
        for h in j["frases"][:6]:
            L.append("       [%s] %s: «%s»" % (h["hora"], h["persona"] or "voz sin nombre", h["texto"][:260]))
    L.append("\n✉️ CORREO ENVIADO RELACIONADO: %d" % len(r["correos"]))
    for c in r["correos"][:5]:
        L.append("   • %s → %s · «%s»%s" % (c["fecha"], c["para"] or "?", c["asunto"], (" · adjuntos: " + ", ".join(c["adjuntos"])) if c["adjuntos"] else ""))
    errores = [c for c in r["correos_sin_filtrar"] if c.get("error")]
    if errores:
        L.append("   ⚠️ " + errores[0]["asunto"])
    L.append("   Correo reciente (recibido o enviado) que menciona el tema:\n" + "\n".join("   " + x for x in (r["correo_reciente"] or "(nada)").splitlines()[:6]))
    L.append("\n📁 DROPBOX (versiones/archivos encontrados):\n" + (r["dropbox"] or "(nada)"))
    if r["agenda"]:
        L.append("\n🗓 YA EN TU AGENDA:\n" + "\n".join("   • " + a for a in r["agenda"]))
    L.append("\nVEREDICTO DE ENVÍO (calculado comparando fechas): " + r["veredicto_txt"])
    if r["prioritario"]:
        L.append("ACCIÓN: " + r["prioritario"])
    return "\n".join(L)


def kc_agente_resumen(core, nombre: str) -> dict:
    """Lo esencial de un agente de Kawiil Central, sacado de agent_registry y agent_activity_logs: {encontrado, display_name, descripcion, estado, modelo, capacidades, ultimas, ultima_fecha}."""
    conn, err = core._kawiil_central_pg()
    if err:
        return {"encontrado": False, "error": err.replace("❌ ", "")}
    try:
        conn.autocommit = True
        cur = conn.cursor()
        q = "%" + nombre.strip().lower() + "%"
        cur.execute("SELECT id::text, name, display_name, description, status, model, capabilities FROM public.agent_registry WHERE lower(name) LIKE %s OR lower(display_name) LIKE %s ORDER BY 1 LIMIT 1", (q, q))
        a = cur.fetchone()
        if not a:
            conn.close()
            return {"encontrado": False}
        cur.execute("SELECT message, created_at FROM public.agent_activity_logs WHERE agent_id::text=%s AND action='task_completed' ORDER BY created_at DESC LIMIT 4", (a[0],))
        ult = []
        for msg, fecha in cur.fetchall():
            m = re.search(r"completed:\s*(.+?)(?:\s*\(\d+(?:\.\d+)?s\))?$", msg or "")
            ult.append({"titulo": (m.group(1) if m else (msg or ""))[:110], "fecha": str(fecha)[:10]})
        conn.close()
        caps = a[6] if isinstance(a[6], list) else []
        return {"encontrado": True, "id": a[0], "name": a[1], "display_name": a[2] or a[1], "descripcion": (a[3] or "")[:300], "estado": a[4] or "?", "modelo": a[5] or "", "capacidades": [str(c) for c in caps][:6],
                "ultimas": ult, "ultima_fecha": ult[0]["fecha"] if ult else ""}
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return {"encontrado": False, "error": str(e)[:160]}


def kc_agente_tareas_esquema(core, agent_id: str) -> str:
    """Solo lectura: columnas de agent_tasks y las últimas 3 tareas de ese agente (valores recortados). Va como EVIDENCIA de la mejora «ejecutar agentes», para que quien la construya
    (Claude Code) vea cómo se le asigna una tarea a un agente sin otra vuelta de preguntas."""
    conn, err = core._kawiil_central_pg()
    if err:
        return "(sin esquema: %s)" % err.replace("❌ ", "")
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='agent_tasks' ORDER BY ordinal_position")
        cols = cur.fetchall()
        if not cols:
            conn.close()
            return "(no encontré la tabla agent_tasks)"
        out = ["agent_tasks — columnas:"] + ["  %s %s%s%s" % (c, t, "" if n == "YES" else " NOT NULL", (" default " + str(d)[:40]) if d else "") for c, t, n, d in cols]
        cur.execute("SELECT DISTINCT status FROM public.agent_tasks LIMIT 12") if any(c[0] == "status" for c in cols) else None
        if any(c[0] == "status" for c in cols):
            out.append("valores de status vistos: " + ", ".join(str(r[0]) for r in cur.fetchall()))
        if agent_id and any(c[0] == "agent_id" for c in cols):
            cur.execute("SELECT * FROM public.agent_tasks WHERE agent_id::text=%s ORDER BY 1 DESC LIMIT 3", (agent_id,))
            nom = [d[0] for d in cur.description]
            for f in cur.fetchall():
                out.append("ejemplo: " + "; ".join("%s=%s" % (n, (str(v) if v is not None else "")[:70]) for n, v in zip(nom, f) if v not in (None, ""))[:700])
        conn.close()
        return "\n".join(out)[:2300]
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return "(no pude leer agent_tasks: %s)" % str(e)[:120]


def brief_agente(nombre: str, a: dict) -> str:
    """Cómo lo diría una asistente: quién es el agente, qué hizo últimamente y qué sigue (sin tablas ni ids)."""
    if a.get("error"):
        return "No pude consultar a %s en Kawiil Central ahora (%s)." % (nombre, a["error"])
    if not a.get("encontrado"):
        return "No encuentro a un agente llamado «%s» en Kawiil Central." % nombre
    L = ["**%s** está en Kawiil Central y ahora está en **%s**." % (a["display_name"], {"idle": "reposo (disponible)", "running": "ejecución", "busy": "ejecución"}.get(a["estado"], a["estado"]))]
    if a["descripcion"]:
        L.append("Se dedica a: " + a["descripcion"].rstrip(".") + ".")
    if a["ultimas"]:
        L.append("Lo último que terminó (%s): %s." % (a["ultima_fecha"], "; ".join("«%s»" % u["titulo"] for u in a["ultimas"][:3])))
    L += ["", "Todavía **no sé cómo asignarle una tarea desde aquí** — solo veo lo que ya hizo. Ya dejé registrado que necesito esa capacidad. Mientras tanto, puedo revisar el correo, las juntas y Dropbox yo misma: dime qué necesitas saber."]
    return "\n".join(L)


# ------------------------------------------------------------------ agentes de Kawiil Central (ver qué hay; ejecutarlos aún NO es posible desde Donna)
def kc_agentes(core, nombre: str = "") -> str:
    conn, err = core._kawiil_central_pg()
    if err:
        return err
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name ILIKE '%agent%' ORDER BY 1")
        tablas = [r[0] for r in cur.fetchall()]
        out = ["Tablas de agentes en Kawiil Central: " + (", ".join(tablas) or "(ninguna)")]
        q = (nombre or "").strip().lower()
        for t in tablas[:6]:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (t,))
            cols = cur.fetchall()
            texto = [c for c, d in cols if d in ("text", "character varying")][:6]
            if not texto:
                continue
            where = " OR ".join("%s::text ILIKE %%s" % ('"%s"' % c) for c in texto) if q else "TRUE"
            cur.execute('SELECT * FROM public."%s" WHERE %s ORDER BY 1 DESC LIMIT 5' % (t, where), [("%" + q + "%")] * len(texto) if q else [])
            filas = cur.fetchall()
            if filas:
                nom = [d[0] for d in cur.description]
                out.append("\n[%s] %d fila(s) (últimas):" % (t, len(filas)))
                for f in filas:
                    out.append("  • " + "; ".join("%s=%s" % (n, (str(v) if v is not None else "")[:90]) for n, v in zip(nom, f) if v not in (None, ""))[:420])
        conn.close()
        out.append("\nNOTA: con esto Donna puede VER a los agentes y lo que han guardado, pero todavía NO tiene cómo EJECUTARLOS (no sé cómo se disparan desde Kawiil Central). Si Polo pide que uno haga algo, dilo claro y regístralo con pedir_mejora.")
        return "\n".join(out)[:4500]
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return "❌ No pude consultar los agentes: %s" % str(e)[:160]
