#!/usr/bin/env python3
"""
SJF Biblioteca — Kawiil Legal
==============================
Cosecha, almacena y mantiene actualizada una biblioteca local de tesis
y jurisprudencias del Semanario Judicial de la Federación (SCJN).

Subcomandos:
  init        - inicializa la base de datos e importa los PDFs existentes
  update      - trae las tesis nuevas publicadas desde la última corrida
  backfill    - descarga un lote de tesis históricas (default 500)
  report      - imprime estadísticas de la biblioteca
  export      - genera el Excel maestro
  pdf REG     - regenera el PDF de un registro digital específico

Uso típico:
  python3 sjf_biblioteca.py init
  python3 sjf_biblioteca.py update
  python3 sjf_biblioteca.py backfill --batch 500
  python3 sjf_biblioteca.py report
"""

from __future__ import annotations  # Compat con Python 3.9 para sintaxis dict | None

import argparse
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ============================================================
# CONFIGURACIÓN — ajustar paths según tu Mac
# ============================================================
HOME = Path.home()
BASE_DIR = HOME / "sjf_biblioteca"

# La BD que Louis LEE en el servidor vive en /opt/openclaw/legal/sjf/biblioteca.db.
# Cuando este scraper corre en Hetzner debe escribir AHÍ (antes escribía en
# ~/sjf_biblioteca/biblioteca.db, una BD muerta que Louis nunca leía → se estancó).
# Orden de resolución:
#   1) $SJF_DB_PATH si está definido (override explícito).
#   2) /opt/openclaw/legal/sjf/biblioteca.db si existe /opt/openclaw (= servidor).
#   3) ~/sjf_biblioteca/biblioteca.db (Mac / dev local).
_OPENCLAW_SJF = Path("/opt/openclaw/legal/sjf/biblioteca.db")
if os.environ.get("SJF_DB_PATH"):
    DB_PATH = Path(os.environ["SJF_DB_PATH"])
elif Path("/opt/openclaw").exists():
    DB_PATH = _OPENCLAW_SJF
else:
    DB_PATH = BASE_DIR / "biblioteca.db"
LOG_DIR = BASE_DIR / "logs"

# Carpeta donde viven los PDFs (Dropbox). AJUSTAR si cambias de carpeta.
PDF_DIR = Path(
    "/Users/leopoldobassoco/Leopoldo Dropbox/Kawiil Mx/"
    "LEGAL, CONSTITUCIONES, CORPORATIVO CLIENTES/Tesis"
)

# Carpeta de plantilla (módulo que genera los PDFs)
PLANTILLA_DIR = BASE_DIR / "plantilla"

# API público del SJF
API_BASE = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis"
# El WAF de la SCJN rechaza User-Agents "raros" con 403 "Acceso denegado: Formato
# inválido". Hay que usar un UA de navegador real + Referer del sitio para pasar.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
SJF_REFERER = "https://sjf2.scjn.gob.mx/"

# Tuning
THROTTLE_MS = 400          # pausa entre peticiones (más alto = menos riesgo de WAF/429)
THROTTLE_JITTER_MS = 250   # jitter aleatorio adicional para no parecer bot
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 3          # segundos
DEFAULT_BACKFILL = 800     # tesis históricas por corrida
DEFAULT_WORKERS = 6        # peticiones en paralelo

# Si el WAF empieza a devolver 403 en cadena, NO es que los registros no existan:
# estamos BLOQUEADOS (rate-limit / sesión caducada / headers). Tras este número de
# 403 consecutivos abortamos la corrida con error claro, SIN marcar esos registros
# como 404 (no envenenar la BD: son tesis reales que sí existen en el sitio).
BLOCK_TOLERANCE_403 = 12


# ============================================================
# LOGGING
# ============================================================
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"sjf_{dt.date.today():%Y%m}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sjf")


# ============================================================
# BASE DE DATOS
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS tesis (
    registro_digital     INTEGER PRIMARY KEY,
    ius                  TEXT,
    rubro                TEXT,
    texto                TEXT,
    precedentes          TEXT,
    notas_genericas      TEXT,
    texto_publicacion    TEXT,
    localizacion         TEXT,
    epoca                TEXT,
    epoca_abr            TEXT,
    instancia            TEXT,
    instancia_abr        TEXT,
    sala                 TEXT,
    materias             TEXT,
    tipo_tesis           TEXT,
    clave_tesis          TEXT,
    fuente               TEXT,
    tomo                 TEXT,
    volumen              TEXT,
    sub_volumen          TEXT,
    pagina               TEXT,
    ta_tj                INTEGER,
    tipo_jurisprudencia  TEXT,
    fecha_publicacion    TEXT,
    fetched_at           TEXT NOT NULL,
    pdf_generated        INTEGER DEFAULT 0,
    pdf_path             TEXT,
    raw_json             TEXT
);

CREATE INDEX IF NOT EXISTS idx_tesis_epoca       ON tesis(epoca);
CREATE INDEX IF NOT EXISTS idx_tesis_materias    ON tesis(materias);
CREATE INDEX IF NOT EXISTS idx_tesis_fecha       ON tesis(fecha_publicacion);
CREATE INDEX IF NOT EXISTS idx_tesis_ta_tj       ON tesis(ta_tj);
CREATE INDEX IF NOT EXISTS idx_tesis_instancia   ON tesis(instancia);

-- FTS para búsqueda full-text (para análisis y future deep learning)
CREATE VIRTUAL TABLE IF NOT EXISTS tesis_fts USING fts5(
    rubro, texto, precedentes,
    content='tesis', content_rowid='registro_digital',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS tesis_ai AFTER INSERT ON tesis BEGIN
    INSERT INTO tesis_fts(rowid, rubro, texto, precedentes)
    VALUES (new.registro_digital, new.rubro, new.texto, new.precedentes);
END;

CREATE TRIGGER IF NOT EXISTS tesis_ad AFTER DELETE ON tesis BEGIN
    INSERT INTO tesis_fts(tesis_fts, rowid, rubro, texto, precedentes)
    VALUES('delete', old.registro_digital, old.rubro, old.texto, old.precedentes);
END;

CREATE TRIGGER IF NOT EXISTS tesis_au AFTER UPDATE ON tesis BEGIN
    INSERT INTO tesis_fts(tesis_fts, rowid, rubro, texto, precedentes)
    VALUES('delete', old.registro_digital, old.rubro, old.texto, old.precedentes);
    INSERT INTO tesis_fts(rowid, rubro, texto, precedentes)
    VALUES (new.registro_digital, new.rubro, new.texto, new.precedentes);
END;

CREATE TABLE IF NOT EXISTS registros_404 (
    registro_digital INTEGER PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    registros_attempted INTEGER DEFAULT 0,
    registros_ok INTEGER DEFAULT 0,
    registros_404 INTEGER DEFAULT 0,
    registros_error INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS progress (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


def db_connect() -> sqlite3.Connection:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def progress_set(conn: sqlite3.Connection, key: str, value):
    conn.execute(
        "INSERT INTO progress(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(value), dt.datetime.now().isoformat(timespec="seconds")),
    )


def progress_get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM progress WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ============================================================
# API DEL SJF
# ============================================================
# Opener con cookie-jar: el WAF de la SCJN suele exigir una cookie de sesión que se
# obtiene al visitar el sitio. Sin ella, las peticiones secuenciales al microservicio
# devuelven 403 "Acceso denegado". Primamos la sesión visitando el referer una vez.
import http.cookiejar as _cookiejar
import random as _random

_COOKIE_JAR = _cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIE_JAR))
_session_primed = False


def _prime_session() -> None:
    """Visita el sitio del SJF para obtener cookies de sesión antes de pegarle al API.
    Idempotente: solo la primera vez (o tras un 403 que fuerza re-prime)."""
    global _session_primed
    try:
        req = urllib.request.Request(SJF_REFERER, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        with _OPENER.open(req, timeout=20) as resp:
            resp.read(2048)  # consumir un poco para cerrar bien
        _session_primed = True
        log.info("Sesión SJF primada (cookies: %d)", len(_COOKIE_JAR))
    except Exception as e:
        log.warning("No pude primar sesión SJF (%s); sigo sin cookie", e)
        _session_primed = True  # no reintentar en bucle


def _sleep_throttle() -> None:
    time.sleep(THROTTLE_MS / 1000 + _random.uniform(0, THROTTLE_JITTER_MS / 1000))


def fetch_tesis(registro: int) -> tuple[int, dict | None]:
    """Devuelve (status, dict|None) para un registro digital.

    Distinción CLAVE:
      - 404 / 410  → PERMANENTE: el registro no existe. No reintentar.
      - 403        → BLOQUEADO por el WAF (no "no existe"): reintentar con backoff y
                     re-primar la sesión. Si persiste, se devuelve 403 para que el
                     caller decida (NO se marca como 404 — son tesis reales).
      - otros      → transitorio: reintentar.
    """
    global _session_primed
    PERMANENT_FAIL = (404, 410)
    if not _session_primed:
        _prime_session()
    url = f"{API_BASE}/{registro}"

    def _mk_req():
        return urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": SJF_REFERER,
            "Origin": "https://sjf2.scjn.gob.mx",
            "X-Requested-With": "XMLHttpRequest",
        })

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with _OPENER.open(_mk_req(), timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            if e.code in PERMANENT_FAIL:
                # Genuinamente no existe — no reintentar.
                return e.code, None
            if e.code == 403:
                # BLOQUEO del WAF, no ausencia. Re-primar sesión y reintentar.
                log.warning(f"HTTP 403 (bloqueo WAF) en registro {registro} intento {attempt}")
                if attempt < RETRY_ATTEMPTS:
                    _session_primed = False  # forzar re-prime de cookies
                    time.sleep(RETRY_BACKOFF * attempt)
                    _prime_session()
                    continue
                return 403, None
            log.warning(f"HTTP {e.code} en registro {registro} intento {attempt}: {e}")
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
        except Exception as e:
            log.warning(f"Error en registro {registro} intento {attempt}: {e}")
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
    return 0, None


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = (
        s.replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    )
    return s.strip()


def normalize_tesis(raw: dict, registro: int) -> dict:
    """Mapea la respuesta cruda del API a las columnas de la tabla `tesis`."""
    return {
        "registro_digital": registro,
        "ius": raw.get("ius"),
        "rubro": strip_html(raw.get("rubro")),
        "texto": strip_html(raw.get("texto")),
        "precedentes": strip_html(raw.get("precedentes")),
        "notas_genericas": strip_html(raw.get("notasGenericas")),
        "texto_publicacion": strip_html(raw.get("textoPublicacion")),
        "localizacion": (raw.get("localizacion") or "").strip(),
        "epoca": raw.get("epoca"),
        "epoca_abr": raw.get("epocaAbr"),
        "instancia": raw.get("instancia"),
        "instancia_abr": raw.get("instanciaAbr"),
        "sala": raw.get("sala"),
        "materias": raw.get("materias"),
        "tipo_tesis": raw.get("tipoTesis"),
        "clave_tesis": raw.get("claveTesis"),
        "fuente": raw.get("fuente"),
        "tomo": raw.get("tomo"),
        "volumen": raw.get("volumen"),
        "sub_volumen": raw.get("subVolumen"),
        "pagina": (raw.get("pagina") or "").strip(),
        "ta_tj": raw.get("ta_tj"),
        "tipo_jurisprudencia": str(raw.get("tipoJurisprudencia") or ""),
        "fecha_publicacion": raw.get("fechaPublicacion"),
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def upsert_tesis(conn: sqlite3.Connection, t: dict):
    cols = list(t.keys())
    placeholders = ",".join("?" * len(cols))
    setters = ",".join(f"{c}=excluded.{c}" for c in cols if c != "registro_digital")
    conn.execute(
        f"INSERT INTO tesis ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(registro_digital) DO UPDATE SET {setters}",
        [t[c] for c in cols],
    )


def mark_404(conn: sqlite3.Connection, registro: int):
    conn.execute(
        "INSERT OR IGNORE INTO registros_404(registro_digital, first_seen_at) VALUES(?,?)",
        (registro, dt.datetime.now().isoformat(timespec="seconds")),
    )


# ============================================================
# GENERACIÓN DE PDFs
# ============================================================
def build_pdf_for_tesis(t: dict) -> Path | None:
    """Genera el PDF usando la plantilla y lo guarda en PDF_DIR."""
    try:
        sys.path.insert(0, str(PLANTILLA_DIR))
        from build_tesis_pdf import build_pdf
        reg = t["registro_digital"]
        out = PDF_DIR / f"Tesis{reg}.pdf"
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        # Adaptar dict a formato esperado por build_pdf
        data = {
            "idDigital": reg,
            "rubro": t.get("rubro", ""),
            "texto": t.get("texto", ""),
            "precedentes": t.get("precedentes", ""),
            "notasGenericas": t.get("notas_genericas", ""),
            "textoPublicacion": t.get("texto_publicacion", ""),
            "localizacion": t.get("localizacion", ""),
            "epoca": t.get("epoca", ""),
            "instancia": t.get("instancia", ""),
            "materias": t.get("materias", ""),
            "tipoTesis": t.get("tipo_tesis", ""),
            "claveTesis": t.get("clave_tesis", ""),
            "fuente": t.get("fuente", ""),
            "tomo": t.get("tomo", ""),
            "volumen": t.get("volumen", ""),
            "subVolumen": t.get("sub_volumen", ""),
            "pagina": t.get("pagina", ""),
            "ta_tj": t.get("ta_tj"),
            "tipoJurisprudencia": t.get("tipo_jurisprudencia", ""),
        }
        build_pdf(str(out), data)
        return out
    except Exception as e:
        log.error(f"Error generando PDF de {t.get('registro_digital')}: {e}")
        return None


# ============================================================
# COMANDOS
# ============================================================
def cmd_init():
    """Inicializa la BD e importa los PDFs y JSONs existentes."""
    conn = db_connect()
    log.info("BD inicializada en %s", DB_PATH)

    # Importar JSONs ya extraídos (de la corrida anterior)
    extracted = Path(
        "/Users/leopoldobassoco/Documents/Claude/Projects/Kawiil Legal/_extract/json"
    )
    if extracted.exists():
        files = list(extracted.glob("tesis_*.json"))
        log.info("Importando %d JSONs previamente extraídos…", len(files))
        imported = 0
        for f in files:
            try:
                with open(f) as fp:
                    raw = json.load(fp)
                reg = raw.get("idDigital") or int(re.search(r"tesis_(\d+)", f.name).group(1))
                t = {
                    "registro_digital": reg,
                    "ius": raw.get("ius") or reg,
                    "rubro": strip_html(raw.get("rubro", "")),
                    "texto": strip_html(raw.get("texto", "")),
                    "precedentes": strip_html(raw.get("precedentes", "")),
                    "notas_genericas": strip_html(raw.get("notasGenericas", "")),
                    "texto_publicacion": strip_html(raw.get("textoPublicacion", "")),
                    "localizacion": (raw.get("localizacion") or "").strip(),
                    "epoca": raw.get("epoca", ""),
                    "epoca_abr": raw.get("epocaAbr", ""),
                    "instancia": raw.get("instancia", ""),
                    "instancia_abr": raw.get("instanciaAbr", ""),
                    "sala": raw.get("sala", ""),
                    "materias": raw.get("materias", ""),
                    "tipo_tesis": raw.get("tipoTesis", ""),
                    "clave_tesis": raw.get("claveTesis", ""),
                    "fuente": raw.get("fuente", ""),
                    "tomo": raw.get("tomo", ""),
                    "volumen": raw.get("volumen", ""),
                    "sub_volumen": raw.get("subVolumen", ""),
                    "pagina": raw.get("pagina", ""),
                    "ta_tj": raw.get("ta_tj"),
                    "tipo_jurisprudencia": str(raw.get("tipoJurisprudencia") or ""),
                    "fecha_publicacion": raw.get("fechaPublicacion", ""),
                    "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "pdf_generated": 1 if (PDF_DIR / f"Tesis{reg}.pdf").exists() else 0,
                    "pdf_path": str(PDF_DIR / f"Tesis{reg}.pdf"),
                    "raw_json": json.dumps(raw, ensure_ascii=False),
                }
                upsert_tesis(conn, t)
                imported += 1
            except Exception as e:
                log.warning("Falló import de %s: %s", f.name, e)
        conn.commit()
        log.info("Importadas %d tesis desde JSONs", imported)

    # Importar el resto desde los PDFs existentes (las que no tenían JSON)
    if PDF_DIR.exists():
        from pypdf import PdfReader
        existing_pdfs = list(PDF_DIR.glob("Tesis*.pdf"))
        log.info("Detectados %d PDFs en %s", len(existing_pdfs), PDF_DIR)
        existing_regs = {
            r["registro_digital"]
            for r in conn.execute("SELECT registro_digital FROM tesis").fetchall()
        }
        nuevos = 0
        for pdf in existing_pdfs:
            m = re.match(r"Tesis(\d+)", pdf.name)
            if not m:
                continue
            reg = int(m.group(1))
            if reg in existing_regs:
                continue
            try:
                r = PdfReader(str(pdf))
                t_text = "\n".join(p.extract_text() or "" for p in r.pages)
                rubro_m = re.search(r"Registro digital:\s*\d+\s*\n+\s*([^\n]{15,})", t_text)
                mat = re.search(r"Materia\(?s?\)?\:\s*([^\n]+)", t_text)
                epoca = re.search(r"(Und[eé]cima|D[eé]cima|Novena|Octava|S[eé]ptima|Sexta|Quinta) [eÉ]poca", t_text)
                ins = re.search(r"Instancia:\s*([^\n]+)", t_text)
                cla = re.search(r"Tesis:\s*([^\n]+)", t_text)
                tipo = re.search(r"Tipo:\s*([^\n]+)", t_text)
                t = {
                    "registro_digital": reg,
                    "ius": reg, "rubro": (rubro_m.group(1).strip() if rubro_m else "")[:600],
                    "texto": "", "precedentes": "", "notas_genericas": "",
                    "texto_publicacion": "", "localizacion": "",
                    "epoca": epoca.group(0) if epoca else "",
                    "epoca_abr": "", "instancia": ins.group(1).strip() if ins else "",
                    "instancia_abr": "", "sala": "",
                    "materias": ", ".join(x.strip() for x in (mat.group(1) if mat else "").split(",") if x.strip()),
                    "tipo_tesis": tipo.group(1).strip() if tipo else "",
                    "clave_tesis": cla.group(1).strip() if cla else "",
                    "fuente": "", "tomo": "", "volumen": "", "sub_volumen": "", "pagina": "",
                    # 1=Jurisprudencia; 0=Aislada (código histórico del SJF, mismo que devuelve el API).
                    "ta_tj": 1 if tipo and "Jurisprudencia" in tipo.group(1) else (0 if tipo else None),
                    "tipo_jurisprudencia": "", "fecha_publicacion": "",
                    "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "pdf_generated": 1, "pdf_path": str(pdf), "raw_json": "",
                }
                upsert_tesis(conn, t)
                nuevos += 1
            except Exception as e:
                log.warning("Falló import de PDF %s: %s", pdf.name, e)
        conn.commit()
        log.info("Importadas %d tesis adicionales desde PDFs", nuevos)

    # Stats finales
    total = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
    progress_set(conn, "init_done", dt.date.today().isoformat())
    progress_set(conn, "universe_total", 262016)
    conn.commit()
    log.info("Init completado. Total tesis en BD: %d", total)
    conn.close()


def cmd_update(max_pull: int = 3000):
    """Cosecha los registros nuevos publicados desde el último max conocido.
    max_pull alto (3000) para cruzar huecos grandes entre publicaciones."""
    conn = db_connect()
    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'update')",
        (dt.datetime.now().isoformat(timespec="seconds"),),
    ).lastrowid
    conn.commit()

    max_reg = conn.execute("SELECT COALESCE(MAX(registro_digital),0) FROM tesis").fetchone()[0]
    log.info("Update: empezando desde registro %d", max_reg + 1)

    ok = miss = err = consec_404 = consec_403 = 0
    blocked = False
    cursor = max_reg
    attempts = 0
    # Avanzamos hasta GAP_TOLERANCE 404 consecutivos. Los registros del SJF NO son
    # contiguos: entre la última tesis y la siguiente publicación puede haber huecos
    # de >100 (ej. 24-abr 2032066 → 29-may 2032185, hueco de ~119). Un corte de 30
    # se rendía antes de alcanzar las nuevas. 800 cruza huecos reales sin escanear de más.
    GAP_TOLERANCE = 800
    while consec_404 < GAP_TOLERANCE and attempts < max_pull:
        cursor += 1
        attempts += 1
        status, raw = fetch_tesis(cursor)
        if status == 200 and raw:
            t = normalize_tesis(raw, cursor)
            upsert_tesis(conn, t)
            pdf = build_pdf_for_tesis(t)
            if pdf:
                conn.execute(
                    "UPDATE tesis SET pdf_generated=1, pdf_path=? WHERE registro_digital=?",
                    (str(pdf), cursor),
                )
            ok += 1
            consec_404 = 0
            consec_403 = 0
            log.info("[update +%d] %s — %s", ok, cursor, t.get("rubro", "")[:80])
        elif status == 403:
            # NO es "no existe": el WAF nos está bloqueando. NO marcamos 404 (no
            # envenenamos la BD). Si se acumulan, abortamos en vez de reportar éxito falso.
            consec_403 += 1
            if consec_403 >= BLOCK_TOLERANCE_403:
                blocked = True
                log.error(
                    "WAF bloqueando: %d HTTP 403 consecutivos desde registro %d. "
                    "Abortando SIN marcar 404 (son tesis reales). Reintentar más tarde "
                    "o con mayor throttle / IP distinta.",
                    consec_403, cursor - consec_403 + 1,
                )
                break
        elif status in (404, 410):
            # Genuinamente no disponible — registro inexistente.
            mark_404(conn, cursor)
            miss += 1
            consec_404 += 1
            consec_403 = 0
        else:
            err += 1
            consec_403 = 0
        if attempts % 25 == 0:
            conn.commit()
        _sleep_throttle()
    conn.commit()
    progress_set(conn, "last_update_at", dt.datetime.now().isoformat(timespec="seconds"))
    conn.execute(
        "UPDATE runs SET finished_at=?, registros_attempted=?, registros_ok=?, "
        "registros_404=?, registros_error=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), attempts, ok, miss, err, run_id),
    )
    conn.commit()
    if blocked:
        log.error("Update ABORTADO por bloqueo WAF: +%d nuevas antes del bloqueo, %d 404s, %d errores", ok, miss, err)
    else:
        log.info("Update terminado: +%d nuevas, %d 404s, %d errores", ok, miss, err)
    conn.close()
    # Señal de salida distinta de 0 para que el systemd timer / cron registre el fallo.
    if blocked:
        raise SystemExit(2)


def cmd_backfill(batch: int = DEFAULT_BACKFILL, workers: int = DEFAULT_WORKERS):
    """Descarga un lote de tesis históricas, hacia atrás desde el mínimo actual.
    Usa peticiones en paralelo para ir más rápido."""
    conn = db_connect()
    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'backfill')",
        (dt.datetime.now().isoformat(timespec="seconds"),),
    ).lastrowid
    conn.commit()

    cursor = int(progress_get(conn, "backfill_cursor",
                  conn.execute("SELECT COALESCE(MIN(registro_digital),2029883) FROM tesis").fetchone()[0]))
    cursor -= 1
    log.info("Backfill: lote de %d con %d workers, empezando en registro %d", batch, workers, cursor)

    known_404 = {
        r[0] for r in conn.execute("SELECT registro_digital FROM registros_404").fetchall()
    }

    # Generar lista de registros a intentar (saltando los 404 ya conocidos)
    targets = []
    c = cursor
    while len(targets) < batch and c > 0:
        if c not in known_404:
            targets.append(c)
        c -= 1
    final_cursor = c

    ok = miss = err = blocked_403 = 0
    t0 = time.time()

    # Fetch en paralelo
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_tesis, reg): reg for reg in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            reg = futures[fut]
            try:
                status, raw = fut.result()
                results[reg] = (status, raw)
            except Exception as e:
                results[reg] = (0, None)
                log.warning("Excepción fetcheando %d: %s", reg, e)
            if i % 50 == 0:
                rate = i / max(time.time() - t0, 1)
                eta = (batch - i) / max(rate, 0.1)
                log.info("[backfill] %d/%d fetched | %.1f req/s | ETA %ds",
                         i, batch, rate, int(eta))

    # Procesar resultados (escribir a BD y generar PDFs en main thread, ya descargados)
    log.info("[backfill] Fetch terminado, procesando %d resultados…", len(results))
    for reg in sorted(results.keys(), reverse=True):
        status, raw = results[reg]
        if status == 200 and raw:
            t = normalize_tesis(raw, reg)
            upsert_tesis(conn, t)
            pdf = build_pdf_for_tesis(t)
            if pdf:
                conn.execute(
                    "UPDATE tesis SET pdf_generated=1, pdf_path=? WHERE registro_digital=?",
                    (str(pdf), reg),
                )
            ok += 1
        elif status in (404, 410):
            # Genuinamente no disponible — registro inexistente.
            mark_404(conn, reg)
            miss += 1
        elif status == 403:
            # Bloqueo WAF — NO marcar 404 (es tesis real). Contar para avisar al final.
            blocked_403 += 1
        else:
            err += 1
        if (ok + miss + err + blocked_403) % 50 == 0:
            conn.commit()

    progress_set(conn, "backfill_cursor", final_cursor)
    conn.commit()
    conn.execute(
        "UPDATE runs SET finished_at=?, registros_attempted=?, registros_ok=?, "
        "registros_404=?, registros_error=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), len(targets), ok, miss, err, run_id),
    )
    conn.commit()
    elapsed = time.time() - t0
    if blocked_403:
        log.warning("Backfill: %d registros devolvieron 403 (bloqueo WAF) — NO marcados 404, "
                    "reintentar más tarde.", blocked_403)
    log.info("Backfill terminado en %.1fs: +%d nuevas, %d 404s, %d errores. Cursor en %d",
             elapsed, ok, miss, err, final_cursor)
    conn.close()


def cmd_catchup(per_day: int = 800, max_total: int = 5000):
    """
    Detecta cuántos días han pasado sin un backfill exitoso y
    ejecuta lotes proporcionales para recuperar.

    Se ejecuta automáticamente al boot del Mac (RunAtLoad de launchd).
    """
    conn = db_connect()
    last_bf = conn.execute(
        "SELECT MAX(finished_at) FROM runs WHERE mode='backfill' AND registros_ok > 0"
    ).fetchone()[0]

    if last_bf:
        last_dt = dt.datetime.fromisoformat(last_bf)
        days_missed = max(0, (dt.datetime.now() - last_dt).days)
    else:
        days_missed = 0

    if days_missed <= 1:
        log.info("Catchup: no hay días perdidos (último backfill: %s). Saliendo.", last_bf)
        conn.close()
        return

    # Si se perdieron N días, intentar N × per_day, capado en max_total
    batch_needed = min(days_missed * per_day, max_total)
    log.info("Catchup: %d días sin backfill. Recuperando con lote de %d.", days_missed, batch_needed)
    conn.close()
    # Reusar cmd_backfill con el batch calculado
    cmd_backfill(batch=batch_needed)


def cmd_search(query: str, limit: int = 20):
    """Búsqueda full-text en la BD."""
    conn = db_connect()
    rows = conn.execute(
        """
        SELECT tesis.registro_digital, tesis.rubro, tesis.materias, tesis.epoca, tesis.instancia,
               snippet(tesis_fts, 1, '«', '»', '…', 12) AS extracto
        FROM tesis_fts JOIN tesis ON tesis.registro_digital = tesis_fts.rowid
        WHERE tesis_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    if not rows:
        print(f"Sin resultados para «{query}».")
    print(f"\n{len(rows)} resultados para «{query}»:\n")
    for r in rows:
        print(f"[{r['registro_digital']}] {r['epoca']} | {r['materias']} | {r['instancia']}")
        print(f"   {r['rubro'][:160]}")
        print(f"   …{r['extracto']}…")
        print()
    conn.close()


def cmd_stats():
    """Estadísticas detalladas para análisis."""
    conn = db_connect()
    total = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
    if total == 0:
        print("No hay tesis en la BD. Corre 'init' o 'update' primero.")
        return

    print("\n=== ANÁLISIS DETALLADO ===\n")

    # Por mes de publicación (top 12)
    print("Tesis por mes de publicación (últimos 12 meses con datos):")
    for r in conn.execute(
        """
        SELECT substr(fecha_publicacion, 1, 7) ym, COUNT(*) n
        FROM tesis WHERE fecha_publicacion IS NOT NULL AND fecha_publicacion != ''
        GROUP BY ym ORDER BY ym DESC LIMIT 12
        """
    ):
        bar = "█" * min(40, r["n"] // 5)
        print(f"  {r['ym']}  {r['n']:>5,}  {bar}")

    # Por tipo
    print("\nDistribución Jurisprudencia vs Aislada:")
    for r in conn.execute(
        # El SJF usa dos códigos para Aislada (0 = código viejo, 2 = código nuevo);
        # 1 = Jurisprudencia. Todo lo demás es realmente "sin dato".
        "SELECT CASE WHEN ta_tj=1 THEN 'Jurisprudencia' "
        "            WHEN ta_tj IN (0,2) THEN 'Aislada' "
        "            ELSE 'Sin dato' END t, "
        "COUNT(*) n FROM tesis "
        "GROUP BY CASE WHEN ta_tj=1 THEN 'Jurisprudencia' "
        "              WHEN ta_tj IN (0,2) THEN 'Aislada' "
        "              ELSE 'Sin dato' END "
        "ORDER BY n DESC"
    ):
        print(f"  {r['t']:<20}  {r['n']:>8,}  ({r['n']/total*100:.1f}%)")

    # Historial de corridas
    print("\nÚltimas 10 corridas:")
    for r in conn.execute(
        "SELECT id, started_at, finished_at, mode, registros_ok, registros_404, registros_error "
        "FROM runs ORDER BY id DESC LIMIT 10"
    ):
        dur = "—"
        if r["finished_at"]:
            try:
                d1 = dt.datetime.fromisoformat(r["started_at"])
                d2 = dt.datetime.fromisoformat(r["finished_at"])
                dur = f"{(d2-d1).total_seconds():.0f}s"
            except Exception:
                pass
        print(f"  #{r['id']:>3}  {r['started_at']}  [{r['mode']:<9}]  "
              f"ok:{r['registros_ok']:>4} 404:{r['registros_404']:>3} err:{r['registros_error']:>2}  ({dur})")

    print()
    conn.close()


def cmd_report():
    """Imprime estadísticas de la biblioteca."""
    conn = db_connect()
    total = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
    universe = int(progress_get(conn, "universe_total", "262016"))
    j = conn.execute("SELECT COUNT(*) FROM tesis WHERE ta_tj=1").fetchone()[0]
    # ta_tj=0 (código viejo del SJF) y ta_tj=2 (código nuevo) son ambos Aisladas.
    a = conn.execute("SELECT COUNT(*) FROM tesis WHERE ta_tj IN (0,2)").fetchone()[0]
    pdfs = conn.execute("SELECT COUNT(*) FROM tesis WHERE pdf_generated=1").fetchone()[0]

    print()
    print("=" * 60)
    print("  BIBLIOTECA SJF — KAWIIL LEGAL")
    print("=" * 60)
    print(f"  Tesis en la biblioteca:  {total:>10,}  ({total/universe*100:.2f}% del universo)")
    print(f"  Universo del SJF:        {universe:>10,}")
    print(f"  Por descargar:           {universe-total:>10,}")
    print(f"  PDFs generados:          {pdfs:>10,}")
    print(f"  Jurisprudencia:          {j:>10,}")
    print(f"  Aisladas:                {a:>10,}")
    print()
    print("  Por época:")
    for row in conn.execute(
        "SELECT COALESCE(epoca,'(sin dato)') ep, COUNT(*) n FROM tesis GROUP BY ep ORDER BY n DESC"
    ):
        print(f"    {row['ep']:<35}  {row['n']:>8,}")
    print()
    print("  Por materia (top 10):")
    for row in conn.execute(
        """
        WITH RECURSIVE split(m, rest) AS (
          SELECT '', materias || ',' FROM tesis WHERE materias IS NOT NULL AND materias != ''
          UNION ALL
          SELECT TRIM(substr(rest, 1, instr(rest,',')-1)),
                 substr(rest, instr(rest,',')+1)
          FROM split WHERE rest != ''
        )
        SELECT m, COUNT(*) n FROM split WHERE m != '' GROUP BY m ORDER BY n DESC LIMIT 10
        """
    ):
        print(f"    {row['m']:<35}  {row['n']:>8,}")
    print()
    last_run = conn.execute(
        "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last_run:
        print(f"  Última corrida ({last_run['mode']}): {last_run['finished_at']}")
        print(f"    Intentos: {last_run['registros_attempted']} | "
              f"OK: {last_run['registros_ok']} | "
              f"404: {last_run['registros_404']} | "
              f"err: {last_run['registros_error']}")
    print()
    conn.close()


def cmd_export():
    """Exporta el inventario a Excel (mismo formato que el Inventario_Tesis_SCJN.xlsx)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        log.error("openpyxl no instalado. Ejecuta: pip3 install openpyxl")
        return

    conn = db_connect()
    rows = list(
        conn.execute(
            "SELECT registro_digital, rubro, materias, epoca, instancia, "
            "tipo_tesis, clave_tesis, tomo, pagina, pdf_path "
            "FROM tesis ORDER BY registro_digital"
        )
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"
    headers = ["Registro", "Rubro", "Materia(s)", "Época", "Instancia",
               "Tipo", "Clave", "Tomo", "Página", "URL SJF"]
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", start_color="2E75B6")
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    for i, r in enumerate(rows, 2):
        ws.cell(row=i, column=1, value=r["registro_digital"])
        ws.cell(row=i, column=2, value=r["rubro"])
        ws.cell(row=i, column=3, value=r["materias"])
        ws.cell(row=i, column=4, value=r["epoca"])
        ws.cell(row=i, column=5, value=r["instancia"])
        ws.cell(row=i, column=6, value=r["tipo_tesis"])
        ws.cell(row=i, column=7, value=r["clave_tesis"])
        ws.cell(row=i, column=8, value=r["tomo"])
        ws.cell(row=i, column=9, value=r["pagina"])
        url = ws.cell(
            row=i, column=10,
            value=f"https://sjf2.scjn.gob.mx/detalle/tesis/{r['registro_digital']}"
        )
        url.font = Font(color="0563C1", underline="single", size=10)

    widths = [14, 70, 22, 16, 26, 14, 22, 8, 10, 40]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    out = BASE_DIR / f"Inventario_SJF_{dt.date.today():%Y%m%d}.xlsx"
    wb.save(out)
    log.info("Inventario exportado a %s", out)
    conn.close()


def cmd_pdf(registro: int):
    """Regenera el PDF de un registro digital específico."""
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM tesis WHERE registro_digital=?", (registro,)
    ).fetchone()
    if not row:
        log.error("Registro %d no está en la BD. Usa update primero.", registro)
        return
    pdf = build_pdf_for_tesis(dict(row))
    if pdf:
        log.info("PDF regenerado: %s", pdf)
    conn.close()


# ============================================================
# MAIN
# ============================================================
def main():
    p = argparse.ArgumentParser(description="SJF Biblioteca — Kawiil Legal")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    up = sub.add_parser("update")
    up.add_argument("--max", type=int, default=200)
    bf = sub.add_parser("backfill")
    bf.add_argument("--batch", type=int, default=DEFAULT_BACKFILL)
    bf.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    cu = sub.add_parser("catchup")
    cu.add_argument("--per-day", type=int, default=800)
    cu.add_argument("--max-total", type=int, default=5000)
    sub.add_parser("report")
    sub.add_parser("export")
    sub.add_parser("stats")
    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)
    pp = sub.add_parser("pdf")
    pp.add_argument("registro", type=int)

    args = p.parse_args()
    if args.cmd == "init":
        cmd_init()
    elif args.cmd == "update":
        cmd_update(max_pull=args.max)
    elif args.cmd == "backfill":
        cmd_backfill(batch=args.batch, workers=args.workers)
    elif args.cmd == "catchup":
        cmd_catchup(per_day=args.per_day, max_total=args.max_total)
    elif args.cmd == "report":
        cmd_report()
    elif args.cmd == "export":
        cmd_export()
    elif args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "search":
        cmd_search(args.query, limit=args.limit)
    elif args.cmd == "pdf":
        cmd_pdf(args.registro)


if __name__ == "__main__":
    main()
