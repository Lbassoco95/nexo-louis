#!/usr/bin/env bash
# backup-spaces-agents.sh — Backup TAR de TODO /opt/openclaw/spaces/ y agentes.
# Se ejecuta automáticamente antes de cualquier consolidación o refactor masivo.
# Idempotente — se puede correr múltiples veces, cada corrida genera un .tgz nuevo.
#
# Uso (en Hetzner, sudo):
#   sudo bash backup-spaces-agents.sh [tag]
#
# tag opcional → se agrega al nombre del archivo (ej: "pre-meta-bootstrap")

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "  \033[1;36m· %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

TAG="${1:-manual}"
SYSTEM_USER="${SYSTEM_USER:-polo}"
BACKUP_DIR="/opt/openclaw/backups"
SPACES_DIR="/opt/openclaw/spaces"
AGENTS_SUBDIR="$SPACES_DIR/general/agents"

mkdir -p "$BACKUP_DIR"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$BACKUP_DIR"

if [[ ! -d "$SPACES_DIR" ]]; then
  fail "$SPACES_DIR no existe — nada que respaldar"
fi

TS=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/spaces-${TAG}-${TS}.tgz"

log "Tarball de $SPACES_DIR → $OUT"
tar -czf "$OUT" -C "$(dirname "$SPACES_DIR")" "$(basename "$SPACES_DIR")" 2>/dev/null
chown "$SYSTEM_USER":"$SYSTEM_USER" "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
ok "Backup creado: $OUT ($SIZE)"

# Conteo de agentes
if [[ -d "$AGENTS_SUBDIR" ]]; then
  TOTAL=$(find "$AGENTS_SUBDIR" -name "*.md" -type f | wc -l)
  LEGAL=$(find "$AGENTS_SUBDIR" -name "legal-*.md" -type f | wc -l)
  KAWIIL=$(find "$AGENTS_SUBDIR" -name "kawiil*.md" -o -name "amatl*.md" -o -name "nelli*.md" -o -name "tepantli*.md" -o -name "matiox*.md" 2>/dev/null | wc -l)
  OTHER=$(( TOTAL - LEGAL - KAWIIL ))
  ok "Agentes respaldados — total: $TOTAL"
  echo "    legal-*: $LEGAL"
  echo "    kawiil/amatl/nelli/tepantli/matiox: $KAWIIL"
  echo "    otros: $OTHER"
fi

# Lista últimos 5 backups
echo ""
log "Últimos 5 backups en $BACKUP_DIR:"
ls -lh "$BACKUP_DIR"/spaces-*.tgz 2>/dev/null | tail -5 | awk '{print "    " $9 " ("$5")"}'

# Mantén solo los 20 más recientes (no acumular GB)
TO_DELETE=$(ls -1t "$BACKUP_DIR"/spaces-*.tgz 2>/dev/null | tail -n +21)
if [[ -n "$TO_DELETE" ]]; then
  echo ""
  log "Limpiando backups antiguos (mantengo 20 más recientes):"
  echo "$TO_DELETE" | while read f; do
    rm -f "$f"
    echo "    eliminado: $(basename "$f")"
  done
fi
