#!/usr/bin/env bash
# mac-push-projects.sh — Sincroniza ~/Documents/Claude/Projects/ de la Mac → Hetzner.
# Así Louis ve los documentos de tus proyectos (Dazon, Vizum, Kawiil*, Yoltik*, …)
# y puede usarlos para mantener PROJECTS.md al día.
#
# Se corre vía launchd cada 30 min (los docs no cambian más rápido).
#
# Variables de entorno requeridas (las pone el plist):
#   LOUIS_REMOTE_HOST   ej: 204.168.131.21
#   LOUIS_REMOTE_USER   ej: polo
#   LOUIS_SSH_KEY       ej: ~/.ssh/id_ed25519
# Opcional:
#   PROJECTS_SRC        default: ~/Documents/Claude/Projects
#
# Destino en Hetzner: /opt/openclaw/projects/
# Logs locales: ~/Library/Logs/louis-projects-sync.log
#
# NOTA macOS/TCC: si corre desde launchd y falla con "Operation not permitted",
# hay que dar "Acceso a disco completo" a /bin/bash en
# Ajustes → Privacidad y seguridad → Acceso a disco completo.

set -uo pipefail

: "${LOUIS_REMOTE_HOST:?LOUIS_REMOTE_HOST no definido}"
: "${LOUIS_REMOTE_USER:?LOUIS_REMOTE_USER no definido}"
: "${LOUIS_SSH_KEY:?LOUIS_SSH_KEY no definido}"

PROJECTS_SRC="${PROJECTS_SRC:-$HOME/Documents/Claude/Projects}"
REMOTE="${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}"
SSH_OPTS="-i $LOUIS_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30"
DEST="/opt/openclaw/projects"

stamp() { date -Iseconds; }
echo "[$(stamp)] === projects-sync start ==="

if [[ ! -d "$PROJECTS_SRC" ]]; then
  echo "[$(stamp)] $PROJECTS_SRC no existe — skip"
  exit 0
fi

# Asegura carpeta destino en Hetzner
ssh $SSH_OPTS "$REMOTE" "sudo mkdir -p $DEST && sudo chown -R ${LOUIS_REMOTE_USER}:${LOUIS_REMOTE_USER} $DEST" 2>/dev/null || \
  ssh $SSH_OPTS "$REMOTE" "mkdir -p $DEST" 2>/dev/null || true

# Sincroniza SOLO documentos de texto/ofimática (lo que Louis puede leer).
# ORDEN DE FILTROS (importante): primero excluir carpetas de código/VCS para que
# rsync NI DESCIENDA en ellas (evita el ruido 'cannot delete non-empty directory'
# de .git/node_modules). Luego incluir dirs restantes + los tipos de archivo.
rsync -rz --delete --prune-empty-dirs --partial \
  --exclude='.git' --exclude='.git/' \
  --exclude='node_modules' --exclude='.cursor' --exclude='.vscode' \
  --exclude='.next' --exclude='dist' --exclude='build' --exclude='venv' \
  --exclude='.DS_Store' \
  --include='*/' \
  --include='*.md' --include='*.markdown' --include='*.txt' \
  --include='*.pdf' --include='*.docx' --include='*.doc' \
  --include='*.csv' --include='*.xlsx' --include='*.xls' \
  --include='*.pptx' --include='*.json' --include='*.rtf' \
  --exclude='*' \
  -e "ssh $SSH_OPTS" \
  "$PROJECTS_SRC/" "$REMOTE:$DEST/" \
  && echo "[$(stamp)] projects rsync OK" \
  || echo "[$(stamp)] projects rsync terminó con avisos (normal si hay repos de código adentro)"

# Escribe un índice simple para que Louis sepa qué proyectos y archivos hay.
ssh $SSH_OPTS "$REMOTE" "cd $DEST 2>/dev/null && \
  { echo '# Índice de proyectos (sync Mac→Hetzner)'; echo \"actualizado: \$(date -Iseconds)\"; echo; \
    for d in */; do n=\$(find \"\$d\" -type f | wc -l | tr -d ' '); echo \"- \${d%/} (\$n archivos)\"; done; } \
  > $DEST/_INDICE.md" 2>/dev/null \
  && echo "[$(stamp)] índice escrito" \
  || echo "[$(stamp)] no pude escribir índice"

echo "[$(stamp)] === projects-sync end ==="
