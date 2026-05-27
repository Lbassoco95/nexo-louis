#!/usr/bin/env bash
# killswitch.sh
# Corta TODO el acceso a la instancia de OpenClaw ante sospecha.
# Uso: bash killswitch.sh
# Idempotente: puedes correrlo varias veces sin daño.

set -euo pipefail

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

echo "${RED}╔═══════════════════════════════════════╗${NC}"
echo "${RED}║   YOLTIK AI · KILLSWITCH ACTIVADO     ║${NC}"
echo "${RED}╚═══════════════════════════════════════╝${NC}"
echo ""

# 1. Detener cloudflared (si está corriendo — fase 2+)
echo "1/4 Deteniendo Cloudflare Tunnel..."
if pgrep -x cloudflared >/dev/null 2>&1; then
  sudo launchctl unload /Library/LaunchDaemons/com.cloudflare.cloudflared.plist 2>/dev/null || true
  sudo killall cloudflared 2>/dev/null || true
  echo "${GREEN}✓${NC} cloudflared detenido"
else
  echo "${YELLOW}—${NC} cloudflared no estaba corriendo"
fi

# 2. Detener OpenClaw daemon
echo "2/4 Deteniendo OpenClaw..."
openclaw daemon stop 2>/dev/null || openclaw stop 2>/dev/null || true
pkill -f openclaw 2>/dev/null || true
echo "${GREEN}✓${NC} OpenClaw detenido"

# 3. Snapshot del audit log antes de cualquier cosa
echo "3/4 Snapshot del audit log..."
SNAP_DIR="$HOME/.openclaw/incident-snapshots/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SNAP_DIR"
cp -R "$HOME/.openclaw/logs/" "$SNAP_DIR/" 2>/dev/null || true
cp "$HOME/.openclaw/openclaw.json" "$SNAP_DIR/" 2>/dev/null || true
echo "${GREEN}✓${NC} Snapshot guardado en $SNAP_DIR"

# 4. Mostrar last actions del agente para revisión rápida
echo "4/4 Últimas 30 acciones del agente:"
echo "─────────────────────────────────────────"
tail -n 30 "$HOME/.openclaw/logs/audit.log" 2>/dev/null || echo "(sin audit log todavía)"
echo "─────────────────────────────────────────"

cat <<EOF

${GREEN}KILLSWITCH COMPLETADO.${NC}

El servicio está OFF. Para reactivar (después de investigar):
  openclaw daemon start
  sudo launchctl load /Library/LaunchDaemons/com.cloudflare.cloudflared.plist  # si usas CF

Recomendado antes de reactivar:
  1. Revisar el snapshot en $SNAP_DIR
  2. Si hubo acceso no autorizado: rotar API keys de Anthropic
  3. Si fue por CVE: actualizar OpenClaw antes de reiniciar
  4. Cambiar token del gateway: openssl rand -hex 32 > ~/.openclaw/gateway-token.txt

EOF
