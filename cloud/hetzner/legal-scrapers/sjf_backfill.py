#!/usr/bin/env python3
"""
sjf_backfill.py — relleno histórico del acervo SJF (corre en segundo plano).

Mientras el harvester diario (sjf_harvest.py) trae lo NUEVO hacia adelante, este
job rellena lo ANTERIOR: camina hacia atrás desde el registro más alto, saltando
los que ya están en la BD y los ya marcados 404, descargando los que faltan. Así
llena huecos dentro del rango y va extendiendo el acervo hacia épocas anteriores.

Diseñado para correr cada par de horas vía systemd timer:
  - Lote acotado por corrida (BACKFILL_BATCH descargas reales) → gentil, no satura.
  - Commit POR REGISTRO → si se corta, no se pierde nada.
  - Guarda el cursor en la tabla `progress` → la próxima corrida continúa donde quedó.
  - Reusa fetch (cookie + isSemanal/consolidado) y normalize/upsert del scraper.

Uso:
    python3 sjf_backfill.py                 # un lote (default 800)
    BACKFILL_BATCH=2000 python3 sjf_backfill.py
    SJF_BACKFILL_FLOOR=1900000 python3 sjf_backfill.py   # no bajar de ese registro

Variables de entorno:
    SJF_DB_PATH          BD (default /opt/openclaw/legal/sjf/biblioteca.db)
    BACKFILL_BATCH       descargas reales por corrida (default 800)
    SJF_BACKFILL_FLOOR   registro mínimo a intentar (default 0 = sin piso)
    SJF_SCRAPER          ruta al sjf_biblioteca.py (normalize/upsert/mark_404)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

# Reutilizamos el fetch del harvester (cookie de sesión + isSemanal + fallback).
HARVEST_PATH = os.environ.get(
    "SJF_HARVEST", "/opt/openclaw/legal/sjf/sjf_harvest.py")
SCRAPER_PATH = os.environ.get("SJF_SCRAPER") or next(
    (p for p in (
        "/opt/openclaw/legal/sjf/sjf_biblioteca.py",
        "/opt/openclaw/mac/Projects/Kawiil Legal/sjf_biblioteca_install/sjf_biblioteca.py",
    ) if Path(p).exists()),
    "/opt/openclaw/legal/sjf/sjf_biblioteca.py",
)
DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
BATCH = int(os.environ.get("BACKFILL_BATCH", "800"))
FLOOR = int(os.environ.get("SJF_BACKFILL_FLOOR", "0"))
THROTTLE_MS = int(os.environ.get("BACKFILL_THROTTLE_MS", "150"))
CONSECUTIVE_404_THRESHOLD = int(os.environ.get("BACKFILL_404_THRESHOLD", "50"))
CONSECUTIVE_404_JUMP = int(os.environ.get("BACKFILL_404_JUMP", "50000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sjf_backfill")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No pude cargar {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if not Path(HARVEST_PATH).exists():
        log.error("No existe el harvester en %s", HARVEST_PATH)
        return 1
    harvest = _load(HARVEST_PATH, "sjf_harvest")   # fetch_tesis + _prime
    sjf = _load(SCRAPER_PATH, "sjf_biblioteca")    # normalize_tesis + upsert_tesis (+ mark_404)
    harvest._prime()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if hasattr(sjf, "SCHEMA"):
        conn.executescript(sjf.SCHEMA)
    # tabla de progreso (key/value) por si no existe
    conn.execute("CREATE TABLE IF NOT EXISTS progress (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()

    max_reg = conn.execute("SELECT COALESCE(MAX(registro_digital),0) FROM tesis").fetchone()[0]
    if not max_reg:
        log.error("BD vacía — corre primero el harvester")
        return 1

    # cursor: continúa donde quedó; si no hay, empieza justo debajo del máximo
    row = conn.execute("SELECT value FROM progress WHERE key='backfill_cursor'").fetchone()
    cursor = int(row[0]) if row and row[0] else max_reg
    if cursor > max_reg:
        cursor = max_reg

    present = {r[0] for r in conn.execute("SELECT registro_digital FROM tesis")}
    known404 = set()
    try:
        known404 = {r[0] for r in conn.execute("SELECT registro_digital FROM registros_404")}
    except sqlite3.OperationalError:
        conn.execute("CREATE TABLE IF NOT EXISTS registros_404 (registro_digital INTEGER PRIMARY KEY, first_seen_at TEXT)")
        conn.commit()

    log.info("Backfill desde %d (piso %d), lote %d. En BD: %d, 404 conocidos: %d",
             cursor, FLOOR, BATCH, len(present), len(known404))

    ok = miss = 0
    done = 0
    consecutive_404s = 0
    while done < BATCH and cursor > FLOOR:
        cursor -= 1
        if cursor in present or cursor in known404:
            continue
        done += 1
        st, raw = harvest.fetch_tesis(cursor)
        if st == 200 and raw:
            t = sjf.normalize_tesis(raw, cursor)
            sjf.upsert_tesis(conn, t)
            present.add(cursor)
            ok += 1
            consecutive_404s = 0
            conn.commit()  # commit por registro → crash-safe
            log.info("[+%d] %d %s | %s", ok, cursor, raw.get("fechaPublicacion", ""),
                     (t.get("rubro") or "")[:55])
        elif st in (404, 410):
            if hasattr(sjf, "mark_404"):
                sjf.mark_404(conn, cursor)
            else:
                conn.execute("INSERT OR IGNORE INTO registros_404(registro_digital, first_seen_at) VALUES(?,?)",
                             (cursor, time.strftime("%Y-%m-%dT%H:%M:%S")))
            known404.add(cursor)
            miss += 1
            consecutive_404s += 1
            if consecutive_404s >= CONSECUTIVE_404_THRESHOLD:
                cursor -= CONSECUTIVE_404_JUMP
                if cursor < FLOOR:
                    cursor = FLOOR
                log.info("Zona muerta detectada: saltando %d IDs, cursor → %d",
                         CONSECUTIVE_404_JUMP, cursor)
                consecutive_404s = 0
            if miss % 50 == 0:
                conn.commit()
        # 403/otros: no marcar, reintentar en otra corrida
        time.sleep(THROTTLE_MS / 1000)

    conn.execute(
        "INSERT INTO progress(key,value,updated_at) VALUES('backfill_cursor',?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (str(cursor), time.strftime("%Y-%m-%dT%H:%M:%S")))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
    conn.close()
    log.info("== backfill: +%d nuevas, %d 404. Cursor en %d. Acervo total: %d",
             ok, miss, cursor, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
