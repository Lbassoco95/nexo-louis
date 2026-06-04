#!/usr/bin/env python3
"""
Cerebro Kawiil — MCP Server
Cerebro compartido Louis ↔ Cowork. Corre en Hetzner, se conecta como
connector en la app de Claude (SSE/Streamable-HTTP).

Variables de entorno:
  CEREBRO_KAWIIL_TOKEN   Bearer token para autenticar el connector
  CEREBRO_PORT           Puerto interno (default: 4040)
  OPENCLAW_SPACES        Ruta a la memoria de Louis (default: /opt/openclaw/spaces/general)
  ENTREGABLES_PATH       Ruta al almacén compartido  (default: /opt/openclaw/entregables)
  SJF_DB_PATH            SQLite del acervo SJF       (default: /opt/openclaw/legal/sjf.db)
  DOF_DB_PATH            SQLite del acervo DOF       (default: /opt/openclaw/legal/dof.db)
"""

import os
import re
import sqlite3
import sys
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

# ── Configuración ───────────────────────────────────────────────────────────
BEARER_TOKEN   = os.environ.get("CEREBRO_KAWIIL_TOKEN", "")
PORT           = int(os.environ.get("CEREBRO_PORT", "4040"))
SPACES_PATH    = Path(os.environ.get("OPENCLAW_SPACES", "/opt/openclaw/spaces/general"))
ENTREGABLES_PATH = Path(os.environ.get("ENTREGABLES_PATH", "/opt/openclaw/entregables"))
SJF_DB_PATH    = Path(os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf.db"))
DOF_DB_PATH    = Path(os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof.db"))

ESTADOS_VALIDOS = {"borrador", "listo", "en_vobo", "aprobado", "archivado"}

# ── MCP Server ───────────────────────────────────────────────────────────────
mcp = FastMCP(
    "Cerebro Kawiil",
    instructions=(
        "Cerebro compartido Louis ↔ Cowork. Expone la memoria de Louis (AGENDA, "
        "IMPORTANT, JOURNAL, PEOPLE, PROJECTS), el acervo legal (SJF + DOF) y el "
        "almacén de entregables de Cowork. Principio: datos duros, sin interpretación."
    ),
)


# ── Auth middleware ──────────────────────────────────────────────────────────
class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/"):
            return await call_next(request)
        if BEARER_TOKEN:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != BEARER_TOKEN:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════
# HERRAMIENTAS: MEMORIA / AGENDA
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def agenda_leer() -> str:
    """Lee el contenido completo de la AGENDA de Louis (pendientes, semana, backlog)."""
    f = SPACES_PATH / "AGENDA.md"
    if not f.exists():
        return f"AGENDA.md no encontrada en {SPACES_PATH}."
    return f.read_text(encoding="utf-8")


@mcp.tool()
def agenda_marcar_hecho(patron: str) -> str:
    """
    Marca como hecho (- [x]) el primer pendiente cuya línea contenga `patron`.
    Dato duro: devuelve la línea original y la modificada, o error si no hay match.
    """
    f = SPACES_PATH / "AGENDA.md"
    if not f.exists():
        return f"AGENDA.md no encontrada en {SPACES_PATH}."

    content = f.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines, changed, original = [], False, ""
    for line in lines:
        if not changed and patron.lower() in line.lower() and "- [ ]" in line:
            original = line
            line = line.replace("- [ ]", "- [x]", 1)
            changed = True
        new_lines.append(line)

    if not changed:
        return f"Sin pendiente abierto que contenga: «{patron}»"

    f.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return f"✅ Marcado:\n  Antes: {original.strip()}\n  Ahora: {new_lines[lines.index(original)].strip()}"


@mcp.tool()
def agenda_editar(patron: str, nuevo_texto: str) -> str:
    """
    Reemplaza en AGENDA.md la primera línea que contenga `patron` por `nuevo_texto`.
    Usar para correcciones de estado o texto, no para marcar hecho (usa agenda_marcar_hecho).
    """
    f = SPACES_PATH / "AGENDA.md"
    if not f.exists():
        return f"AGENDA.md no encontrada en {SPACES_PATH}."

    content = f.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines, changed = [], False
    for line in lines:
        if not changed and patron.lower() in line.lower():
            new_lines.append(nuevo_texto)
            changed = True
        else:
            new_lines.append(line)

    if not changed:
        return f"Sin línea que contenga: «{patron}»"

    f.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return f"✅ Línea actualizada en AGENDA.md"


@mcp.tool()
def memoria_leer(archivo: str = "IMPORTANT") -> str:
    """
    Lee un archivo de memoria de Louis.
    Opciones válidas: IMPORTANT, JOURNAL, LEARNINGS, PEOPLE, PROJECTS
    """
    PERMITIDOS = {"IMPORTANT", "JOURNAL", "LEARNINGS", "PEOPLE", "PROJECTS"}
    nombre = archivo.upper()
    if nombre not in PERMITIDOS:
        return f"Archivo no permitido. Opciones: {', '.join(sorted(PERMITIDOS))}"

    f = SPACES_PATH / f"{nombre}.md"
    if not f.exists():
        return f"{nombre}.md no encontrado en {SPACES_PATH}."
    return f.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# HERRAMIENTAS: ACERVO LEGAL (SJF + DOF)
# ═══════════════════════════════════════════════════════════════════════════

def _query_db(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Ejecuta una query SQLite y devuelve lista de dicts. Silencia si la BD no existe."""
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _tablas_db(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    rows = _query_db(db_path, "SELECT name FROM sqlite_master WHERE type='table'")
    return [r["name"] for r in rows]


@mcp.tool()
def legal_estado() -> str:
    """Estado de las bases de datos legales: si existen, tamaño y tablas disponibles."""
    lineas = ["## Acervo Legal — Estado"]
    for nombre, db_path in [("SJF", SJF_DB_PATH), ("DOF", DOF_DB_PATH)]:
        if not db_path.exists():
            lineas.append(f"  ⚠️  {nombre}: no disponible ({db_path})")
        else:
            mb = db_path.stat().st_size / 1024 / 1024
            tablas = _tablas_db(db_path)
            lineas.append(f"  ✅ {nombre}: {mb:.1f} MB — tablas: {', '.join(tablas) or '(ninguna)'}")
    return "\n".join(lineas)


@mcp.tool()
def legal_buscar(termino: str, fuente: str = "ambas", limite: int = 10) -> str:
    """
    Busca texto en el acervo legal. Devuelve fragmentos con metadatos.
    fuente: 'sjf' | 'dof' | 'ambas'
    limite: número máximo de resultados (default 10, max 50)
    """
    limite = min(int(limite), 50)
    resultados = []

    if fuente in ("sjf", "ambas") and SJF_DB_PATH.exists():
        tablas = _tablas_db(SJF_DB_PATH)
        # Intentamos columnas comunes; si la estructura es diferente, _query_db devuelve vacío
        if "tesis" in tablas:
            filas = _query_db(
                SJF_DB_PATH,
                "SELECT rubro, texto, fecha FROM tesis WHERE texto LIKE ? OR rubro LIKE ? LIMIT ?",
                (f"%{termino}%", f"%{termino}%", limite),
            )
            for f in filas:
                rubro = f.get("rubro", "")
                texto = (f.get("texto") or "")[:600]
                fecha = f.get("fecha", "")
                resultados.append(f"[SJF] {rubro}\nFecha: {fecha}\n{texto}…")
        elif tablas:
            # Tabla desconocida: hacemos PRAGMA para obtener columnas e intentamos
            primera = tablas[0]
            cols = _query_db(SJF_DB_PATH, f"PRAGMA table_info({primera})")
            col_names = [c["name"] for c in cols]
            resultados.append(f"[SJF] BD disponible — tabla '{primera}', columnas: {', '.join(col_names)}")

    if fuente in ("dof", "ambas") and DOF_DB_PATH.exists():
        tablas = _tablas_db(DOF_DB_PATH)
        if "publicaciones" in tablas:
            filas = _query_db(
                DOF_DB_PATH,
                "SELECT titulo, contenido, fecha_publicacion FROM publicaciones WHERE contenido LIKE ? OR titulo LIKE ? LIMIT ?",
                (f"%{termino}%", f"%{termino}%", limite),
            )
            for f in filas:
                titulo = f.get("titulo", "")
                contenido = (f.get("contenido") or "")[:600]
                fecha = f.get("fecha_publicacion", "")
                resultados.append(f"[DOF] {titulo}\nFecha: {fecha}\n{contenido}…")
        elif tablas:
            primera = tablas[0]
            cols = _query_db(DOF_DB_PATH, f"PRAGMA table_info({primera})")
            col_names = [c["name"] for c in cols]
            resultados.append(f"[DOF] BD disponible — tabla '{primera}', columnas: {', '.join(col_names)}")

    if not resultados:
        disponibles = []
        if SJF_DB_PATH.exists():
            disponibles.append("SJF")
        if DOF_DB_PATH.exists():
            disponibles.append("DOF")
        if not disponibles:
            return f"Sin resultados: bases de datos legales no disponibles aún en {SJF_DB_PATH.parent}."
        return f"Sin resultados para «{termino}» en {', '.join(disponibles)}."

    separador = f"\n{'─'*60}\n"
    return separador.join(resultados)


# ═══════════════════════════════════════════════════════════════════════════
# HERRAMIENTAS: ENTREGABLES DE COWORK
# ═══════════════════════════════════════════════════════════════════════════

ICONO_ESTADO = {
    "borrador": "📝",
    "listo": "✅",
    "en_vobo": "🔄",
    "aprobado": "✔️",
    "archivado": "📦",
}


def _parsear_frontmatter(path: Path) -> dict:
    """Lee el bloque YAML frontmatter de un .md y devuelve dict. Graceful si falta."""
    meta: dict = {"archivo": path.name, "titulo": path.stem, "estado": "desconocido"}
    try:
        content = path.read_text(encoding="utf-8")
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


@mcp.tool()
def entregables_listar(estado: Optional[str] = None, cliente: Optional[str] = None) -> str:
    """
    Lista todos los entregables registrados en el almacén compartido.
    Filtra opcionalmente por estado (borrador/listo/en_vobo/aprobado/archivado) y/o cliente.
    """
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    archivos = sorted(ENTREGABLES_PATH.glob("*.md"))
    briefs_path = ENTREGABLES_PATH / "_briefs"

    entregables = []
    for f in archivos:
        if f.name.startswith("_"):
            continue
        meta = _parsear_frontmatter(f)
        if estado and meta.get("estado", "").lower() != estado.lower():
            continue
        if cliente and cliente.lower() not in meta.get("cliente", "").lower():
            continue
        entregables.append(meta)

    briefs = list(briefs_path.glob("*.md")) if briefs_path.exists() else []

    if not entregables:
        filtros = []
        if estado:
            filtros.append(f"estado={estado}")
        if cliente:
            filtros.append(f"cliente={cliente}")
        nota = f" ({', '.join(filtros)})" if filtros else ""
        return (
            f"Sin entregables{nota}.\n"
            f"Briefs pendientes: {len(briefs)}\n"
            f"Directorio: {ENTREGABLES_PATH}"
        )

    lineas = [f"## Entregables ({len(entregables)})\n"]
    for e in entregables:
        icono = ICONO_ESTADO.get(e.get("estado", ""), "❓")
        lineas.append(f"{icono} **{e.get('titulo', e['archivo'])}**")
        lineas.append(
            f"   Cliente: {e.get('cliente', 'N/A')} | "
            f"Estado: {e.get('estado', 'N/A')} | "
            f"Actualizado: {e.get('fecha_actualizacion', 'N/A')}"
        )
    lineas.append(f"\n📋 Briefs de dispatch pendientes: {len(briefs)}")
    return "\n".join(lineas)


@mcp.tool()
def entregable_estado(nombre_o_titulo: str) -> str:
    """
    Devuelve el contenido completo (frontmatter + cuerpo) de un entregable.
    Busca por nombre de archivo o por título en el frontmatter.
    """
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    termino = nombre_o_titulo.lower()

    for f in ENTREGABLES_PATH.glob("*.md"):
        if f.name.startswith("_"):
            continue
        meta = _parsear_frontmatter(f)
        if termino in f.stem.lower() or termino in meta.get("titulo", "").lower():
            return f.read_text(encoding="utf-8")

    return f"Entregable «{nombre_o_titulo}» no encontrado en {ENTREGABLES_PATH}."


@mcp.tool()
def entregable_registrar(
    titulo: str,
    cliente: str,
    estado: str = "borrador",
    descripcion: str = "",
    responsable: str = "Cowork",
) -> str:
    """
    Registra un nuevo entregable producido en Cowork.
    estado: borrador | listo | en_vobo | aprobado | archivado
    Devuelve el path del archivo creado.
    """
    estado = estado.lower()
    if estado not in ESTADOS_VALIDOS:
        return f"Estado inválido «{estado}». Opciones: {', '.join(sorted(ESTADOS_VALIDOS))}"

    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y-%m-%d")
    filename = f"{fecha}-{_slug(titulo)}.md"
    filepath = ENTREGABLES_PATH / filename

    if filepath.exists():
        return f"Ya existe un entregable con ese nombre: {filename}. Usa entregable_actualizar_estado para modificarlo."

    content = (
        f"---\n"
        f"titulo: {titulo}\n"
        f"cliente: {cliente}\n"
        f"estado: {estado}\n"
        f"responsable: {responsable}\n"
        f"fecha_creacion: {fecha}\n"
        f"fecha_actualizacion: {fecha}\n"
        f"vobo: pendiente\n"
        f"---\n\n"
        f"# {titulo}\n\n"
        f"**Cliente:** {cliente}  \n"
        f"**Estado:** {estado}  \n"
        f"**Responsable:** {responsable}  \n"
        f"**Fecha de creación:** {fecha}  \n\n"
        f"## Descripción\n\n"
        f"{descripcion or 'Sin descripción registrada.'}\n\n"
        f"## Historial\n\n"
        f"- {fecha} — Registrado como `{estado}` por {responsable}\n"
    )
    filepath.write_text(content, encoding="utf-8")
    return f"✅ Entregable registrado: {filepath}\nEstado inicial: {estado}"


@mcp.tool()
def entregable_actualizar_estado(
    nombre_o_titulo: str,
    nuevo_estado: str,
    nota: str = "",
) -> str:
    """
    Actualiza el estado de un entregable (ej: borrador → listo → en_vobo → aprobado).
    nota: comentario opcional que se añade al historial.
    """
    nuevo_estado = nuevo_estado.lower()
    if nuevo_estado not in ESTADOS_VALIDOS:
        return f"Estado inválido «{nuevo_estado}». Opciones: {', '.join(sorted(ESTADOS_VALIDOS))}"

    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    termino = nombre_o_titulo.lower()
    fecha = datetime.now().strftime("%Y-%m-%d")

    for f in ENTREGABLES_PATH.glob("*.md"):
        if f.name.startswith("_"):
            continue
        meta = _parsear_frontmatter(f)
        if termino in f.stem.lower() or termino in meta.get("titulo", "").lower():
            content = f.read_text(encoding="utf-8")
            # Actualizar frontmatter
            content = re.sub(r"(?m)^estado:.*$", f"estado: {nuevo_estado}", content)
            content = re.sub(r"(?m)^fecha_actualizacion:.*$", f"fecha_actualizacion: {fecha}", content)
            # Añadir al historial
            entrada_historial = f"- {fecha} — Estado actualizado a `{nuevo_estado}`"
            if nota:
                entrada_historial += f": {nota}"
            content = content.rstrip() + f"\n{entrada_historial}\n"
            f.write_text(content, encoding="utf-8")
            titulo_real = meta.get("titulo", f.stem)
            return f"✅ «{titulo_real}» → estado: {nuevo_estado}"

    return f"Entregable «{nombre_o_titulo}» no encontrado."


# ═══════════════════════════════════════════════════════════════════════════
# HERRAMIENTAS: DISPATCH (Louis → Cowork)
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def dispatch_preparar_brief(
    tarea: str,
    cliente: str,
    insumos: str = "",
    urgencia: str = "normal",
    contexto_adicional: str = "",
) -> str:
    """
    Louis prepara un brief de dispatch para que Cowork produzca un entregable.
    El brief se guarda en _briefs/ del almacén compartido, listo para tomarse en Cowork.
    urgencia: baja | normal | alta | urgente
    """
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    briefs_path = ENTREGABLES_PATH / "_briefs"
    briefs_path.mkdir(exist_ok=True)

    ahora = datetime.now()
    fecha = ahora.strftime("%Y-%m-%d")
    hora = ahora.strftime("%H:%M")
    filename = f"{fecha}-brief-{_slug(tarea)}.md"
    filepath = briefs_path / filename

    # Buscar contexto relevante en la AGENDA
    agenda_context = "(AGENDA no disponible)"
    agenda_file = SPACES_PATH / "AGENDA.md"
    if agenda_file.exists():
        lineas = agenda_file.read_text(encoding="utf-8").splitlines()
        relevantes = [
            l for l in lineas
            if any(kw in l.lower() for kw in [tarea.lower()[:20], cliente.lower()[:15]])
        ]
        agenda_context = "\n".join(relevantes[:8]) if relevantes else "(sin entradas relevantes en AGENDA)"

    # Buscar entregables previos del mismo cliente
    previos = []
    for f in ENTREGABLES_PATH.glob("*.md"):
        if f.name.startswith("_"):
            continue
        meta = _parsear_frontmatter(f)
        if cliente.lower() in meta.get("cliente", "").lower():
            icono = ICONO_ESTADO.get(meta.get("estado", ""), "❓")
            previos.append(f"  {icono} {meta.get('titulo', f.stem)} ({meta.get('estado', 'desconocido')})")
    previos_txt = "\n".join(previos) if previos else "  (ninguno registrado)"

    content = (
        f"---\n"
        f"tipo: brief_dispatch\n"
        f"tarea: {tarea}\n"
        f"cliente: {cliente}\n"
        f"urgencia: {urgencia}\n"
        f"estado: pendiente\n"
        f"preparado_por: Louis\n"
        f"fecha_creacion: {fecha} {hora}\n"
        f"---\n\n"
        f"# Brief de Dispatch: {tarea}\n\n"
        f"| Campo | Valor |\n"
        f"|-------|-------|\n"
        f"| **Cliente** | {cliente} |\n"
        f"| **Urgencia** | {urgencia} |\n"
        f"| **Preparado por** | Louis (Cerebro Kawiil) |\n"
        f"| **Fecha** | {fecha} {hora} |\n\n"
        f"## Tarea\n\n"
        f"{tarea}\n\n"
        f"## Insumos disponibles\n\n"
        f"{insumos or 'Sin insumos específicos señalados. Consultar acervo legal y memoria si aplica.'}\n\n"
        f"## Entregables previos del cliente\n\n"
        f"{previos_txt}\n\n"
        f"## Contexto de AGENDA relevante\n\n"
        f"{agenda_context}\n\n"
        f"## Contexto adicional\n\n"
        f"{contexto_adicional or 'Sin contexto adicional.'}\n\n"
        f"## Instrucciones para Cowork\n\n"
        f"1. Revisar los insumos listados arriba\n"
        f"2. Consultar acervo legal si aplica (`legal_buscar`)\n"
        f"3. Producir el entregable\n"
        f"4. Registrarlo con `entregable_registrar(titulo, cliente, estado='borrador')`\n"
        f"5. Actualizar a `listo` cuando esté terminado\n"
        f"6. Si requiere VoBo de dirección, actualizar a `en_vobo`\n\n"
        f"---\n"
        f"*Brief generado por Louis/Cerebro Kawiil — {ahora.isoformat()}*\n"
    )

    filepath.write_text(content, encoding="utf-8")
    return (
        f"✅ Brief preparado: _briefs/{filename}\n\n"
        f"Resumen:\n"
        f"  Tarea: {tarea}\n"
        f"  Cliente: {cliente}\n"
        f"  Urgencia: {urgencia}\n"
        f"  Entregables previos del cliente: {len(previos)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# HERRAMIENTA: ESTADO GENERAL DEL CEREBRO
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def cerebro_estado() -> str:
    """
    Estado general del Cerebro Kawiil: qué tiene disponible, qué falta.
    Usar como punto de partida para orientar una sesión de Cowork.
    """
    lineas = [
        "# Estado Cerebro Kawiil",
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Memoria
    lineas.append("## Memoria Louis")
    for nombre in ("AGENDA", "IMPORTANT", "JOURNAL", "PROJECTS", "PEOPLE", "LEARNINGS"):
        f = SPACES_PATH / f"{nombre}.md"
        if f.exists():
            kb = f.stat().st_size / 1024
            lineas.append(f"  ✅ {nombre}.md ({kb:.1f} KB)")
        else:
            lineas.append(f"  ❌ {nombre}.md — no encontrado en {SPACES_PATH}")

    # Legal
    lineas.append("\n## Acervo Legal")
    for nombre, db_path in [("SJF", SJF_DB_PATH), ("DOF", DOF_DB_PATH)]:
        if db_path.exists():
            mb = db_path.stat().st_size / 1024 / 1024
            tablas = _tablas_db(db_path)
            lineas.append(f"  ✅ {nombre}: {mb:.1f} MB — tablas: {', '.join(tablas)}")
        else:
            lineas.append(f"  ⚠️  {nombre}: no disponible aún ({db_path})")

    # Entregables
    lineas.append("\n## Entregables (Cowork)")
    ENTREGABLES_PATH.mkdir(parents=True, exist_ok=True)
    conteo: dict[str, int] = {}
    for f in ENTREGABLES_PATH.glob("*.md"):
        if not f.name.startswith("_"):
            meta = _parsear_frontmatter(f)
            estado = meta.get("estado", "desconocido")
            conteo[estado] = conteo.get(estado, 0) + 1

    if conteo:
        for estado, n in sorted(conteo.items()):
            icono = ICONO_ESTADO.get(estado, "❓")
            lineas.append(f"  {icono} {estado}: {n}")
    else:
        lineas.append("  (Sin entregables registrados aún)")

    briefs_path = ENTREGABLES_PATH / "_briefs"
    n_briefs = len(list(briefs_path.glob("*.md"))) if briefs_path.exists() else 0
    lineas.append(f"  📋 Briefs de dispatch pendientes: {n_briefs}")

    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ═══════════════════════════════════════════════════════════════════════════

async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "cerebro-kawiil", "version": "1.0"})


def build_app() -> Starlette:
    """Construye la app Starlette con auth middleware y el MCP montado en /mcp."""
    mcp_app = mcp.sse_app()
    return Starlette(
        routes=[
            Route("/health", health_endpoint),
            Mount("/", app=mcp_app),
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
    )


if __name__ == "__main__":
    if not BEARER_TOKEN:
        print(
            "⚠️  ADVERTENCIA: CEREBRO_KAWIIL_TOKEN no configurado. "
            "El servidor aceptará cualquier petición.",
            file=sys.stderr,
        )

    print(f"🧠 Cerebro Kawiil MCP v1.0 — iniciando en 127.0.0.1:{PORT}")
    print(f"   Memoria : {SPACES_PATH}")
    print(f"   Entregables: {ENTREGABLES_PATH}")
    print(f"   SJF DB  : {SJF_DB_PATH}")
    print(f"   DOF DB  : {DOF_DB_PATH}")
    print(f"   Auth    : {'Bearer token configurado' if BEARER_TOKEN else 'SIN TOKEN (inseguro)'}")

    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="info")
