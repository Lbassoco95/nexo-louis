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
import base64
import hashlib
import html as _html
import json
import secrets
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
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
    # Detrás de Caddy con dominio propio: el Host no es localhost. Desactivamos
    # la protección anti DNS-rebinding del transporte SSE (ya protegemos con el
    # Bearer token + TLS de Caddy), si no rechaza con "Request validation failed".
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ── OAuth shim ─────────────────────────────────────────────────────────────
# Los connectors de Claude exigen OAuth (con Dynamic Client Registration +
# PKCE). Este shim implementa el flujo mínimo: Claude registra un cliente,
# manda al usuario a /authorize, donde el server pide el TOKEN del Cerebro
# (Bearer estático). Si es correcto, emite un code y luego en /token devuelve
# el propio Bearer como access_token (que el middleware ya valida). Así el
# acceso queda protegido por el mismo token, pero hablando OAuth con Claude.

_oauth_clients: dict[str, dict] = {}   # client_id -> metadata
_oauth_codes: dict[str, dict] = {}     # code -> {challenge, redirect_uri, exp}


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme or "https")
    host = request.headers.get("host", request.url.netloc)
    return f"{proto}://{host}"


async def oauth_protected_resource(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse({
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
    })


async def oauth_metadata(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["cerebro"],
    })


async def oauth_register(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    client_id = "cl_" + secrets.token_hex(16)
    meta = {
        "client_id": client_id,
        "redirect_uris": body.get("redirect_uris", []),
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "client_id_issued_at": int(time.time()),
    }
    for k in ("client_name", "scope", "redirect_uris"):
        if k in body:
            meta[k] = body[k]
    _oauth_clients[client_id] = meta
    return JSONResponse(meta, status_code=201)


_AUTHORIZE_FORM = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cerebro Kawiil — Autorizar</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b5d2e;color:#fff;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#fff;color:#1a1a1a;max-width:380px;padding:32px;border-radius:14px;
box-shadow:0 10px 40px rgba(0,0,0,.3)}}
h1{{font-size:1.3em;margin:0 0 6px}} p{{color:#555;font-size:.92em;line-height:1.5}}
input{{width:100%;box-sizing:border-box;padding:12px;margin:14px 0;border:1px solid #ccc;
border-radius:8px;font-size:1em}}
button{{width:100%;padding:12px;background:#0b5d2e;color:#fff;border:0;border-radius:8px;
font-size:1em;font-weight:bold;cursor:pointer}}
.err{{color:#c0392b;font-size:.9em}}
</style></head><body><div class="card">
<h1>🧠 Cerebro Kawiil</h1>
<p>Pega el <b>token del Cerebro</b> para autorizar la conexión con Claude.</p>
{error}
<form method="post" action="/authorize">{fields}
<input type="password" name="token" placeholder="Token del Cerebro Kawiil" autofocus required>
<button type="submit">Autorizar conexión</button>
</form></div></body></html>"""


def _render_authorize(params: dict, error: str = "") -> HTMLResponse:
    fields = ""
    for k in ("client_id", "redirect_uri", "state", "code_challenge",
              "code_challenge_method", "response_type", "scope"):
        v = _html.escape(params.get(k, "") or "")
        fields += f'<input type="hidden" name="{k}" value="{v}">'
    err_html = f'<p class="err">{_html.escape(error)}</p>' if error else ""
    return HTMLResponse(_AUTHORIZE_FORM.format(fields=fields, error=err_html))


async def oauth_authorize(request: Request) -> HTMLResponse:
    return _render_authorize(dict(request.query_params))


async def oauth_authorize_post(request: Request):
    form = await request.form()
    params = {k: form.get(k, "") for k in (
        "client_id", "redirect_uri", "state", "code_challenge",
        "code_challenge_method", "response_type", "scope")}
    token = form.get("token", "")
    if not BEARER_TOKEN or token != BEARER_TOKEN:
        return _render_authorize(params, error="Token inválido. Intenta de nuevo.")
    redirect_uri = params["redirect_uri"]
    if not redirect_uri:
        return JSONResponse({"error": "invalid_request", "error_description": "missing redirect_uri"}, status_code=400)
    code = secrets.token_urlsafe(32)
    _oauth_codes[code] = {
        "challenge": params.get("code_challenge", ""),
        "redirect_uri": redirect_uri,
        "exp": time.time() + 300,
    }
    sep = "&" if "?" in redirect_uri else "?"
    loc = f"{redirect_uri}{sep}code={urllib.parse.quote(code)}"
    if params.get("state"):
        loc += f"&state={urllib.parse.quote(params['state'])}"
    return RedirectResponse(loc, status_code=302)


async def oauth_token(request: Request) -> JSONResponse:
    form = await request.form()
    if form.get("grant_type", "") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    rec = _oauth_codes.pop(form.get("code", ""), None)
    if not rec or rec["exp"] < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    # PKCE S256
    if rec.get("challenge"):
        verifier = form.get("code_verifier", "")
        digest = hashlib.sha256(verifier.encode()).digest()
        calc = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        if calc != rec["challenge"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status_code=400)
    return JSONResponse({
        "access_token": BEARER_TOKEN,
        "token_type": "Bearer",
        "expires_in": 31536000,
        "scope": "cerebro",
    })


# ── Auth middleware ────────────────────────────────────────────────────────
_OPEN_PATHS = ("/health", "/", "/authorize", "/token", "/register")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _OPEN_PATHS or path.startswith("/.well-known"):
            return await call_next(request)
        if BEARER_TOKEN:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != BEARER_TOKEN:
                base = _base_url(request)
                return JSONResponse(
                    {"error": "Unauthorized"}, status_code=401,
                    headers={"WWW-Authenticate":
                             f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'},
                )
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


# ── Aprendizaje: Cowork siembra contexto en la memoria de Louis ─────────────
_APRENDER_DESTINOS = {
    "IMPORTANT": "IMPORTANT.md",
    "PROJECTS": "PROJECTS.md",
    "PEOPLE": "PEOPLE.md",
    "CLIENTES": "CLIENTES.md",
    "LEARNINGS": "LEARNINGS.md",
}


def _norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


@mcp.tool()
def aprender(detalle: str, archivo: str = "IMPORTANT", cliente: str = "") -> str:
    """
    Enseña a Louis un hecho/contexto DURABLE desde Cowork — queda en su memoria de
    largo plazo (la lee en cada sesión de Telegram/Slack). Úsalo cuando Polo te da
    contexto que conviene que Louis recuerde: datos de un cliente/proyecto, una
    decisión, una preferencia, una persona o una instrucción permanente.
    archivo: IMPORTANT | PROJECTS | PEOPLE | CLIENTES | LEARNINGS
      - IMPORTANT: decisiones/hechos clave o instrucciones permanentes
      - PROJECTS:  estado/contexto vivo de un proyecto o caso
      - PEOPLE:    datos durables de una persona (rol, empresa, relación)
      - CLIENTES:  datos de un cliente/prospecto (razón social, contacto, estatus)
      - LEARNINGS: reglas/lecciones de cómo trabaja Polo
    `cliente` (opcional) antepone la empresa/cliente para que el contexto quede bien definido.
    """
    detalle = (detalle or "").strip()
    if len(detalle) < 4:
        return "Dame un detalle con sustancia (mín. 4 caracteres)."
    nombre = archivo.strip().upper().replace(".MD", "")
    fname = _APRENDER_DESTINOS.get(nombre)
    if not fname:
        return f"archivo inválido. Opciones: {', '.join(_APRENDER_DESTINOS)}"
    f = SPACES_PATH / fname
    fecha = datetime.now().strftime("%Y-%m-%d")
    cuerpo = f"{cliente.strip()} — {detalle}" if cliente.strip() else detalle
    objetivo = _norm_line(cuerpo)
    for l in _read_cached(f).splitlines():       # dedup: no repetir lo equivalente
        if objetivo and objetivo in _norm_line(l):
            return f"👍 Ya estaba en {nombre}, no dupliqué."
    existente = f.read_text(encoding="utf-8") if f.exists() else ""
    with f.open("a", encoding="utf-8") as fh:
        if existente and not existente.endswith("\n"):
            fh.write("\n")
        fh.write(f"- [{fecha}] {cuerpo}  · [Cowork]\n")
    _invalidate(f)
    return f"🧠 Aprendido en {nombre}: {cuerpo[:120]}"


@mcp.tool()
def bitacora_cowork(resumen: str, cliente: str = "") -> str:
    """
    Registra una nota de lo trabajado en esta sesión de Cowork. Se guarda en la
    bitácora que Louis DESTILA cada noche → de ahí extrae hechos durables a PEOPLE/
    CLIENTES/AGENDA/IMPORTANT automáticamente. Úsalo al cerrar un tema o al final de
    la sesión, con un resumen de qué se hizo y qué contexto nuevo surgió.
    """
    resumen = (resumen or "").strip()
    if len(resumen) < 8:
        return "Dame un resumen con sustancia (mín. 8 caracteres)."
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    texto = f"[Cowork{(' · ' + cliente.strip()) if cliente.strip() else ''}] {resumen}"
    # 1) Línea JSONL que el destilador nocturno de Louis ingiere (mismo formato que Telegram).
    jl = SPACES_PATH / "cowork-history.jsonl"
    with jl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": ts, "role": "user", "content": texto}, ensure_ascii=False) + "\n")
    _invalidate(jl)
    # 2) Copia legible en COWORK.md (para revisión humana).
    md = SPACES_PATH / "COWORK.md"
    with md.open("a", encoding="utf-8") as fh:
        fh.write(f"- [{ts[:16].replace('T', ' ')}] {texto}\n")
    _invalidate(md)
    return "📓 Bitácora guardada — Louis lo destilará esta noche a su memoria."


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
            # OAuth shim (discovery + DCR + authorize + token)
            Route("/.well-known/oauth-protected-resource", oauth_protected_resource),
            Route("/.well-known/oauth-protected-resource/{rest:path}", oauth_protected_resource),
            Route("/.well-known/oauth-authorization-server", oauth_metadata),
            Route("/.well-known/oauth-authorization-server/{rest:path}", oauth_metadata),
            Route("/.well-known/openid-configuration", oauth_metadata),
            Route("/register", oauth_register, methods=["POST"]),
            Route("/authorize", oauth_authorize, methods=["GET"]),
            Route("/authorize", oauth_authorize_post, methods=["POST"]),
            Route("/token", oauth_token, methods=["POST"]),
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
