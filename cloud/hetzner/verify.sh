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

# 4) OpenClaw systemd
if systemctl is-active --quiet openclaw; then
  ok "OpenClaw service activo"
else
  nope "OpenClaw service NO activo (journalctl -u openclaw -n 30)"
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

# 9b) Ollama (modelo local)
if systemctl is-active --quiet ollama; then
  ok "Ollama service activo"
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama responde en 127.0.0.1:11434"
    if curl -fsS http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "gpt-oss"; then
      ok "Modelo gpt-oss disponible"
    else
      warn "Modelo gpt-oss NO encontrado en Ollama (corre: sudo ollama pull gpt-oss:20b)"
    fi
  else
    nope "Ollama NO responde en 127.0.0.1:11434"
  fi
else
  warn "Ollama NO activo (¿VPS sin 16GB+ RAM? bootstrap/06 lo skipea si <14GB)"
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
