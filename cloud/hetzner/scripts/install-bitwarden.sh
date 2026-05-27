#!/usr/bin/env bash
# install-bitwarden.sh — Instala bw CLI en Hetzner y configura unlock
# automático vía API service account o session key.
#
# Recomendado: usar API service account (clientid + clientsecret) para que
# no haya master pass en el VPS. Polo crea uno en:
#   https://vault.bitwarden.com → Account Settings → Security → Keys → API Key
#   (Bitwarden Personal account API key, scope "api")
#
# Uso (en Hetzner como root o sudo):
#   sudo bash install-bitwarden.sh
#
# Te pide:
#   BW_CLIENTID       (client_id de tu API key Bitwarden)
#   BW_CLIENTSECRET   (client_secret correspondiente)
#   BW_PASSWORD       (master password — necesario para `bw unlock`)
#
# Los guarda en /opt/openclaw/credentials/bitwarden.env mode 0600.

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "  \033[1;36m· %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

SYSTEM_USER="${SYSTEM_USER:-polo}"
CREDS_DIR="/opt/openclaw/credentials"
BW_ENV="$CREDS_DIR/bitwarden.env"
OPENCLAW_ENV="/opt/openclaw/openclaw.env"

mkdir -p "$CREDS_DIR"

echo "════════════════════════════════════════"
echo "  Instalación Bitwarden CLI"
echo "════════════════════════════════════════"

# 1) Instala bw CLI
if command -v bw >/dev/null 2>&1; then
  ok "bw CLI ya instalado: $(bw --version 2>/dev/null | head -1)"
else
  log "Instalando bw CLI..."
  # bw oficial vía npm (Node ya está en Hetzner)
  if ! command -v npm >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -yq npm
  fi
  npm install -g @bitwarden/cli 2>&1 | tail -3
  if ! command -v bw >/dev/null 2>&1; then
    fail "bw no quedó en PATH después de npm install"
  fi
  ok "bw CLI instalado: $(bw --version)"
fi

# 2) Credenciales
if [[ -f "$BW_ENV" ]]; then
  set -a; source "$BW_ENV"; set +a
  log "Credenciales previas leídas de $BW_ENV"
fi

if [[ -z "${BW_CLIENTID:-}" ]]; then
  if [[ -t 0 ]]; then
    echo ""
    echo "Genera API key en https://vault.bitwarden.com → Settings → Security → Keys → View API Key"
    echo "Formato esperado: user.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    read -rp "BW_CLIENTID: " BW_CLIENTID
  else
    fail "BW_CLIENTID no definido"
  fi
fi
# Trim whitespace + newlines (curso de pegado)
BW_CLIENTID="$(echo -n "$BW_CLIENTID" | tr -d '[:space:]')"
if [[ ! "$BW_CLIENTID" == user.* ]]; then
  fail "BW_CLIENTID '$BW_CLIENTID' no parece válido — debe empezar con 'user.' (es un UUID, no una URL)"
fi

if [[ -z "${BW_CLIENTSECRET:-}" ]]; then
  if [[ -t 0 ]]; then
    read -rsp "BW_CLIENTSECRET (no se muestra al teclear): " BW_CLIENTSECRET
    echo ""
  else
    fail "BW_CLIENTSECRET no definido"
  fi
fi
BW_CLIENTSECRET="$(echo -n "$BW_CLIENTSECRET" | tr -d '[:space:]')"
if [[ ${#BW_CLIENTSECRET} -lt 20 ]]; then
  fail "BW_CLIENTSECRET parece corto (${#BW_CLIENTSECRET} chars) — debería ser 30+ caracteres. ¿Pegaste el valor completo?"
fi

if [[ -z "${BW_PASSWORD:-}" ]]; then
  if [[ -t 0 ]]; then
    echo ""
    echo "Master password de tu vault (necesario para 'bw unlock'). Se guarda en disco mode 0600."
    read -rsp "BW_PASSWORD (no se muestra al teclear): " BW_PASSWORD
    echo ""
  else
    fail "BW_PASSWORD no definido"
  fi
fi
if [[ -z "$BW_PASSWORD" ]]; then
  fail "BW_PASSWORD vacío"
fi

cat > "$BW_ENV" <<EOF
# Generado por install-bitwarden.sh — $(date -Iseconds)
# NUNCA cometas este archivo a git ni lo subas a backups públicos
BW_CLIENTID="$BW_CLIENTID"
BW_CLIENTSECRET="$BW_CLIENTSECRET"
BW_PASSWORD="$BW_PASSWORD"
EOF
chmod 0600 "$BW_ENV"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$BW_ENV"
ok "Credenciales guardadas en $BW_ENV (mode 0600)"

# Inyecta paths en openclaw.env (no las creds — solo apunta al env file)
if [[ -f "$OPENCLAW_ENV" ]]; then
  sed -i '/^BITWARDEN_ENV_FILE=/d' "$OPENCLAW_ENV"
  echo "BITWARDEN_ENV_FILE=$BW_ENV" >> "$OPENCLAW_ENV"
  ok "openclaw.env apunta al env file"
fi

# 3) Test: login con API + unlock con master
log "Test: bw login (API) + bw unlock..."
sudo -u "$SYSTEM_USER" -i bash <<INNER
set -a; source "$BW_ENV"; set +a
export BW_CLIENTID BW_CLIENTSECRET BW_PASSWORD
# Logout previo (idempotente)
bw logout >/dev/null 2>&1 || true
bw config server https://vault.bitwarden.com >/dev/null 2>&1
# Login con captura completa de error
LOGIN_OUT=\$(bw login --apikey 2>&1)
LOGIN_RC=\$?
if [[ \$LOGIN_RC -ne 0 ]]; then
  echo "  ✗ bw login --apikey FALLÓ (rc=\$LOGIN_RC):"
  echo "\$LOGIN_OUT" | grep -v "DeprecationWarning\|--trace-deprecation" | sed 's/^/      /'
  echo ""
  echo "  Diagnóstico común:"
  echo "    - Si dice 'Username or password is incorrect': client_id/secret mal"
  echo "    - Si dice 'Two-step login is required': tu cuenta tiene 2FA habilitado"
  echo "      → solución: bw login con email/password manual y código 2FA (no --apikey)"
  echo "    - Si dice 'Invalid email address': el client_id está mal formado"
  exit 1
fi
echo "  ✓ bw login OK"
SESSION_OUT=\$(bw unlock --passwordenv BW_PASSWORD --raw 2>&1)
UNLOCK_RC=\$?
if [[ \$UNLOCK_RC -ne 0 ]] || [[ -z "\$SESSION_OUT" ]]; then
  echo "  ✗ bw unlock FALLÓ:"
  echo "\$SESSION_OUT" | grep -v "DeprecationWarning\|--trace-deprecation" | sed 's/^/      /'
  echo "    → Verifica que BW_PASSWORD sea tu master password actual"
  exit 1
fi
SESSION="\$SESSION_OUT"
echo "  ✓ Login + unlock OK (session length: \${#SESSION} chars)"
# Guarda session para que vault.py la reutilice (vence ~1 hora — vault.py la renueva auto)
echo "BW_SESSION=\$SESSION" > "$CREDS_DIR/bitwarden-session.env"
chmod 0600 "$CREDS_DIR/bitwarden-session.env"
# Test rápido: cuántos items hay
N=\$(BW_SESSION=\$SESSION bw list items 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
echo "  ✓ Vault accesible — \$N items disponibles"
INNER
RC=$?
chown "$SYSTEM_USER":"$SYSTEM_USER" "$CREDS_DIR/bitwarden-session.env" 2>/dev/null || true

if [[ $RC -ne 0 ]]; then
  fail "Test de bw login/unlock falló"
fi

# 4) Reinicia servicios para tomar el env
for svc in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  systemctl restart "$svc" 2>/dev/null && ok "$svc reiniciado" || warn "$svc no reinició"
done

echo ""
ok "Listo. Prueba desde Telegram: 'busca en el vault las credenciales de XXX'"
