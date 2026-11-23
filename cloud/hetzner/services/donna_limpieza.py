#!/usr/bin/env python3
"""donna_limpieza.py — «Limpieza guiada» de la AGENDA dentro de Donna (sin pasar por Claude/Cerebro).

Escanea los pendientes abiertos y los agrupa para decidir de a lotes:
  · duplicados  — el mismo pendiente capturado dos veces; se propone cerrar el VIEJO solo si el nuevo lo cubre completo
  · caducados   — la fecha que traen ya pasó hace 14+ días
  · decisiones  — opciones A/B/C sueltas («Opción A: …», «Polo confirma DECISIÓN…»)
  · viejos      — sin fecha y sin tocar hace 30+ días
Lo SENSIBLE (regulatorio, litigios, dinero, clientes) nunca se pre-selecciona ni se cierra en bloque: se muestra aparte, uno por uno.

Acciones: cerrar · seguimiento (lo convierte en un seguimiento de Donna con fecha) · conservar (no lo vuelve a mostrar por 30 días) · deshacer.
Se cierra por IDENTIDAD DE LÍNEA exacta (no por palabras clave) y todo queda en una bitácora con la línea original, para poder reabrir.
"""
import fcntl
import hashlib
import json
import re
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import donna_db

TZ = ZoneInfo("America/Mexico_City")
SCHEMA = """
CREATE TABLE IF NOT EXISTS limpieza_estado (id TEXT PRIMARY KEY, estado TEXT NOT NULL, hasta INTEGER NOT NULL, actualizado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS limpieza_bitacora (n INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, accion TEXT NOT NULL, id TEXT NOT NULL, linea TEXT NOT NULL, detalle TEXT NOT NULL DEFAULT '');
"""
DIAS_CADUCADO = 14
DIAS_VIEJO = 30
DIAS_CONSERVAR = 30
SENSIBLE = re.compile(r"(?i)\b(oficio|cnbv|uif|amparo|litig\w*|juzgado|exp\.|expediente|denuncia|demanda|sucesorio|reivindicatoria|regulator\w*|reporte[s]? R\d\d|\bR\d\d\b|banxico|condusef|\bsat\b|imss|pld|aviso|acuse|folio|clabe|pago|cuenta bancaria|factur\w*|contrato\w*|cliente|seguridad|contrase\w*)\b|\$\s?\d")
OPCION = re.compile(r"(?i)^(opci[oó]n\s+[a-c]\b|polo confirma\s+decisi[oó]n|si opci[oó]n\s+[a-c]\b|donna inicia paso)")
_ABIERTA = re.compile(r"^\s*-\s*\[\s*\]\s+")
_SELLO = re.compile(r"\[(?:auto|visto|capturado)\s+(\d{4}-\d{2}-\d{2})")


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


def _agenda_path(core) -> Path:
    return Path(core.SPACE) / "AGENDA.md"


def _limpio(linea: str) -> str:
    return re.sub(r"^\s*-\s*\[\s*\]\s*", "", linea).replace("**", "").strip().strip("*").strip()


def _id(linea: str) -> str:
    return hashlib.sha1(_limpio(linea).encode("utf-8")).hexdigest()[:10]


def _titulo(linea: str) -> str:
    t = re.sub(r"\[[^\]]*\]", " ", _limpio(linea))
    t = re.sub(r"\(de la junta[^)]*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ·—-")


def _tokens(linea: str) -> set:
    return {w for w in re.findall(r"\w+", _titulo(linea).lower()) if len(w) > 3}


def _sello(linea: str):
    m = _SELLO.search(linea)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _ocultos() -> set:
    c = _c()
    try:
        return {r["id"] for r in c.execute("SELECT id FROM limpieza_estado WHERE estado='conservar' AND hasta>?", (int(time.time()),))}
    finally:
        c.close()


def _item(linea: str, extra: dict = None) -> dict:
    d = {"id": _id(linea), "texto": _titulo(linea)[:200], "sensible": bool(SENSIBLE.search(_titulo(linea)))}
    s = _sello(linea)
    if s:
        d["sello"] = s.isoformat()
    d.update(extra or {})
    return d


def escanear(core, hoy: date = None) -> dict:
    hoy = hoy or datetime.now(TZ).date()
    path = _agenda_path(core)
    lineas = [l for l in path.read_text().splitlines() if _ABIERTA.match(l)] if path.exists() else []
    ocultos = _ocultos()
    lineas = [l for l in lineas if _id(l) not in ocultos]
    por_id = {_id(l): l for l in lineas}
    usados = set()
    G = {"duplicados": [], "caducados": [], "decisiones": [], "viejos": []}

    # decisiones sueltas
    for l in lineas:
        if OPCION.match(_titulo(l)):
            G["decisiones"].append(_item(l, {"nota": "Opción o decisión suelta: ciérrala cuando elijas, o conviértela en seguimiento."}))
            usados.add(_id(l))

    # duplicados: se cierra el más viejo SOLO si el más nuevo lo cubre (≥80 % de sus palabras)
    toks = {i: _tokens(l) for i, l in por_id.items() if i not in usados}
    ids = list(toks)
    for a in ids:
        for b in ids:
            if a >= b or a in usados or b in usados or not toks[a] or not toks[b]:
                continue
            if len(toks[a] & toks[b]) / min(len(toks[a]), len(toks[b])) < 0.7:             # uno contiene al otro (casi)
                continue
            sa, sb = _sello(por_id[a]) or date.min, _sello(por_id[b]) or date.min
            viejo, nuevo = (a, b) if sa <= sb else (b, a)
            if sa == sb:
                continue
            cubre = len(toks[viejo] & toks[nuevo]) / len(toks[viejo])
            it = _item(por_id[viejo], {"conserva": _titulo(por_id[nuevo])[:120], "cubre": round(cubre, 2)})
            if cubre >= 0.8:
                it["nota"] = "El pendiente más nuevo ya lo cubre."
            else:
                it["sensible"] = True                                              # el viejo trae información que el nuevo no: se revisa a mano
                it["nota"] = "El viejo trae información que el nuevo no tiene: revísalo antes de cerrar."
            G["duplicados"].append(it)
            usados.add(viejo)

    # caducados: la fecha que traen pasó hace DIAS_CADUCADO+ días (misma lectura de fechas que «Hoy»)
    try:
        venc = core.agenda_por_fechas("\n".join(lineas), hoy, 400).get("vencidas", [])
    except Exception:
        venc = []
    clean_a_id = {_limpio(l): i for i, l in por_id.items()}
    for v in venc:
        i = clean_a_id.get(v["texto"])
        if not i or i in usados or not v.get("fecha"):
            continue
        dias = (hoy - date.fromisoformat(v["fecha"])).days
        if dias >= DIAS_CADUCADO:
            G["caducados"].append(_item(por_id[i], {"vencio": v["fecha"], "dias": dias}))
            usados.add(i)

    # viejos sin fecha ni toque
    for i, l in por_id.items():
        if i in usados:
            continue
        s = _sello(l)
        if s and (hoy - s).days >= DIAS_VIEJO:
            G["viejos"].append(_item(l, {"dias": (hoy - s).days}))
    for k in G:
        G[k].sort(key=lambda x: (x["sensible"], -(x.get("dias") or 0)))
    return {"fecha": hoy.isoformat(), "grupos": G, "total": sum(len(v) for v in G.values()), "abiertos": len(lineas)}


# ---------------------------------------------------------------- acciones
def _bitacora(c, accion, i, linea, detalle=""):
    c.execute("INSERT INTO limpieza_bitacora(ts,accion,id,linea,detalle) VALUES(?,?,?,?,?)", (int(time.time()), accion, i, linea, detalle))


def _reescribir(core, mapa) -> list:
    """Aplica {id: función(línea)→línea nueva} sobre líneas ABIERTAS cuyo id coincide EXACTO. Devuelve los ids tocados."""
    path = _agenda_path(core)
    if not path.exists():
        return []
    tocados = []
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        lineas = f.read().split("\n")
        for n, l in enumerate(lineas):
            if _ABIERTA.match(l) and _id(l) in mapa and _id(l) not in tocados:
                lineas[n] = mapa[_id(l)](l)
                tocados.append(_id(l))
        f.seek(0)
        f.write("\n".join(lineas))
        f.truncate()
    return tocados


def cerrar(core, ids: list, motivo: str = "limpieza") -> dict:
    ids = [i for i in dict.fromkeys(ids or []) if re.fullmatch(r"[0-9a-f]{10}", str(i))]
    previas = {}
    path = _agenda_path(core)
    for l in (path.read_text().splitlines() if path.exists() else []):
        if _ABIERTA.match(l) and _id(l) in ids:
            previas[_id(l)] = l
    hechos = _reescribir(core, {i: (lambda l: re.sub(r"\[\s*\]", "[x]", l, count=1)) for i in ids})
    c = _c()
    try:
        for i in hechos:
            _bitacora(c, "cerrar", i, previas.get(i, ""), motivo)
        c.commit()
    finally:
        c.close()
    return {"cerrados": len(hechos), "ids": hechos}


def conservar(ids: list, dias: int = DIAS_CONSERVAR) -> dict:
    c = _c()
    n = 0
    try:
        for i in dict.fromkeys(ids or []):
            if re.fullmatch(r"[0-9a-f]{10}", str(i)):
                now = int(time.time())
                c.execute("INSERT INTO limpieza_estado(id,estado,hasta,actualizado) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET estado=excluded.estado, hasta=excluded.hasta, actualizado=excluded.actualizado", (i, "conservar", now + dias * 86400, now))
                n += 1
        c.commit()
    finally:
        c.close()
    return {"conservados": n}


def a_seguimiento(core, ids: list, cuando: str = "", en_horas: float = 0, usuario: str = "admin") -> dict:
    """Convierte pendientes en SEGUIMIENTOS de Donna (ella los revisa y te avisa) y los saca de la lista de limpieza."""
    import donna_seg
    ids = [i for i in dict.fromkeys(ids or []) if re.fullmatch(r"[0-9a-f]{10}", str(i))]
    path = _agenda_path(core)
    lineas = {_id(l): l for l in (path.read_text().splitlines() if path.exists() else []) if _ABIERTA.match(l)}
    creados = []
    for i in ids:
        if i in lineas:
            s = donna_seg.crear(_titulo(lineas[i])[:200], cuando, en_horas or (0 if cuando else 24), "Viene de la limpieza de pendientes.", "limpieza", i, usuario=usuario)
            creados.append(s.get("id") if isinstance(s, dict) else i)
    cerrados = cerrar(core, list(lineas.keys() & set(ids)), "pasó a seguimiento")["cerrados"]
    return {"seguimientos": len(creados), "cerrados": cerrados}


def deshacer(core, ids: list) -> dict:
    """Reabre lo cerrado por la limpieza (usa la bitácora)."""
    ids = [i for i in dict.fromkeys(ids or []) if re.fullmatch(r"[0-9a-f]{10}", str(i))]
    c = _c()
    try:
        filas = {r["id"]: r["linea"] for r in c.execute("SELECT id, linea FROM limpieza_bitacora WHERE accion='cerrar' ORDER BY n") if r["id"] in ids}
        path = _agenda_path(core)
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            texto = f.read()
            n = 0
            for i, original in filas.items():
                cerrada = re.sub(r"\[\s*\]", "[x]", original, count=1)
                if original and cerrada in texto and original not in texto:
                    texto = texto.replace(cerrada, original, 1)
                    n += 1
                    _bitacora(c, "deshacer", i, original)
            f.seek(0)
            f.write(texto)
            f.truncate()
        c.commit()
    finally:
        c.close()
    return {"reabiertos": n}


def resumen_texto(e: dict) -> str:
    g = e["grupos"]
    return "%d por revisar: %d duplicados, %d caducados, %d decisiones sueltas, %d viejos sin fecha." % (e["total"], len(g["duplicados"]), len(g["caducados"]), len(g["decisiones"]), len(g["viejos"]))
