#!/usr/bin/env bash
# 07-cerebro-kawiil.sh — Instala dependencias Python del Cerebro Kawiil MCP
# Idempotente: verifica antes de instalar.
set -euo pipefail

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }

log "Instalando dependencias Python para Cerebro Kawiil MCP"

# mcp >= 1.0 incluye FastMCP y el transport SSE/streamable-HTTP
# uvicorn para correr el server ASGI
pip3 install --quiet --upgrade \
    "mcp>=1.0" \
    "uvicorn[standard]>=0.30" \
    "starlette>=0.37"

ok "Dependencias de Cerebro Kawiil instaladas"
python3 -c "from mcp.server.fastmcp import FastMCP; print('  FastMCP OK')"
python3 -c "import uvicorn; print('  uvicorn OK')"

# Crear directorio de entregables
ENTREGABLES_DIR="${ENTREGABLES_PATH:-/opt/openclaw/entregables}"
mkdir -p "${ENTREGABLES_DIR}/_briefs"
chown -R "${SYSTEM_USER:-polo}":"${SYSTEM_USER:-polo}" "${ENTREGABLES_DIR}"
ok "Directorio de entregables listo: ${ENTREGABLES_DIR}"

# Crear directorio de DBs legales (las BDs se poblarán con el pipeline existente)
# Rutas canónicas alineadas con louis_core / sjf_harvest / dof scrapers.
LEGAL_SJF_DB="${SJF_DB_PATH:-/opt/openclaw/legal/sjf/biblioteca.db}"
LEGAL_DOF_DB="${DOF_DB_PATH:-/opt/openclaw/legal/dof/biblioteca_dof.db}"
mkdir -p "$(dirname "$LEGAL_SJF_DB")" "$(dirname "$LEGAL_DOF_DB")"
chown -R "${SYSTEM_USER:-polo}":"${SYSTEM_USER:-polo}" \
  "$(dirname "$LEGAL_SJF_DB")" "$(dirname "$LEGAL_DOF_DB")"
ok "Directorios legales listos: $(dirname "$LEGAL_SJF_DB") + $(dirname "$LEGAL_DOF_DB")"
