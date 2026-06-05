#!/usr/bin/env python3
"""
Cerebro Kawiil — MCP Server  (token-efficient edition)
Cerebro compartido Louis ↔ Cowork. Corre en Hetzner, se conecta como
connector en la app de Claude (SSE).

Principio de costo: resumen primero, detalle solo si se pide.
  - agenda_pendientes / agenda_snapshot  →  lean por defecto
  - legal_buscar                         →  5 resultados, 250 chars c/u
  - entregable_estado                    →  solo metadatos por defecto
  - Todo read está cacheado 60 s en RAM  →  evita re-lecturas en una sesión

Variables de entorno:
  CEREBRO_KAWIIL_TOKEN   Bearer token para autenticar el connector
  CEREBRO_PORT           Puerto interno (default: 4040)
  OPENCLAW_SPACES        Ruta a la memoria de Louis (default: /opt/openclaw/spaces/general)
  ENTREGABLES_PATH       Almacén compartido  (default: /opt/openclaw/entregables)
  SJF_DB_PATH            SQLite SJF          (default: /opt/openclaw/legal/sjf.db)
  DOF_DB_PATH            SQLite DOF          (default: /opt/openclaw/legal/dof.db)
  CEREBRO_CACHE_TTL      Segundos de cache   (default: 60)
"""

import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

# ── Configuración ──────────────────────────────────────────────────────────
BEARER_TOKEN     = os.environ.get("CEREBRO_KAWIIL_TOKEN", "")
PORT             = int(os.environ.get("CEREBRO_PORT", "4040"))
SPACES_PATH      = Path(os.environ.get("OPENCLAW_SPACES", "/opt/openclaw/spaces/general"))
ENTREGABLES_PATH = Path(os.environ.get("ENTREGABLES_PATH", "/opt/openclaw/entregables"))
SJF_DB_PATH      = Path(os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf.db"))
DOF_DB_PATH      = Path(os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof.db"))
CACHE_TTL        = int(os.environ.get("CEREBRO_CACHE_TTL", "60"))

ESTADOS_VALIDOS = {"borrador", "listo", "en_vobo", "aprobado", "archivado"}
ICONO_ESTADO    = {"borrador": "📝", "listo": "✅", "en_vobo": "🔄",
                   "aprobado": "✔️", "archivado": "📦"}

# ── Cache en RAM (TTL) ─────────────────────────────────────────────────────
# Evita re-leer el mismo archivo en llamadas consecutivas de la misma sesión.
_cache: dict[str, tuple[float, str]] = {}


def _read_cached(path: Path) -> str:
    key = str(path)
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        text = ""
    _cache[key] = (now, text)
    return text


def _invalidate(path: Path) -> None:
    _cache.pop(str(path), None)


# ── MCP Server ─────────────────────────────────────────────────────────────
mcp = FastMCP(
    "Cerebro Kawiil",
    instructions=(
        "Cerebro compartido Louis ↔ Cowork.\n"
        "REGLA DE COSTO: empezar siempre por las herramientas compactas:\n"
        "  1. cerebro_estado()        → resumen de todo (poca tokens)\n"
        "  2. agenda_pendientes()     → solo ítems sin hacer (poca tokens)\n"
        "  3. entregables_listar()    → índice de entregables (poca tokens)\n"
        "Solo escalar a herramientas de detalle si el usuario lo necesita:\n"
        "  4. agenda_snapshot()       → contexto operativo completo\n"
        "  5. entregable_estado(completo=True)  → cuerpo del documento\n"
        "  6. memoria_leer()          → archivo de memoria completo\n"
        "  7. legal_buscar()          → acervo SJF/DOF\n"
        "Los writes (registrar, marcar_hecho, dispatch) tienen respuesta corta.\n"
        "Principio: datos duros, sin interpretación."
    ),
)


# ── Auth middleware ────────────────────────────────────────────────────────
class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/"):
            return await call_next(request)
        if BEARER_TOKEN:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != BEARER_TOKEN:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


# ══════════════════════════════════════════════════════════════════════════
# NIVEL 1 — HERRAMIENTAS COMPACTAS (usar siempre primero)
# ══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def cerebro_estado() -> str:
    """
    [COMPACTO] Estado general: memoria disponible, acervo legal, conteo de
    entregables por estado. Punto de partida de cualquier sesión. ~100 tokens.
    """
    lineas = [f"Cerebro Kawiil — {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    # Memoria: solo indica qué archivos existen y su tamaño
    partes_mem = []
    for nombre in ("AGENDA", "IMPORTANT", "JOURNAL", "PROJECTS", "PEOPLE"):
        f = SPACES_PATH / f"{nombre}.md"
        if f.exists():
            partes_mem.append(f"{nombre}({f.stat().st_size//1024}KB)")
        else:
            partes_mem.append(f"{nombre}(❌)")
    lineas.append("Memoria: " + " | ".join(partes_mem))

    # Legal: solo disponibilidad
    legal_partes = []
    for nombre, db_path in [("SJF", SJF_DB_PATH), ("DOF", DOF_DB_PATH)]:
        if db_path.exists():
            legal_partes.append(f"{nombre}✅({db_path.stat().st_size//1024//1024}MB)")
        else:
            legal_partes.append(f"{nombre}⚠️")
    lineas.append("Legal: " + " | ".join(legal_partes))

    # Entregables: conteo por estado
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    conteo: dict[str, int] = {}
    for f in ENTREGABLES_PATH.glob("*.md"):
        if not f.name.startswith("_"):
            meta = _parsear_fm(f)
            e = meta.get("estado", "?")
            conteo[e] = conteo.get(e, 0) + 1
    briefs_path = ENTREGABLES_PATH / "_briefs"
    n_briefs = len(list(briefs_path.glob("*.md"))) if briefs_path.exists() else 0

    if conteo:
        conteo_str = " | ".join(
            f"{ICONO_ESTADO.get(k,'❓')}{k}:{v}" for k, v in sorted(conteo.items())
        )
    else:
        conteo_str = "(vacío)"
    lineas.append(f"Entregables: {conteo_str} | briefs_pendientes:{n_briefs}")

    return "\n".join(lineas)


@mcp.tool()
def agenda_pendientes() -> str:
    """
    [COMPACTO] Solo los ítems sin hacer (- [ ]) de la AGENDA, máximo 20.
    Usar en lugar de agenda_snapshot() cuando solo se necesita la lista de tareas.
    ~200 tokens típico.
    """
    texto = _read_cached(SPACES_PATH / "AGENDA.md")
    if not texto:
        return "AGENDA.md no disponible."

    items = [l.strip() for l in texto.splitlines() if re.match(r"^\s*-\s*\[\s*\]\s+", l)]
    if not items:
        return "Sin pendientes abiertos en AGENDA."
    return f"Pendientes ({len(items)}):\n" + "\n".join(items[:20])


@mcp.tool()
def entregables_listar(estado: Optional[str] = None, cliente: Optional[str] = None) -> str:
    """
    [COMPACTO] Lista entregables: título, cliente, estado, fecha. Una línea por ítem.
    Filtrar por estado (borrador/listo/en_vobo/aprobado/archivado) y/o cliente.
    """
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    entregables = []
    for f in sorted(ENTREGABLES_PATH.glob("*.md")):
        if f.name.startswith("_"):
            continue
        meta = _parsear_fm(f)
        if estado and meta.get("estado", "").lower() != estado.lower():
            continue
        if cliente and cliente.lower() not in meta.get("cliente", "").lower():
            continue
        entregables.append(meta)

    briefs_path = ENTREGABLES_PATH / "_briefs"
    n_briefs = len(list(briefs_path.glob("*.md"))) if briefs_path.exists() else 0

    if not entregables:
        filtros = [f"estado={estado}" if estado else "", f"cliente={cliente}" if cliente else ""]
        filtros = [f for f in filtros if f]
        return f"Sin entregables{(' ('+', '.join(filtros)+')') if filtros else ''}. Briefs pendientes: {n_briefs}"

    lineas = []
    for e in entregables:
        icono = ICONO_ESTADO.get(e.get("estado", ""), "❓")
        lineas.append(
            f"{icono} {e.get('titulo', e['archivo'])} | "
            f"{e.get('cliente','?')} | {e.get('estado','?')} | {e.get('fecha_actualizacion','?')}"
        )
    lineas.append(f"Total: {len(entregables)} | Briefs pendientes: {n_briefs}")
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════════════
# NIVEL 2 — HERRAMIENTAS DE DETALLE (escalar solo si se necesita)
# ══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def agenda_snapshot() -> str:
    """
    [DETALLE] Contexto operativo de Louis: pendientes de HOY, sección URGENTE,
    último IMPORTANT crítico y última entrada de JOURNAL. Truncado inteligente.
    Usar solo cuando agenda_pendientes() no es suficiente. ~500 tokens típico.
    """
    agenda    = _read_cached(SPACES_PATH / "AGENDA.md")
    important = _read_cached(SPACES_PATH / "IMPORTANT.md")
    journal   = _read_cached(SPACES_PATH / "JOURNAL.md")

    partes = [f"Snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    # HOY
    hoy = _seccion_md(agenda, "Para HOY") or _seccion_md(agenda, "Para hoy")
    if hoy:
        partes.append(f"\n## Para HOY\n{hoy[:1200]}")
    else:
        abiertos = [l.strip() for l in agenda.splitlines() if re.match(r"^\s*-\s*\[\s*\]\s+", l)]
        partes.append(f"\n## Pendientes abiertos\n" + "\n".join(abiertos[:12]))

    # URGENTE
    urgente = _seccion_md(agenda, "URGENTE") or _seccion_md(agenda, "Urgente")
    if urgente:
        partes.append(f"\n## URGENTE\n{urgente[:600]}")

    # IMPORTANT — solo sección CRÍTICO
    critico = _seccion_md(important, "CRÍTICO") or _seccion_md(important, "CRITICO")
    partes.append(f"\n## IMPORTANT-CRÍTICO\n{critico[:600] if critico else '(vacío)'}")

    # JOURNAL — última entrada
    partes.append(f"\n## JOURNAL (última entrada)\n{_ultima_entrada_journal(journal, 400)}")

    return "\n".join(partes)


@mcp.tool()
def entregable_estado(nombre_o_titulo: str, completo: bool = False) -> str:
    """
    [DETALLE] Estado de un entregable específico.
    completo=False (default): solo frontmatter (~80 tokens).
    completo=True: frontmatter + cuerpo del documento completo.
    """
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    termino = nombre_o_titulo.lower()

    for f in ENTREGABLES_PATH.glob("*.md"):
        if f.name.startswith("_"):
            continue
        meta = _parsear_fm(f)
        if termino in f.stem.lower() or termino in meta.get("titulo", "").lower():
            if completo:
                return _read_cached(f)
            # Solo frontmatter (más barato)
            lineas = [f"Entregable: {meta.get('titulo', f.stem)}"]
            for campo in ("cliente", "estado", "vobo", "responsable",
                          "fecha_creacion", "fecha_actualizacion"):
                if campo in meta:
                    lineas.append(f"  {campo}: {meta[campo]}")
            return "\n".join(lineas)

    return f"Entregable «{nombre_o_titulo}» no encontrado."


@mcp.tool()
def memoria_leer(archivo: str = "IMPORTANT", max_chars: int = 2000) -> str:
    """
    [DETALLE] Lee un archivo de memoria de Louis (IMPORTANT, JOURNAL, LEARNINGS,
    PEOPLE, PROJECTS). max_chars limita la respuesta (default 2000, max 8000).
    Solo usar cuando agenda_snapshot() no alcanza.
    """
    PERMITIDOS = {"IMPORTANT", "JOURNAL", "LEARNINGS", "PEOPLE", "PROJECTS"}
    nombre = archivo.upper()
    if nombre not in PERMITIDOS:
        return f"Archivo no permitido. Opciones: {', '.join(sorted(PERMITIDOS))}"

    max_chars = min(int(max_chars), 8000)
    texto = _read_cached(SPACES_PATH / f"{nombre}.md")
    if not texto:
        return f"{nombre}.md no encontrado."

    if len(texto) <= max_chars:
        return texto
    return texto[:max_chars] + f"\n…[truncado — {len(texto)-max_chars} chars más; aumenta max_chars si se necesita]"


@mcp.tool()
def legal_buscar(termino: str, fuente: str = "ambas", limite: int = 5,
                 excerpt_chars: int = 250) -> str:
    """
    [DETALLE] Busca en el acervo legal (SJF y/o DOF).
    Defaults conservadores: 5 resultados, 250 chars por resultado.
    Aumentar solo si el usuario necesita más contexto legal.
    fuente: 'sjf' | 'dof' | 'ambas'
    """
    limite      = min(int(limite), 20)
    excerpt_chars = min(int(excerpt_chars), 1200)
    resultados  = []

    if fuente in ("sjf", "ambas") and SJF_DB_PATH.exists():
        tablas = _tablas_db(SJF_DB_PATH)
        if "tesis" in tablas:
            filas = _query_db(
                SJF_DB_PATH,
                "SELECT rubro, texto, fecha FROM tesis WHERE texto LIKE ? OR rubro LIKE ? LIMIT ?",
                (f"%{termino}%", f"%{termino}%", limite),
            )
            for f in filas:
                resultados.append(
                    f"[SJF/{f.get('fecha','')}] {f.get('rubro','')}\n"
                    f"{(f.get('texto') or '')[:excerpt_chars]}…"
                )
        elif tablas:
            cols = [c["name"] for c in _query_db(SJF_DB_PATH, f"PRAGMA table_info({tablas[0]})")]
            resultados.append(f"[SJF] tabla='{tablas[0]}' cols={cols} (esquema no estándar)")

    if fuente in ("dof", "ambas") and DOF_DB_PATH.exists():
        tablas = _tablas_db(DOF_DB_PATH)
        if "publicaciones" in tablas:
            filas = _query_db(
                DOF_DB_PATH,
                "SELECT titulo, contenido, fecha_publicacion FROM publicaciones "
                "WHERE contenido LIKE ? OR titulo LIKE ? LIMIT ?",
                (f"%{termino}%", f"%{termino}%", limite),
            )
            for f in filas:
                resultados.append(
                    f"[DOF/{f.get('fecha_publicacion','')}] {f.get('titulo','')}\n"
                    f"{(f.get('contenido') or '')[:excerpt_chars]}…"
                )
        elif tablas:
            cols = [c["name"] for c in _query_db(DOF_DB_PATH, f"PRAGMA table_info({tablas[0]})")]
            resultados.append(f"[DOF] tabla='{tablas[0]}' cols={cols} (esquema no estándar)")

    if not resultados:
        disp = [n for n, p in [("SJF", SJF_DB_PATH), ("DOF", DOF_DB_PATH)] if p.exists()]
        if not disp:
            return "Acervo legal no disponible aún. Usar legal_estado() para diagnóstico."
        return f"Sin resultados para «{termino}» en {', '.join(disp)}."

    return f"\n{'─'*50}\n".join(resultados)


@mcp.tool()
def legal_estado() -> str:
    """[COMPACTO] Disponibilidad y tamaño de las bases de datos legales SJF y DOF."""
    partes = []
    for nombre, db_path in [("SJF", SJF_DB_PATH), ("DOF", DOF_DB_PATH)]:
        if not db_path.exists():
            partes.append(f"{nombre}:⚠️no_disponible")
        else:
            tablas = _tablas_db(db_path)
            partes.append(f"{nombre}:✅{db_path.stat().st_size//1024//1024}MB tablas={','.join(tablas)}")
    return " | ".join(partes)


# ══════════════════════════════════════════════════════════════════════════
# WRITES — respuesta corta siempre
# ══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def agenda_marcar_hecho(patron: str) -> str:
    """Marca como hecho (- [x]) el primer pendiente que contenga `patron`."""
    f = SPACES_PATH / "AGENDA.md"
    if not f.exists():
        return "AGENDA.md no encontrada."
    content = f.read_text(encoding="utf-8")
    lineas, cambiado, original = [], False, ""
    for l in content.splitlines():
        if not cambiado and patron.lower() in l.lower() and "- [ ]" in l:
            original = l.strip()
            l = l.replace("- [ ]", "- [x]", 1)
            cambiado = True
        lineas.append(l)
    if not cambiado:
        return f"Sin pendiente abierto con: «{patron}»"
    f.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    _invalidate(f)
    return f"✅ Marcado: {original}"


@mcp.tool()
def agenda_editar(patron: str, nuevo_texto: str) -> str:
    """Reemplaza la primera línea de AGENDA.md que contenga `patron` por `nuevo_texto`."""
    f = SPACES_PATH / "AGENDA.md"
    if not f.exists():
        return "AGENDA.md no encontrada."
    content = f.read_text(encoding="utf-8")
    lineas, cambiado = [], False
    for l in content.splitlines():
        if not cambiado and patron.lower() in l.lower():
            lineas.append(nuevo_texto)
            cambiado = True
        else:
            lineas.append(l)
    if not cambiado:
        return f"Sin línea con: «{patron}»"
    f.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    _invalidate(f)
    return "✅ AGENDA actualizada."


@mcp.tool()
def entregable_registrar(
    titulo: str,
    cliente: str,
    estado: str = "borrador",
    descripcion: str = "",
    responsable: str = "Cowork",
) -> str:
    """
    Registra un entregable producido en Cowork.
    estado: borrador | listo | en_vobo | aprobado | archivado
    """
    estado = estado.lower()
    if estado not in ESTADOS_VALIDOS:
        return f"Estado inválido. Opciones: {', '.join(sorted(ESTADOS_VALIDOS))}"
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    fecha    = datetime.now().strftime("%Y-%m-%d")
    filepath = ENTREGABLES_PATH / f"{fecha}-{_slug(titulo)}.md"
    if filepath.exists():
        return f"Ya existe: {filepath.name}. Usa entregable_actualizar_estado()."
    content = (
        f"---\ntitulo: {titulo}\ncliente: {cliente}\nestado: {estado}\n"
        f"responsable: {responsable}\nfecha_creacion: {fecha}\n"
        f"fecha_actualizacion: {fecha}\nvobo: pendiente\n---\n\n"
        f"# {titulo}\n\n"
        f"**Cliente:** {cliente} | **Estado:** {estado} | **Responsable:** {responsable}\n\n"
        f"## Descripción\n\n{descripcion or 'Sin descripción.'}\n\n"
        f"## Historial\n\n- {fecha} — Registrado como `{estado}` por {responsable}\n"
    )
    filepath.write_text(content, encoding="utf-8")
    return f"✅ {filepath.name} | estado:{estado}"


@mcp.tool()
def entregable_actualizar_estado(
    nombre_o_titulo: str,
    nuevo_estado: str,
    nota: str = "",
) -> str:
    """Actualiza el estado de un entregable. Añade entrada al historial."""
    nuevo_estado = nuevo_estado.lower()
    if nuevo_estado not in ESTADOS_VALIDOS:
        return f"Estado inválido. Opciones: {', '.join(sorted(ESTADOS_VALIDOS))}"
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    termino = nombre_o_titulo.lower()
    fecha   = datetime.now().strftime("%Y-%m-%d")
    for f in ENTREGABLES_PATH.glob("*.md"):
        if f.name.startswith("_"):
            continue
        meta = _parsear_fm(f)
        if termino in f.stem.lower() or termino in meta.get("titulo", "").lower():
            content = f.read_text(encoding="utf-8")
            content = re.sub(r"(?m)^estado:.*$", f"estado: {nuevo_estado}", content)
            content = re.sub(r"(?m)^fecha_actualizacion:.*$",
                             f"fecha_actualizacion: {fecha}", content)
            entrada = f"- {fecha} — `{nuevo_estado}`" + (f": {nota}" if nota else "")
            f.write_text(content.rstrip() + f"\n{entrada}\n", encoding="utf-8")
            _invalidate(f)
            return f"✅ «{meta.get('titulo', f.stem)}» → {nuevo_estado}"
    return f"No encontrado: «{nombre_o_titulo}»"


@mcp.tool()
def dispatch_preparar_brief(
    tarea: str,
    cliente: str,
    insumos: str = "",
    urgencia: str = "normal",
    contexto_adicional: str = "",
) -> str:
    """
    Louis prepara un brief para que Cowork produzca un entregable.
    Se guarda en _briefs/. Devuelve confirmación corta.
    urgencia: baja | normal | alta | urgente
    """
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    briefs_path = ENTREGABLES_PATH / "_briefs"
    briefs_path.mkdir(exist_ok=True)

    ahora    = datetime.now()
    fecha    = ahora.strftime("%Y-%m-%d")
    hora     = ahora.strftime("%H:%M")
    filepath = briefs_path / f"{fecha}-brief-{_slug(tarea)}.md"

    # Contexto de AGENDA (solo líneas relevantes)
    agenda_txt  = _read_cached(SPACES_PATH / "AGENDA.md")
    kws         = [tarea.lower()[:20], cliente.lower()[:15]]
    relevantes  = [l for l in agenda_txt.splitlines()
                   if any(k in l.lower() for k in kws)][:8]
    agenda_ctx  = "\n".join(relevantes) if relevantes else "(sin entradas relevantes)"

    # Entregables previos del cliente (solo títulos)
    previos = []
    for f in ENTREGABLES_PATH.glob("*.md"):
        if not f.name.startswith("_"):
            m = _parsear_fm(f)
            if cliente.lower() in m.get("cliente", "").lower():
                previos.append(
                    f"  {ICONO_ESTADO.get(m.get('estado',''),'❓')} "
                    f"{m.get('titulo', f.stem)} ({m.get('estado','?')})"
                )
    previos_txt = "\n".join(previos) if previos else "  (ninguno)"

    content = (
        f"---\ntipo: brief_dispatch\ntarea: {tarea}\ncliente: {cliente}\n"
        f"urgencia: {urgencia}\nestado: pendiente\npreparado_por: Louis\n"
        f"fecha_creacion: {fecha} {hora}\n---\n\n"
        f"# Brief: {tarea}\n\n"
        f"Cliente: {cliente} | Urgencia: {urgencia} | Fecha: {fecha} {hora}\n\n"
        f"## Tarea\n{tarea}\n\n"
        f"## Insumos\n{insumos or 'Consultar acervo legal y memoria según aplique.'}\n\n"
        f"## Entregables previos del cliente\n{previos_txt}\n\n"
        f"## AGENDA relevante\n{agenda_ctx}\n\n"
        f"## Contexto adicional\n{contexto_adicional or '(ninguno)'}\n\n"
        f"## Pasos para Cowork\n"
        f"1. Revisar insumos y entregables previos\n"
        f"2. `legal_buscar()` si aplica\n"
        f"3. Producir entregable\n"
        f"4. `entregable_registrar()` + `entregable_actualizar_estado('listo')`\n"
        f"5. Marcar VoBo si lo requiere dirección\n"
    )
    filepath.write_text(content, encoding="utf-8")
    return f"✅ Brief guardado: _briefs/{filepath.name} | urgencia:{urgencia} | previos:{len(previos)}"


# ══════════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════════════════

def _parsear_fm(path: Path) -> dict:
    meta: dict = {"archivo": path.name, "titulo": path.stem, "estado": "desconocido"}
    try:
        content = _read_cached(path) if path.stat().st_mtime > 0 else ""
        m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
    except Exception:
        pass
    return meta


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")[:60]


def _seccion_md(texto: str, titulo: str) -> str:
    """Extrae el bloque de una sección ## de un Markdown."""
    m = re.search(rf"(?im)^##\s+{re.escape(titulo)}\b[^\n]*\n([\s\S]*?)(?=^##\s+|\Z)", texto)
    return m.group(1).strip() if m else ""


def _ultima_entrada_journal(texto: str, max_chars: int = 400) -> str:
    if not texto.strip():
        return "(sin entradas)"
    partes = re.split(r"(?m)^##\s+(\d{4}-\d{2}-\d{2})", texto)
    if len(partes) >= 3:
        return f"## {partes[-2]}\n{partes[-1].strip()[:max_chars]}"
    return texto.strip()[:max_chars]


def _query_db(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _tablas_db(db_path: Path) -> list[str]:
    rows = _query_db(db_path, "SELECT name FROM sqlite_master WHERE type='table'")
    return [r["name"] for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# SERVIDOR
# ══════════════════════════════════════════════════════════════════════════

async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "cerebro-kawiil", "version": "1.1"})


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", health_endpoint),
            Mount("/", app=mcp.sse_app()),
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
    )


if __name__ == "__main__":
    if not BEARER_TOKEN:
        print("⚠️  CEREBRO_KAWIIL_TOKEN no configurado — sin autenticación.", file=sys.stderr)
    print(f"🧠 Cerebro Kawiil v1.1 — 127.0.0.1:{PORT}")
    print(f"   Memoria    : {SPACES_PATH}")
    print(f"   Entregables: {ENTREGABLES_PATH}")
    print(f"   Legal      : {SJF_DB_PATH.parent}")
    print(f"   Cache TTL  : {CACHE_TTL}s")
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="info")
