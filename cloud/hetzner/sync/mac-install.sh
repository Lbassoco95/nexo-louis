#!/usr/bin/env bash
# mac-install.sh — Se corre EN LA MAC de Polo, una sola vez.
#
# Hace 4 cosas:
#   1) Genera una llave SSH dedicada (~/.ssh/louis_sync) si no existe.
#   2) Te muestra la llave pública para que la pegues en .env del VPS
#      (variable MAC_SYNC_SSH_PUB_KEY) — al re-correr deploy.sh allá,
#      hetzner-prepare.sh la añade a authorized_keys de `polo`.
#   3) Hace un push inicial dry-run para verificar conectividad.
#   4) Registra el launchd job (cada 5 min).
#
# Uso:
#   ./mac-install.sh LOUIS_REMOTE_HOST [SYSTEM_USER]
#   ./mac-install.sh donna.kawiil.mx polo

set -euo pipefail

REMOTE_HOST="${1:-}"
REMOTE_USER="${2:-polo}"

if [[ -z "$REMOTE_HOST" ]]; then
  echo "Uso: $0 LOUIS_REMOTE_HOST [SYSTEM_USER]"
  echo "Ej:   $0 donna.kawiil.mx polo"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_PUSH_SH="$SCRIPT_DIR/mac-push.sh"
PLIST_TEMPLATE="$SCRIPT_DIR/mac-launchd.plist"
SSH_KEY="$HOME/.ssh/louis_sync"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_LABEL="ai.kawiil.louissync"
PLIST_PATH="$LAUNCH_AGENTS/$PLIST_LABEL.plist"

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }

# ── 1) SSH key dedicada ──────────────────────────────────────
if [[ ! -f "$SSH_KEY" ]]; then
  log "Generando llave SSH dedicada en $SSH_KEY"
  ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -C "louis-sync@$(scutil --get ComputerName 2>/dev/null || hostname)"
else
  ok "Llave SSH ya existe: $SSH_KEY"
fi

chmod 600 "$SSH_KEY"
chmod 644 "$SSH_KEY.pub"

# ── 2) Mostrar pubkey al usuario ─────────────────────────────
PUBKEY=$(cat "$SSH_KEY.pub")
cat <<EOF

──────────────────────────────────────────────────────────
COPIA esta llave pública al .env del VPS Hetzner:

  MAC_SYNC_SSH_PUB_KEY="$PUBKEY"

Luego en el VPS (el paquete debe estar en /opt/louis):
  ssh ${REMOTE_USER}@${REMOTE_HOST}
  sudo -i
  cd /opt/louis
  nano .env                              # pega la línea de arriba, guarda
  bash sync/hetzner-prepare.sh           # re-ejecuta sólo este paso

Eso autorizará a esta Mac a hacer rsync sin password.
──────────────────────────────────────────────────────────

EOF

read -r -p "¿Ya pegaste la llave y corriste hetzner-prepare.sh? [s/N] " confirm
if [[ ! "$confirm" =~ ^[sSyY]$ ]]; then
  warn "OK, aborta. Cuando termines, vuelve a correr este script."
  exit 0
fi

# ── 3) Probar conectividad ──────────────────────────────────
log "Probando SSH a ${REMOTE_USER}@${REMOTE_HOST}"
if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
      "${REMOTE_USER}@${REMOTE_HOST}" 'echo OK' >/dev/null 2>&1; then
  ok "SSH funciona"
else
  warn "SSH falló. Revisa que:"
  echo "    - ${REMOTE_HOST} apunte a tu IP de Hetzner (dig ${REMOTE_HOST})"
  echo "    - El usuario ${REMOTE_USER} exista en el VPS"
  echo "    - La pubkey esté en /home/${REMOTE_USER}/.ssh/authorized_keys"
  exit 1
fi

# ── 4) Push inicial ──────────────────────────────────────────
log "Push inicial (puede tardar unos segundos)"
LOUIS_REMOTE_HOST="$REMOTE_HOST" LOUIS_REMOTE_USER="$REMOTE_USER" LOUIS_SSH_KEY="$SSH_KEY" \
  bash "$MAC_PUSH_SH"
ok "Push inicial completado"

# ── 5) Registrar launchd ─────────────────────────────────────
log "Instalando launchd job"
mkdir -p "$LAUNCH_AGENTS"

sed \
  -e "s|@@MAC_PUSH_SH@@|$MAC_PUSH_SH|g" \
  -e "s|@@LOUIS_REMOTE_HOST@@|$REMOTE_HOST|g" \
  -e "s|@@LOUIS_REMOTE_USER@@|$REMOTE_USER|g" \
  -e "s|@@LOUIS_SSH_KEY@@|$SSH_KEY|g" \
  -e "s|@@HOME@@|$HOME|g" \
  "$PLIST_TEMPLATE" > "$PLIST_PATH"

# Reload si ya estaba cargado
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"
ok "Launchd job activo: $PLIST_LABEL (cada 5 min)"

# ── 6) Hacer mac-push.sh ejecutable ──────────────────────────
chmod +x "$MAC_PUSH_SH"

cat <<EOF

✓ Sync Mac → Hetzner instalado.

  Logs:        ~/Library/Logs/louissync.log
  Errores:     ~/Library/Logs/louissync.err.log
  Plist:       $PLIST_PATH
  Push manual: LOUIS_REMOTE_HOST=$REMOTE_HOST LOUIS_REMOTE_USER=$REMOTE_USER \\
                  LOUIS_SSH_KEY=$SSH_KEY bash $MAC_PUSH_SH

  Para desinstalar:
    launchctl unload $PLIST_PATH && rm $PLIST_PATH

EOF
