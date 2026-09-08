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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reparar", action="store_true", help="intenta llenar la fecha desde raw_json")
    ap.add_argument("--clave", default="", help="campo del raw_json a usar (ver diagnóstico)")
    ap.add_argument("--aplicar", action="store_true", help="escribe de verdad (sin esto, ensayo)")
    ap.add_argument("--lote", type=int, default=5000, help="filas por lote (default 5000)")
    ap.add_argument("--muestra", type=int, default=200, help="tesis a inspeccionar (default 200)")
    a = ap.parse_args()
    if a.reparar:
        if not a.clave:
            sys.exit("--reparar necesita --clave. Corre el diagnóstico primero.")
        reparar(a.clave, a.lote, a.aplicar)
    else:
        diagnosticar(a.muestra)


if __name__ == "__main__":
    main()
