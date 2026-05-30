#!/usr/bin/env bash
# verify.sh — Health check post-deploy. Salida 0 si todo OK, 1 si algo falla.
# Diseñado para correrse después de deploy.sh, antes de cantar victoria.

set -uo pipefail

PASS=0
FAIL=0

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; PASS=$((PASS+1)); }
nope() { printf "\033[1;31m✗\033[0m %s\n" "$*"; FAIL=$((FAIL+1)); }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

echo "══════════════════════════════════════════"
echo " Louis health check — $(date -Iseconds)"
echo "══════════════════════════════════════════"

# 1) ufw
if ufw status 2>/dev/null | grep -q "Status: active"; then
  ok "ufw activo"
else
  nope "ufw NO activo"
fi

# 2) fail2ban
if systemctl is-active --quiet fail2ban; then
  ok "fail2ban activo"
else
  nope "fail2ban NO activo"
fi

# 3) Docker
if docker info >/dev/null 2>&1; then
  ok "Docker daemon OK"
else
  nope "Docker daemon NO responde"
fi

# 4) Cerebro en :3000 — binario oficial (raro) o gateway Python nativo (lo normal)
if systemctl is-active --quiet openclaw; then
  ok "OpenClaw native service activo"
elif systemctl is-active --quiet openclaw-gateway; then
  ok "OpenClaw gateway (Python nativo) activo — sin binario oficial, es lo esperado"
else
  nope "Ni openclaw ni openclaw-gateway activos (journalctl -u openclaw-gateway -n 30)"
fi

# 5) OpenClaw HTTP local
PORT="${OPENCLAW_PORT:-3000}"
if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 \
   || curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
  ok "OpenClaw responde en 127.0.0.1:$PORT"
else
  nope "OpenClaw NO responde en 127.0.0.1:$PORT"
fi

# 6) Caddy container
# Detecta con ps + grep para compatibilidad con docker compose <2.20
if docker compose ps --format '{{.Service}}\t{{.State}}' 2>/dev/null | grep -E '^caddy\s+running' >/dev/null; then
  ok "Caddy container running"
else
  nope "Caddy container NO running"
fi

# 7) kawiil-agents container
if docker compose ps --format '{{.Service}}\t{{.State}}' 2>/dev/null | grep -E '^kawiil-agents\s+running' >/dev/null; then
  ok "kawiil-agents container running"
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 \
     || curl -fsS http://127.0.0.1:8000/api/agents >/dev/null 2>&1; then
    ok "kawiil-agents responde en 127.0.0.1:8000"
  else
    nope "kawiil-agents NO responde en 127.0.0.1:8000"
  fi
else
  warn "kawiil-agents container NO running (revisa que /opt/kawiil/repo se clonó OK)"
fi

# 8) Puertos públicos
for port in 80 443; do
  if ss -tln 2>/dev/null | grep -qE ":${port}\b"; then
    ok "Puerto $port escuchando"
  else
    nope "Puerto $port NO escuchando"
  fi
done

# 9) TLS público (si DNS ya propagó)
if [[ -n "${LOUIS_DOMAIN:-}" ]]; then
  if curl -fsS -m 10 -I "https://${LOUIS_DOMAIN}" 2>/dev/null | head -1 | grep -qE "HTTP/[12]"; then
    ok "https://${LOUIS_DOMAIN} responde"
  else
    warn "https://${LOUIS_DOMAIN} aún no responde (DNS o ACME en curso)"
  fi
fi

# 9b) Louis bridges + gateway (systemd nativo)
for unit in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    ok "$unit activo"
  else
    nope "$unit NO activo"
  fi
done
GW_PORT="${OPENCLAW_PORT:-3000}"
if curl -fsS "http://127.0.0.1:${GW_PORT}/v1/status" 2>/dev/null | grep -q '"agents_count"'; then
  ok "openclaw-gateway /v1/status OK"
  AC=$(curl -fsS "http://127.0.0.1:${GW_PORT}/v1/status" 2>/dev/null | grep -o '"agents_count": *[0-9]*' | grep -o '[0-9]*' || echo 0)
  if [[ "${AC:-0}" -gt 0 ]]; then
    ok "agents_count=${AC}"
  else
    warn "agents_count=0 (corre: sudo bash /opt/openclaw/scripts/import-legal-agents.sh)"
  fi
else
  nope "openclaw-gateway /v1/status NO responde"
fi

# 9c) Ollama (modelo local)
if systemctl is-active --quiet ollama; then
  ok "Ollama service activo"
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama responde en 127.0.0.1:11434"
    TAGS=$(curl -fsS http://127.0.0.1:11434/api/tags 2>/dev/null || echo "")
    if echo "$TAGS" | grep -q "llama3.1"; then
      ok "Modelo llama3.1 (chat rápido) disponible"
    else
      warn "llama3.1:8b NO en Ollama (corre: sudo ollama pull llama3.1:8b)"
    fi
    if echo "$TAGS" | grep -q "gpt-oss"; then
      ok "Modelo gpt-oss (calidad /oss) disponible"
    else
      warn "gpt-oss:20b NO encontrado (opcional: sudo ollama pull gpt-oss:20b)"
    fi
  else
    nope "Ollama NO responde en 127.0.0.1:11434"
  fi
else
  warn "Ollama NO activo (¿VPS sin 16GB+ RAM? bootstrap/06 lo skipea si <14GB)"
fi

# 9d) louis_core smoke (routing)
if python3 -c "
import sys
sys.path.insert(0, '/opt/openclaw/scripts')
import louis_core as c
assert c.should_deterministic_operational_response('hola')
assert c.needs_memory_write('anota en agenda: x')
print('louis_core routing OK')
" 2>/dev/null; then
  ok "louis_core routing smoke OK"
else
  warn "louis_core smoke falló (¿scripts desactualizados?)"
fi

# 10) Sync infra
if [[ -d /opt/openclaw-sync ]]; then
  ok "Carpeta /opt/openclaw-sync existe"
else
  nope "Carpeta /opt/openclaw-sync NO existe"
fi

if [[ -f /etc/cron.d/louis-sync ]]; then
  ok "Cron louis-sync registrado"
else
  nope "Cron louis-sync NO registrado"
fi

echo "══════════════════════════════════════════"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "══════════════════════════════════════════"

[[ $FAIL -eq 0 ]] || exit 1
