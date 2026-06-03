#!/usr/bin/env python3
"""
sjf_harvest.py — actualización diaria del Semanario Judicial (SJF) para Louis.

Por qué existe (03-jun-2026): el scraper grande (`sjf_biblioteca.py`) se quedó
estancado en abril porque (a) no había timer que lo corriera, y (b) le pegaba al
endpoint viejo sin el parámetro `?isSemanal=true`, con el que el API sirve las
publicaciones nuevas del Semanario. Sin ese parámetro las tesis recientes
devuelven 404 aunque existan (verificado: registro 2032190 del 29-may → 404 sin
param, 200 con isSemanal=true).

Este harvester es ligero y autosuficiente para el FETCH (cookie de sesión +
isSemanal=true), pero reutiliza la persistencia del scraper instalado
(`db_connect`, `normalize_tesis`, `upsert_tesis`) para escribir EXACTAMENTE en el
mismo esquema que Louis ya lee. Camina desde MAX(registro)+1 hacia adelante hasta
GAP_TOLERANCE 404 consecutivos.

Uso:
    python3 sjf_harvest.py            # corre el update
    SJF_DB_PATH=/ruta/db python3 sjf_harvest.py

Variables de entorno:
    SJF_SCRAPER  ruta al sjf_biblioteca.py instalado (para reusar db/normalize/upsert)
    SJF_DB_PATH  override de la BD (si no, el scraper resuelve /opt/openclaw/...)
    SJF_MAX_PULL tope de intentos por corrida (default 3000)
"""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────
SCRAPER_PATH = os.environ.get("SJF_SCRAPER") or next(
    (p for p in (
        "/opt/openclaw/legal/sjf/sjf_biblioteca.py",
        "/opt/openclaw/mac/Projects/Kawiil Legal/sjf_biblioteca_install/sjf_biblioteca.py",
    ) if Path(p).exists()),
    "/opt/openclaw/legal/sjf/sjf_biblioteca.py",
)
API_BASE = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis"
SEMANAL_QS = "?isSemanal=true&hostName=https://sjf2.scjn.gob.mx"
SJF_REFERER = "https://sjf2.scjn.gob.mx/"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
GAP_TOLERANCE = 800        # 404 consecutivos antes de rendirse (registros no contiguos)
BLOCK_TOLERANCE_403 = 12   # 403 consecutivos = bloqueo WAF → abortar sin envenenar BD
MAX_PULL = int(os.environ.get("SJF_MAX_PULL", "3000"))
THROTTLE_MS = 400
THROTTLE_JITTER_MS = 250
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sjf_harvest")

# ── HTTP con cookie de sesión (el WAF la exige; sin ella, 403 en cadena) ───────
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_primed = False


def _prime() -> None:
    global _primed
    try:
        req = urllib.request.Request(SJF_REFERER, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html"})
        _OPENER.open(req, timeout=20).read(2048)
        _primed = True
        log.info("Sesión SJF primada (cookies: %d)", len(_JAR))
    except Exception as e:
        log.warning("No pude primar sesión (%s)", e)
        _primed = True


def _headers(reg: int) -> dict:
    return {
        "User-Agent": USER_AGENT, "Accept": "application/json",
        "Referer": f"https://sjf2.scjn.gob.mx/detalle/tesis/{reg}",
        "Origin": "https://sjf2.scjn.gob.mx", "X-Requested-With": "XMLHttpRequest",
    }


def _fetch_one(url: str, reg: int) -> tuple[int, dict | None]:
    global _primed
    if not _primed:
        _prime()
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with _OPENER.open(urllib.request.Request(url, headers=_headers(reg)), timeout=25) as r:
                d = json.loads(r.read().decode("utf-8"))
                return 200, (d.get("data", d) if isinstance(d, dict) else d)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, None
            if e.code == 403:
                log.warning("403 (bloqueo WAF) en %d intento %d", reg, attempt)
                if attempt < RETRY_ATTEMPTS:
                    _primed = False
                    time.sleep(RETRY_BACKOFF * attempt)
                    _prime()
                    continue
                return 403, None
            log.warning("HTTP %d en %d intento %d", e.code, reg, attempt)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
        except Exception as e:
            log.warning("Error en %d intento %d: %s", reg, attempt, e)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
    return 0, None


def fetch_tesis(reg: int) -> tuple[int, dict | None]:
    """Pide con isSemanal=true (publicaciones nuevas); cae al endpoint sin param
    para lo histórico ya consolidado. Solo si AMBAS dan 404 es inexistente."""
    st, data = _fetch_one(f"{API_BASE}/{reg}{SEMANAL_QS}", reg)
    if st == 200 and data:
        return 200, data
    if st == 403:
        return 403, None
    st2, data2 = _fetch_one(f"{API_BASE}/{reg}", reg)
    if st2 == 200 and data2:
        return 200, data2
    if st2 == 403:
        return 403, None
    return 404, None


def _load_scraper():
    spec = importlib.util.spec_from_file_location("sjf_biblioteca", SCRAPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No pude cargar el scraper en {SCRAPER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    sjf = _load_scraper()
    _prime()
    conn = sjf.db_connect()
    max_reg = conn.execute("SELECT COALESCE(MAX(registro_digital),0) FROM tesis").fetchone()[0]
    log.info("Update SJF: desde registro %d", max_reg + 1)

    ok = miss = consec_404 = consec_403 = 0
    blocked = False
    cur = max_reg
    while consec_404 < GAP_TOLERANCE and (cur - max_reg) < MAX_PULL:
        cur += 1
        st, raw = fetch_tesis(cur)
        if st == 200 and raw:
            t = sjf.normalize_tesis(raw, cur)
            sjf.upsert_tesis(conn, t)
            ok += 1
            consec_404 = consec_403 = 0
            log.info("[+%d] %d %s | %s", ok, cur, raw.get("fechaPublicacion", ""),
                     (t.get("rubro") or "")[:60])
            if ok % 10 == 0:
                conn.commit()
        elif st == 403:
            consec_403 += 1
            if consec_403 >= BLOCK_TOLERANCE_403:
                blocked = True
                log.error("WAF bloqueando (%d×403). Aborto sin marcar 404.", consec_403)
                break
        else:  # 404/410
            miss += 1
            consec_404 += 1
            consec_403 = 0
        time.sleep(THROTTLE_MS / 1000 + random.uniform(0, THROTTLE_JITTER_MS / 1000))

    conn.commit()
    new_max = conn.execute("SELECT MAX(registro_digital) FROM tesis").fetchone()[0]
    conn.close()
    log.info("== +%d nuevas, %d huecos. MAX ahora: %s", ok, miss, new_max)
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
