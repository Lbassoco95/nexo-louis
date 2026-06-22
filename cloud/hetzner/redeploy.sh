#!/usr/bin/env bash
# redeploy.sh — Despliegue ligero: copia los servicios .py del repo al runtime
# (/opt/openclaw/scripts) y reinicia los servicios. NO toca datos (spaces/, entregables/,
# credenciales). Idempotente. Correr DESPUÉS de un `git pull`.
#
# Uso (como root, desde el repo):
#   sudo bash cloud/hetzner/redeploy.sh
#
# A diferencia de deploy.sh (orquestador completo con bootstrap/Caddy/seeds), esto solo
# actualiza la lógica de los servicios — el caso del día a día.
set -euo pipefail

USER_OWN="${SYSTEM_USER:-polo}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/services" && pwd)"
DST=/opt/openclaw/scripts

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "Corre como root (sudo)."
mkdir -p "$DST" "$DST/m365"

SERVICES=(louis_core.py telegram-bridge.py slack-bridge.py scheduler.py
          openclaw_gateway.py self_update.py browser_runner.py cerebro_kawiil_mcp.py)

# 1) Gate de compilación — si algún .py no compila, abortamos ANTES de copiar nada.
log "Verificando que los servicios compilan"
for svc in "${SERVICES[@]}"; do
  [[ -f "$SRC/$svc" ]] || continue
  python3 -m py_compile "$SRC/$svc" || fail "$svc no compila — abortando sin tocar el runtime."
done
[[ -f "$SRC/m365.py" ]] && { python3 -m py_compile "$SRC/m365.py" || fail "m365.py no compila."; }
ok "Todo compila"

# 2) Copiar al runtime
log "Copiando servicios a $DST"
for svc in "${SERVICES[@]}"; do
  [[ -f "$SRC/$svc" ]] && install -m 0755 -o "$USER_OWN" -g "$USER_OWN" "$SRC/$svc" "$DST/$svc" && ok "$svc"
done
[[ -f "$SRC/m365.py" ]] && install -m 0755 -o "$USER_OWN" -g "$USER_OWN" "$SRC/m365.py" "$DST/m365/m365.py"

# 3) Reiniciar servicios (solo los que existen/están activos)
log "Reiniciando servicios"
for unit in telegram-bridge slack-bridge scheduler openclaw-gateway cerebro-kawiil; do
  if systemctl list-unit-files "${unit}.service" >/dev/null 2>&1 && \
     systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl restart "$unit" && ok "↻ $unit" || warn "$unit no reinició — journalctl -u $unit -n 30"
  else
    warn "$unit no instalado, omito"
  fi
done

echo ""
ok "Redeploy completo."
echo "Verifica:  systemctl is-active telegram-bridge cerebro-kawiil scheduler"
