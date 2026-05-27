#!/usr/bin/env bash
# 03-docker.sh — Instala Docker Engine + compose plugin desde el repo oficial.
# Idempotente.
set -euo pipefail

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }

SYSTEM_USER="${SYSTEM_USER:?SYSTEM_USER no definido}"

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  log "Docker + compose ya instalados ($(docker --version)) — skip"
  usermod -aG docker "$SYSTEM_USER" 2>/dev/null || true
  exit 0
fi

log "Instalando Docker Engine desde el repo oficial"

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list

DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

log "Habilitando docker.service"
systemctl enable --now docker
systemctl enable --now containerd

log "Añadiendo $SYSTEM_USER al grupo docker"
usermod -aG docker "$SYSTEM_USER"

docker --version
docker compose version

echo "  ✓ Docker listo"
