#!/usr/bin/env bash
# mac-push-projects.sh — Sincroniza ~/Documents/Claude/Projects/ de la Mac → Hetzner.
# Así Donna ve los documentos de tus proyectos (Dazon, Vizum, Kawiil*, Yoltik*, …)
# y puede usarlos para mantener PROJECTS.md al día.
# Nota: se evaluó ampliar la raíz a ~/Documents/Claude, pero ahí solo vive Projects/
# (no hay docs sueltos), y subir el padre anidaría todo bajo "Projects/" en Hetzner,
# rompiendo la estructura de proyectos que lee Donna. Se mantiene la raíz en Projects/.
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

# Sincroniza SOLO documentos de texto/ofimática (lo que Donna puede leer).
# ORDEN DE FILTROS (importante): primero excluir carpetas de código/VCS para que
# rsync NI DESCIENDA en ellas (evita el ruido 'cannot delete non-empty directory'
# de .git/node_modules). Luego incluir dirs restantes + los tipos de archivo.
# --exclude='/_Louis-Generados/' EXCLUYE por completo la carpeta de generados de
# Donna del push: rsync ni la sube ni la borra. Es crítico — un 'P dir/' solo
# protege la carpeta pero NO su contenido, así que el --delete borraba los PDFs
# que Donna genera (existen solo en Hetzner). El exclude anclado lo evita: los
# archivos excluidos no se transfieren ni se eliminan (sin --delete-excluded).
rsync -rz --delete --prune-empty-dirs --partial \
  --exclude='/_Louis-Generados/' \
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
  && echo "[$(stamp)] projects push OK" \
  || echo "[$(stamp)] projects push terminó con avisos (normal si hay repos de código adentro)"

# === PULL: baja lo que Donna generó (Hetzner → Mac) ===
# Bidireccional seguro: solo bajamos _Louis-Generados/ (namespace separado de tus
# docs), así nunca hay conflicto con lo que tú editas en la Mac.
GEN_LOCAL="$PROJECTS_SRC/_Louis-Generados"
mkdir -p "$GEN_LOCAL"
# Sin -z: openrsync (rsync nativo de macOS) puede dejar la transferencia en 0
# bytes con compresión en descarga. Es poco volumen, no hace falta comprimir.
rsync -r --partial --stats \
  -e "ssh $SSH_OPTS" \
  "$REMOTE:$DEST/_Louis-Generados/" "$GEN_LOCAL/" \
  && echo "[$(stamp)] generados pull OK (Hetzner→Mac)" \
  || echo "[$(stamp)] generados pull: nada que bajar todavía"

# Escribe un índice simple para que Donna sepa qué proyectos y archivos hay.
ssh $SSH_OPTS "$REMOTE" "cd $DEST 2>/dev/null && \
  { echo '# Índice de proyectos (sync Mac→Hetzner)'; echo \"actualizado: \$(date -Iseconds)\"; echo; \
    for d in */; do n=\$(find \"\$d\" -type f | wc -l | tr -d ' '); echo \"- \${d%/} (\$n archivos)\"; done; } \
  > $DEST/_INDICE.md" 2>/dev/null \
  && echo "[$(stamp)] índice escrito" \
  || echo "[$(stamp)] no pude escribir índice"

echo "[$(stamp)] === projects-sync end ==="
