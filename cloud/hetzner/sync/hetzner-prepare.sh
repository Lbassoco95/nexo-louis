#!/usr/bin/env bash
# hetzner-prepare.sh — Se corre en el VPS Hetzner.
# Prepara la zona de "staging" donde la Mac empuja archivos, y autoriza la pubkey del Mac.
#
# Se invoca:
#   - desde deploy.sh (paso [5/6]) — usa $SYSTEM_USER y $MAC_SYNC_SSH_PUB_KEY del .env
#   - manualmente: sudo SYSTEM_USER=polo MAC_SYNC_SSH_PUB_KEY="ssh-ed25519 AAAA..." bash hetzner-prepare.sh

set -euo pipefail

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }

SYSTEM_USER="${SYSTEM_USER:?SYSTEM_USER no definido}"
SYNC_DIR="${SYNC_DIR:-/opt/openclaw-sync}"

# Si vienes de deploy.sh el .env ya está exportado; si vienes a mano, intenta cargarlo.
if [[ -z "${MAC_SYNC_SSH_PUB_KEY:-}" && -f "/opt/louis/.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source /opt/louis/.env; set +a
fi

# ── Crear staging dir ────────────────────────────────────────
log "Preparando $SYNC_DIR (propietario: $SYSTEM_USER)"
mkdir -p "$SYNC_DIR/spaces"
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$SYNC_DIR"
chmod 750 "$SYNC_DIR"

# ── Autorizar llave del Mac ──────────────────────────────────
USER_HOME=$(eval echo "~$SYSTEM_USER")
AUTH_KEYS="$USER_HOME/.ssh/authorized_keys"
mkdir -p "$USER_HOME/.ssh"
touch "$AUTH_KEYS"
chmod 700 "$USER_HOME/.ssh"
chmod 600 "$AUTH_KEYS"
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$USER_HOME/.ssh"

if [[ -n "${MAC_SYNC_SSH_PUB_KEY:-}" ]]; then
  if grep -qxF "$MAC_SYNC_SSH_PUB_KEY" "$AUTH_KEYS"; then
    log "Pubkey del Mac ya autorizada"
  else
    log "Autorizando MAC_SYNC_SSH_PUB_KEY"
    echo "$MAC_SYNC_SSH_PUB_KEY" >> "$AUTH_KEYS"
  fi
else
  log "MAC_SYNC_SSH_PUB_KEY vacío — primero corre mac-install.sh en la Mac y pega la pubkey en .env"
fi

# ── Log file para hetzner-apply.sh ────────────────────────────
touch /var/log/louis-sync.log
chown "$SYSTEM_USER":"$SYSTEM_USER" /var/log/louis-sync.log

# ── Sudo NOPASSWD selectivo para que el cron del usuario pueda recargar OpenClaw ──
SUDOERS_FILE="/etc/sudoers.d/louis-sync"
cat > "$SUDOERS_FILE" <<EOF
# Permite al usuario $SYSTEM_USER recargar/reiniciar OpenClaw sin password.
# Lo usa sync/hetzner-apply.sh después de aplicar deltas que tocan system-prompt.
$SYSTEM_USER ALL=(root) NOPASSWD: /bin/systemctl reload openclaw
$SYSTEM_USER ALL=(root) NOPASSWD: /bin/systemctl restart openclaw
$SYSTEM_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reload openclaw
$SYSTEM_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart openclaw
EOF
chmod 440 "$SUDOERS_FILE"
# Validar sintaxis
visudo -cf "$SUDOERS_FILE" >/dev/null || { rm -f "$SUDOERS_FILE"; echo "  ✗ sudoers inválido — abort"; exit 1; }

echo "  ✓ Sync target listo en $SYNC_DIR"
