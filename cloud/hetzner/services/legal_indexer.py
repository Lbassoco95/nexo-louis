#!/usr/bin/env python3
"""
legal_indexer.py — indexación incremental de DOF y SJF para los agentes kawiil-*.

Reemplaza la selección del indexador original de louis_core.py, que tenía estos defectos
(medidos con datos reales del servidor el 14-oct-2026: ~1 % de cobertura por agente):

  1. Pedía SIEMPRE los 10 documentos más recientes por palabra clave; cuando ya estaban
     indexados volvía a pedir los mismos y concluía "al día": nunca bajaba en el tiempo.
     → Ahora cada flujo (fuente × nivel de relevancia) lleva un CURSOR de teclado (fecha, id)
       que solo avanza hacia atrás, y una VENTANA RECIENTE que se revisa siempre para captar
       publicaciones nuevas o tardías.
  2. Daba lo mismo a una tesis aislada que a una jurisprudencia, y lo mismo a una coincidencia
     en el título que a una mención perdida en el texto.
     → Flujos con prioridad: jurisprudencia/título primero, luego texto, luego tesis aisladas.
  3. La memoria de "ya indexados" guardaba 500 ids AL AZAR (list(set)[-500:]).
     → La verdad es la carpeta docs/ del agente: el id numérico al final del nombre del archivo.
  4. Sin presupuesto: cada documento es una llamada a DeepSeek.
     → Tope diario de documentos (archivo de estado) y avance visible en cursor.json.

Este módulo no importa louis_core: recibe por parámetro lo que necesita (conexiones, resumidor,
escritura de memoria), por lo que se puede probar solo.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ID_RE = re.compile(r"^(dof|sjf)-.*-(\d+)\.md$")
FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# (nombre, fuente, nivel, tipo, peso). El peso es cuántos turnos tiene el flujo en cada vuelta;
# el orden de la lista es la prioridad.
FLUJOS = (
    ("sjf_jur_rubro", "sjf", "titulo", "jur", 4),   # jurisprudencia con la palabra en el rubro
    ("dof_titulo",    "dof", "titulo", None,  4),   # nota del DOF con la palabra en el título
    ("sjf_jur_texto", "sjf", "texto",  "jur", 2),   # jurisprudencia que la menciona en el texto
    ("dof_texto",     "dof", "texto",  None,  2),
    ("sjf_ais_rubro", "sjf", "titulo", "ais", 1),   # tesis aisladas
    ("sjf_ais_texto", "sjf", "texto",  "ais", 1),
)
TOTAL_TTL_S = 6 * 3600          # cada cuánto se recalcula el universo (consultas de conteo)
VENTANA_DIAS = 90               # ventana reciente que se revisa en cada pasada
LOTE = 40


# ── utilidades ───────────────────────────────────────────────────────────────
def ids_indexados(docs_dir: Path) -> set[str]:
    """Verdad de lo indexado: el id numérico al final del nombre de cada .md de docs/."""
    out = set()
    if docs_dir.exists():
        for f in docs_dir.glob("*.md"):
            m = ID_RE.match(f.name)
            if m:
                out.add(f"{m.group(1)}-{m.group(2)}")
    return out


def fecha10(f) -> str:
    f = (f or "").strip()
    return f[:10] if FECHA_RE.match(f) else "nd"


def consulta_or(palabras) -> str:
    """Frases entre comillas unidas con OR (un '"' dentro de una palabra se descarta)."""
    ps = []
    for p in palabras or []:
        p = re.sub(r'"', " ", str(p)).strip()
        if p:
            ps.append(f'"{p}"')
    return " OR ".join(ps)


def _leer_json(p: Path, defecto):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return defecto


def _escribir_json(p: Path, data) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


# ── presupuesto diario ───────────────────────────────────────────────────────
_presupuesto_lock = threading.Lock()


def presupuesto_restante(archivo: Path, tope: int) -> int:
    hoy = dt.date.today().isoformat()
    with _presupuesto_lock:
        d = _leer_json(archivo, {})
        usados = d.get("docs", 0) if d.get("fecha") == hoy else 0
    return max(0, tope - usados)


def gastar(archivo: Path, n: int, tope: int | None = None) -> None:
    if n <= 0:
        return
    hoy = dt.date.today().isoformat()
    with _presupuesto_lock:
        d = _leer_json(archivo, {})
        usados = d.get("docs", 0) if d.get("fecha") == hoy else 0
        archivo.parent.mkdir(parents=True, exist_ok=True)
        nuevo = {"fecha": hoy, "docs": usados + n}
        if tope is not None:
            nuevo["tope"] = tope            # lo lee la Biblioteca para mostrar «hoy: X de Y»
        _escribir_json(archivo, nuevo)


# ── un flujo = una consulta FTS ordenada (fecha, id) descendente ──────────────
class Flujo:
    def __init__(self, nombre, fuente, nivel, tipo, peso, conn, keywords, ya, estado, ventana_dias):
        self.nombre, self.fuente, self.nivel, self.tipo, self.peso = nombre, fuente, nivel, tipo, peso
        self.conn, self.ya = conn, ya
        self.est = estado.setdefault(nombre, {})
        self.cursor = tuple(self.est["cursor"]) if self.est.get("cursor") else None
        self.fin = bool(self.est.get("fin"))
        self.hechos_pasada = 0
        self.error = None
        q = consulta_or(keywords)
        if q and nivel == "titulo":
            q = ("{titulo} : (" if fuente == "dof" else "{rubro} : (") + q + ")"
        self.q = q
        self.cutoff = (dt.date.today() - dt.timedelta(days=ventana_dias)).isoformat()
        self._cola: list = []          # filas del cursor ya leídas, aún no entregadas
        self._ventana: list | None = None

        if fuente == "dof":
            self.sel = ("SELECT n.cod_nota AS id, n.fecha AS fecha, n.titulo AS titulo, n.tipo_documento AS tipo, "
                        "n.texto_plano AS texto, COALESCE(n.fecha,'') AS k")
            self.base = ("FROM notas_fts JOIN notas n ON n.cod_nota = notas_fts.rowid "
                         "WHERE notas_fts MATCH ? AND n.incluido = 1 AND n.texto_plano IS NOT NULL "
                         "AND length(n.texto_plano) > 200")
            self.kcol, self.idcol = "COALESCE(n.fecha,'')", "n.cod_nota"
        else:
            self.sel = ("SELECT t.registro_digital AS id, t.fecha_publicacion AS fecha, t.rubro AS titulo, "
                        "t.epoca AS epoca, t.instancia AS instancia, t.materias AS materias, t.ta_tj AS ta_tj, "
                        "t.texto AS texto, COALESCE(t.fecha_publicacion,'') AS k")
            tipo_sql = ("(t.ta_tj = 1 OR COALESCE(t.tipo_tesis,'') LIKE '%urisprud%')" if tipo == "jur" else
                        "(COALESCE(t.ta_tj,0) != 1 AND COALESCE(t.tipo_tesis,'') NOT LIKE '%urisprud%')")
            self.base = ("FROM tesis_fts JOIN tesis t ON t.registro_digital = tesis_fts.rowid "
                         "WHERE tesis_fts MATCH ? AND t.texto IS NOT NULL AND length(t.texto) > 200 AND " + tipo_sql)
            self.kcol, self.idcol = "COALESCE(t.fecha_publicacion,'')", "t.registro_digital"
        self.activo = bool(self.q) and conn is not None

    def _filas(self, extra: str, args: list, n: int):
        sql = f"{self.sel} {self.base} {extra} ORDER BY {self.kcol} DESC, {self.idcol} DESC LIMIT ?"
        return self.conn.execute(sql, [self.q] + args + [n]).fetchall()

    def total(self) -> int:
        return self.conn.execute(f"SELECT COUNT(*) {self.base}", [self.q]).fetchone()[0]

    def _id(self, fila) -> str:
        return f"{self.fuente}-{fila['id']}"

    def siguiente(self):
        """Siguiente fila NO indexada: primero la ventana reciente, luego el cursor hacia atrás."""
        if not self.activo:
            return None
        try:
            if self._ventana is None:
                self._ventana = [f for f in self._filas(f"AND {self.kcol} >= ?", [self.cutoff], 300)
                                 if self._id(f) not in self.ya]
            while self._ventana:
                f = self._ventana.pop(0)
                if self._id(f) not in self.ya:
                    return f
            while not self.fin:
                if not self._cola:
                    if self.cursor is None:
                        filas = self._filas("", [], LOTE)
                    else:
                        filas = self._filas(f"AND ({self.kcol} < ? OR ({self.kcol} = ? AND {self.idcol} < ?))",
                                            [self.cursor[0], self.cursor[0], self.cursor[1]], LOTE)
                    if not filas:
                        self.fin = True
                        break
                    self._cola = list(filas)
                f = self._cola.pop(0)
                self.cursor = (f["k"], f["id"])          # se avanza al examinar, indexada o no
                if self._id(f) not in self.ya:
                    return f
        except Exception as e:                           # FTS roto, base bloqueada...
            self.error = str(e)[:120]
            self.activo = False
        return None


# ── indexación de un agente ──────────────────────────────────────────────────
def indexar_agente(agente: str, cfg: dict, kdir: Path, docs_dir: Path, dof_conn, sjf_conn, *,
                   summarize, append_memory, limite: int, forzar: bool = False, workers: int = 3,
                   ventana_dias: int = VENTANA_DIAS, log=None) -> dict:
    """Indexa hasta `limite` documentos nuevos del agente. Devuelve un resumen de la pasada.

    summarize(agente, titulo, fecha, fuente, texto) -> (resumen, aprendizaje)
    append_memory(agente, area, fecha, titulo, aprendizaje)
    """
    docs_dir.mkdir(parents=True, exist_ok=True)
    f_estado = kdir / "cursor.json"
    estado = {} if forzar else _leer_json(f_estado, {})
    ya = set() if forzar else ids_indexados(docs_dir)
    ya_antes = len(ya)

    flujos = []
    for nombre, fuente, nivel, tipo, peso in FLUJOS:
        kws = cfg.get(fuente) or []
        conn = dof_conn if fuente == "dof" else sjf_conn
        flujos.append(Flujo(nombre, fuente, nivel, tipo, peso, conn, kws, ya, estado, ventana_dias))

    # Universo por flujo (cacheado: las consultas de conteo son las caras)
    ahora = time.time()
    for fl in flujos:
        if fl.activo and (ahora - fl.est.get("total_ts", 0) > TOTAL_TTL_S or "total" not in fl.est):
            try:
                fl.est["total"], fl.est["total_ts"] = fl.total(), ahora
            except Exception as e:
                fl.error, fl.activo = str(e)[:120], False

    # Selección ponderada y en orden de prioridad hasta `limite` candidatos
    turnos = []
    for i in range(max(f.peso for f in flujos)):
        turnos += [f for f in flujos if i < f.peso]
    candidatos, vistos = [], set()
    vacias = 0
    for fl in itertools.cycle(turnos):
        if len(candidatos) >= limite or vacias >= len(turnos):
            break
        fila = fl.siguiente() if fl.activo else None
        if fila is None:
            vacias += 1
            continue
        vacias = 0
        did = fl._id(fila)
        if did in vistos:
            continue
        vistos.add(did)
        candidatos.append((fl, fila))

    # Resumen en paralelo (llamadas a DeepSeek) y escritura secuencial
    def tarea(par):
        fl, f = par
        titulo = f["titulo"] or ("(sin título)" if fl.fuente == "dof" else "(sin rubro)")
        try:
            return summarize(agente, titulo, f["fecha"] or "", fl.fuente.upper(), f["texto"] or "")
        except Exception as e:                           # nunca perder la pasada por un documento
            if log:
                log.warning("indexer: resumen falló (%s %s): %s", agente, fl._id(f), e)
            return (str(f["texto"] or "")[:800] + "…", titulo[:150])

    nuevos = errores = 0
    por_flujo: dict = {}
    if candidatos:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            resultados = list(pool.map(tarea, candidatos))
        for (fl, f), (resumen, aprendizaje) in zip(candidatos, resultados):
            try:
                fe = fecha10(f["fecha"])
                titulo = f["titulo"] or ("(sin título)" if fl.fuente == "dof" else "(sin rubro)")
                if fl.fuente == "dof":
                    cab = (f"# {titulo}\n**Fuente:** DOF | **Fecha:** {f['fecha']} | "
                           f"**Tipo:** {f['tipo'] or '?'} | **ID:** {f['id']}\n\n")
                else:
                    kind = "Jurisprudencia" if (f["ta_tj"] == 1 or fl.tipo == "jur") else "Tesis Aislada"
                    cab = (f"# {titulo}\n**Fuente:** SJF ({kind}) | **Época:** {f['epoca'] or '?'} | "
                           f"**Instancia:** {f['instancia'] or '?'} | **Fecha:** {f['fecha']} | **ID:** {f['id']}\n"
                           f"**Materias:** {f['materias'] or '?'}\n\n")
                destino = docs_dir / f"{fl.fuente}-{fe}-{f['id']}.md"
                tmp = destino.with_suffix(".md.tmp")
                tmp.write_text(cab + resumen + "\n", encoding="utf-8")
                tmp.replace(destino)
                append_memory(agente, cfg.get("label", fl.fuente.upper()), f["fecha"] or "", titulo, aprendizaje)
                ya.add(fl._id(f))
                nuevos += 1
                por_flujo[fl.nombre] = por_flujo.get(fl.nombre, 0) + 1
            except Exception as e:
                errores += 1
                if log:
                    log.warning("indexer: no pude escribir %s %s: %s", agente, fl._id(f), e)

    # Cursores y estado visibles (la biblioteca los lee para mostrar el avance)
    for fl in flujos:
        fl.est["cursor"] = list(fl.cursor) if fl.cursor else None
        fl.est["fin"] = fl.fin
        fl.est["hechos"] = fl.est.get("hechos", 0) + por_flujo.get(fl.nombre, 0)
        fl.est["actualizado"] = dt.datetime.now().isoformat(timespec="seconds")
        if fl.error:
            fl.est["error"] = fl.error
        else:
            fl.est.pop("error", None)
    try:
        _escribir_json(f_estado, estado)
    except Exception as e:
        if log:
            log.warning("indexer: no pude guardar cursor.json de %s: %s", agente, e)

    return {"nuevos": nuevos, "errores": errores, "por_flujo": por_flujo,
            "agotados": [f.nombre for f in flujos if f.fin], "indexados_antes": ya_antes,
            "indexados_ahora": len(ya), "errores_flujo": {f.nombre: f.error for f in flujos if f.error}}
