#!/usr/bin/env bash
# setup-cloudflare-tunnel.sh
# Configura Cloudflare Tunnel + DNS para Yoltik AI.
# Prerequisito: dominio kawiil.mx en Cloudflare, cuenta Zero Trust activada,
#               y haber corrido install-yoltik-ai.sh (OpenClaw funcionando).
# Uso: bash setup-cloudflare-tunnel.sh

set -euo pipefail

GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
RED=$'\033[0;31m'
NC=$'\033[0m'

log()  { printf "%s\n" "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { printf "%s\n" "${GREEN}✓${NC} $*"; }
warn() { printf "%s\n" "${YELLOW}⚠${NC} $*"; }
err()  { printf "%s\n" "${RED}✗${NC} $*" >&2; }

if ! command -v cloudflared >/dev/null 2>&1; then
  err "cloudflared no instalado. Corre: brew install cloudflared"
  exit 1
fi

log "Paso 1/5: Login en Cloudflare (abre el browser)..."
cloudflared tunnel login
ok "Login completado"

log "Paso 2/5: Creando túnel 'yoltik-ai'..."
if cloudflared tunnel list 2>/dev/null | grep -q yoltik-ai; then
  warn "Túnel 'yoltik-ai' ya existía. Lo reutilizo."
  TUNNEL_UUID=$(cloudflared tunnel list | grep yoltik-ai | awk '{print $1}')
else
  cloudflared tunnel create yoltik-ai
  TUNNEL_UUID=$(cloudflared tunnel list | grep yoltik-ai | awk '{print $1}')
fi
ok "Túnel UUID: $TUNNEL_UUID"

log "Paso 3/5: Generando config.yml..."
TEMPLATE_PATH="$(dirname "$0")/cloudflared-config.yml.template"
TARGET_CONFIG="$HOME/.cloudflared/config.yml"

if [[ ! -f "$TEMPLATE_PATH" ]]; then
  err "Template no encontrado en $TEMPLATE_PATH"
  exit 1
fi

USER_NAME="$(whoami)"
sed -e "s|<USER>|$USER_NAME|g" \
    -e "s|<TUNNEL_UUID>|$TUNNEL_UUID|g" \
    "$TEMPLATE_PATH" > "$TARGET_CONFIG"

chmod 600 "$TARGET_CONFIG"
ok "Config generado en $TARGET_CONFIG"

log "Paso 4/5: Creando DNS record ai.kawiil.mx → túnel..."
cloudflared tunnel route dns yoltik-ai ai.kawiil.mx
ok "DNS configurado. Propagación: 0-2 min."

log "Paso 5/5: Instalando como servicio (auto-start)..."
sudo cloudflared service install
ok "Servicio instalado"

# Esperar y verificar
sleep 5
log "Verificando que el túnel está activo..."
if cloudflared tunnel info yoltik-ai 2>/dev/null | grep -q "Connector"; then
  ok "Túnel conectado a Cloudflare"
else
  warn "Túnel parece no estar conectado. Revisa: cloudflared tunnel info yoltik-ai"
fi

cat <<EOF

${GREEN}═══════════════════════════════════════════════════${NC}
${GREEN}  Cloudflare Tunnel configurado${NC}
${GREEN}═══════════════════════════════════════════════════${NC}

⚠ TODAVÍA NO ES SEGURO ABRIR ai.kawiil.mx EN EL BROWSER.
   Falta configurar Cloudflare Access (autenticación SSO + MFA).

Siguientes pasos manuales en el dashboard de Cloudflare:

1. https://one.dash.cloudflare.com → Settings → Authentication
   → Login methods → Add → Google
   (autoriza la conexión a tu Workspace de kawiil.mx)

2. Access → Applications → Add an application → Self-hosted
   - Application name: Yoltik AI
   - Subdomain: ai
   - Domain: kawiil.mx
   - Session duration: 8 hours
   - Identity providers: Google

3. Crea policy "Directivos":
   - Action: Allow
   - Include → Emails ending in @kawiil.mx
   - Require → Authentication method = Any MFA

4. Aplica WAF rules custom (ver waf-rules.txt en esta carpeta)

5. Verifica con:
   bash $(dirname "$0")/validate-cloudflare.sh

Hasta no completar 1-4, ai.kawiil.mx redirigirá a OpenClaw SIN auth.
${RED}Es decir: NO ABRAS LA URL todavía.${NC}

EOF
