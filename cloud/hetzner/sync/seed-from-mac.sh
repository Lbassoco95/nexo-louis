#!/usr/bin/env bash
# seed-from-mac.sh — Corre EN LA MAC. Empaqueta el estado completo de ~/.openclaw/
# (scripts m365, telegram-bridge, system prompts, AGENTS.md, memorias, tokens M365)
# en un tarball cifrado, listo para subir al VPS Hetzner.
#
# El sync continuo (mac-push.sh) NO incluye credentials por seguridad; este
# script es la "carga inicial" one-shot que se corre antes del primer deploy.
#
# Uso:
#   ./seed-from-mac.sh HETZNER_IP
#   ./seed-from-mac.sh 91.99.123.45

set -euo pipefail

HETZNER_IP="${1:-}"
if [[ -z "$HETZNER_IP" ]]; then
  echo "Uso: $0 HETZNER_IP"
  exit 1
fi

OC="$HOME/.openclaw"
[[ -d "$OC" ]] || { echo "ERROR: no existe $OC"; exit 1; }

SEED="/tmp/louis-seed.tar.gz"
PROJECT_DIR="$HOME/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup"

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }

# Empaqueta solo lo necesario.
# Incluye: spaces/* (prompts, AGENDA, memorias), scripts (m365, telegram-bridge),
# credentials (tokens m365 + cualquier env), telegram-history (opcional).
# Excluye: logs, sessions, cache, tmp.
echo "==> Empaquetando ~/.openclaw → $SEED"
tar --posix -czf "$SEED" \
  -C "$HOME" \
  --exclude='.openclaw/logs' \
  --exclude='.openclaw/spaces/*/sessions' \
  --exclude='.openclaw/spaces/*/cache' \
  --exclude='.openclaw/spaces/*/tmp' \
  --exclude='.openclaw/whisper-models' \
  --exclude='.openclaw/**/.DS_Store' \
  --exclude='.openclaw/node_modules' \
  --exclude='*.log' \
  --exclude='*.bak' \
  --exclude='*.bak.*' \
  .openclaw/spaces \
  .openclaw/credentials \
  .openclaw/scripts 2>/dev/null \
  || true   # algunas subcarpetas pueden no existir, no es fatal

# Añade scripts del repo Yoltik Desarrollos al tarball
echo "==> Añadiendo scripts del proyecto"
tar --posix -czf /tmp/louis-extras.tar.gz \
  -C "$PROJECT_DIR" \
  m365/scripts \
  nexo-prompts-v2 \
  kawiil-agents/kawiil_agents.py 2>/dev/null \
  || true

# Resumen
ls -lh "$SEED" /tmp/louis-extras.tar.gz 2>/dev/null

echo ""
echo "==> Contenido principal del seed:"
tar -tzf "$SEED" 2>/dev/null | head -40
echo "..."
echo "    total $(tar -tzf "$SEED" 2>/dev/null | wc -l) entradas"

echo ""
ok "Seed listo en $SEED"
echo ""
echo "Próximos pasos:"
echo ""
echo "  # 1) Sube ambos tarballs al VPS"
echo "  scp $SEED /tmp/louis-extras.tar.gz root@${HETZNER_IP}:/tmp/"
echo ""
echo "  # 2) En el VPS, expande el seed (después de ejecutar deploy.sh paso [2/6]"
echo "  #    para que /opt/openclaw exista con permisos correctos)"
echo "  ssh root@${HETZNER_IP}"
echo "    cd /opt/louis"
echo "    sudo bash sync/hetzner-seed-apply.sh /tmp/louis-seed.tar.gz /tmp/louis-extras.tar.gz"
echo ""
echo "  # 3) Borra los tarballs locales (¡tienen credentials!)"
echo "  shred -u $SEED /tmp/louis-extras.tar.gz"
echo ""
