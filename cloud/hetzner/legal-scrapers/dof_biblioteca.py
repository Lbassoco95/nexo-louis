#!/usr/bin/env python3
"""
DOF Biblioteca — Kawiil Legal
==============================
Cosecha, filtra, almacena y mantiene actualizada una biblioteca local de
publicaciones del Diario Oficial de la Federación (DOF) y leyes federales
consolidadas de la Cámara de Diputados.

Subcomandos:
  init                  - inicializa la base de datos
  update                - trae publicaciones de los últimos días
  backfill              - descarga histórico hacia atrás por mes/año
  classify              - aplica el filtro de inclusión a notas pendientes
  download-contents     - baja HTML/PDF de las notas incluidas
  catchup               - recupera días perdidos automáticamente
  leyes-init            - primera descarga de leyes vigentes (Diputados)
  leyes-update          - verifica versiones vigentes
  leyes-link            - cruza leyes con sus reformas en DOF
  report                - estado general
  stats                 - análisis detallado
  search QUERY          - búsqueda full-text
  export                - genera Excel maestro
  organizar             - reorganiza PDFs en Dropbox según convención

Uso típico:
  python3 dof_biblioteca.py init
  python3 dof_biblioteca.py update
  python3 dof_biblioteca.py backfill --year-from 2024 --year-to 2026
  python3 dof_biblioteca.py classify
  python3 dof_biblioteca.py download-contents
  python3 dof_biblioteca.py report
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ============================================================
# CONFIGURACIÓN
# ============================================================
HOME = Path.home()
# BASE_DIR configurable por env (en el servidor: /opt/openclaw/legal/dof) para usar
# la BD autoritativa del server, no una nueva en ~/dof_biblioteca.
BASE_DIR = Path(os.environ.get("DOF_BASE_DIR", str(HOME / "dof_biblioteca")))
DB_PATH = Path(os.environ.get("DOF_DB_PATH", str(BASE_DIR / "biblioteca_dof.db")))
LOG_DIR = BASE_DIR / "logs"

# Carpeta donde se guardan los PDFs individuales por nota (configurable; en el
# servidor a /opt/openclaw/legal/dof/pdfs para consulta offline / Ollama).
PDF_OUT_DIR = Path(os.environ.get("DOF_PDF_DIR", str(BASE_DIR / "pdfs")))

# Carpeta de archivos en Dropbox
DROPBOX_BASE = Path(
    "/Users/leopoldobassoco/Leopoldo Dropbox/Kawiil Mx/"
    "LEGAL, CONSTITUCIONES, CORPORATIVO CLIENTES/DOF"
)
DIR_LEYES = DROPBOX_BASE / "leyes_federales"
# Publicaciones (PDFs por nota): configurable por env. En el servidor →
# DOF_PDF_DIR (/opt/openclaw/legal/dof/pdfs); en la Mac, el Dropbox de siempre.
DIR_PUBLICACIONES = Path(os.environ.get("DOF_PDF_DIR", str(DROPBOX_BASE / "publicaciones_diarias")))
DIR_TRATADOS = DROPBOX_BASE / "tratados_internacionales"

# Endpoints API
SIDOF_BASE = "https://sidof.segob.gob.mx/datos_abiertos"
LEGACY_BASE = "http://diariooficial.gob.mx"

API_FECHA = f"{LEGACY_BASE}/WS_getDiarioFecha.php"        # ?year=YYYY&month=MM → availableDays
API_FULL = f"{LEGACY_BASE}/WS_getDiarioFull.php"          # ?day=DD&month=MM&year=YYYY&edicion=MAT → ediciones + notas
API_MENU_PRINCIPAL = f"{LEGACY_BASE}/BB_menuPrincipal.php"  # ?day=DD&month=MM&year=YYYY → 99 cod_diarios
API_DETALLE_EDICION = f"{LEGACY_BASE}/BB_DetalleEdicion.php"  # ?cod_diario=NNN
API_NOTA_DETALLE = f"{LEGACY_BASE}/nota_detalle_popup.php"    # ?codigo=NNN
API_PDF = f"{LEGACY_BASE}/WS_getDiarioPDF.php"            # ?day=DD&month=MM&year=YYYY&edicion=MAT → URLs

# Diputados (leyes consolidadas)
DIPUTADOS_INDEX = "https://www.diputados.gob.mx/LeyesBiblio/index.htm"

USER_AGENT = "KawiilLegal-DOF-Biblioteca/1.0 (consulta normativa)"

# Tuning
THROTTLE_MS = 150
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 5
DEFAULT_WORKERS = 4
DEFAULT_BACKFILL_MONTHS = 6  # cuántos meses históricos por corrida

# Filtro de inclusión (sección 1.2 del plan)
INCLUDED_TIPOS = {
    "LEY", "REFORMA", "DECRETO", "REGLAMENTO", "ACUERDO",
    "REGLAS", "RESOLUCION", "DISPOSICIONES", "TRATADO",
    "CONSTITUCION", "FE_DE_ERRATAS", "PROTOCOLO", "CONVENCION",
}
EXCLUDED_TIPOS = {
    "LICITACION", "CONVOCATORIA", "FALLO", "ADJUDICACION",
    "EDICTO", "AVISO_INTERNO", "DESIGNACION", "RATIFICACION_MENOR",
    "AVISO_NOTARIAL", "AVISO_JUDICIAL",
}

# ============================================================
# LOGGING
# ============================================================
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"dof_{dt.date.today():%Y%m}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("dof")


# ============================================================
# ESQUEMA DE BASE DE DATOS
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS notas (
    cod_nota INTEGER PRIMARY KEY,
    cod_diario INTEGER,
    fecha TEXT,                       -- ISO yyyy-mm-dd
    edicion TEXT,                     -- MAT/VES/EXT
    seccion TEXT,
    pagina INTEGER,
    titulo TEXT,
    cod_orga_uno TEXT,
    nombre_cod_orga_uno TEXT,
    cod_orga_dos TEXT,
    tipo_nota_raw TEXT,
    tipo_documento TEXT,              -- nuestro filtro: LEY/REFORMA/DECRETO/etc.
    incluido INTEGER DEFAULT NULL,    -- 1 incluido, 0 excluido, NULL sin clasificar
    motivo_exclusion TEXT,
    existe_html INTEGER,
    existe_doc INTEGER,
    existe_imagen INTEGER,
    texto_plano TEXT,
    pdf_path TEXT,
    html_path TEXT,
    raw_json TEXT,
    fetched_at TEXT,
    classified_at TEXT,
    content_downloaded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notas_fecha ON notas(fecha);
CREATE INDEX IF NOT EXISTS idx_notas_orga ON notas(cod_orga_uno, cod_orga_dos);
CREATE INDEX IF NOT EXISTS idx_notas_tipo ON notas(tipo_documento);
CREATE INDEX IF NOT EXISTS idx_notas_incluido ON notas(incluido);

CREATE VIRTUAL TABLE IF NOT EXISTS notas_fts USING fts5(
    titulo, texto_plano,
    content='notas', content_rowid='cod_nota',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS notas_ai AFTER INSERT ON notas BEGIN
    INSERT INTO notas_fts(rowid, titulo, texto_plano)
    VALUES (new.cod_nota, new.titulo, new.texto_plano);
END;

CREATE TRIGGER IF NOT EXISTS notas_au AFTER UPDATE OF titulo, texto_plano ON notas BEGIN
    INSERT INTO notas_fts(notas_fts, rowid, titulo, texto_plano)
    VALUES('delete', old.cod_nota, old.titulo, old.texto_plano);
    INSERT INTO notas_fts(rowid, titulo, texto_plano)
    VALUES (new.cod_nota, new.titulo, new.texto_plano);
END;

CREATE TABLE IF NOT EXISTS ediciones (
    cod_diario INTEGER PRIMARY KEY,
    fecha TEXT,
    edicion TEXT,
    pdf_path TEXT,
    num_notas INTEGER DEFAULT 0,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ediciones_fecha ON ediciones(fecha);

CREATE TABLE IF NOT EXISTS leyes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ambito TEXT,                      -- FEDERAL
    tipo TEXT,                        -- CONSTITUCION/LEY/CODIGO/REGLAMENTO/TRATADO
    nombre_corto TEXT,
    nombre_completo TEXT,
    version_vigente_fecha TEXT,
    texto_completo TEXT,
    pdf_path TEXT,
    url_origen TEXT,
    fetched_at TEXT,
    UNIQUE(ambito, nombre_completo)
);

CREATE VIRTUAL TABLE IF NOT EXISTS leyes_fts USING fts5(
    nombre_completo, texto_completo,
    content='leyes', content_rowid='id'
);

CREATE TABLE IF NOT EXISTS reformas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ley_id INTEGER,
    fecha_dof TEXT,
    cod_nota_dof INTEGER,
    titulo_reforma TEXT,
    articulos_modificados TEXT,
    pdf_path TEXT,
    FOREIGN KEY (ley_id) REFERENCES leyes(id),
    FOREIGN KEY (cod_nota_dof) REFERENCES notas(cod_nota)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    items_attempted INTEGER DEFAULT 0,
    items_ok INTEGER DEFAULT 0,
    items_error INTEGER DEFAULT 0,
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
    conn = sqlite3.connect(DB_PATH, timeout=30)
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
# UTILIDADES
# ============================================================
def http_get(url: str, params: dict | None = None, accept: str = "*/*",
             timeout: int = 30) -> tuple[int, bytes, dict]:
    """GET HTTP con retries. Devuelve (status, body_bytes, headers)."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
    })
    last_err = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return resp.status, body, dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, b"", {}
            last_err = e
            log.warning("HTTP %s en %s intento %d", e.code, url[:80], attempt)
        except Exception as e:
            last_err = e
            log.warning("Error %s en %s intento %d", e, url[:80], attempt)
        time.sleep(RETRY_BACKOFF * attempt)
    log.error("Falló %s tras %d intentos: %s", url[:80], RETRY_ATTEMPTS, last_err)
    return 0, b"", {}


def to_iso_date(s: str) -> str:
    """Convierte fechas tipo '13-06-2016' a '2016-06-13'."""
    if not s:
        return ""
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return s


def fix_encoding(s: str) -> str:
    """El DOF a veces devuelve caracteres en latin-1 marcados como UTF-8. Intenta arreglarlo."""
    if not s:
        return ""
    try:
        # Detectar reemplazos típicos
        if "Â" in s or "Ã" in s or "" in s:
            try:
                s = s.encode("latin-1").decode("utf-8")
            except Exception:
                pass
        return s
    except Exception:
        return s


def slugify(s: str, max_len: int = 80) -> str:
    """Convierte un texto a slug seguro para nombre de archivo."""
    if not s:
        return "sin_titulo"
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:max_len]


# ============================================================
# API DEL DOF
# ============================================================
def fetch_dias_mes(year: int, month: int) -> list[str]:
    """Devuelve lista de días con publicación en un mes. Ejemplo: ['01','04','05',...]"""
    status, body, _ = http_get(API_FECHA, {"year": year, "month": f"{month:02d}"})
    if status != 200 or not body:
        return []
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            return data.get("availableDays", [])
    except Exception as e:
        log.warning("No se pudo parsear días de %d-%02d: %s", year, month, e)
    return []


def fetch_edicion_full(year: int, month: int, day: int, edicion: str = "MAT") -> dict:
    """
    Devuelve la edición completa con sus notas vía WS_getDiarioFull.

    Devuelve dict con estructura:
      {ejemplares: [{id, fecha, edicion, secciones: [{secc, contentsection: {name, content}}]}]}
    """
    status, body, _ = http_get(API_FULL, {
        "day": f"{day:02d}",
        "month": f"{month:02d}",
        "year": str(year),
        "edicion": edicion,
    })
    if status != 200 or not body:
        return {}
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        log.warning("No se pudo parsear edición %04d-%02d-%02d %s: %s",
                    year, month, day, edicion, e)
    return {}


def _normalizar_edicion(s: str) -> str:
    """matutina → MAT, vespertina → VES, extraordinaria → EXT"""
    if not s:
        return "MAT"
    s_low = s.lower()
    if "matut" in s_low:
        return "MAT"
    if "vesper" in s_low:
        return "VES"
    if "extra" in s_low:
        return "EXT"
    return s.upper()[:3]


def _detect_orga_uno(poder_name: str) -> str:
    """Detecta codOrgaUno desde el nombre del poder."""
    if not poder_name:
        return ""
    n = poder_name.upper()
    if "EJECUTIVO" in n:
        return "PE"
    if "LEGISLATIVO" in n:
        return "PL"
    if "JUDICIAL" in n:
        return "PJ"
    if "AUTONOM" in n or "AUTÓNOM" in n:
        return "OA"
    if "BANCO" in n or "INE" in n or "INAI" in n or "COFECE" in n or "IFT" in n:
        return "OA"
    return ""


def extract_notas_from_ejemplar(ejemplar: dict) -> list[dict]:
    """Aplana la estructura jerárquica de WS_getDiarioFull en lista plana de notas
    estilo metadatos. La estructura puede tener varios niveles de anidación
    contentsection → content (puede ser dict o list)."""
    notas: list[dict] = []
    cod_diario = int(ejemplar.get("id", 0) or 0)
    edicion = _normalizar_edicion(ejemplar.get("edicion", "MAT"))
    fecha_raw = ejemplar.get("fecha", "")  # "martes 05 de marzo 2024"

    # Recorrer secciones (PRIMERA / SEGUNDA / UNICA / TERCERA / etc.)
    for sec in ejemplar.get("secciones") or []:
        seccion_name = sec.get("secc", "")
        cs = sec.get("contentsection")
        if not cs:
            continue
        # cs puede ser dict (un poder) o list (varios poderes)
        cs_items = cs if isinstance(cs, list) else [cs]
        for cs_item in cs_items:
            if not isinstance(cs_item, dict):
                continue
            poder_name = fix_encoding(cs_item.get("name", "") or "")
            cod_orga_uno = _detect_orga_uno(poder_name)

            inner = cs_item.get("content")
            inner_items = inner if isinstance(inner, list) else ([inner] if isinstance(inner, dict) else [])

            for inner_item in inner_items:
                if not isinstance(inner_item, dict):
                    continue
                # Caso A: inner_item es dependencia con .content = list de notas
                dep_name = fix_encoding(inner_item.get("name", "") or "")
                inner_content = inner_item.get("content")

                if isinstance(inner_content, list) and inner_content and isinstance(inner_content[0], dict) and "titulo" in inner_content[0]:
                    # son notas directas
                    nota_list = inner_content
                    notas.extend(_make_nota_records(
                        nota_list, cod_diario, edicion, seccion_name,
                        cod_orga_uno, poder_name, dep_name))
                elif isinstance(inner_content, list):
                    # otro nivel de anidación: dependencias con sub-content
                    for sub in inner_content:
                        if not isinstance(sub, dict):
                            continue
                        sub_dep_name = fix_encoding(sub.get("name", "") or dep_name)
                        sub_notas = sub.get("content")
                        if isinstance(sub_notas, list):
                            notas.extend(_make_nota_records(
                                sub_notas, cod_diario, edicion, seccion_name,
                                cod_orga_uno, poder_name, sub_dep_name))
                elif isinstance(inner_content, dict):
                    # un solo dict
                    sub_dep_name = fix_encoding(inner_content.get("name", "") or dep_name)
                    sub_notas = inner_content.get("content")
                    if isinstance(sub_notas, list):
                        notas.extend(_make_nota_records(
                            sub_notas, cod_diario, edicion, seccion_name,
                            cod_orga_uno, poder_name, sub_dep_name))
    return notas


def _make_nota_records(items: list, cod_diario: int, edicion: str, seccion: str,
                       cod_orga_uno: str, poder_name: str, dep_name: str) -> list[dict]:
    """Convierte items crudos de WS_getDiarioFull a registros normalizados."""
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cod_nota_raw = it.get("id")
        if not cod_nota_raw:
            continue
        try:
            cod_nota = int(cod_nota_raw)
        except (ValueError, TypeError):
            continue
        titulo = fix_encoding(it.get("titulo", "") or "")
        # Decodificar entidades HTML que vienen en el título
        titulo = html_mod.unescape(titulo)
        fecha = it.get("date", "")
        url_nota = it.get("url", "")
        out.append({
            "codNota": cod_nota,
            "codDiario": cod_diario,
            "fecha": fecha,
            "codSeccion": seccion,
            "_edicion": edicion,
            "titulo": titulo,
            "url_origen": url_nota,
            "existeHtml": "S",  # si tiene url, asumimos
            "existeDoc": "N",
            "existeImagen": "N",
            "tipoNota": None,
            "estado": 2,
            "pagina": it.get("pagina") or 0,
            "paginaHasta": 0,
            "nombreCodOrgaUno": poder_name,
            "codOrgaUno": cod_orga_uno,
            "codOrgaDos": dep_name,
            "orden": 0,
        })
    return out


def fetch_notas_edicion(cod_diario: int) -> list[dict]:
    """
    LEGACY: ya no se usa. Se mantiene por compatibilidad.
    El nuevo flujo usa fetch_edicion_full() + extract_notas_from_ejemplar().
    """
    return []


def fetch_nota_html(cod_nota: int) -> str:
    """Devuelve el HTML del detalle de una nota."""
    status, body, _ = http_get(API_NOTA_DETALLE, {"codigo": cod_nota},
                               accept="text/html")
    if status != 200 or not body:
        return ""
    try:
        return body.decode("utf-8", errors="replace")
    except Exception:
        return ""


def fetch_edicion_pdf(year: int, month: int, day: int, edicion: str) -> bytes:
    """Descarga el PDF completo de una edición."""
    status, body, _ = http_get(API_PDF, {
        "year": year, "month": f"{month:02d}", "day": f"{day:02d}",
        "edicion": edicion,
    }, accept="application/pdf")
    if status != 200 or not body:
        return b""
    return body


# ============================================================
# CLASIFICACIÓN (filtro de inclusión)
# ============================================================
RULES_EXCLUDE = [
    # ----- Ruido administrativo y referencias internas -----
    # Avisos genéricos numerados (la gran mayoría del ruido del DOF)
    (r"^\s*AVISO REF:\s*\d+", "AVISO_REF"),
    # Organismos + REF: (típico de avisos internos)
    (r"^\s*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,\.()\-]{8,100}- REF:\s*\d+", "AVISO_REF"),
    (r"^\s*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,\.()\-]+- REF\b", "AVISO_REF"),
    # Códigos contables (007H0C- EMPRESA..., 068017 - SECRETARIA...)
    (r"^\s*\d{3,}\s*[A-Z]+\s*[-–]", "CODIGO_CONTABLE"),
    (r"^\s*\d{3,}\s*-\s+[A-Z]", "CODIGO_CONTABLE"),

    # ----- Licitaciones / concursos -----
    (r"^\s*CONVOCATORIA.*\bLICITACI", "LICITACION"),
    (r"^\s*LICITACI(?:O|Ó)N\b", "LICITACION"),
    (r"^\s*FALLO.*LICITACI", "FALLO_LICITACION"),
    (r"^\s*ADJUDICACI(?:O|Ó)N", "ADJUDICACION"),

    # ----- Edictos y citatorios judiciales -----
    (r"^\s*EDICTO\b", "EDICTO"),
    (r"^\s*CITACI(?:O|Ó)N\b", "EDICTO"),
    (r"^\s*EMPLAZAMIENTO\b", "EDICTO"),

    # ----- Avisos notariales y sucesorios -----
    (r"^\s*AVISO NOTARIAL\b", "AVISO_NOTARIAL"),
    (r"^\s*AVISO.*(?:NOTARIO|FALLECIMIENTO|DEFUNCI|SUCESORIO|HEREDERO|INSTITUYE)", "AVISO_NOTARIAL"),

    # ----- Designaciones internas menores -----
    (r"^\s*OFICIO DE DESIGNACI", "DESIGNACION"),
    (r"^\s*NOMBRAMIENTO\b", "DESIGNACION"),
]


RULES_INCLUDE = [
    # ----- Constitución -----
    (r"\bCONSTITUCI(?:O|Ó)N POL(?:I|Í)TICA\b", "CONSTITUCION"),

    # ----- Leyes nuevas y códigos -----
    (r"^\s*DECRETO POR EL QUE SE EXPIDE LA LEY", "LEY"),
    (r"^\s*LEY (?:DE|GENERAL|FEDERAL|NACIONAL|ORG(?:A|Á)NICA|RELATIVA|QUE)\b", "LEY"),
    (r"^\s*C(?:O|Ó)DIGO\s+(?:CIVIL|PENAL|FISCAL|DE COMERCIO|NACIONAL|FEDERAL|DE PROCEDIMIENTOS|DE JUSTICIA|MILITAR)", "LEY"),

    # ----- Reformas (decreto reformatorio) -----
    (r"^\s*DECRETO POR EL QUE SE REFORMAN", "REFORMA"),
    (r"^\s*DECRETO POR EL QUE SE (?:ADICIONAN|DEROGAN|REFORMA|MODIFICA|ADICIONA)", "REFORMA"),
    (r"^\s*DECRETO POR EL QUE SE EXPIDE", "REFORMA"),
    (r"^\s*DECRETO POR EL QUE SE DECLARA", "REFORMA"),
    (r"^\s*DECRETO PROMULGATORIO", "REFORMA"),

    # ----- Reglamentos -----
    (r"^\s*REGLAMENTO\b", "REGLAMENTO"),
    (r"^\s*DECRETO POR EL QUE SE (?:EXPIDE|REFORMA|ADICIONA|MODIFICA).*REGLAMENTO", "REGLAMENTO"),

    # ----- Acuerdos (más amplio: cualquier "Acuerdo" al inicio) -----
    (r"^\s*ACUERDO\b", "ACUERDO"),

    # ----- Aviso mediante el cual se da a conocer (acuerdos publicados como aviso) -----
    (r"^\s*AVISO MEDIANTE EL CUAL SE DA A CONOCER", "ACUERDO"),
    (r"^\s*AVISO POR EL QUE SE (?:DA A CONOCER|MODIFICA|ESTABLECE|REFORMA)", "ACUERDO"),
    (r"^\s*AVISO GENERAL", "ACUERDO"),

    # ----- Circulares (CNBV, Banxico, IFT, etc.) -----
    (r"^\s*CIRCULAR\b", "CIRCULAR"),

    # ----- Lineamientos -----
    (r"^\s*LINEAMIENTOS\b", "LINEAMIENTOS"),
    (r"^\s*MODIFICACI(?:O|Ó)N (?:A )?(?:LOS )?LINEAMIENTOS", "LINEAMIENTOS"),

    # ----- Normas Oficiales Mexicanas (NOM / NMX) -----
    (r"^\s*NORMA OFICIAL MEXICANA\b", "NORMA"),
    (r"^\s*PROYECTO DE NORMA OFICIAL", "NORMA"),
    (r"^\s*NOM[\s\-]", "NORMA"),
    (r"^\s*NMX[\s\-]", "NORMA"),
    (r"^\s*MODIFICACI(?:O|Ó)N (?:A )?LA NORMA OFICIAL", "NORMA"),
    (r"^\s*RESPUESTA A LOS COMENTARIOS.*NORMA OFICIAL", "NORMA"),

    # ----- Resoluciones (más amplio) -----
    (r"^\s*RESOLUCI(?:O|Ó)N\b", "RESOLUCION"),
    (r"^\s*RESOLUCION MISCEL(?:A|Á)NEA", "RESOLUCION"),

    # ----- Reglas de carácter general -----
    (r"^\s*REGLAS\b", "REGLAS"),
    (r"^\s*MODIFICACIONES? A LAS REGLAS", "REGLAS"),

    # ----- Disposiciones de carácter general -----
    (r"^\s*DISPOSICIONES\b", "DISPOSICIONES"),
    (r"^\s*MODIFICACI(?:O|Ó)N A LAS DISPOSICIONES", "DISPOSICIONES"),

    # ----- Manuales de operación y organización -----
    (r"^\s*MANUAL DE (?:ORGANIZACI|PROCEDIMIENTOS|OPERACI|TR(?:A|Á)MITES|SERVICIOS)", "MANUAL"),

    # ----- Estatutos -----
    (r"^\s*ESTATUTO\b", "ESTATUTO"),

    # ----- Tratados, convenciones, protocolos internacionales -----
    (r"^\s*TRATADO\b", "TRATADO"),
    (r"^\s*CONVENCI(?:O|Ó)N\b", "TRATADO"),
    (r"^\s*PROTOCOLO\b", "TRATADO"),

    # ----- Convenios marco / coordinación / colaboración -----
    (r"^\s*CONVENIO\s+(?:DE COORDINACI|MARCO|ESPEC(?:I|Í)FICO|GENERAL)", "CONVENIO"),
    (r"^\s*CONVENIO\s+(?:DE COLABORACI|DE ADHESI|MODIFICATORIO)", "CONVENIO"),

    # ----- Políticas, programas y planes sectoriales -----
    (r"^\s*PROGRAMA\s+(?:NACIONAL|FEDERAL|SECTORIAL|INSTITUCIONAL|ANUAL|ESPECIAL)", "PROGRAMA"),
    (r"^\s*POL(?:I|Í)TICA\s+(?:NACIONAL|P(?:U|Ú)BLICA|FEDERAL)", "POLITICA"),
    (r"^\s*PLAN\s+(?:NACIONAL|SECTORIAL|ESTRAT(?:E|É)GICO|ANUAL)", "PLAN"),

    # ----- Asignaciones y concesiones -----
    (r"^\s*ASIGNACI(?:O|Ó)N QUE OTORGA", "CONCESION"),
    (r"^\s*CONCESI(?:O|Ó)N\b", "CONCESION"),
    (r"^\s*T(?:I|Í)TULO DE CONCESI", "CONCESION"),
    (r"^\s*PERMISO\s+(?:DE|PARA)", "CONCESION"),

    # ----- Indicadores financieros oficiales (Banxico) -----
    (r"^\s*TASAS? DE INTER(?:E|É)S", "FINANCIERO"),
    (r"^\s*TIPO DE CAMBIO\b", "FINANCIERO"),
    (r"^\s*VALOR DE LA UNIDAD", "FINANCIERO"),
    (r"^\s*(?:I|Í)NDICE NACIONAL DE PRECIOS", "FINANCIERO"),
    (r"^\s*UNIDAD DE MEDIDA Y ACTUALIZACI", "FINANCIERO"),
    (r"^\s*EQUIVALENCIA DE LAS MONEDAS", "FINANCIERO"),
    (r"^\s*VALOR EN MONEDA NACIONAL", "FINANCIERO"),

    # ----- Fe de erratas -----
    (r"^\s*FE DE ERRATAS", "FE_DE_ERRATAS"),
    (r"^\s*ACLARACI(?:O|Ó)N\b", "FE_DE_ERRATAS"),

    # ----- Anexos técnicos de regulación -----
    (r"^\s*ANEXO\s+(?:T(?:E|É)CNICO|\d)", "ANEXO"),

    # ----- Oficios circulares (con efectos generales) -----
    (r"^\s*OFICIO (?:CIRCULAR|MEDIANTE EL CUAL)", "OFICIO"),

    # ----- Decreto catch-all (al final, para no robar matches específicos) -----
    (r"^\s*DECRETO\b", "DECRETO"),
]


def classify_titulo(titulo: str) -> tuple[int, str, str]:
    """
    Devuelve (incluido, tipo_documento, motivo_exclusion).

    - incluido=1 si entra al sistema, 0 si no.
    - tipo_documento es la categoría asignada (LEY/REFORMA/etc.).
    - motivo_exclusion sólo se llena si incluido=0.
    """
    if not titulo:
        return 0, "DESCONOCIDO", "sin titulo"
    t = titulo.upper()

    # Excluir primero
    for pattern, tipo in RULES_EXCLUDE:
        if re.search(pattern, t):
            return 0, tipo, f"match exclude: {tipo}"
    # Incluir
    for pattern, tipo in RULES_INCLUDE:
        if re.search(pattern, t):
            return 1, tipo, ""

    # Por defecto, si no matchea ningún include, lo dejamos como BAJO (no incluido)
    return 0, "OTRO", "no matchea reglas de inclusion"


# ============================================================
# UPSERT DE NOTAS
# ============================================================
def upsert_nota(conn: sqlite3.Connection, n: dict, cod_diario: int, edicion: str) -> bool:
    """Inserta o actualiza una nota desde el JSON del API. Devuelve True si es nueva."""
    cod_nota = int(n.get("codNota", 0))
    if not cod_nota:
        return False
    fecha = to_iso_date(n.get("fecha", ""))
    titulo = fix_encoding(n.get("titulo", "") or "")
    existing = conn.execute("SELECT cod_nota FROM notas WHERE cod_nota=?", (cod_nota,)).fetchone()
    is_new = existing is None
    cols = {
        "cod_nota": cod_nota,
        "cod_diario": cod_diario,
        "fecha": fecha,
        "edicion": edicion,
        "seccion": n.get("codSeccion"),
        "pagina": n.get("pagina"),
        "titulo": titulo,
        "cod_orga_uno": n.get("codOrgaUno"),
        "nombre_cod_orga_uno": fix_encoding(n.get("nombreCodOrgaUno", "") or ""),
        "cod_orga_dos": fix_encoding(n.get("codOrgaDos", "") or ""),
        "tipo_nota_raw": n.get("tipoNota"),
        "existe_html": 1 if n.get("existeHtml") == "S" else 0,
        "existe_doc": 1 if n.get("existeDoc") == "S" else 0,
        "existe_imagen": 1 if n.get("existeImagen") == "S" else 0,
        "raw_json": json.dumps(n, ensure_ascii=False),
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    placeholders = ",".join("?" * len(cols))
    setters = ",".join(f"{k}=excluded.{k}" for k in cols if k != "cod_nota")
    conn.execute(
        f"INSERT INTO notas ({','.join(cols.keys())}) VALUES ({placeholders}) "
        f"ON CONFLICT(cod_nota) DO UPDATE SET {setters}",
        list(cols.values()),
    )
    return is_new


def upsert_edicion(conn: sqlite3.Connection, cod_diario: int, fecha: str, edicion: str,
                   num_notas: int):
    conn.execute(
        "INSERT INTO ediciones(cod_diario, fecha, edicion, num_notas, fetched_at) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(cod_diario) DO UPDATE SET "
        "fecha=excluded.fecha, edicion=excluded.edicion, "
        "num_notas=excluded.num_notas, fetched_at=excluded.fetched_at",
        (cod_diario, fecha, edicion, num_notas, dt.datetime.now().isoformat(timespec="seconds")),
    )


# ============================================================
# DESCARGA DE CONTENIDO HTML/PDF DE NOTAS
# ============================================================
def html_to_text(html: str) -> str:
    """Extrae texto plano de un HTML del DOF."""
    if not html:
        return ""
    # Quitar scripts y styles
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    # Reemplazar saltos
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p>", "\n\n", html, flags=re.I)
    html = re.sub(r"</div>", "\n", html, flags=re.I)
    # Quitar tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode entities
    html = html_mod.unescape(html)
    # Normalizar espacios
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n", "\n\n", html)
    return html.strip()


def download_nota_content(conn: sqlite3.Connection, cod_nota: int) -> bool:
    """Descarga el HTML de una nota y lo guarda en BD y archivo."""
    row = conn.execute(
        "SELECT cod_nota, fecha, edicion, titulo, incluido FROM notas WHERE cod_nota=?",
        (cod_nota,)
    ).fetchone()
    if not row or row["incluido"] != 1:
        return False
    html = fetch_nota_html(cod_nota)
    if not html:
        return False
    texto = html_to_text(html)
    # Guardar HTML a archivo
    fecha = row["fecha"] or "sin_fecha"
    try:
        year, month, day = fecha.split("-")
    except ValueError:
        year, month, day = "0000", "00", "00"
    dest_dir = DIR_PUBLICACIONES / year / month / fecha
    dest_dir.mkdir(parents=True, exist_ok=True)
    html_file = dest_dir / f"nota_{cod_nota}_{slugify(row['titulo'] or 'sin_titulo', 60)}.html"
    try:
        html_file.write_text(html, encoding="utf-8")
    except Exception as e:
        log.warning("No se pudo escribir HTML de %d: %s", cod_nota, e)
        html_file = None
    conn.execute(
        "UPDATE notas SET texto_plano=?, html_path=?, content_downloaded_at=? "
        "WHERE cod_nota=?",
        (texto, str(html_file) if html_file else None,
         dt.datetime.now().isoformat(timespec="seconds"), cod_nota),
    )
    return True


# ============================================================
# GENERACIÓN DE PDFs
# ============================================================
def download_edicion_pdf(year: int, month: int, day: int, edicion: str = "MAT") -> Path | None:
    """Descarga el PDF oficial completo de una edición del DOF.

    Usa el endpoint `abrirPDF.php` que sí retorna el PDF binario.
    El formato del archivo es DDMMYYYY-EDICION.pdf
    """
    # Construir nombre del archivo en el formato del DOF
    archivo = f"{day:02d}{month:02d}{year}-{edicion}.pdf"
    url = f"{LEGACY_BASE}/abrirPDF.php"
    status, body, _ = http_get(url, {
        "archivo": archivo,
        "anio": str(year),
    }, accept="application/pdf", timeout=120)
    if status != 200 or not body or len(body) < 1000:
        return None
    if body[:4] != b"%PDF":
        # No es PDF (posiblemente HTML de error o redirect)
        return None
    fecha_iso = f"{year}-{month:02d}-{day:02d}"
    dest_dir = DIR_PUBLICACIONES / str(year) / f"{month:02d}" / fecha_iso
    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = dest_dir / f"_edicion_{edicion}_completa.pdf"
    try:
        pdf_file.write_bytes(body)
        return pdf_file
    except Exception as e:
        log.warning("No se pudo guardar PDF de edición %s %s: %s",
                    fecha_iso, edicion, e)
    return None


def build_nota_pdf(nota_row: sqlite3.Row, texto_plano: str) -> Path | None:
    """Genera un PDF individual con plantilla DOF para una nota."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
        from reportlab.platypus import (
            BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
            Table, TableStyle, HRFlowable
        )
        from reportlab.platypus import SimpleDocTemplate
    except ImportError:
        log.warning("reportlab no instalado. Skipping PDF generation.")
        return None

    cod_nota = nota_row["cod_nota"]
    fecha = nota_row["fecha"] or ""
    titulo = nota_row["titulo"] or "Sin título"
    poder = nota_row["nombre_cod_orga_uno"] or ""
    dependencia = nota_row["cod_orga_dos"] or ""
    edicion = nota_row["edicion"] or "MAT"
    seccion = nota_row["seccion"] or ""
    tipo_doc = nota_row["tipo_documento"] or ""

    try:
        year, month, day = fecha.split("-") if fecha else ("0000", "00", "00")
    except ValueError:
        year, month, day = "0000", "00", "00"
    dest_dir = DIR_PUBLICACIONES / year / month / fecha
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"nota_{cod_nota}_{slugify(titulo, 60)}.pdf"

    # Colores DOF: verde-azul + dorado
    DOF_GREEN = colors.HexColor("#1B5E20")
    DOF_GOLD = colors.HexColor("#B8860B")
    DOF_DARK = colors.HexColor("#1F1F1F")

    def header_footer(canvas_obj, doc):
        canvas_obj.saveState()
        w, h = A4
        # Banner superior dorado
        canvas_obj.setFillColor(DOF_GOLD)
        canvas_obj.rect(0, h - 18 * mm, w, 18 * mm, stroke=0, fill=1)
        canvas_obj.setFillColor(colors.white)
        canvas_obj.setFont("Helvetica-Bold", 14)
        canvas_obj.drawRightString(w - 12 * mm, h - 11 * mm,
                                   "Diario Oficial de la Federación")
        canvas_obj.setFont("Helvetica-Bold", 8)
        canvas_obj.drawString(12 * mm, h - 10 * mm, "DOF")
        canvas_obj.setFont("Helvetica", 7)
        canvas_obj.drawString(12 * mm, h - 14 * mm, "Gobierno de México")
        # Línea verde debajo
        canvas_obj.setStrokeColor(DOF_GREEN)
        canvas_obj.setLineWidth(1.2)
        canvas_obj.line(12 * mm, h - 19 * mm, w - 12 * mm, h - 19 * mm)

        # Footer
        canvas_obj.setStrokeColor(colors.lightgrey)
        canvas_obj.setLineWidth(0.4)
        canvas_obj.line(12 * mm, 18 * mm, w - 12 * mm, 18 * mm)

        canvas_obj.setFillColor(colors.grey)
        canvas_obj.setFont("Helvetica", 7.5)
        url = f"https://www.dof.gob.mx/nota_detalle.php?codigo={cod_nota}"
        canvas_obj.drawString(12 * mm, 13 * mm, url)
        fecha_imp = dt.date.today().strftime("%d/%m/%Y")
        canvas_obj.drawCentredString(w / 2, 13 * mm, f"Pág. {canvas_obj.getPageNumber()}")
        canvas_obj.drawRightString(w - 12 * mm, 13 * mm,
                                   f"Fecha de impresión: {fecha_imp}")
        canvas_obj.setFont("Helvetica-Oblique", 6.5)
        canvas_obj.setFillColor(colors.HexColor("#999999"))
        canvas_obj.drawCentredString(
            w / 2, 8 * mm,
            "Documento elaborado con datos del API público del Diario Oficial de la Federación. "
            "Consulte la fuente oficial en el enlace indicado."
        )
        canvas_obj.restoreState()

    # Estilos
    rubro_st = ParagraphStyle("rubro", fontName="Times-Bold", fontSize=11,
                              leading=14, alignment=TA_CENTER,
                              spaceBefore=8, spaceAfter=8, textColor=DOF_DARK)
    body_st = ParagraphStyle("body", fontName="Times-Roman", fontSize=10,
                             leading=13, alignment=TA_JUSTIFY, spaceAfter=6)
    val_st = ParagraphStyle("val", fontName="Times-Roman", fontSize=9.5, leading=12)

    def _esc(s):
        if not s:
            return ""
        return re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)", "&amp;", s)

    # Frame
    frame = Frame(15 * mm, 22 * mm, A4[0] - 30 * mm, A4[1] - 46 * mm,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                  showBoundary=0)
    doc = BaseDocTemplate(str(out_path), pagesize=A4,
                          leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=24 * mm, bottomMargin=22 * mm,
                          title=f"DOF nota {cod_nota}",
                          author="DOF (reproducción)",
                          subject=titulo[:200])
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=header_footer)])

    story = []
    story.append(Paragraph("<b>Diario Oficial de la Federación</b>",
                           ParagraphStyle("h", fontName="Helvetica-Bold",
                                          fontSize=11, leading=14,
                                          spaceAfter=4)))
    story.append(Spacer(1, 2))

    # Tabla de metadatos
    meta = [
        [Paragraph(f"<b>Fecha:</b> {_esc(fecha)}", val_st),
         Paragraph(f"<b>Edición:</b> {_esc(edicion)}", val_st)],
        [Paragraph(f"<b>Sección:</b> {_esc(seccion)}", val_st),
         Paragraph(f"<b>Cod. nota:</b> {cod_nota}", val_st)],
        [Paragraph(f"<b>Poder:</b> {_esc(poder)}", val_st),
         Paragraph(f"<b>Tipo:</b> {_esc(tipo_doc)}", val_st)],
        [Paragraph(f"<b>Dependencia:</b> {_esc(dependencia)}", val_st), ""],
    ]
    t = Table(meta, colWidths=[90 * mm, 90 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(t)
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.4,
                            color=colors.lightgrey, spaceAfter=8))

    # Título (rubro)
    story.append(Paragraph(_esc(titulo), rubro_st))
    story.append(Spacer(1, 4))

    # Texto plano
    if texto_plano:
        # Limitar a párrafos razonables
        for parrafo in re.split(r"\n\s*\n", texto_plano):
            parrafo = parrafo.strip()
            if not parrafo or len(parrafo) < 3:
                continue
            try:
                story.append(Paragraph(_esc(parrafo), body_st))
            except Exception:
                # Si reportlab falla por chars exóticos, parsea el escape
                story.append(Paragraph(_esc(parrafo.encode("ascii", errors="ignore").decode()), body_st))
    else:
        story.append(Paragraph("<i>Contenido no descargado. Visite el enlace oficial.</i>",
                               body_st))

    try:
        doc.build(story)
        return out_path
    except Exception as e:
        log.warning("Falló generar PDF nota %d: %s", cod_nota, e)
        return None


# ============================================================
# COMANDOS
# ============================================================
def cmd_init():
    """Inicializa la BD y la estructura de carpetas."""
    conn = db_connect()
    log.info("BD inicializada en %s", DB_PATH)
    for d in (DIR_LEYES, DIR_PUBLICACIONES, DIR_TRATADOS):
        d.mkdir(parents=True, exist_ok=True)
    progress_set(conn, "init_done", dt.date.today().isoformat())
    conn.commit()
    log.info("Estructura de carpetas creada en %s", DROPBOX_BASE)
    log.info("Init completado.")
    conn.close()


def _process_month(conn: sqlite3.Connection, year: int, month: int) -> tuple[int, int]:
    """Procesa un mes: trae días → para cada día consulta WS_getDiarioFull en MAT/VES/EXT → guarda notas."""
    dias = fetch_dias_mes(year, month)
    if not dias:
        log.warning("Sin fechas para %d-%02d", year, month)
        return 0, 0

    total_notas = 0
    nuevas_notas = 0
    for d_str in dias:
        try:
            day = int(d_str)
        except (ValueError, TypeError):
            continue

        # Intentar las 3 ediciones posibles
        ediciones_dia = []
        for ed_label in ("MAT", "VES", "EXT"):
            full = fetch_edicion_full(year, month, day, ed_label)
            time.sleep(THROTTLE_MS / 1000)
            ejemplares = (full or {}).get("ejemplares") or []
            if not ejemplares:
                continue
            for ejemplar in ejemplares:
                notas_extraidas = extract_notas_from_ejemplar(ejemplar)
                if not notas_extraidas:
                    continue
                cod_diario = int(ejemplar.get("id", 0) or 0)
                edicion_normalizada = _normalizar_edicion(ejemplar.get("edicion", ed_label))
                fecha_iso = f"{year}-{month:02d}-{day:02d}"
                upsert_edicion(conn, cod_diario, fecha_iso,
                               edicion_normalizada, len(notas_extraidas))
                for n in notas_extraidas:
                    # n["fecha"] viene en formato dd/mm/yyyy; lo normalizamos
                    n["fecha"] = to_iso_date(n.get("fecha", "")) or fecha_iso
                    is_new = upsert_nota(conn, n, cod_diario, edicion_normalizada)
                    if is_new:
                        nuevas_notas += 1
                    total_notas += 1
                ediciones_dia.append((cod_diario, edicion_normalizada, len(notas_extraidas)))
                conn.commit()
        if ediciones_dia:
            ed_summary = ", ".join(f"{ed}={n}" for cd, ed, n in ediciones_dia)
            log.info("  %d-%02d-%02d → %s (acum %d nuevas)",
                     year, month, day, ed_summary, nuevas_notas)
    return total_notas, nuevas_notas


def cmd_update(meses_atras: int = 1):
    """Trae publicaciones de los últimos N meses (por default: el actual)."""
    conn = db_connect()
    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'update')",
        (dt.datetime.now().isoformat(timespec="seconds"),)
    ).lastrowid
    conn.commit()

    hoy = dt.date.today()
    total_notas = 0
    nuevas_notas = 0
    for offset in range(meses_atras):
        y, m = hoy.year, hoy.month - offset
        while m <= 0:
            m += 12
            y -= 1
        log.info("Update: procesando %d-%02d", y, m)
        t, n = _process_month(conn, y, m)
        total_notas += t
        nuevas_notas += n

    progress_set(conn, "last_update_at", dt.datetime.now().isoformat(timespec="seconds"))
    conn.execute(
        "UPDATE runs SET finished_at=?, items_attempted=?, items_ok=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), total_notas, nuevas_notas, run_id)
    )
    conn.commit()
    log.info("Update terminado: %d notas vistas, %d nuevas.", total_notas, nuevas_notas)
    conn.close()


def cmd_backfill(year_from: int | None = None, year_to: int | None = None,
                 months: int = DEFAULT_BACKFILL_MONTHS):
    """Descarga meses históricos. Si no se da rango, usa cursor + N meses."""
    conn = db_connect()
    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'backfill')",
        (dt.datetime.now().isoformat(timespec="seconds"),)
    ).lastrowid
    conn.commit()

    if year_from and year_to:
        # rango explícito
        ym_pairs = []
        y = year_to
        m = 12
        while y >= year_from:
            ym_pairs.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
    else:
        # cursor automático: continuar hacia atrás desde lo último que se cubrió
        cursor_str = progress_get(conn, "backfill_cursor_ym")
        if cursor_str:
            y, m = map(int, cursor_str.split("-"))
        else:
            hoy = dt.date.today()
            y, m = hoy.year, hoy.month
        ym_pairs = []
        for _ in range(months):
            ym_pairs.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
            if y < 2000:
                break

    log.info("Backfill: %d meses planeados, desde %d-%02d hasta %d-%02d",
             len(ym_pairs), ym_pairs[0][0], ym_pairs[0][1],
             ym_pairs[-1][0], ym_pairs[-1][1])

    total_notas = 0
    nuevas_notas = 0
    last_processed = None
    for y, m in ym_pairs:
        if y < 2000:
            break
        log.info("Backfill: procesando %d-%02d", y, m)
        t, n = _process_month(conn, y, m)
        total_notas += t
        nuevas_notas += n
        last_processed = (y, m)
        # actualizar cursor: el siguiente mes a procesar es uno antes
        prev_y, prev_m = y, m - 1
        if prev_m == 0:
            prev_m = 12
            prev_y -= 1
        progress_set(conn, "backfill_cursor_ym", f"{prev_y}-{prev_m:02d}")
        conn.commit()

    conn.execute(
        "UPDATE runs SET finished_at=?, items_attempted=?, items_ok=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), total_notas, nuevas_notas, run_id)
    )
    conn.commit()
    log.info("Backfill terminado: %d notas vistas, %d nuevas. Último mes: %s",
             total_notas, nuevas_notas, last_processed)
    conn.close()


def cmd_catchup():
    """Detecta días sin update y los recupera."""
    conn = db_connect()
    last = progress_get(conn, "last_update_at")
    if not last:
        log.info("Catchup: sin updates previos, corriendo update default.")
        conn.close()
        cmd_update(meses_atras=2)
        return
    try:
        last_dt = dt.datetime.fromisoformat(last)
        days_missed = (dt.datetime.now() - last_dt).days
    except Exception:
        days_missed = 0
    if days_missed <= 1:
        log.info("Catchup: sin días perdidos.")
        conn.close()
        return
    months_to_redo = min(max(1, days_missed // 15 + 1), 6)
    log.info("Catchup: %d días perdidos → re-procesando %d meses.", days_missed, months_to_redo)
    conn.close()
    cmd_update(meses_atras=months_to_redo)


def cmd_classify():
    """Aplica las reglas de clasificación a notas no clasificadas."""
    conn = db_connect()
    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'classify')",
        (dt.datetime.now().isoformat(timespec="seconds"),)
    ).lastrowid
    conn.commit()

    rows = conn.execute(
        "SELECT cod_nota, titulo FROM notas WHERE incluido IS NULL"
    ).fetchall()
    log.info("Classify: %d notas sin clasificar.", len(rows))
    incluidas = excluidas = 0
    for r in rows:
        included, tipo, motivo = classify_titulo(r["titulo"] or "")
        conn.execute(
            "UPDATE notas SET incluido=?, tipo_documento=?, motivo_exclusion=?, classified_at=? "
            "WHERE cod_nota=?",
            (included, tipo, motivo or None, dt.datetime.now().isoformat(timespec="seconds"),
             r["cod_nota"])
        )
        if included:
            incluidas += 1
        else:
            excluidas += 1
        if (incluidas + excluidas) % 500 == 0:
            conn.commit()
            log.info("  classify progreso: %d incluidas, %d excluidas",
                     incluidas, excluidas)
    conn.commit()
    conn.execute(
        "UPDATE runs SET finished_at=?, items_ok=?, items_attempted=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"),
         incluidas + excluidas, len(rows), run_id)
    )
    conn.commit()
    log.info("Classify terminado: %d incluidas, %d excluidas.", incluidas, excluidas)
    conn.close()


def cmd_download_contents(batch: int = 200, workers: int = DEFAULT_WORKERS):
    """Descarga HTML de las notas incluidas sin contenido."""
    conn = db_connect()
    rows = conn.execute(
        "SELECT cod_nota FROM notas "
        "WHERE incluido=1 AND existe_html=1 AND content_downloaded_at IS NULL "
        "ORDER BY fecha DESC "   # más recientes primero → mayor tasa de éxito (HTML disponible)
        "LIMIT ?",
        (batch,)
    ).fetchall()
    if not rows:
        log.info("download-contents: nada pendiente.")
        conn.close()
        return
    log.info("download-contents: %d notas pendientes en este lote", len(rows))
    cod_notas = [r["cod_nota"] for r in rows]

    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'download_contents')",
        (dt.datetime.now().isoformat(timespec="seconds"),)
    ).lastrowid
    conn.commit()

    ok = err = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_nota_html, cn): cn for cn in cod_notas}
        for i, fut in enumerate(as_completed(futures), 1):
            cn = futures[fut]
            try:
                html = fut.result()
            except Exception as e:
                log.warning("Excepción fetch nota %d: %s", cn, e)
                html = ""
            if html:
                texto = html_to_text(html)
                row = conn.execute(
                    "SELECT fecha, titulo FROM notas WHERE cod_nota=?", (cn,)
                ).fetchone()
                fecha = row["fecha"] or "0000-00-00"
                try:
                    year, month, _ = fecha.split("-")
                except ValueError:
                    year, month = "0000", "00"
                dest_dir = DIR_PUBLICACIONES / year / month / fecha
                dest_dir.mkdir(parents=True, exist_ok=True)
                html_file = dest_dir / f"nota_{cn}_{slugify(row['titulo'] or 'sin_titulo', 60)}.html"
                try:
                    html_file.write_text(html, encoding="utf-8")
                except Exception as e:
                    log.warning("Falló escribir HTML de %d: %s", cn, e)
                    html_file = None
                conn.execute(
                    "UPDATE notas SET texto_plano=?, html_path=?, content_downloaded_at=? "
                    "WHERE cod_nota=?",
                    (texto, str(html_file) if html_file else None,
                     dt.datetime.now().isoformat(timespec="seconds"), cn)
                )
                ok += 1
            else:
                # Sin HTML: marcar como intentada para que no bloquee la cola.
                # texto_plano=NULL indica "sin contenido disponible" (nota antigua/escaneada).
                conn.execute(
                    "UPDATE notas SET content_downloaded_at=? WHERE cod_nota=?",
                    (dt.datetime.now().isoformat(timespec="seconds") + " [sin-html]", cn)
                )
                err += 1
            if i % 25 == 0:
                conn.commit()
                log.info("  download-contents %d/%d: ok=%d err=%d", i, len(cod_notas), ok, err)
            time.sleep(THROTTLE_MS / 1000 / workers)
    conn.commit()
    conn.execute(
        "UPDATE runs SET finished_at=?, items_attempted=?, items_ok=?, items_error=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), len(cod_notas), ok, err, run_id)
    )
    conn.commit()
    log.info("download-contents terminado: %d ok, %d errores", ok, err)
    conn.close()


def cmd_report():
    """Estado general."""
    conn = db_connect()
    total_notas = conn.execute("SELECT COUNT(*) FROM notas").fetchone()[0]
    sin_clasif = conn.execute("SELECT COUNT(*) FROM notas WHERE incluido IS NULL").fetchone()[0]
    incluidas = conn.execute("SELECT COUNT(*) FROM notas WHERE incluido=1").fetchone()[0]
    excluidas = conn.execute("SELECT COUNT(*) FROM notas WHERE incluido=0").fetchone()[0]
    con_contenido = conn.execute(
        "SELECT COUNT(*) FROM notas WHERE incluido=1 AND content_downloaded_at IS NOT NULL"
    ).fetchone()[0]
    total_ediciones = conn.execute("SELECT COUNT(*) FROM ediciones").fetchone()[0]
    total_leyes = conn.execute("SELECT COUNT(*) FROM leyes").fetchone()[0]
    total_reformas = conn.execute("SELECT COUNT(*) FROM reformas").fetchone()[0]

    print()
    print("=" * 64)
    print("  DOF BIBLIOTECA — KAWIIL LEGAL")
    print("=" * 64)
    print(f"  Notas en BD:                  {total_notas:>10,}")
    print(f"    Sin clasificar:             {sin_clasif:>10,}")
    print(f"    Incluidas (entran):         {incluidas:>10,}")
    print(f"    Excluidas (no entran):      {excluidas:>10,}")
    print(f"    Con contenido descargado:   {con_contenido:>10,}")
    print(f"  Ediciones únicas:             {total_ediciones:>10,}")
    print(f"  Leyes federales:              {total_leyes:>10,}")
    print(f"  Reformas vinculadas:          {total_reformas:>10,}")
    print()
    print("  Por tipo de documento (incluidas):")
    for r in conn.execute(
        "SELECT tipo_documento, COUNT(*) n FROM notas WHERE incluido=1 "
        "GROUP BY tipo_documento ORDER BY n DESC"
    ):
        print(f"    {r['tipo_documento'] or '(null)':<30} {r['n']:>8,}")

    print()
    print("  Última corrida:")
    last = conn.execute(
        "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last:
        print(f"    {last['finished_at']} [{last['mode']}] "
              f"ok={last['items_ok']} err={last['items_error']}")
    print()
    conn.close()


def cmd_stats():
    """Análisis detallado."""
    conn = db_connect()
    total = conn.execute("SELECT COUNT(*) FROM notas").fetchone()[0]
    if total == 0:
        print("No hay notas en la BD. Corre 'update' o 'backfill' primero.")
        return

    print("\n=== ANÁLISIS DETALLADO DOF ===\n")
    print("Notas por año:")
    for r in conn.execute(
        "SELECT substr(fecha,1,4) yr, COUNT(*) n FROM notas "
        "WHERE fecha IS NOT NULL AND fecha != '' GROUP BY yr ORDER BY yr"
    ):
        bar = "█" * min(40, r["n"] // 200)
        print(f"  {r['yr']}  {r['n']:>7,}  {bar}")

    print("\nTop 10 organismos emisores (notas incluidas):")
    for r in conn.execute(
        "SELECT nombre_cod_orga_uno o1, cod_orga_dos o2, COUNT(*) n "
        "FROM notas WHERE incluido=1 "
        "GROUP BY o1, o2 ORDER BY n DESC LIMIT 10"
    ):
        print(f"  [{r['o1']:<20.20}] {r['o2']:<40.40} {r['n']:>6,}")

    print("\nÚltimas 10 corridas:")
    for r in conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 10"
    ):
        dur = "—"
        if r["finished_at"] and r["started_at"]:
            try:
                d1 = dt.datetime.fromisoformat(r["started_at"])
                d2 = dt.datetime.fromisoformat(r["finished_at"])
                dur = f"{(d2 - d1).total_seconds():.0f}s"
            except Exception:
                pass
        print(f"  #{r['id']:>3} {r['started_at']} [{r['mode']:<18}] "
              f"items={r['items_ok'] or 0}/{r['items_attempted'] or 0} ({dur})")
    print()
    conn.close()


def cmd_search(query: str, limit: int = 20):
    """Búsqueda full-text."""
    conn = db_connect()
    rows = conn.execute(
        """
        SELECT notas.cod_nota, notas.titulo, notas.fecha, notas.tipo_documento,
               notas.nombre_cod_orga_uno, notas.cod_orga_dos,
               snippet(notas_fts, 1, '«', '»', '…', 15) AS extracto
        FROM notas_fts JOIN notas ON notas.cod_nota = notas_fts.rowid
        WHERE notas_fts MATCH ?
          AND notas.incluido = 1
        ORDER BY rank
        LIMIT ?
        """,
        (query, limit)
    ).fetchall()
    print(f"\n{len(rows)} resultados para «{query}»:\n")
    for r in rows:
        print(f"[{r['cod_nota']}] {r['fecha']} | {r['tipo_documento']}")
        print(f"   {r['nombre_cod_orga_uno']} → {r['cod_orga_dos']}")
        print(f"   {(r['titulo'] or '')[:200]}")
        if r["extracto"]:
            print(f"   …{r['extracto']}…")
        print()
    conn.close()


def cmd_export():
    """Genera Excel con inventario."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        log.error("openpyxl no instalado. pip install openpyxl")
        return
    conn = db_connect()
    rows = list(conn.execute(
        "SELECT cod_nota, fecha, edicion, tipo_documento, nombre_cod_orga_uno, "
        "cod_orga_dos, titulo, incluido, pdf_path "
        "FROM notas ORDER BY fecha DESC, cod_nota"
    ))
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario DOF"
    headers = ["codNota", "Fecha", "Edición", "Tipo", "Poder/Órgano",
               "Dependencia", "Título", "Incluido", "PDF"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="2E75B6")
    for i, r in enumerate(rows, 2):
        for j, k in enumerate(["cod_nota", "fecha", "edicion", "tipo_documento",
                                "nombre_cod_orga_uno", "cod_orga_dos", "titulo",
                                "incluido", "pdf_path"], 1):
            ws.cell(row=i, column=j, value=r[k])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    out = BASE_DIR / f"Inventario_DOF_{dt.date.today():%Y%m%d}.xlsx"
    wb.save(out)
    log.info("Export generado: %s", out)
    conn.close()


def cmd_organizar():
    """Reorganiza PDFs en Dropbox según convención (futura)."""
    log.info("organizar: pendiente de implementar — placeholder.")


def cmd_download_pdfs(batch: int = 50, workers: int = 3):
    """Descarga PDFs oficiales completos de ediciones que aún no los tienen."""
    conn = db_connect()
    rows = conn.execute(
        "SELECT cod_diario, fecha, edicion FROM ediciones "
        "WHERE pdf_path IS NULL OR pdf_path = '' "
        "ORDER BY fecha DESC LIMIT ?",
        (batch,)
    ).fetchall()
    if not rows:
        log.info("download-pdfs: nada pendiente.")
        conn.close()
        return
    log.info("download-pdfs: %d ediciones pendientes en este lote", len(rows))

    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'download_pdfs')",
        (dt.datetime.now().isoformat(timespec="seconds"),)
    ).lastrowid
    conn.commit()

    ok = err = 0
    for r in rows:
        fecha = r["fecha"]
        edicion = r["edicion"] or "MAT"
        cod_diario = r["cod_diario"]
        try:
            year, month, day = fecha.split("-")
            year, month, day = int(year), int(month), int(day)
        except (ValueError, AttributeError):
            err += 1
            continue
        pdf_file = download_edicion_pdf(year, month, day, edicion)
        if pdf_file:
            conn.execute(
                "UPDATE ediciones SET pdf_path=? WHERE cod_diario=?",
                (str(pdf_file), cod_diario)
            )
            ok += 1
            log.info("  [%d/%d] PDF edición %s %s → %s",
                     ok + err, len(rows), fecha, edicion, pdf_file.name)
        else:
            err += 1
            log.warning("  No se pudo bajar PDF %s %s", fecha, edicion)
        conn.commit()
        time.sleep(THROTTLE_MS * 2 / 1000)

    conn.execute(
        "UPDATE runs SET finished_at=?, items_attempted=?, items_ok=?, items_error=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), len(rows), ok, err, run_id)
    )
    conn.commit()
    log.info("download-pdfs terminado: %d ok, %d errores", ok, err)
    conn.close()


def cmd_generate_pdfs(batch: int = 500):
    """Genera PDFs individuales con plantilla DOF para notas incluidas con contenido."""
    conn = db_connect()
    rows = conn.execute(
        "SELECT cod_nota, cod_diario, fecha, edicion, seccion, titulo, "
        "cod_orga_uno, nombre_cod_orga_uno, cod_orga_dos, tipo_documento, "
        "texto_plano, pdf_path FROM notas "
        "WHERE incluido=1 "
        "  AND content_downloaded_at IS NOT NULL "
        "  AND (pdf_path IS NULL OR pdf_path = '') "
        "LIMIT ?",
        (batch,)
    ).fetchall()
    if not rows:
        log.info("generate-pdfs: nada pendiente.")
        conn.close()
        return
    log.info("generate-pdfs: %d notas pendientes de PDF", len(rows))

    run_id = conn.execute(
        "INSERT INTO runs(started_at, mode) VALUES(?, 'generate_pdfs')",
        (dt.datetime.now().isoformat(timespec="seconds"),)
    ).lastrowid
    conn.commit()

    ok = err = 0
    for i, row in enumerate(rows, 1):
        pdf_file = build_nota_pdf(row, row["texto_plano"] or "")
        if pdf_file:
            conn.execute(
                "UPDATE notas SET pdf_path=? WHERE cod_nota=?",
                (str(pdf_file), row["cod_nota"])
            )
            ok += 1
        else:
            err += 1
        if i % 50 == 0:
            conn.commit()
            log.info("  [%d/%d] ok=%d err=%d", i, len(rows), ok, err)
    conn.commit()
    conn.execute(
        "UPDATE runs SET finished_at=?, items_attempted=?, items_ok=?, items_error=? WHERE id=?",
        (dt.datetime.now().isoformat(timespec="seconds"), len(rows), ok, err, run_id)
    )
    conn.commit()
    log.info("generate-pdfs terminado: %d ok, %d errores", ok, err)
    conn.close()


# ============================================================
# LEYES FEDERALES (placeholder — se implementa en Fase 4)
# ============================================================
def cmd_leyes_init():
    log.info("leyes-init: pendiente. Se conectará a diputados.gob.mx en Fase 4.")


def cmd_leyes_update():
    log.info("leyes-update: pendiente. Se conectará a diputados.gob.mx en Fase 4.")


def cmd_leyes_link():
    log.info("leyes-link: pendiente. Cruzará leyes con reformas DOF en Fase 5.")


# ============================================================
# MAIN
# ============================================================
def main():
    p = argparse.ArgumentParser(description="DOF Biblioteca — Kawiil Legal")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    up = sub.add_parser("update")
    up.add_argument("--meses", type=int, default=1)

    bf = sub.add_parser("backfill")
    bf.add_argument("--year-from", type=int, default=None)
    bf.add_argument("--year-to", type=int, default=None)
    bf.add_argument("--months", type=int, default=DEFAULT_BACKFILL_MONTHS)

    sub.add_parser("classify")

    dc = sub.add_parser("download-contents")
    dc.add_argument("--batch", type=int, default=200)
    dc.add_argument("--workers", type=int, default=DEFAULT_WORKERS)

    dpdfs = sub.add_parser("download-pdfs")
    dpdfs.add_argument("--batch", type=int, default=50)
    dpdfs.add_argument("--workers", type=int, default=3)

    gpdfs = sub.add_parser("generate-pdfs")
    gpdfs.add_argument("--batch", type=int, default=500)

    sub.add_parser("catchup")
    sub.add_parser("leyes-init")
    sub.add_parser("leyes-update")
    sub.add_parser("leyes-link")
    sub.add_parser("report")
    sub.add_parser("stats")
    sub.add_parser("export")
    sub.add_parser("organizar")

    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)

    args = p.parse_args()
    {
        "init": cmd_init,
        "update": lambda: cmd_update(meses_atras=args.meses),
        "backfill": lambda: cmd_backfill(year_from=args.year_from,
                                          year_to=args.year_to,
                                          months=args.months),
        "classify": cmd_classify,
        "download-contents": lambda: cmd_download_contents(batch=args.batch,
                                                           workers=args.workers),
        "download-pdfs": lambda: cmd_download_pdfs(batch=args.batch,
                                                    workers=args.workers),
        "generate-pdfs": lambda: cmd_generate_pdfs(batch=args.batch),
        "catchup": cmd_catchup,
        "leyes-init": cmd_leyes_init,
        "leyes-update": cmd_leyes_update,
        "leyes-link": cmd_leyes_link,
        "report": cmd_report,
        "stats": cmd_stats,
        "export": cmd_export,
        "organizar": cmd_organizar,
        "search": lambda: cmd_search(args.query, limit=args.limit),
    }[args.cmd]()


if __name__ == "__main__":
    main()
