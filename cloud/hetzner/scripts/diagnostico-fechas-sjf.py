#!/usr/bin/env python3
"""
diagnostico-fechas-sjf.py — ¿por qué las tesis del SJF no traen fecha?

Síntoma: `legal_buscar` devuelve todos los resultados como `[SJF/None]`. La columna
`fecha_publicacion` existe (y tiene índice), y el scraper la mapea desde
`raw.get("fechaPublicacion")` — pero llega vacía. Sin fecha no se puede ordenar por
vigencia ni distinguir una tesis de 1995 de una de 2026, y citar una tesis superada
como vigente es riesgo profesional, no un detalle cosmético.

La buena noticia: `tesis.raw_json` guarda la respuesta COMPLETA del API, así que si
el dato viene con otro nombre de campo, se recupera sin volver a descargar nada.

Uso:
  # 1) Diagnóstico (solo lectura, no toca nada):
  python3 diagnostico-fechas-sjf.py

  # 2) Reparación, con la clave que el diagnóstico haya identificado:
  python3 diagnostico-fechas-sjf.py --reparar --clave fechaPublicacion
  python3 diagnostico-fechas-sjf.py --reparar --clave fechaPublicacion --aplicar
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DB = Path(os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db"))
# Cualquier cosa que parezca fecha: ISO, dd/mm/aaaa, o un timestamp en ms.
_FECHA_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{10,13})")


def abrir(escritura=False):
    if not DB.exists():
        sys.exit(f"No existe la BD: {DB}  (define SJF_DB_PATH si está en otra ruta)")
    uri = f"file:{DB}" + ("" if escritura else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def parece_fecha(v) -> bool:
    return bool(v is not None and _FECHA_RE.match(str(v)))


def diagnosticar(muestra: int) -> list:
    con = abrir()
    try:
        total = con.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
        con_f = con.execute(
            "SELECT COUNT(*) FROM tesis "
            "WHERE fecha_publicacion IS NOT NULL AND fecha_publicacion != ''"
        ).fetchone()[0]
        con_raw = con.execute(
            "SELECT COUNT(*) FROM tesis WHERE raw_json IS NOT NULL AND raw_json != ''"
        ).fetchone()[0]
        print(f"BD: {DB}  ({DB.stat().st_size / 1024 / 1024:,.0f} MB)")
        print(f"  tesis totales            : {total:,}")
        print(f"  con fecha_publicacion    : {con_f:,}  ({100*con_f//total if total else 0}%)")
        print(f"  SIN fecha                : {total - con_f:,}")
        print(f"  con raw_json recuperable : {con_raw:,}")
        if con_f == total and total:
            print("\n✓ Todas traen fecha. No hay nada que reparar.")
            return []
        if not con_raw:
            print("\n✗ Sin raw_json no hay de dónde recuperarla: habría que re-descargar.")
            return []

        # Qué campos trae de verdad la respuesta del API, y cuáles parecen fecha
        filas = con.execute(
            "SELECT raw_json FROM tesis "
            "WHERE raw_json IS NOT NULL AND raw_json != '' "
            "  AND (fecha_publicacion IS NULL OR fecha_publicacion = '') "
            f"LIMIT {int(muestra)}"
        ).fetchall()
        claves = Counter()
        candidatas = {}
        for f in filas:
            try:
                d = json.loads(f["raw_json"])
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            for k, v in d.items():
                claves[k] += 1
                if parece_fecha(v) or ("fech" in k.lower() or "date" in k.lower()):
                    candidatas.setdefault(k, []).append(v)

        print(f"\n--- Campos del API en la muestra ({len(filas)} tesis sin fecha) ---")
        print("  " + ", ".join(sorted(claves)))
        if not candidatas:
            print("\n✗ Ningún campo del raw_json parece una fecha.")
            print("  El API no la devuelve para estas tesis; la vigencia habrá que")
            print("  derivarla de `epoca`/`fuente`/`tomo`, o de otro endpoint.")
            return []
        print("\n--- Candidatas a la fecha ---")
        ranking = []
        for k, vals in sorted(candidatas.items(), key=lambda kv: -len(kv[1])):
            utiles = [v for v in vals if parece_fecha(v)]
            no_vacios = [v for v in vals if v not in (None, "", "null")]
            print(f"  • {k}: presente en {len(vals)}/{len(filas)} · "
                  f"{len(utiles)} parecen fecha · ejemplos: {no_vacios[:3] or '(todos vacíos)'}")
            if utiles:
                ranking.append((len(utiles), k))
        if not ranking:
            print("\n⚠ Los campos de fecha existen pero vienen VACÍOS en el API.")
            print("  No es un bug del scraper: el dato no viene. Usar `epoca` como proxy.")
        else:
            ranking.sort(reverse=True)
            print(f"\n→ Mejor candidata: --clave {ranking[0][1]} "
                  f"({ranking[0][0]} de {len(filas)} con valor usable)")
        return ranking
    finally:
        con.close()


def reparar(clave: str, lote: int, aplicar: bool):
    con = abrir(escritura=aplicar)
    try:
        cuantos = con.execute(
            "SELECT COUNT(*) FROM tesis "
            "WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
            "  AND raw_json IS NOT NULL "
            f"  AND json_extract(raw_json, '$.{clave}') IS NOT NULL"
        ).fetchone()[0]
        print(f"Filas reparables con la clave «{clave}»: {cuantos:,}")
        if not cuantos:
            print("Nada que hacer. Corre el diagnóstico para ver otras candidatas.")
            return
        if not aplicar:
            ej = con.execute(
                "SELECT registro_digital, json_extract(raw_json, ?) v FROM tesis "
                "WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
                "  AND json_extract(raw_json, ?) IS NOT NULL LIMIT 5",
                (f"$.{clave}", f"$.{clave}")).fetchall()
            print("\nMuestra de lo que se escribiría:")
            for r in ej:
                print(f"  registro {r['registro_digital']} → {r['v']}")
            print("\n(ENSAYO — nada se escribió. Agrega --aplicar para hacerlo de verdad.)")
            print("OJO al aplicar: la tabla tiene un trigger `tesis_au` que reindexa el")
            print("FTS en cada UPDATE, así que el arreglo se hace por lotes y tarda.")
            return

        # Por lotes: el trigger tesis_au reindexa el FTS en cada UPDATE, así que un
        # UPDATE masivo de una sola vez reescribiría el índice completo en una
        # transacción gigante. Por lotes se puede seguir el avance e interrumpir.
        hechas = 0
        while True:
            cur = con.execute(
                "UPDATE tesis SET fecha_publicacion = json_extract(raw_json, ?) "
                "WHERE registro_digital IN ("
                "  SELECT registro_digital FROM tesis "
                "  WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
                "    AND raw_json IS NOT NULL "
                "    AND json_extract(raw_json, ?) IS NOT NULL "
                f"  LIMIT {int(lote)})",
                (f"$.{clave}", f"$.{clave}"))
            con.commit()
            if not cur.rowcount:
                break
            hechas += cur.rowcount
            print(f"  … {hechas:,}/{cuantos:,}", flush=True)
        r = con.execute(
            "SELECT MIN(fecha_publicacion), MAX(fecha_publicacion) FROM tesis "
            "WHERE fecha_publicacion IS NOT NULL AND fecha_publicacion != ''").fetchone()
        print(f"\n✓ {hechas:,} tesis con fecha. Rango ahora: {r[0]} … {r[1]}")
    finally:
        con.close()



# ── Fecha aproximada desde la CITA ─────────────────────────────────────────
# El API deja `fechaPublicacion` vacía en las tesis históricas (el backfill), pero
# la cita del Semanario lleva el mes y el año dentro: "Gaceta del Semanario Judicial
# de la Federación. Libro 87, Junio de 2021, Tomo III". De ahí se puede derivar una
# fecha aproximada, que para saber si una tesis es vigente alcanza y sobra.
_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_MES_ANIO_RE = re.compile(
    r"\b(" + "|".join(_MESES) + r")\s+de\s+(\d{4})\b", re.IGNORECASE)
# El Semanario arranca con la Quinta Época (1917). Cualquier año fuera de
# [1917, año actual] no es una fecha de tesis: es otro número que se colló.
_ANIO_MIN = 1917
_ANIO_MAX = __import__("datetime").date.today().year


def fecha_desde_cita(*textos):
    """Devuelve (fecha_iso, 'mes') a partir de la cita, o (None, None).

    SOLO acepta el patrón inequívoco «MES de AÑO» ("Tomo V, Mayo de 1997"), y valida
    que el año caiga en [1917, año actual].

    La primera versión tenía un respaldo "cualquier año de 4 dígitos" sobre
    `localizacion`/`tomo`/`volumen` — que es donde vive el NÚMERO DE PÁGINA. Con eso,
    "Pág. 1995" se convertía en el año 1995 y el rango derivado salió
    1800-01-01 … 2099-01-01: 6,096 fechas basura que además PARECÍAN buenas. Un
    `~1800-01` en un resultado legal es peor que no tener fecha, así que el respaldo
    se eliminó: si la cita no trae mes, la fecha no es derivable y se dice.
    """
    for t in textos:
        if not t:
            continue
        m = _MES_ANIO_RE.search(str(t))
        if not m:
            continue
        anio = int(m.group(2))
        if not (_ANIO_MIN <= anio <= _ANIO_MAX):
            continue
        return f"{anio:04d}-{_MESES[m.group(1).lower()]:02d}-01", "mes"
    return None, None


def analizar_proxy(muestra: int):
    """Qué información de fecha traen los campos alternativos de las tesis sin fecha.
    No escribe nada: sirve para decidir si vale derivar la fecha y de dónde."""
    con = abrir()
    try:
        # Muestreo ESTRATIFICADO por época. Un `LIMIT 200` sin ORDER BY devuelve las
        # primeras filas en orden de rowid, que vienen en bloques del mismo tomo y mes
        # (ej. 198693-198698, todas Novena Época mayo-1997): una cobertura del 100%
        # sobre ese bloque no dice nada del acervo completo.
        epocas = [r[0] for r in con.execute(
            "SELECT epoca FROM tesis "
            "WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
            "GROUP BY epoca ORDER BY COUNT(*) DESC").fetchall()]
        por_epoca_n = max(int(muestra) // max(len(epocas), 1), 20)
        filas = []
        for ep in epocas:
            filas += con.execute(
                "SELECT registro_digital, epoca, fuente, localizacion, tomo, volumen, raw_json "
                "FROM tesis WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
                "  AND epoca IS ? ORDER BY RANDOM() LIMIT ?",
                (ep, por_epoca_n)).fetchall()
        print(f"(muestreo estratificado: hasta {por_epoca_n} al azar de cada una de "
              f"{len(epocas)} época(s) sin fecha)\n")
        if not filas:
            print("No hay tesis sin fecha.")
            return
        print(f"--- Campos de fecha alternativos ({len(filas)} tesis sin fecha) ---\n")
        for r in filas[:6]:
            print(f"  registro {r['registro_digital']}")
            for c in ("epoca", "fuente", "localizacion", "tomo", "volumen"):
                v = r[c]
                if v not in (None, ""):
                    print(f"      {c:14} = {str(v)[:110]}")
            f, prec = fecha_desde_cita(r["fuente"], r["localizacion"], r["tomo"], r["volumen"])
            print(f"      → derivable   = {f or 'NO'}" + (f"  (precisión: {prec})" if f else ""))
            print()

        # ¿De cuántas se podría derivar, y con qué precisión?
        cont = Counter()
        por_ep = {}
        for r in filas:
            f, prec = fecha_desde_cita(r["fuente"], r["localizacion"], r["tomo"], r["volumen"])
            cont[prec or "ninguna"] += 1
            ep = r["epoca"] or "sin época"
            d = por_ep.setdefault(ep, Counter())
            d[prec or "ninguna"] += 1
        print(f"--- Cobertura global sobre la muestra de {len(filas)} ---")
        for k, n in cont.most_common():
            print(f"  {k:9}: {n:,} ({100*n//len(filas)}%)")
        # Por época es lo que importa: si una época no es derivable, se ve aquí y no
        # se diluye en el promedio.
        print("\n--- Cobertura POR ÉPOCA (con el total real de cada una) ---")
        totales = {r[0]: r[1] for r in con.execute(
            "SELECT epoca, COUNT(*) FROM tesis "
            "WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
            "GROUP BY epoca").fetchall()}
        for ep, d in sorted(por_ep.items(), key=lambda kv: -totales.get(kv[0], 0)):
            n = sum(d.values())
            ok = n - d.get("ninguna", 0)
            tot = totales.get(ep if ep != "sin época" else None, 0)
            marca = "✓" if ok == n else ("⚠" if ok else "✗")
            print(f"  {marca} {str(ep)[:32]:34} {ok}/{n} derivables "
                  f"· {tot:,} tesis sin fecha en esta época")

        # ¿Y las que SÍ tienen fecha, de qué época son? Confirma la hipótesis de que
        # el API solo la da para las recientes.
        print("\n--- Épocas de las tesis que SÍ tienen fecha ---")
        for r in con.execute(
            "SELECT epoca, COUNT(*) n FROM tesis "
            "WHERE fecha_publicacion IS NOT NULL AND fecha_publicacion != '' "
            "GROUP BY epoca ORDER BY n DESC LIMIT 8"):
            print(f"  {str(r['epoca'])[:40]:42} {r['n']:,}")
    finally:
        con.close()



def integridad_fts(con) -> str:
    """Comprueba el índice FTS. Devuelve '' si está bien, o el error.

    Importa antes de escribir: el trigger `tesis_au` hace un `'delete'` contra el
    índice FTS externo en cada UPDATE de `tesis`, y si el índice no tiene esa fila
    —porque la tabla se pobló antes de que existieran los triggers, o el índice se
    reconstruyó— FTS5 lo reporta como `database disk image is malformed`. Un UPDATE
    masivo podría tronar a media corrida sobre 178 mil filas.
    """
    try:
        con.execute("INSERT INTO tesis_fts(tesis_fts) VALUES('integrity-check')")
        return ""
    except Exception as e:
        # La comprobación se emite como INSERT, así que en modo solo-lectura falla
        # por permisos y no por el índice. No hay que confundir una cosa con la otra.
        if "readonly" in str(e).lower():
            return "(no comprobable en modo lectura — se verifica al aplicar)"
        return str(e)


def derivar_fechas(lote: int, aplicar: bool, rehacer: bool = False):
    """Llena la fecha aproximada derivándola de la cita del Semanario.

    Dos decisiones de diseño:

    1. Va en una TABLA APARTE (`tesis_fecha_aprox`), no en una columna de `tesis`.
       Así no se dispara el trigger `tesis_au`, que en cada UPDATE reindexa el FTS:
       eso evita reescribir el índice de 1.5 GB fila por fila, y evita el riesgo de
       `database disk image is malformed` si el índice estuviera desincronizado.
       De paso es reversible con un `DROP TABLE`.

    2. La fecha derivada NO se mezcla con `fecha_publicacion`. Una fecha sacada de
       "Tomo V, Mayo de 1997" es una inferencia nuestra con precisión de mes, no el
       dato que publica la Corte. Si algún día se cita una tesis apoyándose en su
       fecha, hay que poder saber de dónde salió.
    """
    con = abrir(escritura=aplicar)
    try:
        problema = integridad_fts(con)
        if problema.startswith("(no comprobable"):
            print(f"· Índice FTS {problema}")
        elif problema:
            print(f"⚠ El índice FTS reporta: {problema[:120]}")
            print("  (Con la tabla aparte no estorba, pero conviene reconstruirlo:")
            print("   sqlite3 <db> \"INSERT INTO tesis_fts(tesis_fts) VALUES('rebuild');\")")
        else:
            print("✓ Índice FTS íntegro")

        existe = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tesis_fecha_aprox'"
        ).fetchone())
        if existe and rehacer:
            n = con.execute("SELECT COUNT(*) FROM tesis_fecha_aprox").fetchone()[0]
            if aplicar:
                con.execute("DELETE FROM tesis_fecha_aprox")
                con.commit()
                print(f"✓ Borradas {n:,} filas anteriores para recalcularlas")
            else:
                print(f"Se borrarían {n:,} filas para recalcularlas (con --aplicar)")
        if not existe:
            if not aplicar:
                print("Se creará la tabla `tesis_fecha_aprox` (con --aplicar)")
            else:
                con.execute("""
                    CREATE TABLE tesis_fecha_aprox (
                        registro_digital INTEGER PRIMARY KEY,
                        fecha_aprox      TEXT,      -- ISO, día 01 (precisión de mes)
                        fecha_origen     TEXT,      -- cita/mes | cita/anio | no-derivable
                        derivada_at      TEXT NOT NULL
                    )""")
                con.execute("CREATE INDEX idx_tfa_fecha ON tesis_fecha_aprox(fecha_aprox)")
                con.commit()
                print("✓ Tabla `tesis_fecha_aprox` creada (no se tocó `tesis`)")

        cond_pend = ("SELECT COUNT(*) FROM tesis t "
                     "WHERE (t.fecha_publicacion IS NULL OR t.fecha_publicacion = '')"
                     + (" AND NOT EXISTS (SELECT 1 FROM tesis_fecha_aprox a "
                        "WHERE a.registro_digital = t.registro_digital)" if existe else ""))
        pend = con.execute(cond_pend).fetchone()[0]
        print(f"Tesis sin fecha del API y sin fecha derivada: {pend:,}")
        if not pend:
            print("Nada que derivar.")
            return

        if not aplicar:
            ej = con.execute(
                "SELECT registro_digital, epoca, localizacion, volumen, tomo, fuente "
                "FROM tesis WHERE (fecha_publicacion IS NULL OR fecha_publicacion = '') "
                "ORDER BY RANDOM() LIMIT 8").fetchall()
            print("\nMuestra de lo que se escribiría (al azar):")
            for r in ej:
                f, prec = fecha_desde_cita(r["fuente"], r["localizacion"], r["tomo"], r["volumen"])
                print(f"  {r['registro_digital']}  {str(r['epoca'])[:16]:18} → "
                      f"{f or 'NO DERIVABLE'}  ({prec or '-'})")
            print("\n(ENSAYO — nada se escribió. Agrega --aplicar.)")
            print("Al aplicar NO se toca la tabla `tesis`: se escribe en "
                  "`tesis_fecha_aprox`,\nasí que no se dispara el trigger del FTS ni se "
                  "reescribe el índice.")
            return

        ahora = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        hechas = derivables = 0
        while True:
            filas = con.execute(
                "SELECT t.registro_digital, t.fuente, t.localizacion, t.tomo, t.volumen "
                "FROM tesis t "
                "WHERE (t.fecha_publicacion IS NULL OR t.fecha_publicacion = '') "
                "  AND NOT EXISTS (SELECT 1 FROM tesis_fecha_aprox a "
                "                  WHERE a.registro_digital = t.registro_digital) "
                f"LIMIT {int(lote)}").fetchall()
            if not filas:
                break
            escrituras = []
            for r in filas:
                f, prec = fecha_desde_cita(r["fuente"], r["localizacion"], r["tomo"], r["volumen"])
                if f:
                    derivables += 1
                # Las no derivables también se registran, para que la consulta de
                # pendientes avance y no se reintenten en cada corrida.
                escrituras.append((r["registro_digital"], f,
                                   f"cita/{prec}" if f else "no-derivable", ahora))
            con.executemany(
                "INSERT OR REPLACE INTO tesis_fecha_aprox "
                "(registro_digital, fecha_aprox, fecha_origen, derivada_at) VALUES (?,?,?,?)",
                escrituras)
            con.commit()
            hechas += len(escrituras)
            print(f"  … {hechas:,}/{pend:,}  (derivadas: {derivables:,})", flush=True)

        r = con.execute("SELECT COUNT(*), MIN(fecha_aprox), MAX(fecha_aprox) "
                        "FROM tesis_fecha_aprox WHERE fecha_aprox IS NOT NULL").fetchone()
        print(f"\n✓ {r[0]:,} tesis con fecha derivada. Rango: {r[1]} … {r[2]}")
        for row in con.execute("SELECT fecha_origen, COUNT(*) n FROM tesis_fecha_aprox "
                               "GROUP BY fecha_origen ORDER BY n DESC"):
            print(f"  {row[0]}: {row[1]:,}")
        # Histograma por década. Es lo que habría delatado de inmediato las fechas
        # basura (1800, 2099) en lugar de tener que notarlas en el MIN/MAX.
        print("\n  Distribución por década:")
        for row in con.execute(
            "SELECT substr(fecha_aprox,1,3) || '0s' d, COUNT(*) n FROM tesis_fecha_aprox "
            "WHERE fecha_aprox IS NOT NULL GROUP BY d ORDER BY d"):
            aviso = "  ⚠ fuera de rango" if not (
                _ANIO_MIN - 10 <= int(row[0][:3]) * 10 <= _ANIO_MAX) else ""
            print(f"    {row[0]}: {row[1]:,}{aviso}")
        print("\nPara revertir todo: DROP TABLE tesis_fecha_aprox;  (`tesis` nunca se tocó)")
    finally:
        con.close()

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reparar", action="store_true", help="intenta llenar la fecha desde raw_json")
    ap.add_argument("--clave", default="", help="campo del raw_json a usar (ver diagnóstico)")
    ap.add_argument("--aplicar", action="store_true", help="escribe de verdad (sin esto, ensayo)")
    ap.add_argument("--lote", type=int, default=5000, help="filas por lote (default 5000)")
    ap.add_argument("--muestra", type=int, default=200, help="tesis a inspeccionar (default 200)")
    ap.add_argument("--proxy", action="store_true",
                    help="analiza fuente/epoca/localizacion para derivar fecha aproximada")
    ap.add_argument("--derivar", action="store_true",
                    help="llena la fecha derivada en la tabla `tesis_fecha_aprox`")
    ap.add_argument("--rehacer", action="store_true",
                    help="borra lo ya derivado y lo recalcula (tras corregir el parser)")
    a = ap.parse_args()
    if a.derivar:
        derivar_fechas(a.lote, a.aplicar, a.rehacer)
    elif a.proxy:
        analizar_proxy(a.muestra)
    elif a.reparar:
        if not a.clave:
            sys.exit("--reparar necesita --clave. Corre el diagnóstico primero.")
        reparar(a.clave, a.lote, a.aplicar)
    else:
        diagnosticar(a.muestra)


if __name__ == "__main__":
    main()
