#!/usr/bin/env bash
# install-playwright.sh — Instala Playwright + Chromium headless en Hetzner.
# Sólo necesita correrse una vez (idempotente).
#
# Costo de disco: ~250 MB (Chromium ~140 MB + deps de sistema ~110 MB).
#
# Uso (en Hetzner como root o sudo):
#   sudo bash install-playwright.sh

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "  \033[1;36m· %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

SYSTEM_USER="${SYSTEM_USER:-polo}"

echo "════════════════════════════════════════"
echo "  Instalando Playwright + Chromium"
echo "════════════════════════════════════════"

# 1) pip install playwright
if python3 -c "import playwright" 2>/dev/null; then
  ok "playwright (Python) ya instalado"
else
  log "Instalando playwright Python..."
  pip3 install --quiet --break-system-packages playwright
  ok "playwright Python instalado"
fi

# 2) System deps para Chromium en Ubuntu 24.04
log "Instalando dependencias de sistema para Chromium..."
DEBIAN_FRONTEND=noninteractive playwright install-deps chromium 2>&1 | tail -3
ok "Dependencias del sistema OK"

# 3) Bajar el binario de Chromium (~140 MB)
# IMPORTANTE: tiene que correr como el usuario que después va a usar el browser.
# Si corre como root, el binario queda en /root/.cache y polo no lo ve.
# Usamos un dir compartido en /opt/openclaw para evitar el problema.
PLAYWRIGHT_BROWSERS_DIR="/opt/openclaw/playwright-browsers"
mkdir -p "$PLAYWRIGHT_BROWSERS_DIR"
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$PLAYWRIGHT_BROWSERS_DIR"

log "Bajando Chromium a $PLAYWRIGHT_BROWSERS_DIR (compartido entre root y $SYSTEM_USER)..."
sudo -u "$SYSTEM_USER" PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_DIR" \
  playwright install chromium 2>&1 | tail -5

# Verifica que el binario quedó
CHROMIUM_BIN=$(find "$PLAYWRIGHT_BROWSERS_DIR" -name "chrome-headless-shell" -o -name "chrome" 2>/dev/null | head -1)
if [[ -n "$CHROMIUM_BIN" ]]; then
  ok "Chromium instalado: $CHROMIUM_BIN"
else
  fail "Chromium no quedó en $PLAYWRIGHT_BROWSERS_DIR — revisa permisos"
fi

# Inyecta PLAYWRIGHT_BROWSERS_PATH en openclaw.env para que browser_runner.py lo use
if [[ -f /opt/openclaw/openclaw.env ]]; then
  sed -i '/^PLAYWRIGHT_BROWSERS_PATH=/d' /opt/openclaw/openclaw.env
  echo "PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_DIR" >> /opt/openclaw/openclaw.env
  ok "openclaw.env actualizado con PLAYWRIGHT_BROWSERS_PATH"
fi

# 4) Crear directorios de estado/cache
mkdir -p /opt/openclaw/state /opt/openclaw/state/browser-cache /opt/openclaw/logs
chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/state /opt/openclaw/logs
chmod 0700 /opt/openclaw/state/browser-cache
touch /opt/openclaw/logs/browser-runner.log
chown "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/logs/browser-runner.log
ok "Directorios de estado preparados"

# 5) Smoke test — abrir example.com (con PLAYWRIGHT_BROWSERS_PATH apuntando al dir compartido)
log "Smoke test: abriendo example.com con headless chromium..."
TEST_OUT=$(echo '{"cmd":"navigate","url":"https://example.com"}' | sudo -u "$SYSTEM_USER" PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_DIR" python3 /opt/openclaw/scripts/browser_runner.py 2>&1)
if echo "$TEST_OUT" | grep -q '"ok": true'; then
  ok "Browser funciona — Chromium pudo abrir example.com"
  TITLE=$(echo "$TEST_OUT" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('title',''))" 2>/dev/null)
  [[ -n "$TITLE" ]] && echo "    Título capturado: $TITLE"
else
  warn "Smoke test falló:"
  echo "$TEST_OUT" | head -20
fi

# 6) Reinicia services para que tomen PLAYWRIGHT_BROWSERS_PATH del env file
for svc in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  systemctl restart "$svc" 2>/dev/null && ok "$svc reiniciado (toma PLAYWRIGHT_BROWSERS_PATH)" || warn "$svc no reinició"
done

echo ""
ok "Listo. Prueba desde Telegram: 'navega a kawiil.mx y resúmeme la home'"
