#!/usr/bin/env bash
# mac-push-legal.sh — Sincroniza las BDs de SJF + DOF de la Mac → Hetzner.
# Se corre vía launchd cada 15 min (las BDs no cambian más rápido).
#
# Variables de entorno requeridas (las pone el plist):
#   LOUIS_REMOTE_HOST   ej: 204.168.131.21
#   LOUIS_REMOTE_USER   ej: polo
#   LOUIS_SSH_KEY       ej: ~/.ssh/louis_sync (o ~/.ssh/id_ed25519)
#
# Lo que sincroniza:
#   ~/sjf_biblioteca/biblioteca.db        → /opt/openclaw/legal/sjf/biblioteca.db
#   ~/sjf_biblioteca/logs/                → /opt/openclaw/legal/sjf/logs/   (sólo últimos 30 días)
#   ~/dof_biblioteca/biblioteca_dof.db    → /opt/openclaw/legal/dof/biblioteca_dof.db
#   ~/dof_biblioteca/logs/                → /opt/openclaw/legal/dof/logs/
#
# Logs locales: ~/Library/Logs/louis-legal-sync.log

set -uo pipefail

: "${LOUIS_REMOTE_HOST:?LOUIS_REMOTE_HOST no definido}"
: "${LOUIS_REMOTE_USER:?LOUIS_REMOTE_USER no definido}"
: "${LOUIS_SSH_KEY:?LOUIS_SSH_KEY no definido}"

REMOTE="${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}"
SSH_OPTS="-i $LOUIS_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30"

stamp() { date -Iseconds; }
echo "[$(stamp)] === legal-sync start ==="

# Asegura carpetas destino en Hetzner
ssh $SSH_OPTS "$REMOTE" "sudo mkdir -p /opt/openclaw/legal/sjf/logs /opt/openclaw/legal/dof/logs && sudo chown -R ${LOUIS_REMOTE_USER}:${LOUIS_REMOTE_USER} /opt/openclaw/legal" 2>/dev/null || true

# Helper: snapshot consistente de una BD SQLite usando .backup (no copia directa).
# Esto evita corrupciones cuando la app dueña está escribiendo (WAL no transferido).
SNAPSHOT_DIR="${TMPDIR:-/tmp}/louis-legal-snapshots"
mkdir -p "$SNAPSHOT_DIR"

snapshot_db() {
  local src="$1" dst="$2"
  # Si sqlite3 CLI no está, fallback a copia directa (puede corromperse)
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "[$(stamp)] WARN: sqlite3 CLI no instalado — copia directa (puede corromperse)"
    cp "$src" "$dst"
    return $?
  fi
  # .backup es atómico — toma snapshot consistente aunque la app esté escribiendo
  sqlite3 "$src" ".backup '$dst'"
  return $?
}

# === SJF ===
# IMPORTANTE (jun-2026): el SERVIDOR es ahora la fuente de la verdad del acervo SJF.
# Corre su propio harvester diario (/opt/openclaw/legal/sjf/sjf_harvest.py via systemd
# timer) que escribe directo en /opt/openclaw/legal/sjf/biblioteca.db. Si la Mac
# empujara su copia (que se queda atrás cuando la Mac duerme), PISARÍA lo que el
# servidor descargó y el acervo se revertiría. Por eso el push de SJF está APAGADO por
# defecto. Para reactivarlo (si algún día la Mac vuelve a ser la fuente): LOUIS_PUSH_SJF=1.
PUSH_SJF="${LOUIS_PUSH_SJF:-0}"
SJF_DB="$HOME/sjf_biblioteca/biblioteca.db"
SJF_LOGS="$HOME/sjf_biblioteca/logs"
SJF_SNAPSHOT="$SNAPSHOT_DIR/sjf_biblioteca.db"
if [[ "$PUSH_SJF" != "1" ]]; then
  echo "[$(stamp)] SJF push DESACTIVADO (servidor es la fuente de la verdad). LOUIS_PUSH_SJF=1 para reactivar."
elif [[ -f "$SJF_DB" ]]; then
  SIZE=$(du -h "$SJF_DB" | cut -f1)
  echo "[$(stamp)] SJF snapshot $SJF_DB ($SIZE) → $SJF_SNAPSHOT"
  if snapshot_db "$SJF_DB" "$SJF_SNAPSHOT"; then
    echo "[$(stamp)] SJF snapshot OK, subiendo a Hetzner"
    rsync -z --partial \
      -e "ssh $SSH_OPTS" \
      "$SJF_SNAPSHOT" "$REMOTE:/opt/openclaw/legal/sjf/biblioteca.db" \
      && echo "[$(stamp)] SJF db OK" \
      || echo "[$(stamp)] SJF rsync FAIL"
    rm -f "$SJF_SNAPSHOT"
  else
    echo "[$(stamp)] SJF snapshot FALLÓ — skip"
  fi
else
  echo "[$(stamp)] $SJF_DB no existe — skip"
fi

if [[ -d "$SJF_LOGS" ]]; then
  rsync -az --delete \
    --include='*.log' --include='*.out.log' --include='*.err.log' \
    --exclude='*' \
    -e "ssh $SSH_OPTS" \
    "$SJF_LOGS/" "$REMOTE:/opt/openclaw/legal/sjf/logs/" \
    && echo "[$(stamp)] SJF logs OK" \
    || echo "[$(stamp)] SJF logs FAIL"
fi

# === DOF ===
# El servidor ahora corre la extracción del DOF (download-contents/generate-pdfs)
# y es la FUENTE DE LA VERDAD. El push desde la Mac queda DESACTIVADO por defecto
# para no sobreescribir el avance del servidor. LOUIS_PUSH_DOF=1 para reactivar.
PUSH_DOF="${LOUIS_PUSH_DOF:-0}"
DOF_DB="$HOME/dof_biblioteca/biblioteca_dof.db"
DOF_LOGS="$HOME/dof_biblioteca/logs"
DOF_SNAPSHOT="$SNAPSHOT_DIR/biblioteca_dof.db"
if [[ "$PUSH_DOF" != "1" ]]; then
  echo "[$(stamp)] DOF push DESACTIVADO (servidor es la fuente de la verdad). LOUIS_PUSH_DOF=1 para reactivar."
elif [[ -f "$DOF_DB" ]]; then
  SIZE=$(du -h "$DOF_DB" | cut -f1)
  echo "[$(stamp)] DOF snapshot $DOF_DB ($SIZE) → $DOF_SNAPSHOT"
  if snapshot_db "$DOF_DB" "$DOF_SNAPSHOT"; then
    echo "[$(stamp)] DOF snapshot OK, subiendo a Hetzner"
    rsync -z --partial \
      -e "ssh $SSH_OPTS" \
      "$DOF_SNAPSHOT" "$REMOTE:/opt/openclaw/legal/dof/biblioteca_dof.db" \
      && echo "[$(stamp)] DOF db OK" \
      || echo "[$(stamp)] DOF rsync FAIL"
    rm -f "$DOF_SNAPSHOT"
  else
    echo "[$(stamp)] DOF snapshot FALLÓ — skip"
  fi
else
  echo "[$(stamp)] $DOF_DB no existe — skip"
fi

if [[ -d "$DOF_LOGS" ]]; then
  rsync -az --delete \
    --include='*.log' --include='*.out.log' --include='*.err.log' \
    --exclude='*' \
    -e "ssh $SSH_OPTS" \
    "$DOF_LOGS/" "$REMOTE:/opt/openclaw/legal/dof/logs/" \
    && echo "[$(stamp)] DOF logs OK" \
    || echo "[$(stamp)] DOF logs FAIL"
fi

echo "[$(stamp)] === legal-sync end ==="
