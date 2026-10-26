"""
cerebro_calidad.py — reglas de confiabilidad del Cerebro Kawiil (funciones puras, sin I/O).

Las usa cerebro_kawiil_mcp.py. Viven aparte para poder probarlas sin levantar el servidor.
Criterio general: ante dato viejo vs nuevo prevalece el nuevo con su fecha; lo que no se
pueda confirmar se marca «por validar» y no se presenta como cierto.
"""

import re
from datetime import date, timedelta

DIAS_SIN_REVISAR = 7          # un pendiente con sello más viejo que esto se marca «SIN REVISAR»
DIAS_ENTREGABLE_INACTIVO = 30

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_MES_RE = "ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic"
_FECHA_TXT = re.compile(
    rf"\b(\d{{1,2}})\s*(?:de\s+)?[-/ ]?\s*({_MES_RE})[a-záéíóú]*\.?"
    rf"(?:\s*(?:de\s+)?[-/ ]?\s*(20\d{{2}}))?", re.I)
_FECHA_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

ITEM_RE = re.compile(r"^(\s*)-\s*\[( |x|X)\]\s+(.*)$")
SELLO_RE = re.compile(r"\[(auto|capturado|visto)\s+(\d{4}-\d{2}-\d{2})\]")
RESP_RE = re.compile(r"\[resp(?:onsable)?:\s*([^\]]+)\]", re.I)
HOY_RE = re.compile(rf"\bHOY\b(?:\s*[:·-]?\s*(\d{{1,2}})\s*[-/ ]?\s*({_MES_RE})[a-záéíóú]*\.?)?")

AUTORIDAD_RE = re.compile(r"\b(cnbv|banxico|uif|sat|condusef|siti|shcp)\b", re.I)
CODIGO_REPORTE_RE = re.compile(r"\bR\d{2}\b")   # R03, R13, R24… códigos de reportes regulatorios
REPORTE_RE = re.compile(r"\b(reportes?|R\d{2}|formulario|aviso|declaraci[oó]n|informe\s+mensual|oficio)\b", re.I)
# Soporte mínimo de que algo se presentó: la palabra acuse/folio seguida de un identificador con dígitos.
ACUSE_RE = re.compile(
    r"(?i)\b(acuse|folio|constancia\s+de\s+(?:recepci[oó]n|presentaci[oó]n)|no\.?\s*de\s+operaci[oó]n)\b"
    r"[^\n.;]{0,40}?[:#]?\s*([A-Z]{0,6}[-/]?\d[\w/-]{3,})")


def _fecha(d, m, y, hoy):
    try:
        return date(int(y) if y else hoy.year, int(m), int(d))
    except ValueError:
        return None


def parse_fechas(texto: str, hoy: date) -> list:
    """Todas las fechas que cita un texto (dd-mmm[-aaaa] o ISO). Sin año → año de `hoy`."""
    out = []
    for m in _FECHA_TXT.finditer(texto or ""):
        f = _fecha(m.group(1), MESES[m.group(2).lower()], m.group(3), hoy)
        if f:
            out.append(f)
    for m in _FECHA_ISO.finditer(texto or ""):
        f = _fecha(m.group(3), m.group(2), m.group(1), hoy)
        if f:
            out.append(f)
    return out


# ═══ 1-2. AGENDA ═══════════════════════════════════════════════════════════

def parse_agenda(texto: str, hoy: date) -> list:
    """Pendientes abiertos (- [ ]) con sello, antigüedad, responsable, fuente y banderas."""
    items = []
    for ln, linea in enumerate((texto or "").splitlines()):
        m = ITEM_RE.match(linea)
        if not m or m.group(2) != " ":
            continue
        cuerpo = m.group(3)
        sellos = SELLO_RE.findall(cuerpo)
        tipo, f_sello = (sellos[-1][0], date.fromisoformat(sellos[-1][1])) if sellos else (None, None)
        limpio = SELLO_RE.sub("", cuerpo).rstrip(" ·\t")
        resp = RESP_RE.search(cuerpo)
        edad = (hoy - f_sello).days if f_sello else None

        flags = []
        if edad is None:
            flags.append("SIN REVISAR (sin sello de fecha)")
        elif edad > DIAS_SIN_REVISAR:
            flags.append(f"SIN REVISAR (sello de hace {edad} d)")

        hm = HOY_RE.search(limpio)
        if hm:
            f_hoy = _fecha(hm.group(1), MESES[hm.group(2).lower()], None, hoy) if hm.group(1) else f_sello
            if f_hoy and f_hoy < hoy:
                flags.append(f"VENCIDO: decía HOY {f_hoy.isoformat()}")

        es_reg = (AUTORIDAD_RE.search(limpio) and REPORTE_RE.search(limpio)) or CODIGO_REPORTE_RE.search(limpio)
        if es_reg and not ACUSE_RE.search(limpio):
            flags.append("sin evidencia de acuse/folio")

        fuente = {"auto": "auto-memoria (conversación)", "capturado": "captura manual",
                  "visto": "sin fuente; sello = primera revisión, no actualización"}.get(tipo, "sin fuente")
        items.append({
            "n": len(items) + 1, "linea": ln, "texto": limpio, "sello_tipo": tipo,
            "fecha_actualizacion": f_sello.isoformat() if f_sello else None,
            "fecha_es_real": tipo in ("auto", "capturado"), "edad_dias": edad,
            "responsable": resp.group(1).strip() if resp else "por validar",
            "fuente": fuente, "flags": flags, "texto_original": linea,
        })
    return items


def render_item(it: dict, texto_max: int = 0) -> str:
    texto = it["texto"]
    if texto_max and len(texto) > texto_max:
        texto = texto[:texto_max].rstrip() + "…"
    # un ítem vencido no puede seguir diciendo «HOY»
    texto = HOY_RE.sub(lambda m: f"(era {m.group(0)})", texto, count=1) \
        if any(f.startswith("VENCIDO") for f in it["flags"]) else texto
    fecha = it["fecha_actualizacion"] or "sin fecha"
    if it["fecha_actualizacion"] and not it["fecha_es_real"]:
        fecha += " (primer visto)"
    marca = "⚠ " + " | ".join(it["flags"]) + "\n    " if it["flags"] else ""
    return (f"{it['n']:>2}. {marca}{texto}\n    ↳ actualizado: {fecha} · responsable: {it['responsable']}"
            f" · fuente: {it['fuente']}")


def render_agenda(items: list, hoy: date, offset: int = 0, limite: int = 0, texto_max: int = 0) -> str:
    total = len(items)
    if not total:
        return "Sin pendientes abiertos en AGENDA."
    sel = items[offset:offset + limite] if limite else items[offset:]
    sin_rev = sum(1 for i in items if any(f.startswith("SIN REVISAR") for f in i["flags"]))
    venc = sum(1 for i in items if any(f.startswith("VENCIDO") for f in i["flags"]))
    sin_ev = sum(1 for i in items if "sin evidencia de acuse/folio" in i["flags"])
    cab = (f"Pendientes abiertos: {total} (mostrando {sel[0]['n'] if sel else 0}–{sel[-1]['n'] if sel else 0}) · "
           f"hoy {hoy.isoformat()} · sin revisar >{DIAS_SIN_REVISAR} d: {sin_rev} · "
           f"«HOY» vencidos: {venc} · regulatorios sin acuse/folio: {sin_ev}")
    pie = ""
    if offset + len(sel) < total:
        pie = f"\n…faltan {total - offset - len(sel)}; pide offset={offset + len(sel)}."
    return cab + "\n" + "\n".join(render_item(i, texto_max) for i in sel) + pie


def normalizar_agenda(texto: str, hoy: date) -> tuple:
    """Reescribe SOLO la forma (marca «SIN REVISAR»/«VENCIDO»); no toca el fondo. → (texto, n_cambios)."""
    lineas = (texto or "").split("\n")
    cambios = 0
    for it in parse_agenda(texto, hoy):
        l, nueva = it["linea"], lineas[it["linea"]]
        for f in it["flags"]:
            if f.startswith("VENCIDO") and "(era HOY" not in nueva:
                nueva = HOY_RE.sub(lambda m: f"(era {m.group(0)})", nueva, count=1)
            if f.startswith("SIN REVISAR") and "[SIN REVISAR" not in nueva:
                nueva = nueva.rstrip() + f"  · [SIN REVISAR al {hoy.isoformat()}]"
        if nueva != lineas[l]:
            lineas[l] = nueva
            cambios += 1
    return "\n".join(lineas), cambios


# ═══ 3. MEMORIA: vigente vs histórico ══════════════════════════════════════

def _bloques_bullets(cuerpo: str) -> list:
    """Agrupa un bloque de sección en bullets (con sus líneas de continuación)."""
    bloques, actual = [], None
    for l in cuerpo.split("\n"):
        if re.match(r"^\s*[-*]\s+", l) and not l.startswith("  "):
            actual = [l]
            bloques.append(actual)
        elif actual is not None and l.strip():
            actual.append(l)
        elif actual is not None:
            actual = None
    return bloques


def clasificar_critico(important: str, hoy: date) -> dict:
    """Bullets de la sección CRÍTICO → vigentes / históricos / reglas (sin fecha).
    Histórico = TODAS las fechas que cita ya pasaron. Lo que cita una fecha futura es vigente."""
    m = re.search(r"(?im)^##\s+CR[IÍ]TICO\b[^\n]*\n([\s\S]*?)(?=^##\s+(?!#)|\Z)", important or "")
    res = {"vigentes": [], "historicos": [], "reglas": []}
    if not m:
        return res
    for b in _bloques_bullets(m.group(1)):
        txt = "\n".join(b)
        fechas = parse_fechas(txt, hoy)
        if not fechas:
            res["reglas"].append(txt)
        elif max(fechas) >= hoy:
            res["vigentes"].append((max(fechas), txt))
        else:
            res["historicos"].append((max(fechas), txt))
    return res


def separar_vigente_historico(important: str, hoy: date) -> str:
    """Devuelve IMPORTANT con los bullets históricos de CRÍTICO movidos a una sección
    «HISTÓRICO (no vigente)» al final del bloque, rotulados con su fecha. No borra nada."""
    cl = clasificar_critico(important, hoy)
    if not cl["historicos"]:
        return important
    out = important
    hist_blk = ["", "## HISTÓRICO — plazos vencidos o superados (NO críticos)",
                f"> Movido de CRÍTICO el {hoy.isoformat()}: todas las fechas que cita ya pasaron. "
                "Se conserva como registro; no es un plazo vigente."]
    for f, txt in cl["historicos"]:
        out = out.replace(txt, "", 1)
        hist_blk.append(f"- [venció {f.isoformat()}] " + re.sub(r"^\s*[-*]\s+", "", txt))
    out = re.sub(r"\n{3,}", "\n\n", out)
    m = re.search(r"(?im)^##\s+CR[IÍ]TICO\b[^\n]*\n[\s\S]*?(?=^##\s+(?!#)|\Z)", out)
    pos = m.end() if m else len(out)
    return out[:pos].rstrip() + "\n" + "\n".join(hist_blk) + "\n\n" + out[pos:].lstrip("\n")


def ultima_fecha_journal(journal: str, hoy: date):
    fechas = []
    for m in re.finditer(r"(?m)^(?:##\s+|-\s*\[)(\d{4}-\d{2}-\d{2})", journal or ""):
        try:
            f = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if f <= hoy:
            fechas.append(f)
    return max(fechas) if fechas else None


# ═══ 5. CREDENCIALES ═══════════════════════════════════════════════════════

MARCA_CRED = "[CREDENCIAL RETIRADA]"
_CRED_KW = r"(?:contrase[ñn]a|password|passwd|pwd|clave\s+de\s+acceso|api[\s_-]?key|secret[oa]?|token)"
_CRED_RE = re.compile(rf"(?i)\b({_CRED_KW})\b([^\n]{{0,40}}?)([:=(])\s*['\"“]?([^\s'\")\]”,;]{{4,}})")
_TOKEN_RES = [
    ("token sk-", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("token GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("token Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.?[A-Za-z0-9_-]*")),
    ("URL con usuario:clave", re.compile(r"(?<=://)[^\s/:@]+:([^\s/@]{4,})(?=@)")),
]


def _parece_secreto(v: str) -> bool:
    if v.startswith("[CREDENCIAL"):
        return False
    return bool(re.search(r"\d", v) and re.search(r"[A-Za-z]", v)) or \
        (len(v) >= 8 and re.search(r"[a-z]", v) and re.search(r"[A-Z]", v))


def _mask(v: str) -> str:
    return f"{v[:2]}{'*' * max(3, len(v) - 2)} ({len(v)} caracteres)"


def redactar_credenciales(texto: str) -> tuple:
    """→ (texto_sin_credenciales, hallazgos[{linea, tipo, valor_enmascarado, contexto}])."""
    hallazgos = []
    lineas = (texto or "").split("\n")
    for i, l in enumerate(lineas):
        nueva = l
        for m in list(_CRED_RE.finditer(l)):
            v = m.group(4)
            if _parece_secreto(v):
                nueva = nueva.replace(v, MARCA_CRED, 1)
                hallazgos.append({"linea": i + 1, "tipo": f"contraseña/clave ({m.group(1).lower()})",
                                  "valor_enmascarado": _mask(v)})
        for tipo, rx in _TOKEN_RES:
            for m in list(rx.finditer(nueva)):
                v = m.group(1) if m.groups() else m.group(0)
                nueva = nueva.replace(v, MARCA_CRED, 1)
                hallazgos.append({"linea": i + 1, "tipo": tipo, "valor_enmascarado": _mask(v)})
        if nueva != l:
            lineas[i] = nueva
            hallazgos[-1]["contexto"] = nueva.strip()[:140]
            for h in hallazgos:
                h.setdefault("contexto", nueva.strip()[:140])
    return "\n".join(lineas), hallazgos


# ═══ 4. ENTREGABLES: estado real ═══════════════════════════════════════════

_STOP = {"para", "como", "con", "del", "los", "las", "una", "uno", "por", "que", "versión", "version",
         "final", "borrador", "nota", "correo", "informe", "oficio", "respuesta", "anexo"}


def _toks(s: str) -> set:
    return {t for t in re.findall(r"[a-záéíóúñ0-9]{4,}", (s or "").lower()) if t not in _STOP}


def _claves(texto: str) -> set:
    t = texto or ""
    c = set(re.findall(r"\b\d{6,}(?:/\d{4})?\b", t))
    c |= set(re.findall(r"\b(?:[A-Z]{2,6}-){1,3}\d{2,4}(?:-\d{4})?\b", t))
    c |= set(re.findall(r"\b\d{1,5}/\d{4}\b", t))
    return c


def _oc(texto: str) -> set:
    return set(re.findall(r"OC-\d{3}/\d{4}", texto or ""))


def auditar_entregables(ents: list, hoy: date) -> list:
    """ents: [{archivo, titulo, cliente, estado, fecha_actualizacion, cuerpo}] (todos los estados).
    Devuelve los de estado abierto (borrador/listo/en_vobo) con su estado real SUGERIDO.
    Nunca declara «enviado» sin acuse/folio; lo no confirmable queda «por validar»."""
    def f_act(e):
        try:
            return date.fromisoformat((e.get("fecha_actualizacion") or "")[:10])
        except ValueError:
            return None

    out = []
    for e in ents:
        if e.get("estado") not in ("borrador", "listo", "en_vobo"):
            continue
        cuerpo, tit = e.get("cuerpo", ""), e.get("titulo", "")
        fa = f_act(e)
        hallazgos, real = [], "vigente (sin hallazgos)"

        # a) superado por una versión posterior
        for o in ents:
            if o is e or not f_act(o) or not fa or f_act(o) <= fa:
                continue
            if _oc(tit) and _oc(o.get("titulo", "")) and _oc(tit) != _oc(o.get("titulo", "")):
                continue  # OC-005 (acuse) y OC-006 (respuesta) son documentos distintos, no versiones
            comp = _toks(tit) & _toks(o.get("titulo", ""))
            union = _toks(tit) | _toks(o.get("titulo", ""))
            sim = len(comp) / len(union) if union else 0
            misma_clave = _claves(tit + " " + cuerpo[:600]) & _claves(o.get("titulo", ""))
            if sim >= 0.55 or (misma_clave and sim >= 0.3):
                final = bool(re.search(r"(?i)\bfinal\b|v\d", o.get("titulo", "")))
                hallazgos.append(f"posible superado por «{o.get('titulo','')[:70]}» ({o.get('estado')}, {o.get('fecha_actualizacion')})")
                real = "superado por versión posterior" + (" (versión FINAL)" if final and o.get("estado") in ("archivado", "aprobado", "listo") else " (por validar)")
                break

        # b) evidencia de envío: solo cuenta acuse/folio; «enviado» a secas queda por validar
        hist = cuerpo.split("## Historial")[-1] if "## Historial" in cuerpo else ""
        ac = ACUSE_RE.search(cuerpo)
        if ac and re.search(r"(?i)acuse|folio", ac.group(1)) and "pendiente" not in cuerpo[max(0, ac.start() - 40):ac.start()].lower():
            hallazgos.append(f"menciona acuse/folio «{ac.group(2)[:30]}» — confirmar que es del envío")
        elif re.search(r"(?i)\b(enviad[oa]|presentad[oa]|remitid[oa])\b", hist):
            hallazgos.append("el historial dice enviado/presentado pero sin acuse ni folio")
            real = "enviado según historial, sin acuse/folio (por validar)"

        # c) plazo vencido sin evidencia de envío
        if real.startswith("vigente"):
            venc = []
            for l in cuerpo.splitlines():
                if re.search(r"(?i)\b(vence|vencimiento|a m[aá]s tardar|fecha l[ií]mite|plazo)\b", l):
                    venc += [d for d in parse_fechas(l, fa or hoy) if d.year >= (fa or hoy).year]
            if venc and max(venc) < hoy:
                hallazgos.append(f"plazo citado {max(venc).isoformat()} ya venció; sin acuse/folio de envío")
                real = "plazo vencido, sin evidencia de envío (por validar)"

        # d) inactividad
        if fa and (hoy - fa).days > DIAS_ENTREGABLE_INACTIVO and real.startswith("vigente"):
            real = f"inactivo {(hoy - fa).days} d (por validar)"
        out.append({"archivo": e.get("archivo"), "titulo": tit, "cliente": e.get("cliente"),
                    "estado_registrado": e.get("estado"), "estado_real_sugerido": real,
                    "dias_sin_actualizar": (hoy - fa).days if fa else None, "hallazgos": hallazgos})
    return out


def archivado_por_inactividad(cuerpo: str) -> bool:
    return bool(re.search(r"(?i)auto-?archivad", cuerpo or ""))


# ═══ 6. KAWIIL CENTRAL: criterio de identidad pendiente ↔ tarea ════════════
#
# Un pendiente del Cerebro y una tarea del tablero son EL MISMO ASUNTO si se cumple, en orden:
#  1) Vínculo explícito: el pendiente lleva [KC:<id>] o la descripción de la tarea lleva [CER:<huella>].
#  2) Identificador duro compartido (expediente, oficio, folio, código de reporte R##/folio, OC-###,
#     monto) Y mismo cliente/asunto → «coincidencia fuerte».
#  3) Sin identificador común: mismo cliente + similitud de título ≥ 0.6 → «candidata por validar»
#     (nunca se vincula sola).
#  4) Si nada aplica → «sin tarea en el tablero».
# Un pendiente tiene a lo más UNA tarea ligada; varias candidatas se reportan como ambiguas.

_ID_PATRONES = [
    r"\b\d{1,5}/\d{4}\b",                                  # exp. 37/2016, D.A. 17/2026
    r"\b\d{3}[-/][\w-]*\d{4,}[\w/-]*\b",                   # oficios 221/DGPORPIA-14558785/2026
    r"\bOC-\d{3}/\d{4}\b",
    r"\b[A-Z]{1,2}-?\d{3,5}\b",                            # B-13211, J-0314
    r"\bR\d{2}\b",                                         # R13, R24
    r"\$\s?\d[\d,]*(?:\.\d+)?",                            # montos
    r"\b\d{5,}\b",
]


def ids_duros(texto: str) -> set:
    t = texto or ""
    ids = set()
    for p in _ID_PATRONES:
        ids |= {re.sub(r"[\s,$]", "", x).upper() for x in re.findall(p, t)}
    return {i for i in ids if i not in {"2026", "2025", "2024"} and len(i) >= 3}


def _cliente(texto: str) -> str:
    m = re.match(r"\s*(?:HOY\s+[\w-]+\s*·\s*)?([^·—:\-(]{3,40}?)\s*(?:·|—|:)", texto or "")
    return (m.group(1).strip().lower() if m else "")


def emparejar(pendientes: list, tareas: list) -> list:
    """pendientes: [{n, texto}], tareas: [{id, titulo, descripcion?, estatus?}].
    → [{n, texto, tarea_id, tarea_titulo, nivel, motivo}] con nivel ∈
    {vinculada, coincidencia fuerte, candidata por validar, ambigua, sin tarea en el tablero}."""
    res = []
    for p in pendientes:
        texto = p["texto"]
        kc = re.search(r"\[KC:([\w-]+)\]", texto)
        ids_p, cli_p, tok_p = ids_duros(texto), _cliente(texto), _toks(texto)
        cand = []
        for t in tareas:
            blob = f"{t.get('titulo','')} {t.get('descripcion','') or ''}"
            if kc and str(t.get("id")) == kc.group(1):
                cand = [(3, t, "vínculo explícito [KC]")]
                break
            if re.search(rf"\[CER:{re.escape(str(p['n']))}\]", blob):
                cand.append((3, t, "vínculo explícito [CER]"))
                continue
            comunes = ids_p & ids_duros(blob)
            sim = len(tok_p & _toks(blob)) / max(1, len(tok_p | _toks(blob)))
            cli_ok = bool(cli_p) and cli_p in blob.lower()
            if comunes and (cli_ok or sim >= 0.15):
                cand.append((2, t, "identificador común: " + ", ".join(sorted(comunes)[:3])))
            elif cli_ok and sim >= 0.6:
                cand.append((1, t, f"mismo cliente y título similar ({sim:.0%})"))
        if not cand:
            res.append({"n": p["n"], "texto": texto[:90], "nivel": "sin tarea en el tablero"})
            continue
        top = max(c[0] for c in cand)
        mejores = [c for c in cand if c[0] == top]
        nivel = {3: "vinculada", 2: "coincidencia fuerte", 1: "candidata por validar"}[top]
        if len(mejores) > 1:
            nivel = "ambigua"
        t0 = mejores[0][1]
        res.append({"n": p["n"], "texto": texto[:90], "tarea_id": t0.get("id"), "tarea_titulo": t0.get("titulo"),
                    "nivel": nivel, "motivo": mejores[0][2] + (f" (+{len(mejores)-1} más)" if len(mejores) > 1 else "")})
    return res


# ═══ 7. OBLIGACIONES REGULATORIAS ══════════════════════════════════════════

def evaluar_obligacion(clave: str, fuentes: dict) -> dict:
    """clave: texto a buscar (p. ej. 'R13 B-13211'). fuentes: {nombre: texto}.
    «presentado» SOLO si cerca de una mención hay acuse/folio con identificador. Si no: «sin evidencia».
    Nunca devuelve «pendiente» (también requeriría soporte)."""
    tokens = [t for t in re.split(r"[\s,]+", clave.strip()) if len(t) >= 2]
    menciones, soportes = [], []
    for nombre, texto in fuentes.items():
        lineas = (texto or "").split("\n")
        for i, l in enumerate(lineas):
            if tokens and all(t.lower() in l.lower() for t in tokens[:2]):
                ventana = " ".join(lineas[max(0, i - 1):i + 2])
                ac = ACUSE_RE.search(ventana)
                if ac:
                    soportes.append({"fuente": nombre, "linea": i + 1, "acuse_o_folio": ac.group(2)[:40],
                                     "texto": l.strip()[:160]})
                else:
                    menciones.append({"fuente": nombre, "linea": i + 1, "texto": l.strip()[:160]})
    if soportes:
        return {"estado": "presentado (con soporte)", "soportes": soportes, "menciones_sin_soporte": menciones}
    return {"estado": "sin evidencia", "soportes": [], "menciones_sin_soporte": menciones}
