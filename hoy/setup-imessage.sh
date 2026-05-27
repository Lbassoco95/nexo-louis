#!/usr/bin/env bash
# setup-imessage.sh — Canal iMessage + binding a agent general (Claude)
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Pre-requisitos iMessage ==="
echo "Manual (macOS): Messages.app signed in, Full Disk Access (Terminal+Cursor), Automation→Messages"
echo ""

# Check chat.db access
if ! sqlite3 "$HOME/Library/Messages/chat.db" "SELECT COUNT(*) FROM chat;" 2>/dev/null | grep -qE '^[0-9]+$'; then
  echo "✗ No hay acceso a ~/Library/Messages/chat.db (Full Disk Access requerido)."
  echo "  Configuración del Sistema → Privacidad → Full Disk Access → Terminal + Cursor"
  echo "  Luego: imsg chats --limit 1"
  exit 1
fi
echo "✓ chat.db accesible"

# imsg
if ! command -v imsg >/dev/null 2>&1; then
  echo "Instalando imsg..."
  brew tap steipete/tap 2>/dev/null || true
  brew install steipete/tap/imsg
fi
echo "✓ imsg $(imsg --version)"

if ! imsg chats --limit 1 2>&1 | head -3 | grep -qv "Permission Error"; then
  imsg chats --limit 1 2>&1 | head -5 || true
  echo "✗ imsg no puede leer chats. Revisa FDA y Automation."
  exit 1
fi
echo "✓ imsg conectado a Messages"

# Phone (never in chat — terminal only)
if [[ -n "${MY_PHONE:-}" ]]; then
  :
elif [[ -f "$HOME/.openclaw/imessage-phone.local" ]]; then
  MY_PHONE=$(tr -d '[:space:]' < "$HOME/.openclaw/imessage-phone.local")
else
  read -r -p "Número iPhone con código país (ej +5215512345678): " MY_PHONE
fi

if [[ ! "$MY_PHONE" =~ ^\+[0-9]{10,15}$ ]]; then
  echo "Formato inválido. Usa +<digitos>"
  exit 1
fi
echo "Número recibido: ${MY_PHONE:0:5}...${MY_PHONE: -4}"

# Claude smoke prerequisite
if ! grep -q '^ANTHROPIC_API_KEY=' "$HOME/.openclaw/.env" 2>/dev/null; then
  echo "✗ Falta ANTHROPIC_API_KEY. Ejecuta setup-anthropic-key.sh y phase1-smoke-tests.sh primero."
  exit 1
fi

cp "$HOME/.openclaw/openclaw.json" \
  "$HOME/.openclaw/openclaw.json.backup-pre-imessage-$(date +%Y%m%d-%H%M%S)"

IMSG_PATH=$(command -v imsg)
TMP=$(mktemp)
jq --arg cliPath "$IMSG_PATH" \
   --arg dbPath "$HOME/Library/Messages/chat.db" \
   --arg myPhone "$MY_PHONE" '
  (.channels //= {}) |
  .channels.imessage = {
    enabled: true,
    cliPath: $cliPath,
    dbPath: $dbPath,
    dmPolicy: "allowlist",
    allowFrom: [$myPhone],
    groupPolicy: "disabled"
  } |
  (.bindings //= []) |
  .bindings = (
    [.bindings[] | select(.match.channel != "imessage")] +
    [{
      agentId: "general",
      match: { channel: "imessage", accountId: "*" }
    }]
  )
' "$HOME/.openclaw/openclaw.json" > "$TMP" && mv "$TMP" "$HOME/.openclaw/openclaw.json"
chmod 600 "$HOME/.openclaw/openclaw.json"

echo ""
openclaw config validate
openclaw daemon restart
sleep 5

echo ""
echo "=== Estado canal ==="
openclaw agents list --bindings 2>&1
openclaw channels status --probe 2>&1 | tail -20

echo ""
echo "✓ iMessage configurado → agent general → Claude"
echo ""
echo "Smoke test iPhone:"
echo "  1. Envía iMessage desde tu iPhone (ej: 'Hola, ¿qué tal?')"
echo "  2. openclaw pairing list imessage"
echo "  3. openclaw pairing approve imessage <CODE>"
echo "  4. Verifica respuesta en español MX (~1-5 s con Claude)"
