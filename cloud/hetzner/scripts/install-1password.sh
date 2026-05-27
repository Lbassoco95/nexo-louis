#!/usr/bin/env bash
# install-1password.sh — Instala 1Password CLI (op) en Hetzner y configura
# acceso vía service account (recomendado para automatización).
#
# Service account = token de acceso a un vault específico, sin master password.
# Genera el token en https://my.1password.com/developer-tools/infrastructure-secrets/serviceaccount
#
# Uso (en Hetzner, sudo):
#   sudo bash install-1password.sh
#
# Te va a pedir interactivamente: OP_SERVICE_ACCOUNT_TOKEN (empieza con ops_)

set -uo pipefail
ok() { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

SYSTEM_USER="${SYSTEM_USER:-polo}"
CREDS_DIR="/opt/openclaw/credentials"
OP_ENV="$CREDS_DIR/1password.env"
OPENCLAW_ENV="/opt/openclaw/openclaw.env"
mkdir -p "$CREDS_DIR"

echo "════════════════════════════════════════"
echo "  Instalación 1Password CLI"
echo "════════════════════════════════════════"

# Instala op CLI si falta (instalación oficial vía apt repo de 1Password)
if ! command -v op >/dev/null 2>&1; then
  echo "Bajando 1Password CLI vía apt repo oficial..."
  # Dep básica
  DEBIAN_FRONTEND=noninteractive apt-get install -yq gnupg curl ca-certificates >/dev/null
  # Llave + repo
  curl -fsSL https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg --yes
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" > /etc/apt/sources.list.d/1password.list
  mkdir -p /etc/debsig/policies/AC2D62742012EA22/
  curl -fsSL https://downloads.1password.com/linux/debian/debsig/1password.pol | \
    tee /etc/debsig/policies/AC2D62742012EA22/1password.pol >/dev/null
  mkdir -p /usr/share/debsig/keyrings/AC2D62742012EA22
  curl -fsSL https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor --output /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg --yes
  DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -yq 1password-cli >/dev/null
  if ! command -v op >/dev/null 2>&1; then
    fail "op no se instaló — revisa apt update output"
  fi
  ok "op CLI instalado: $(op --version)"
else
  ok "op CLI ya instalado: $(op --version)"
fi

# Token
[[ -f "$OP_ENV" ]] && { set -a; source "$OP_ENV"; set +a; }
if [[ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
  if [[ -t 0 ]]; then
    echo ""
    echo "Genera service account token en:"
    echo "  https://my.1password.com/developer-tools/infrastructure-secrets/serviceaccount"
    echo "Token empieza con: ops_..."
    read -rsp "OP_SERVICE_ACCOUNT_TOKEN: " OP_SERVICE_ACCOUNT_TOKEN
    echo ""
  else
    fail "OP_SERVICE_ACCOUNT_TOKEN no definido"
  fi
fi
OP_SERVICE_ACCOUNT_TOKEN="$(echo -n "$OP_SERVICE_ACCOUNT_TOKEN" | tr -d '[:space:]')"
if [[ ! "$OP_SERVICE_ACCOUNT_TOKEN" == ops_* ]]; then
  fail "Token debe empezar con 'ops_' (es service account token, no master password)"
fi

cat > "$OP_ENV" <<EOF
# Generado por install-1password.sh — $(date -Iseconds)
OP_SERVICE_ACCOUNT_TOKEN="$OP_SERVICE_ACCOUNT_TOKEN"
EOF
chmod 0600 "$OP_ENV"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$OP_ENV"
ok "Token guardado en $OP_ENV (mode 0600)"

# Inyecta en openclaw.env
sed -i '/^OP_SERVICE_ACCOUNT_TOKEN=/d' "$OPENCLAW_ENV" 2>/dev/null || true
echo "OP_SERVICE_ACCOUNT_TOKEN=$OP_SERVICE_ACCOUNT_TOKEN" >> "$OPENCLAW_ENV"

# Test rápido — lista vaults
echo ""
echo "Test: listar vaults..."
TEST=$(OP_SERVICE_ACCOUNT_TOKEN="$OP_SERVICE_ACCOUNT_TOKEN" op vault list --format=json 2>&1)
RC=$?
if [[ $RC -ne 0 ]]; then
  fail "op vault list FALLÓ:
$TEST"
fi
N=$(echo "$TEST" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
ok "Vaults accesibles: $N"

# Reinicia servicios
for svc in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  systemctl restart "$svc" 2>/dev/null && ok "$svc reiniciado"
done
echo ""
ok "Listo. Prueba en Telegram: 'busca en 1password las credenciales de kawiil central'"
