#!/usr/bin/env bash
# 04-node.sh — Instala Node.js 22 LTS para OpenClaw.
# Usa el script oficial NodeSource.
set -euo pipefail

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }

if command -v node >/dev/null && node --version | grep -qE '^v(22|23|24)\.'; then
  log "Node $(node --version) ya instalado — skip"
  exit 0
fi

log "Instalando Node.js 22 LTS"
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
DEBIAN_FRONTEND=noninteractive apt-get install -yq nodejs

node --version
npm --version

echo "  ✓ Node.js listo"
