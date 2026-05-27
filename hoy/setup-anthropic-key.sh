#!/usr/bin/env bash
# setup-anthropic-key.sh
# Guarda ANTHROPIC_API_KEY en ~/.openclaw/.env y sincroniza al daemon OpenClaw.
# Uso: bash setup-anthropic-key.sh

set -euo pipefail

echo "Antes de continuar:"
echo "  1. console.anthropic.com → API Keys → Create 'yoltik-ai-mac-polo'"
echo "  2. Settings → Usage Limits → hard cap \$100 USD/mes"
echo ""

read -r -s -p "Pega tu API key de Anthropic (no se mostrará): " ANTHROPIC_KEY
echo ""

if [[ ! "$ANTHROPIC_KEY" =~ ^sk-ant-[a-zA-Z0-9_-]+$ ]]; then
  echo "Formato inválido. Debe empezar con sk-ant-"
  exit 1
fi

mkdir -p "$HOME/.openclaw"
ENV_FILE="$HOME/.openclaw/.env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

if grep -q "^ANTHROPIC_API_KEY=" "$ENV_FILE" 2>/dev/null; then
  sed -i '' "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$ANTHROPIC_KEY|" "$ENV_FILE"
else
  echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" >> "$ENV_FILE"
fi

# Auth profile para agent general (infer CLI + routing)
AGENT_DIR="$HOME/.openclaw/agents/general/agent"
mkdir -p "$AGENT_DIR"
AUTH_FILE="$AGENT_DIR/auth-profiles.json"
if [[ -f "$AUTH_FILE" ]]; then
  cp "$AUTH_FILE" "${AUTH_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
fi
TMP_AUTH=$(mktemp)
jq -n --arg key "$ANTHROPIC_KEY" '{
  version: 1,
  profiles: {
    "anthropic:manual": {
      type: "api_key",
      provider: "anthropic",
      key: $key
    }
  },
  order: { anthropic: ["anthropic:manual"] }
}' > "$TMP_AUTH"
mv "$TMP_AUTH" "$AUTH_FILE"
chmod 600 "$AUTH_FILE"

unset ANTHROPIC_KEY

echo "✓ ANTHROPIC_API_KEY guardada en $ENV_FILE (perm 600)"
echo "✓ Auth profile anthropic:manual → $AUTH_FILE"

echo "Sincronizando variables al LaunchAgent del gateway..."
openclaw gateway install --force --port 3000
openclaw daemon restart
sleep 5

echo ""
echo "Verificación:"
grep -E '^export (ANTHROPIC|OLLAMA)_API_KEY' "$HOME/.openclaw/service-env/ai.openclaw.gateway.env" 2>/dev/null \
  | sed 's/=.*/=***/' || true
openclaw models list --provider anthropic 2>&1 | head -5

echo ""
echo "Siguiente: bash $(dirname "$0")/phase1-smoke-tests.sh"
