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
    SJF_SCRAPER            ruta al sjf_biblioteca.py instalado (db/normalize/upsert)
    SJF_DB_PATH            override de la BD (si no, el scraper resuelve /opt/openclaw/...)
    SJF_MAX_PULL           tope de intentos por corrida (default 3000)
    SJF_THROTTLE_MS        pausa base entre peticiones (default 1500)
    SJF_THROTTLE_JITTER_MS jitter aleatorio encima del throttle (default 600)
    SJF_USER_AGENT         override del User-Agent (default: Safari falso; preferible
                           identificarse, p.ej. KawiilLegalBot/1.0 (+https://kawiil.mx))
"""
from __future__ import annotations

import datetime as dt
import http.cookiejar
import importlib.util
import json
import logging
import os
import random
import sqlite3
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
# BD que Louis LEE. El harvester escribe AQUÍ directamente (no vía el scraper, cuyo
# DB_PATH lo revierte el sync de la Mac).
DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
API_BASE = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis"
SEMANAL_QS = "?isSemanal=true&hostName=https://sjf2.scjn.gob.mx"
SJF_REFERER = "https://sjf2.scjn.gob.mx/"
# Default = UA de navegador que ya estaba (no cambia cómo se presenta Kawiil ante
# la SCJN). Conviene identificarse: un cliente que da la cara y va despacio se
# desbloquea; uno que se disfraza, no.
#   SJF_USER_AGENT="KawiilLegalBot/1.0 (+https://kawiil.mx) investigación jurídica"
USER_AGENT = os.environ.get("SJF_USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
GAP_TOLERANCE = 800        # 404 consecutivos antes de rendirse (registros no contiguos)
BLOCK_TOLERANCE_403 = 12   # 403 consecutivos = bloqueo WAF → abortar sin envenenar BD
MAX_PULL = int(os.environ.get("SJF_MAX_PULL", "3000"))
# Ritmo. Estaba en 400 ms (≈7,000 peticiones/hora) y el backfill en ~150–400 ms
# contra un servicio público de la SCJN. El 10-sep el WAF nos bloqueó y el acervo
# dejó de crecer (última tesis 2026-08-28). A 1,500 ms son ~2,400 peticiones/hora.
THROTTLE_MS = int(os.environ.get("SJF_THROTTLE_MS", "1500"))
THROTTLE_JITTER_MS = int(os.environ.get("SJF_THROTTLE_JITTER_MS", "600"))
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sjf_harvest")

# ── HTTP con cookie de sesión (el WAF la exige; sin ella, 403 en cadena) ───────
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_primed = False


# ── Interruptor de bloqueo del WAF ──────────────────────────────────────────
# Al ser bloqueados, harvest y backfill seguían disparándose y cada corrida
# quemaba 12 peticiones más contra un WAF que ya había dicho que no. Ahora el
# bloqueo se anota con espera creciente (2h → 6h → 12h → 24h, tope) y las
# corridas siguientes se saltan solas hasta que venza.
WAF_ESPERAS_H = (2, 6, 12, 24)


def waf_estado(conn) -> tuple[str, int]:
    """(hasta_iso, veces) del bloqueo vigente. ('', 0) si no hay ninguno."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS progress "
                     "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        r = conn.execute("SELECT value FROM progress WHERE key='waf_bloqueo'").fetchone()
    except Exception:
        return "", 0
    if not r or not r[0]:
        return "", 0
    partes = str(r[0]).split("|")
    return partes[0], int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else 1


def waf_bloqueado(conn) -> str:
    """Devuelve el mensaje de espera si seguimos bloqueados, o '' si ya se puede."""
    hasta, veces = waf_estado(conn)
    if not hasta:
        return ""
    try:
        falta = (dt.datetime.fromisoformat(hasta) - dt.datetime.now()).total_seconds()
    except Exception:
        return ""
    if falta <= 0:
        return ""
    return (f"WAF del SJF nos bloqueó (bloqueo #{veces}). No se intenta nada hasta "
            f"{hasta} — faltan {falta/3600:.1f} h. Insistir alarga el bloqueo.")


def waf_marcar(conn) -> None:
    """Anota un bloqueo nuevo, con espera más larga que la vez anterior."""
    _, veces = waf_estado(conn)
    veces += 1
    horas = WAF_ESPERAS_H[min(veces - 1, len(WAF_ESPERAS_H) - 1)]
    hasta = (dt.datetime.now() + dt.timedelta(hours=horas)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO progress(key,value,updated_at) VALUES('waf_bloqueo',?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                 "updated_at=excluded.updated_at",
                 (f"{hasta}|{veces}", dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    log.error("WAF bloqueó (bloqueo #%d). Me aparto %d h, hasta %s. "
              "Si se repite, baja el ritmo (SJF_THROTTLE_MS) o identifica al cliente "
              "con SJF_USER_AGENT en vez de insistir.", veces, horas, hasta)


def waf_liberar(conn) -> None:
    """Se llama tras una descarga exitosa: el bloqueo quedó atrás."""
    try:
        hasta, veces = waf_estado(conn)
        if hasta or veces:
            conn.execute("DELETE FROM progress WHERE key='waf_bloqueo'")
            conn.commit()
            log.info("WAF desbloqueado: descarga exitosa, contador en cero")
    except Exception:
        pass


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
                body = r.read()
                ct = (r.headers.get("Content-Type") or "").lower()
                # Incapsula puede devolver 200 HTML (challenge) en vez de JSON.
                if "html" in ct or body.lstrip()[:1] == b"<":
                    log.warning("Respuesta no-JSON (posible WAF) en %d intento %d", reg, attempt)
                    if attempt < RETRY_ATTEMPTS:
                        _primed = False
                        time.sleep(RETRY_BACKOFF * attempt)
                        _prime()
                        continue
                    return 403, None
                d = json.loads(body.decode("utf-8"))
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
    # Antes de primar la sesión: si el WAF nos bloqueó hace poco, se salta la
    # corrida entera. Cada intento durante el bloqueo lo alarga.
    _c = sqlite3.connect(DB_PATH)
    try:
        espera = waf_bloqueado(_c)
    finally:
        _c.close()
    if espera:
        log.warning(espera)
        return 0
    _prime()
    # IMPORTANTE: abrimos NUESTRA conexión a la BD que Louis lee (DB_PATH), en vez de
    # usar sjf.db_connect(). El scraper sincronizado desde la Mac tiene su DB_PATH
    # apuntando a ~/sjf_biblioteca/biblioteca.db (y el sync revierte cualquier parche),
    # así que escribir vía su db_connect mandaba los datos al archivo equivocado.
    # normalize_tesis/upsert_tesis son puras (reciben la conexión), así que las
    # reutilizamos con NUESTRA conexión al archivo correcto.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if hasattr(sjf, "SCHEMA"):
        conn.executescript(sjf.SCHEMA)  # asegura tablas (idempotente; ya existen)
    max_reg = conn.execute("SELECT COALESCE(MAX(registro_digital),0) FROM tesis").fetchone()[0]
    log.info("Update SJF en %s: desde registro %d (throttle %dms+%dj)",
             DB_PATH, max_reg + 1, THROTTLE_MS, THROTTLE_JITTER_MS)

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
            if not ok % 25 or ok == 1:
                waf_liberar(conn)  # hubo descarga real: el bloqueo quedó atrás
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
                waf_marcar(conn)
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
