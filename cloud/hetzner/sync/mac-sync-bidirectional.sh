#!/usr/bin/env bash
# mac-sync-bidirectional.sh — Sync bidireccional Mac ↔ Hetzner.
# Corre cada 5 min vía launchd. Sólo si la Mac tiene red.
#
# Carpetas que sincroniza (default; puede modificarse vía LOUIS_SYNC_DIRS):
#   ~/Documents/Claude/Projects/      → /opt/openclaw/mac/Projects/
#   ~/.openclaw/spaces/                → /opt/openclaw/spaces/   (bidireccional)
#
# Conflictos: gana el más reciente (rsync --update). No borra archivos
# (sin --delete) para evitar pérdidas accidentales.
#
# Variables:
#   LOUIS_REMOTE_HOST  ej: 204.168.131.21
#   LOUIS_REMOTE_USER  ej: polo
#   LOUIS_SSH_KEY      ej: ~/.ssh/id_ed25519
#   LOUIS_SYNC_DIRS    JSON-ish: "Projects:~/Documents/Claude/Projects/:/opt/openclaw/mac/Projects/,spaces:~/.openclaw/spaces/:/opt/openclaw/spaces/"

set -uo pipefail

: "${LOUIS_REMOTE_HOST:?LOUIS_REMOTE_HOST no definido}"
: "${LOUIS_REMOTE_USER:?LOUIS_REMOTE_USER no definido}"
: "${LOUIS_SSH_KEY:?LOUIS_SSH_KEY no definido}"

REMOTE="${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}"
SSH_OPTS="-i $LOUIS_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30 -o BatchMode=yes"

stamp() { date -Iseconds; }

# Pares: NAME:LOCAL_DIR:REMOTE_DIR  (delimitador coma)
DIRS_DEFAULT="Projects:$HOME/Documents/Claude/Projects/:/opt/openclaw/mac/Projects/,spaces:$HOME/.openclaw/spaces/:/opt/openclaw/spaces/"
DIRS="${LOUIS_SYNC_DIRS:-$DIRS_DEFAULT}"

echo "[$(stamp)] === louis-sync bidirectional start ==="

# Verifica que haya red antes de intentar rsync
if ! ping -c 1 -W 2 "$LOUIS_REMOTE_HOST" >/dev/null 2>&1; then
  echo "[$(stamp)] Sin red a $LOUIS_REMOTE_HOST — skip"
  exit 0
fi

# Reglas comunes de rsync
EXCLUDES=(
  --exclude='.DS_Store'
  --exclude='*.log'
  --exclude='*.tmp'
  --exclude='*.bak'
  --exclude='*.bak.*'
  --exclude='node_modules/'
  --exclude='.git/objects/'
  --exclude='.git/logs/'
  --exclude='.next/'
  --exclude='__pycache__/'
  --exclude='.venv/'
  --exclude='venv/'
  --exclude='*.pyc'
  --exclude='.openclaw/credentials/'
  --exclude='sessions/'
  --exclude='cache/'
  --exclude='tmp/'
)

IFS=',' read -ra PAIRS <<< "$DIRS"
for pair in "${PAIRS[@]}"; do
  IFS=':' read -r name local remote <<< "$pair"
  local=$(eval echo "$local")  # expande ~

  if [[ ! -d "$local" ]]; then
    echo "[$(stamp)] $name: $local no existe — skip"
    continue
  fi

  echo "[$(stamp)] $name: PUSH Mac→Hetzner ($local → $remote)"
  # Asegura carpeta remota
  ssh $SSH_OPTS "$REMOTE" "mkdir -p '$remote'" 2>/dev/null || true

  # Push: archivos más recientes en Mac van a Hetzner
  rsync -az --update "${EXCLUDES[@]}" \
    -e "ssh $SSH_OPTS" \
    "$local" "$REMOTE:$remote" 2>&1 | tail -5 \
    && echo "[$(stamp)] $name: push OK" \
    || echo "[$(stamp)] $name: push FAIL"

  echo "[$(stamp)] $name: PULL Hetzner→Mac ($remote → $local)"
  # Pull: archivos más recientes en Hetzner vienen a Mac
  rsync -az --update "${EXCLUDES[@]}" \
    -e "ssh $SSH_OPTS" \
    "$REMOTE:$remote" "$local" 2>&1 | tail -5 \
    && echo "[$(stamp)] $name: pull OK" \
    || echo "[$(stamp)] $name: pull FAIL"
done

echo "[$(stamp)] === louis-sync bidirectional end ==="
