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


def _juntas_sobre(ks: list, usuario="admin") -> list:
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


def _notas_memoria(core, ks: list) -> list:
    """Líneas de la memoria viva (Cowork, JOURNAL, PROJECTS…) que hablan del tema. Son NOTAS/RESÚMENES: sirven de pista cuando no hay transcripción, y se rotulan así."""
    out = []
    archivos = list(getattr(core, "MEMORY_FILES", [])) + ["COWORK.md"]
    for f in dict.fromkeys(archivos):
        if f in ("AGENDA.md", "USER.md"):
            continue
        try:
            txt = (core.SPACE / f).read_text()
        except Exception:
            continue
        for l in txt.splitlines():
            n = donna_personas.norm(l)
            if len([k for k in ks if k in n]) >= min(2, len(ks)) and len(l.strip()) > 20:
                out.append({"archivo": f, "linea": l.strip()[:260]})
                if len(out) >= 8:
                    return out
    return out


def _correo_reciente(ks: list, m=None) -> str:
    import donna_m365
    try:
        return donna_m365.buscar_correo(" ".join(ks[:3]), m, 5)
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


def _correos_enviados(ks: list, m=None) -> list:
    import donna_m365
    vistos, out = set(), []
    for q in (" ".join(ks[:3]), ks[0] if ks else ""):
        for e in donna_m365.correo_enviado_buscar(q, m):
            clave = (e["fecha"], e["asunto"])
            if clave not in vistos:
                vistos.add(clave)
                out.append(e)
    return sorted(out, key=lambda x: x["fecha"], reverse=True)


def veredicto_envio(ultima_junta: str, ultimo_envio: str, ultimo_cambio: str) -> tuple:
    """(código, texto). Compara FECHAS: ¿se envió después de lo último que se habló y de la última actualización?"""
    if not ultimo_envio:
        return "sin_evidencia", "SIN EVIDENCIA DE ENVÍO: no encontré en tu correo enviado nada con este documento."
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
    juntas = _juntas_sobre(ks, usuario)
    correos = _correos_enviados(ks, m365)
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
    notas = _notas_memoria(core, ks)
    reciente = _correo_reciente(ks, m365)
    r = {"notas": notas, "correo_reciente": reciente, "donde": _donde_busque(), "claves": ks, "entregable": ent, "juntas": juntas, "correos": rel, "correos_sin_filtrar": correos, "dropbox": str(dbx)[:1500], "agenda": ag, "veredicto": cod, "veredicto_txt": txt,
         "ultima_junta": ultima_junta, "ultimo_envio": ultimo_envio, "prioritario": None}
    if marcar_prioritario and cod != "enviado":
        r["prioritario"] = _marcar_prioritario(core, titulo, txt, juntas)
    r["texto"] = _texto(r)
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
    base = "- [ ] **URGENTE** Revisar «%s» conforme a lo último dicho en las juntas%s · [capturado %s]" % (titulo[:90], (" (junta del %s)" % juntas[0]["fecha"]) if juntas else "", hoy)
    p.write_text(txt.rstrip("\n") + "\n" + base + "\n")
    try:
        import donna_seg
        donna_seg.crear("Revisar «%s» (prioritario)" % titulo[:80], "", 0, motivo, origen="tarea", cadencia_horas=24, prioridad=1)
    except Exception:
        pass
    return "Lo dejé como URGENTE en la agenda y en seguimiento diario."


def _texto(r: dict) -> str:
    L = ["REVISIÓN CONTRA FUENTES (los datos de abajo son la evidencia; no afirmes nada que no esté aquí)"]
    e = r["entregable"]
    L.append("\n1) ENTREGABLE EN CEREBRO: " + ("«%s» · estado: %s · VoBo: %s · última actualización: %s%s" % (e["titulo"], e["estado"], e["vobo"] or "(sin dato)", e["actualizado"] or "(sin fecha)", (" · cliente: " + e["cliente"]) if e["cliente"] else "")
                                                if e else "no lo encontré por ese nombre en Cerebro."))
    L.append("\n2) JUNTAS GUARDADAS QUE HABLAN DE ESTO (transcripción literal, no resumen): %d  [busqué entre %s]" % (len(r["juntas"]), r["donde"]))
    if not r["juntas"]:
        L.append("   ⚠️ NO HAY TRANSCRIPCIONES GUARDADAS sobre este tema. Si la junta se grabó fuera de Escuchar (Teams, Zoom, Cowork), Donna todavía no puede leerla: dile a Polo dónde está y regístralo con pedir_mejora.")
    if r["notas"]:
        L.append("   Pistas en tus NOTAS/MEMORIA (son resúmenes, NO la transcripción; úsalas solo como pista y dilo así):")
        L += ["   · [%s] %s" % (n["archivo"], n["linea"]) for n in r["notas"]]
    for j in r["juntas"][:4]:
        L.append("   • %s — «%s» (id %s)" % (j["fecha"], j["titulo"], j["sesion_id"]))
        for h in j["frases"][:6]:
            L.append("       [%s] %s: «%s»" % (h["hora"], h["persona"] or "voz sin nombre", h["texto"][:260]))
    L.append("\n3) CORREO ENVIADO RELACIONADO: %d" % len(r["correos"]))
    for c in r["correos"][:5]:
        L.append("   • %s → %s · «%s»%s" % (c["fecha"], c["para"] or "?", c["asunto"], (" · adjuntos: " + ", ".join(c["adjuntos"])) if c["adjuntos"] else ""))
    errores = [c for c in r["correos_sin_filtrar"] if c.get("error")]
    if errores:
        L.append("   ⚠️ " + errores[0]["asunto"])
    L.append("   Correo reciente (recibido o enviado) que menciona el tema:\n" + "\n".join("   " + x for x in (r["correo_reciente"] or "(nada)").splitlines()[:6]))
    L.append("\n4) DROPBOX (versiones/archivos encontrados):\n" + (r["dropbox"] or "(nada)"))
    if r["agenda"]:
        L.append("\n5) YA EN TU AGENDA:\n" + "\n".join("   • " + a for a in r["agenda"]))
    L.append("\nVEREDICTO DE ENVÍO (calculado comparando fechas): " + r["veredicto_txt"])
    if r["prioritario"]:
        L.append("ACCIÓN: " + r["prioritario"])
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
