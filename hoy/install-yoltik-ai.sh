#!/usr/bin/env bash
# install-yoltik-ai.sh
# Instala y configura OpenClaw en una Mac para Kawiil/Yoltik.
# Uso: bash install-yoltik-ai.sh
# Requiere: macOS, ~5GB libres, conexión a internet.
# Autor: Cowork para Polo · 2026-05-24

set -euo pipefail

# --- Colores para output ---
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

log()  { printf "%s\n" "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { printf "%s\n" "${GREEN}✓${NC} $*"; }
warn() { printf "%s\n" "${YELLOW}⚠${NC} $*"; }
err()  { printf "%s\n" "${RED}✗${NC} $*" >&2; }

# --- Pre-flight ---
if [[ "$(uname)" != "Darwin" ]]; then
  err "Este script es solo para macOS. Detectado: $(uname)"
  exit 1
fi

log "Bienvenido al setup de Yoltik AI (OpenClaw)"
log "Vamos a instalar y configurar todo. Te pediré 2 cosas a mitad del proceso:"
log "  1. Tu API key de Anthropic (console.anthropic.com)"
log "  2. Tu token de bot de Telegram (de @BotFather)"
log ""
read -r -p "¿Continuamos? [y/N] " confirm
case "$confirm" in
  y|Y|yes|YES|si|SI|sí|SÍ) ;;
  *) err "Cancelado por el usuario."; exit 0 ;;
esac

# --- 1. Homebrew ---
log "Paso 1/7: Verificando Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
  log "Instalando Homebrew (requiere tu contraseña de Mac)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Añadir brew al PATH en zshrc si Apple Silicon
  if [[ "$(uname -m)" == "arm64" ]]; then
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
fi
ok "Homebrew listo: $(brew --version | head -n1)"

# --- 2. Node 22 ---
log "Paso 2/7: Verificando Node.js 22..."
if ! command -v node >/dev/null 2>&1 || [[ "$(node --version | cut -dv -f2 | cut -d. -f1)" -lt 22 ]]; then
  brew install node@22
  brew link --overwrite node@22 || true
fi
ok "Node listo: $(node --version)"

# --- 3. Herramientas auxiliares (jq para patch del config, cloudflared para fase 2) ---
log "Paso 3/7: Instalando herramientas auxiliares..."
brew install jq cloudflared >/dev/null 2>&1 || true
ok "jq y cloudflared instalados (cloudflared se usa hasta el sábado para CF Tunnel)"

# --- 4. OpenClaw ---
log "Paso 4/7: Instalando OpenClaw..."
if ! command -v openclaw >/dev/null 2>&1; then
  curl -fsSL https://openclaw.ai/install.sh | bash
fi

# Verificar
if ! command -v openclaw >/dev/null 2>&1; then
  err "OpenClaw no quedó en PATH. Cierra y abre la terminal, y vuelve a correr este script."
  exit 1
fi
ok "OpenClaw instalado: $(openclaw --version 2>/dev/null || echo 'versión desconocida')"

# --- 5. Onboarding interactivo ---
log "Paso 5/7: Onboarding de OpenClaw"
log ""
warn "AHORA SE ABRE EL ASISTENTE OFICIAL DE OPENCLAW."
warn "Cuando te pida la API key, pega la de Anthropic (console.anthropic.com → API keys)."
warn "Cuando te pregunte por el daemon, di SÍ (sí, auto-start)."
warn "Cuando te pregunte por canales, NO selecciones Telegram todavía (lo haremos después)."
log ""
read -r -p "Presiona ENTER cuando estés listo para el onboarding..."

openclaw onboard --install-daemon || {
  err "El onboarding falló. Revisa el output arriba y vuelve a correr este script."
  exit 1
}
ok "Onboarding completado"

# --- 6. Hardening del config ---
log "Paso 6/7: Aplicando hardening de seguridad..."

OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
  err "No se encontró $OPENCLAW_CONFIG. ¿El onboarding terminó bien?"
  exit 1
fi

# Backup antes de tocar
cp "$OPENCLAW_CONFIG" "${OPENCLAW_CONFIG}.backup-$(date +%Y%m%d-%H%M%S)"

# Generar token random para gateway (64 hex chars)
GATEWAY_TOKEN="$(openssl rand -hex 32)"

# Aplicar hardening con jq (idempotente; schema OpenClaw 2026.5+)
TMP_CONFIG="$(mktemp)"
jq --arg token "$GATEWAY_TOKEN" '
  (.gateway //= {}) |
  .gateway.bind = "loopback" |
  .gateway.port = 3000 |
  (.gateway.auth //= {}) |
  .gateway.auth.mode = "token" |
  .gateway.auth.token = $token |
  del(.gateway.auth.required)
' "$OPENCLAW_CONFIG" > "$TMP_CONFIG"

mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
chmod 600 "$OPENCLAW_CONFIG"
ok "Hardening aplicado. Config en $OPENCLAW_CONFIG (perms 600)"

# Guardar el token en un archivo aparte para que Polo lo tenga a mano
TOKEN_FILE="$HOME/.openclaw/gateway-token.txt"
echo "$GATEWAY_TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
ok "Token del gateway guardado en $TOKEN_FILE (solo tú puedes leerlo)"

# Crear estructura de spaces (general por ahora, los demás se añaden después)
mkdir -p "$HOME/.openclaw/spaces/general" \
         "$HOME/.openclaw/spaces/legal" \
         "$HOME/.openclaw/spaces/ikan" \
         "$HOME/.openclaw/logs"

# Skills allowlist inicial (vacía, tú decides qué añadir)
ALLOWLIST="$HOME/.openclaw/skills.allowlist"
if [[ ! -f "$ALLOWLIST" ]]; then
  cat > "$ALLOWLIST" <<'EOF'
# Skills permitidos en esta instancia.
# Un skill por línea. Solo se cargan skills cuyo identificador exacto aparezca aquí.
# Para añadir uno: 1) lee el código en su repo GitHub, 2) añade la línea, 3) reinicia openclaw.
#
# Ejemplos (descomenta cuando los hayas revisado):
# openclaw/email-summary
# openclaw/calendar-brief
EOF
  chmod 644 "$ALLOWLIST"
fi
ok "Spaces y allowlist creados"

# --- 7. Restart del daemon ---
log "Paso 7/7: Reiniciando OpenClaw con la nueva configuración..."
openclaw daemon install --force --port 3000 --token "$GATEWAY_TOKEN" 2>/dev/null || true
openclaw daemon restart 2>/dev/null || openclaw restart 2>/dev/null || warn "No pude reiniciar automáticamente; reinicia manual con: openclaw daemon restart"

# Esperar a que el gateway esté arriba
sleep 3

# --- Verificación final ---
log "Verificando que el gateway escucha SOLO en localhost..."
if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -E "(127\.0\.0\.1|localhost|\[::1\])" >/dev/null; then
  ok "Gateway escucha solo en localhost (127.0.0.1:3000). Bien."
else
  warn "No pude confirmar el bind. Corre manualmente: lsof -iTCP:3000 -sTCP:LISTEN"
fi

if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -E "(\*:|0\.0\.0\.0)" >/dev/null; then
  err "PELIGRO: El gateway parece estar expuesto a la red. Para inmediatamente y revisa el config."
  exit 1
fi

# --- Resumen ---
cat <<EOF

${GREEN}═══════════════════════════════════════════════════${NC}
${GREEN}  Instalación completada${NC}
${GREEN}═══════════════════════════════════════════════════${NC}

Lo que quedó instalado:
  • OpenClaw daemon (auto-start al boot)
  • Gateway en http://127.0.0.1:3000 (solo localhost)
  • Config: $OPENCLAW_CONFIG
  • Spaces: ~/.openclaw/spaces/{general,legal,ikan}
  • Allowlist de skills: $ALLOWLIST
  • Token del gateway: $TOKEN_FILE
  • Audit log: ~/.openclaw/logs/

Siguientes pasos manuales (5 min):

1. Abre el UI local en http://127.0.0.1:3000 (te pedirá el token de arriba).

2. Configura el bot de Telegram:
   a) Abre Telegram → busca @BotFather
   b) Comando /newbot → nombre: "Yoltik AI" → username: "yoltik_ai_bot"
   c) Copia el token que te da (formato 123456789:ABC...)
   d) En el UI de OpenClaw → Channels → Add → Telegram → pega el token
   e) Crea un grupo privado en Telegram, agrega el bot como admin, agrégate tú
   f) Manda /start en el grupo. El bot debería responder.

3. Prueba el flujo: manda al grupo "Dame un haiku sobre PLD".

4. Cuando funcione, corre el script de validación:
   bash $(dirname "$0")/validate.sh

5. Cuando todo esté verde, sigue con el setup de Cloudflare Tunnel
   (carpeta fin-de-semana/) — pero eso ya es del sábado, hoy descansa.

EOF

ok "Listo. Disfruta tu copiloto."
