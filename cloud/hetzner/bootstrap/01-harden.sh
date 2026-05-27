#!/usr/bin/env bash
# 01-harden.sh — Hardening básico de Ubuntu para servidor expuesto a internet.
# Idempotente: se puede correr varias veces.
set -euo pipefail

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }

log "Actualizando paquetes"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get -yq -o Dpkg::Options::="--force-confnew" upgrade

log "Instalando utilidades base + seguridad"
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
  ufw fail2ban unattended-upgrades curl wget git nano vim htop \
  ca-certificates gnupg lsb-release jq rsync cron

# --- ufw -----------------------------------------------------
log "Configurando ufw (firewall)"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP (Caddy ACME)'
ufw allow 443/tcp comment 'HTTPS (Caddy)'
ufw --force enable
ufw status verbose | sed 's/^/    /'

# --- fail2ban ------------------------------------------------
log "Configurando fail2ban jail SSH"
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = %(sshd_log)s
backend = %(sshd_backend)s
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

# --- swap (1.5 GB) -------------------------------------------
if [[ ! -f /swapfile ]]; then
  log "Creando swap de 1.5 GB"
  fallocate -l 1536M /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
  sysctl --quiet -p /etc/sysctl.d/99-swappiness.conf
else
  log "Swap ya existe en /swapfile (skip)"
fi

# --- unattended-upgrades -------------------------------------
log "Activando unattended-upgrades (parches automáticos)"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# --- timezone --------------------------------------------------
log "Ajustando timezone a America/Mexico_City"
timedatectl set-timezone America/Mexico_City || true

# --- sysctl básico --------------------------------------------
log "Endureciendo sysctl"
cat > /etc/sysctl.d/99-hardening.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.log_martians = 1
EOF
sysctl --quiet -p /etc/sysctl.d/99-hardening.conf

echo "  ✓ Hardening aplicado"
