#!/usr/bin/env bash
# 02-user.sh — Crea usuario no-root con sudo, configura SSH key-only, deshabilita root login.
# Lee $SYSTEM_USER y $SYSTEM_USER_SSH_KEY del .env (heredados del padre).
set -euo pipefail

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }

SYSTEM_USER="${SYSTEM_USER:?SYSTEM_USER no definido en .env}"

# --- Crear usuario ------------------------------------------
if id -u "$SYSTEM_USER" >/dev/null 2>&1; then
  log "Usuario $SYSTEM_USER ya existe (skip create)"
else
  log "Creando usuario $SYSTEM_USER"
  adduser --disabled-password --gecos "" "$SYSTEM_USER"
fi
usermod -aG sudo "$SYSTEM_USER"

# Permitir sudo sin password (cómodo, está blindado por SSH key-only)
echo "$SYSTEM_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/$SYSTEM_USER
chmod 440 /etc/sudoers.d/$SYSTEM_USER

# --- Authorized keys ---------------------------------------
USER_HOME=$(eval echo "~$SYSTEM_USER")
mkdir -p "$USER_HOME/.ssh"
chmod 700 "$USER_HOME/.ssh"
touch "$USER_HOME/.ssh/authorized_keys"
chmod 600 "$USER_HOME/.ssh/authorized_keys"

# Copia las llaves de root al usuario nuevo (Hetzner ya las puso ahí)
if [[ -f /root/.ssh/authorized_keys ]]; then
  log "Copiando llaves de root a $SYSTEM_USER"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    grep -qxF "$line" "$USER_HOME/.ssh/authorized_keys" || echo "$line" >> "$USER_HOME/.ssh/authorized_keys"
  done < /root/.ssh/authorized_keys
fi

# Añade la llave del .env si existe (idempotente)
if [[ -n "${SYSTEM_USER_SSH_KEY:-}" ]]; then
  log "Añadiendo SYSTEM_USER_SSH_KEY al authorized_keys"
  grep -qxF "$SYSTEM_USER_SSH_KEY" "$USER_HOME/.ssh/authorized_keys" \
    || echo "$SYSTEM_USER_SSH_KEY" >> "$USER_HOME/.ssh/authorized_keys"
fi

chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$USER_HOME/.ssh"

# --- Endurecer sshd ----------------------------------------
log "Endureciendo SSH (root login deshabilitado, solo llaves)"
SSHD_CFG=/etc/ssh/sshd_config.d/99-louis.conf
cat > "$SSHD_CFG" <<EOF
# Generado por deploy.sh — no editar a mano
PermitRootLogin no
PasswordAuthentication no
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
AllowUsers $SYSTEM_USER
ClientAliveInterval 60
ClientAliveCountMax 3
MaxAuthTries 4
EOF

# Verifica config antes de aplicar
sshd -t -f /etc/ssh/sshd_config

# Reinicia sshd (Ubuntu 24 usa ssh.service)
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh

echo "  ✓ Usuario $SYSTEM_USER listo. Root login deshabilitado."
echo "    ⚠ IMPORTANTE: prueba SSH como $SYSTEM_USER en OTRA terminal antes de cerrar la actual."
echo "      ssh $SYSTEM_USER@$(hostname -I | awk '{print $1}')"
