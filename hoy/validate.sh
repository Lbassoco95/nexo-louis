#!/usr/bin/env bash
# validate.sh
# Verifica que la instalación de Yoltik AI quedó correctamente hardened.
# Uso: bash validate.sh
# Retorna 0 si todo pasa, 1 si algo falló.

set -uo pipefail

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

PASS=0
FAIL=0
WARN=0

check_pass() { echo "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
check_fail() { echo "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
check_warn() { echo "  ${YELLOW}⚠${NC} $1"; WARN=$((WARN+1)); }

echo "═══════════════════════════════════════════════════"
echo "  Yoltik AI · Validación de seguridad"
echo "═══════════════════════════════════════════════════"
echo ""

# === Test 1: gateway escucha solo en localhost ===
echo "Test 1: Gateway debe escuchar SOLO en 127.0.0.1"
listen_output=$(lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null || true)
if [[ -z "$listen_output" ]]; then
  check_fail "Nadie escucha en el puerto 3000. ¿OpenClaw está corriendo?"
elif echo "$listen_output" | grep -E "(\*:|0\.0\.0\.0)" >/dev/null; then
  check_fail "CRÍTICO: Gateway expuesto a 0.0.0.0 o *. Para todo: bash killswitch.sh"
elif echo "$listen_output" | grep -E "(127\.0\.0\.1|localhost|\[::1\])" >/dev/null; then
  check_pass "Gateway en localhost:3000 (correcto)"
else
  check_warn "Output ambiguo: $listen_output"
fi
echo ""

# === Test 2: archivo de config existe y tiene permisos correctos ===
echo "Test 2: Config file con permisos correctos"
CONFIG="$HOME/.openclaw/openclaw.json"
if [[ -f "$CONFIG" ]]; then
  check_pass "Config existe: $CONFIG"
  PERMS=$(stat -f "%Lp" "$CONFIG" 2>/dev/null || stat -c "%a" "$CONFIG" 2>/dev/null)
  if [[ "$PERMS" == "600" ]]; then
    check_pass "Permisos 600 (solo tú puedes leer)"
  else
    check_warn "Permisos son $PERMS, deberían ser 600. Corre: chmod 600 $CONFIG"
  fi
else
  check_fail "No existe $CONFIG"
fi
echo ""

# === Test 3: hardening flags ===
echo "Test 3: Flags de hardening en el config"
if command -v jq >/dev/null 2>&1 && [[ -f "$CONFIG" ]]; then
  bind=$(jq -r '.gateway.bind // "missing"' "$CONFIG")
  auth_mode=$(jq -r '.gateway.auth.mode // "missing"' "$CONFIG")
  auth_token=$(jq -r '.gateway.auth.token // ""' "$CONFIG")
  gateway_port=$(jq -r '.gateway.port // 3000' "$CONFIG")

  [[ "$bind" == "loopback" || "$bind" == "127.0.0.1" ]] && check_pass "gateway.bind = $bind (localhost)" || check_fail "gateway.bind = $bind (debe ser loopback)"
  [[ "$auth_mode" == "token" && -n "$auth_token" ]] && check_pass "gateway.auth.mode = token (con token)" || check_fail "auth del gateway no configurado correctamente"
  [[ "$gateway_port" == "3000" ]] && check_pass "gateway.port = 3000" || check_warn "gateway.port = $gateway_port (esperado 3000 para el checklist)"
else
  check_warn "jq no instalado o config inexistente, no pude validar flags"
fi
echo ""

# === Test 4: token del gateway existe y es fuerte ===
echo "Test 4: Token del gateway"
TOKEN_FILE="$HOME/.openclaw/gateway-token.txt"
if [[ -f "$TOKEN_FILE" ]]; then
  TOKEN=$(cat "$TOKEN_FILE")
  if [[ ${#TOKEN} -ge 32 ]]; then
    check_pass "Token tiene ${#TOKEN} caracteres (suficientemente largo)"
  else
    check_fail "Token solo tiene ${#TOKEN} caracteres. Regenera: openssl rand -hex 32"
  fi
else
  check_fail "No existe token file $TOKEN_FILE"
fi
echo ""

# === Test 5: nada expuesto al exterior (mejor esfuerzo) ===
echo "Test 5: Puerto 3000 NO expuesto a la LAN"
# Detectar IP de la LAN
LAN_IP=$(ifconfig 2>/dev/null | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -n1)
if [[ -n "$LAN_IP" ]]; then
  # Intentar conectar al puerto 3000 (nc funciona en macOS sin GNU timeout)
  port_open=false
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 "$LAN_IP" 3000 2>/dev/null && port_open=true
  elif command -v timeout >/dev/null 2>&1; then
    timeout 2 bash -c "echo > /dev/tcp/$LAN_IP/3000" 2>/dev/null && port_open=true
  else
    check_warn "nc/timeout no disponibles; no pude probar acceso LAN"
    port_open=skip
  fi
  if [[ "$port_open" == "skip" ]]; then
    :
  elif [[ "$port_open" == "true" ]]; then
    check_fail "CRÍTICO: el puerto 3000 está accesible desde $LAN_IP (la LAN). Revisa el bind."
  else
    check_pass "Puerto 3000 NO responde desde la IP de LAN $LAN_IP (correcto)"
  fi
else
  check_warn "No detecté IP de LAN, no pude probar"
fi
echo ""

# === Test 6: FileVault ===
echo "Test 6: FileVault encendido (cifrado de disco)"
if fdesetup status 2>/dev/null | grep -q "FileVault is On"; then
  check_pass "FileVault encendido"
else
  check_warn "FileVault apagado. Recomendado encender: Configuración → Privacidad y seguridad → FileVault"
fi
echo ""

# === Test 7: backups del config ===
echo "Test 7: Existe al menos un backup del config"
if ls "$HOME/.openclaw/openclaw.json.backup-"* >/dev/null 2>&1; then
  COUNT=$(ls "$HOME/.openclaw/openclaw.json.backup-"* | wc -l | tr -d ' ')
  check_pass "$COUNT backup(s) del config encontrado(s)"
else
  check_warn "No hay backups del config. Crea uno: cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.backup"
fi
echo ""

# === Resumen ===
echo "═══════════════════════════════════════════════════"
echo "  Resumen"
echo "═══════════════════════════════════════════════════"
echo "  ${GREEN}Passed:${NC}   $PASS"
echo "  ${YELLOW}Warnings:${NC} $WARN"
echo "  ${RED}Failed:${NC}   $FAIL"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "${RED}✗ Hay fallos críticos. Resuélvelos antes de seguir.${NC}"
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo "${YELLOW}⚠ Pasaste con warnings. Revísalos pero puedes seguir con cuidado.${NC}"
  exit 0
else
  echo "${GREEN}✓ Todo en verde. Estás listo para usar la instancia.${NC}"
  exit 0
fi
