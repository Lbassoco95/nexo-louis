#!/usr/bin/env bash
# mac-push.sh — Se corre en la Mac (vía launchd cada 5 min).
# Empuja ~/.openclaw/spaces/ → polo@HETZNER:/opt/openclaw-sync/spaces/
#
# Variables de entorno requeridas (las pone el plist):
#   LOUIS_REMOTE_HOST   ej: donna.kawiil.mx
#   LOUIS_REMOTE_USER   ej: polo
#   LOUIS_SSH_KEY       ej: ~/.ssh/louis_sync
#
# Logs: ~/Library/Logs/louissync.log (lo rota launchd vía StandardOutPath).

set -euo pipefail

: "${LOUIS_REMOTE_HOST:?LOUIS_REMOTE_HOST no definido}"
: "${LOUIS_REMOTE_USER:?LOUIS_REMOTE_USER no definido}"
: "${LOUIS_SSH_KEY:?LOUIS_SSH_KEY no definido}"

LOCAL_SRC="$HOME/.openclaw"
REMOTE_DST="/opt/openclaw-sync"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER_FILE="$SCRIPT_DIR/rsync-files.txt"

# Si no hay carpeta local todavía, no falles silenciosamente, anuncia y sal.
if [[ ! -d "$LOCAL_SRC" ]]; then
  echo "[$(date -Iseconds)] $LOCAL_SRC no existe — skip"
  exit 0
fi
if [[ ! -f "$FILTER_FILE" ]]; then
  echo "[$(date -Iseconds)] Falta filter file: $FILTER_FILE" >&2
  exit 1
fi

echo "[$(date -Iseconds)] sync → ${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}:${REMOTE_DST}"

# rsync con:
#   -a            archive (recursive, preserva permisos/symlinks/timestamps)
#   -z            compresión
#   --delete      borra en destino lo que ya no está en origen (dentro del filtro)
#   --filter ". X" merge global del archivo X (sin "." sería per-directory, no funciona aquí)
#   -e ssh        con la llave dedicada
#   --partial-dir para resumir si se corta
rsync -az \
  --delete \
  --filter=". $FILTER_FILE" \
  --partial-dir=".rsync-partial" \
  -e "ssh -i $LOUIS_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30" \
  "$LOCAL_SRC/" \
  "${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}:${REMOTE_DST}/"

echo "[$(date -Iseconds)] sync OK"
