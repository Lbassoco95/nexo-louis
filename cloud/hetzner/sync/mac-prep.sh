#!/usr/bin/env bash
# mac-prep.sh — Corre en la Mac de Polo. Arma /tmp/louis.env desde
# ~/.openclaw/credentials/*.env y prepara el push al VPS.
#
# Uso:
#   ./mac-prep.sh HETZNER_IP [LOUIS_DOMAIN] [AGENTS_DOMAIN]
#   ./mac-prep.sh 91.99.123.45
#   ./mac-prep.sh 91.99.123.45 louis.kawiil.mx agents.kawiil.mx
#
# Salida:
#   /tmp/louis.env       — listo para scp al VPS como /opt/louis/.env
#   /tmp/louis-env-summary.txt — qué tokens encontró (sin valores, solo tamaños)

# NOTA: pipefail está DESACTIVADO a propósito.
# getenv_local() hace `grep | head | sed` y si grep no encuentra la var devuelve 1.
# Con pipefail+errexit eso aborta el script. Aceptamos vars vacías como input válido
# (.env del VPS las admite vacías y el verify.sh las flagea si son obligatorias).
set -uo pipefail

HETZNER_IP="${1:-}"
LOUIS_DOMAIN="${2:-louis.kawiil.mx}"
AGENTS_DOMAIN="${3:-agents.kawiil.mx}"

if [[ -z "$HETZNER_IP" ]]; then
  echo "Uso: $0 HETZNER_IP [LOUIS_DOMAIN] [AGENTS_DOMAIN]"
  echo "Ej:   $0 91.99.123.45"
  exit 1
fi

CREDS_DIR="$HOME/.openclaw/credentials"
OUT="/tmp/louis.env"
SUMMARY="/tmp/louis-env-summary.txt"

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

[[ -d "$CREDS_DIR" ]] || fail "No existe $CREDS_DIR"

# Helper: extrae el valor de una variable de los *.env.
# Si no encuentra match, devuelve vacío sin fallar (no propaga exit 1 al caller).
getenv_local() {
  local key="$1"
  local val
  val=$(grep -h -rE "^(export[[:space:]]+)?${key}=" "$CREDS_DIR"/*.env 2>/dev/null | head -1 || true)
  if [[ -z "$val" ]]; then
    echo ""
    return 0
  fi
  echo "$val" \
    | sed -E "s/^(export[[:space:]]+)?${key}=//" \
    | sed -E 's/^["'\''"]//; s/["'\''"]$//'
  return 0
}

# Recolecta tokens
ANTHROPIC_API_KEY=$(getenv_local ANTHROPIC_API_KEY)

TELEGRAM_BOT_TOKEN=$(getenv_local TELEGRAM_BOT_TOKEN)
TELEGRAM_ALLOWED_CHAT_ID=$(getenv_local TELEGRAM_ALLOWED_CHAT_ID)

SLACK_BOT_TOKEN=$(getenv_local SLACK_BOT_TOKEN)
SLACK_APP_TOKEN=$(getenv_local SLACK_APP_TOKEN)
SLACK_SIGNING_SECRET=$(getenv_local SLACK_SIGNING_SECRET)

# M365 vars del setup real (PKCE público, sin client_secret). Los tokens vivos
# están en m365-{tenant}-tokens.json (los manda seed-from-mac.sh, no aquí).
M365_KAWIIL_TENANT_ID=$(getenv_local M365_KAWIIL_TENANT_ID)
M365_KAWIIL_CLIENT_ID=$(getenv_local M365_KAWIIL_CLIENT_ID)
M365_KAWIIL_REDIRECT_URI=$(getenv_local M365_KAWIIL_REDIRECT_URI)
[[ -z "$M365_KAWIIL_REDIRECT_URI" ]] && M365_KAWIIL_REDIRECT_URI="http://localhost:8765/callback"

M365_YOLTIK_TENANT_ID=$(getenv_local M365_YOLTIK_TENANT_ID)
M365_YOLTIK_CLIENT_ID=$(getenv_local M365_YOLTIK_CLIENT_ID)
M365_YOLTIK_REDIRECT_URI=$(getenv_local M365_YOLTIK_REDIRECT_URI)
[[ -z "$M365_YOLTIK_REDIRECT_URI" ]] && M365_YOLTIK_REDIRECT_URI="http://localhost:8765/callback"

SUPABASE_URL=$(getenv_local SUPABASE_URL)
[[ -z "$SUPABASE_URL" ]] && SUPABASE_URL="https://qppfampapbxdgednkofc.supabase.co"
SUPABASE_SERVICE_KEY=$(getenv_local SUPABASE_SERVICE_KEY)
SUPABASE_ANON_KEY=$(getenv_local SUPABASE_ANON_KEY)

KAWIIL_DISPATCH_TOKEN=$(getenv_local KAWIIL_DISPATCH_TOKEN)
KAWIIL_ORG_ID=$(getenv_local KAWIIL_ORG_ID)
KAWIIL_KAWIIL_ORG_ID=$(getenv_local KAWIIL_KAWIIL_ORG_ID)
KAWIIL_YOLTIK_ORG_ID=$(getenv_local KAWIIL_YOLTIK_ORG_ID)

# SSH key del Mac (para autorizar al usuario polo)
SYSTEM_USER_SSH_KEY=""
for k in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub; do
  [[ -f "$k" ]] && { SYSTEM_USER_SSH_KEY=$(cat "$k"); break; }
done

# Llave dedicada de sync (la crea mac-install.sh; aquí solo la incluimos si ya existe)
MAC_SYNC_SSH_PUB_KEY=""
[[ -f "$HOME/.ssh/louis_sync.pub" ]] && MAC_SYNC_SSH_PUB_KEY=$(cat "$HOME/.ssh/louis_sync.pub")

# ── Generar .env ────────────────────────────────────────────
# Helper: emite KEY="VALUE" con escape de dobles internas, seguro para
# `source` en bash, EnvironmentFile en systemd, y --env-file en docker compose.
emit() {
  local key="$1" val="${2:-}"
  # Escapa \ y " dentro del valor (no debería haber, pero por si acaso)
  val=${val//\\/\\\\}
  val=${val//\"/\\\"}
  printf '%s="%s"\n' "$key" "$val"
}

{
  echo "# Generado por mac-prep.sh el $(date -Iseconds)"
  echo "# Destino: /opt/louis/.env en $HETZNER_IP"
  echo ""
  emit LOUIS_DOMAIN              "$LOUIS_DOMAIN"
  emit AGENTS_DOMAIN             "$AGENTS_DOMAIN"
  emit ACME_EMAIL                "lbassoco@kawiil.mx"
  emit SYSTEM_USER               "polo"
  emit SYSTEM_USER_SSH_KEY       "$SYSTEM_USER_SSH_KEY"
  echo ""
  emit ANTHROPIC_API_KEY         "$ANTHROPIC_API_KEY"
  echo ""
  emit TELEGRAM_BOT_TOKEN        "$TELEGRAM_BOT_TOKEN"
  emit TELEGRAM_ALLOWED_CHAT_ID  "$TELEGRAM_ALLOWED_CHAT_ID"
  echo ""
  emit SLACK_BOT_TOKEN           "$SLACK_BOT_TOKEN"
  emit SLACK_APP_TOKEN           "$SLACK_APP_TOKEN"
  emit SLACK_SIGNING_SECRET      "$SLACK_SIGNING_SECRET"
  echo ""
  emit M365_KAWIIL_TENANT_ID     "$M365_KAWIIL_TENANT_ID"
  emit M365_KAWIIL_CLIENT_ID     "$M365_KAWIIL_CLIENT_ID"
  emit M365_KAWIIL_REDIRECT_URI  "$M365_KAWIIL_REDIRECT_URI"
  echo ""
  emit M365_YOLTIK_TENANT_ID     "$M365_YOLTIK_TENANT_ID"
  emit M365_YOLTIK_CLIENT_ID     "$M365_YOLTIK_CLIENT_ID"
  emit M365_YOLTIK_REDIRECT_URI  "$M365_YOLTIK_REDIRECT_URI"
  echo ""
  emit SUPABASE_URL              "$SUPABASE_URL"
  emit SUPABASE_SERVICE_KEY      "$SUPABASE_SERVICE_KEY"
  emit SUPABASE_ANON_KEY         "$SUPABASE_ANON_KEY"
  echo ""
  emit KAWIIL_DISPATCH_TOKEN     "$KAWIIL_DISPATCH_TOKEN"
  emit KAWIIL_ORG_ID             "$KAWIIL_ORG_ID"
  emit KAWIIL_KAWIIL_ORG_ID      "$KAWIIL_KAWIIL_ORG_ID"
  emit KAWIIL_YOLTIK_ORG_ID      "$KAWIIL_YOLTIK_ORG_ID"
  echo ""
  emit OPENCLAW_PORT             "3000"
  emit OPENCLAW_VERSION          "latest"
  echo ""
  emit MAC_SYNC_SSH_PUB_KEY      "$MAC_SYNC_SSH_PUB_KEY"
} > "$OUT"
chmod 600 "$OUT"

# ── Summary (tamaños, no valores) ───────────────────────────
{
  echo "=== Resumen /tmp/louis.env (sin revelar secretos) ==="
  echo "Generado: $(date -Iseconds)"
  echo "Destino:  $HETZNER_IP : /opt/louis/.env"
  echo ""
  echo "Var                              Tamaño  ¿OK?"
  echo "─────────────────────────────────────────────"
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    len=${#value}
    if [[ $len -eq 0 ]]; then
      printf "%-32s %5d   ⚠ vacío\n" "$key" "$len"
    else
      printf "%-32s %5d   ✓\n" "$key" "$len"
    fi
  done < "$OUT"
} | tee "$SUMMARY"

echo ""
ok ".env generado en $OUT  (chmod 600)"
ok "Resumen en $SUMMARY"
echo ""
echo "Próximos pasos:"
echo "  1. Revisa el resumen arriba. Las variables marcadas ⚠ vacío:"
echo "       - Si no las necesitas (ej. Slack si no usas Slack), está OK."
echo "       - Si SÍ las necesitas, edita /tmp/louis.env a mano y pega el valor."
echo ""
echo "  2. Subir paquete + .env al VPS:"
echo "       rsync -avz --exclude='.env' \\"
echo "         \"\$HOME/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/cloud/hetzner/\" \\"
echo "         root@${HETZNER_IP}:/opt/louis/"
echo "       scp /tmp/louis.env root@${HETZNER_IP}:/opt/louis/.env"
echo ""
echo "  3. SSH y deploy:"
echo "       ssh root@${HETZNER_IP}"
echo "       cd /opt/louis && ./deploy.sh && ./verify.sh"
echo ""
echo "  4. Borrar el .env local (¡importante!):"
echo "       shred -u /tmp/louis.env /tmp/louis-env-summary.txt"
