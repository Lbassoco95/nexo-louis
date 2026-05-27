#!/usr/bin/env bash
# validate-cloudflare.sh
# Verifica que Cloudflare Tunnel + hardening local están correctos post-setup.
# Uso: bash validate-cloudflare.sh
# Retorna 0 si todo pasa, 1 si hay fallos críticos.

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
echo "  Yoltik AI · Validación Cloudflare"
echo "═══════════════════════════════════════════════════"
echo ""

# === Test 1: cloudflared instalado ===
echo "Test 1: cloudflared disponible"
if command -v cloudflared >/dev/null 2>&1; then
  check_pass "cloudflared en PATH: $(cloudflared --version 2>/dev/null | head -n1)"
else
  check_fail "cloudflared no instalado. Corre: brew install cloudflared"
fi
echo ""

# === Test 2: túnel yoltik-ai existe y conectado ===
echo "Test 2: Túnel 'yoltik-ai' activo"
if command -v cloudflared >/dev/null 2>&1; then
  if cloudflared tunnel list 2>/dev/null | grep -q yoltik-ai; then
    check_pass "Túnel 'yoltik-ai' registrado"
    if cloudflared tunnel info yoltik-ai 2>/dev/null | grep -q "Connector"; then
      check_pass "Connector activo (túnel conectado a Cloudflare)"
    else
      check_warn "Túnel existe pero sin connector. Revisa: cloudflared tunnel info yoltik-ai"
    fi
  else
    check_fail "Túnel 'yoltik-ai' no encontrado. Corre setup-cloudflare-tunnel.sh"
  fi
else
  check_warn "No se pudo verificar el túnel (cloudflared ausente)"
fi
echo ""

# === Test 3: config.yml generado ===
echo "Test 3: Config de cloudflared"
CF_CONFIG="$HOME/.cloudflared/config.yml"
if [[ -f "$CF_CONFIG" ]]; then
  check_pass "Config existe: $CF_CONFIG"
  PERMS=$(stat -f "%Lp" "$CF_CONFIG" 2>/dev/null || stat -c "%a" "$CF_CONFIG" 2>/dev/null)
  [[ "$PERMS" == "600" ]] && check_pass "Permisos 600" || check_warn "Permisos son $PERMS, deberían ser 600"
  if grep -q "ai.kawiil.mx" "$CF_CONFIG" && grep -q "localhost:3000" "$CF_CONFIG"; then
    check_pass "Ingress apunta ai.kawiil.mx → localhost:3000"
  else
    check_fail "Config no tiene el ingress esperado (ai.kawiil.mx → localhost:3000)"
  fi
else
  check_fail "No existe $CF_CONFIG"
fi
echo ""

# === Test 4: servicio cloudflared corriendo ===
echo "Test 4: Proceso cloudflared"
if pgrep -x cloudflared >/dev/null 2>&1; then
  check_pass "cloudflared corriendo (PID: $(pgrep -x cloudflared | head -n1))"
else
  check_warn "cloudflared no está corriendo. Prueba: sudo launchctl load /Library/LaunchDaemons/com.cloudflare.cloudflared.plist"
fi
echo ""

# === Test 5: gateway sigue solo en localhost ===
echo "Test 5: OpenClaw gateway solo en localhost"
listen_output=$(lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null || true)
if [[ -z "$listen_output" ]]; then
  check_fail "Nadie escucha en 3000. ¿OpenClaw está corriendo?"
elif echo "$listen_output" | grep -E "(\*:|0\.0\.0\.0)" >/dev/null; then
  check_fail "CRÍTICO: Gateway expuesto a la red. Corre: bash $(dirname "$0")/../hoy/killswitch.sh"
elif echo "$listen_output" | grep -E "(127\.0\.0\.1|localhost|\[::1\])" >/dev/null; then
  check_pass "Gateway en localhost:3000 (correcto)"
else
  check_warn "Output ambiguo: $listen_output"
fi
echo ""

# === Test 6: Cloudflare responde (redirect a Access o challenge) ===
echo "Test 6: https://ai.kawiil.mx responde vía Cloudflare"
if command -v curl >/dev/null 2>&1; then
  CF_HEADERS=$(curl -sI --max-time 10 "https://ai.kawiil.mx" 2>/dev/null || true)
  if [[ -z "$CF_HEADERS" ]]; then
    check_warn "Sin respuesta de ai.kawiil.mx (DNS aún propagando o túnel caído)"
  elif echo "$CF_HEADERS" | grep -qiE "cf-ray|cloudflare"; then
    check_pass "Respuesta pasa por Cloudflare"
    if echo "$CF_HEADERS" | grep -qiE "location:.*cloudflareaccess|location:.*login"; then
      check_pass "Redirect a Cloudflare Access detectado (auth configurada)"
    elif echo "$CF_HEADERS" | grep -qiE "HTTP/[0-9.]+ 200|HTTP/[0-9.]+ 302"; then
      check_warn "Cloudflare responde pero NO hay redirect a Access. Configura Access antes de abrir la URL."
    fi
  else
    check_warn "Respuesta sin headers CF típicos. Revisa DNS y túnel."
  fi
else
  check_warn "curl no disponible, no se pudo probar ai.kawiil.mx"
fi
echo ""

# === Test 7: puerto 3000 no expuesto en IP pública ===
echo "Test 7: Puerto 3000 NO accesible desde internet"
if command -v curl >/dev/null 2>&1; then
  PUBLIC_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || true)
  if [[ -n "$PUBLIC_IP" ]]; then
    if command -v nc >/dev/null 2>&1; then
      if nc -z -w 3 "$PUBLIC_IP" 3000 2>/dev/null; then
        check_fail "CRÍTICO: puerto 3000 abierto en IP pública $PUBLIC_IP"
      else
        check_pass "Puerto 3000 cerrado/filtered en $PUBLIC_IP"
      fi
    else
      check_warn "nc no disponible; instala nmap o nc para verificar puerto público"
    fi
  else
    check_warn "No pude obtener IP pública para probar"
  fi
else
  check_warn "curl no disponible para obtener IP pública"
fi
echo ""

# === Test 8: re-correr validador local ===
echo "Test 8: Validador local (validate.sh)"
LOCAL_VALIDATE="$(dirname "$0")/../hoy/validate.sh"
if [[ -f "$LOCAL_VALIDATE" ]]; then
  if bash "$LOCAL_VALIDATE"; then
    check_pass "validate.sh pasó (o con warnings no críticos)"
  else
    check_fail "validate.sh reportó fallos críticos"
  fi
else
  check_warn "No se encontró $LOCAL_VALIDATE"
fi
echo ""

# === Resumen ===
echo "═══════════════════════════════════════════════════"
echo "  Resumen Cloudflare"
echo "═══════════════════════════════════════════════════"
echo "  ${GREEN}Passed:${NC}   $PASS"
echo "  ${YELLOW}Warnings:${NC} $WARN"
echo "  ${RED}Failed:${NC}   $FAIL"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "${RED}✗ Hay fallos críticos. No invites directivos hasta resolverlos.${NC}"
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo "${YELLOW}⚠ Pasaste con warnings. Revisa Access/WAF antes de producción.${NC}"
  exit 0
else
  echo "${GREEN}✓ Todo en verde. Seguro invitar al primer directivo.${NC}"
  exit 0
fi
