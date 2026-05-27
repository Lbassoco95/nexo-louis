#!/usr/bin/env bash
# install-slack.sh
# Conecta la Slack App "Louis - Nexo" (Kawiil Mx) con OpenClaw.
# Tokens recibidos por OAuth + Socket Mode el 2026-05-25.

set -euo pipefail

HOME_OC="$HOME/.openclaw"
CREDS_DIR="$HOME_OC/credentials"
STAMP="$(date +%Y%m%d-%H%M%S)"

# === Credenciales (Slack App ID A0B5UHKGQSX, workspace Kawiil Mx) ===
SLACK_BOT_TOKEN="xoxb-6192093466389-11208617964900-g75MyfMOyqB4QCzdU95jRjMf"
SLACK_APP_TOKEN="xapp-1-A0B5UHKGQSX-11208605930852-44ed7af3241f50eae46f76e6ab8bb20489234387d566cb2adef4eb4e6529a521"
SLACK_APP_ID="A0B5UHKGQSX"
SLACK_WORKSPACE_ID="T065N2RDQBF"

echo "==> 1. Guardando credenciales en $CREDS_DIR/slack.env"
mkdir -p "$CREDS_DIR"
cat > "$CREDS_DIR/slack.env" <<EOF
# Slack App "Louis - Nexo" — workspace Kawiil Mx
# Creada 2026-05-25 con manifest (17 bot scopes + connections:write app-level)
SLACK_APP_ID="$SLACK_APP_ID"
SLACK_WORKSPACE_ID="$SLACK_WORKSPACE_ID"
SLACK_BOT_TOKEN="$SLACK_BOT_TOKEN"
SLACK_APP_TOKEN="$SLACK_APP_TOKEN"
EOF
chmod 600 "$CREDS_DIR/slack.env"
echo "    OK (perms 600)"

echo ""
echo "==> 2. Registrando canal Slack en OpenClaw"
# OpenClaw espera ambos tokens. --token = bot token, --app-token = app-level
openclaw channels add \
  --channel slack \
  --token "$SLACK_BOT_TOKEN" \
  --app-token "$SLACK_APP_TOKEN" 2>&1 | head -10

echo ""
echo "==> 3. Esperando que gateway se reinicie..."
sleep 5

echo ""
echo "==> 4. Status del canal Slack"
openclaw channels status --probe 2>&1 | head -20

echo ""
echo "==> 5. Config aplicada"
jq '.channels.slack' "$HOME_OC/openclaw.json" 2>/dev/null | head -20

echo ""
echo "============================================================"
echo "Slack conectado a OpenClaw. Pasos finales en tu Slack:"
echo ""
echo "  1. Abre Slack desktop o app móvil."
echo "  2. En la barra lateral izquierda, busca 'Louis - Nexo'"
echo "     (debajo de Apps, o en 'Add apps' si no aparece)."
echo "  3. Click sobre Louis-Nexo para abrir DM con él."
echo "  4. Manda: 'Hola Louis'."
echo "  5. Louis debe responder. La primera respuesta puede tardar"
echo "     5-10s mientras OpenClaw establece la conexión WebSocket."
echo ""
echo "Para que Louis responda en CANALES:"
echo "  • Invita el bot al canal: /invite @Louis-Nexo"
echo "  • Arróbalo: @Louis-Nexo qué tengo pendiente"
echo "  • DMs no necesitan invitación, solo escribes."
echo ""
echo "Tokens guardados en $CREDS_DIR/slack.env (perms 600)."
echo "============================================================"
