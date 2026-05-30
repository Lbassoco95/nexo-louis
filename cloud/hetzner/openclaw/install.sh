#!/usr/bin/env bash
# install.sh — Prepara data dir + env file + spaces + config para Louis, e intenta
# instalar OpenClaw nativo. Si OpenClaw no instala, el resto del stack
# (telegram-bridge, kawiil-agents, Ollama) sigue funcionando.
#
# Idempotente: re-ejecutable.

set -euo pipefail

log()  { printf "  \033[1;36m·\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }

SYSTEM_USER="${SYSTEM_USER:?SYSTEM_USER no definido en .env}"
OPENCLAW_PORT="${OPENCLAW_PORT:-3000}"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-latest}"
OPENCLAW_HOME="/opt/openclaw"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1) Data dir ────────────────────────────────────────────
log "Preparando $OPENCLAW_HOME"
mkdir -p "$OPENCLAW_HOME"/{spaces,credentials,logs,backups,scripts}
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$OPENCLAW_HOME"

# ── 2) Symlink al home del usuario ─────────────────────────
USER_HOME=$(eval echo "~$SYSTEM_USER")
if [[ ! -e "$USER_HOME/.openclaw" ]]; then
  sudo -u "$SYSTEM_USER" ln -s "$OPENCLAW_HOME" "$USER_HOME/.openclaw"
  log "Symlink $USER_HOME/.openclaw → $OPENCLAW_HOME"
fi

# ── 3) Spaces ──────────────────────────────────────────────
for SPACE in general legal ikan quick; do
  mkdir -p "$OPENCLAW_HOME/spaces/$SPACE"
  [[ -f "$OPENCLAW_HOME/spaces/$SPACE/AGENDA.md" ]] || echo "# AGENDA — $SPACE" > "$OPENCLAW_HOME/spaces/$SPACE/AGENDA.md"
done
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$OPENCLAW_HOME/spaces"

# ── 4) Config (independiente del binario) ──────────────────
CONFIG="$OPENCLAW_HOME/openclaw.json"
if [[ ! -f "$CONFIG" ]]; then
  log "Creando openclaw.json con routing híbrido (Ollama local + Claude)"
  cat > "$CONFIG" <<EOF
{
  "version": "1",
  "host": "127.0.0.1",
  "port": ${OPENCLAW_PORT},
  "dataDir": "${OPENCLAW_HOME}",
  "spaces": {
    "general": {
      "model": "anthropic:claude-sonnet-4-6",
      "fallbackModel": "ollama:gpt-oss:20b",
      "systemPromptFile": "spaces/general/system-prompt.md",
      "comment": "Razonamiento ejecutivo (Polo en Kawiil HQ). Claude Sonnet por calidad; fallback local."
    },
    "legal": {
      "model": "ollama:gpt-oss:20b",
      "fallbackModel": "anthropic:claude-sonnet-4-6",
      "systemPromptFile": "spaces/legal/system-prompt.md",
      "comment": "Datos sensibles legales/contratos. Modelo local primero."
    },
    "ikan": {
      "model": "ollama:gpt-oss:20b",
      "fallbackModel": "anthropic:claude-sonnet-4-6",
      "systemPromptFile": "spaces/ikan/system-prompt.md",
      "comment": "Cumplimiento PLD Ikán. Modelo local prioridad."
    },
    "quick": {
      "model": "anthropic:claude-haiku-4-5",
      "fallbackModel": "ollama:gpt-oss:20b",
      "systemPromptFile": "spaces/quick/system-prompt.md",
      "comment": "Recordatorios cortos, clasificación. Haiku 4x más barato que Sonnet."
    }
  },
  "providers": {
    "anthropic": {
      "apiKeyEnv": "ANTHROPIC_API_KEY",
      "baseUrl": "https://api.anthropic.com"
    },
    "ollama": {
      "baseUrl": "http://127.0.0.1:11434",
      "keepAliveMin": 30
    }
  },
  "routing": {
    "defaultSpace": "general",
    "rules": [
      { "if": { "channel": "telegram", "intent": "recordatorio" }, "then": { "space": "quick" } },
      { "if": { "channel": "telegram", "intent": "clasificacion" }, "then": { "space": "quick" } },
      { "if": { "contentSensitive": true }, "then": { "space": "legal" } }
    ]
  }
}
EOF
  chown "$SYSTEM_USER":"$SYSTEM_USER" "$CONFIG"
else
  log "openclaw.json ya existe (skip)"
fi

# ── 5) Env file (SIEMPRE — telegram-bridge.service lo necesita) ──────
ENV_OUT="$OPENCLAW_HOME/openclaw.env"
log "Generando $ENV_OUT (consumido por openclaw.service Y telegram-bridge.service)"
{
  echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
  echo "OPENCLAW_PORT=${OPENCLAW_PORT}"
  echo "OPENCLAW_HOST=127.0.0.1"
  echo "OPENCLAW_DATA_DIR=${OPENCLAW_HOME}"
  echo "NODE_ENV=production"
  [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]      && echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}"
  [[ -n "${TELEGRAM_ALLOWED_CHAT_ID:-}" ]] && echo "TELEGRAM_ALLOWED_CHAT_ID=${TELEGRAM_ALLOWED_CHAT_ID}"
  [[ -n "${SLACK_BOT_TOKEN:-}" ]]         && echo "SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}"
  [[ -n "${SLACK_APP_TOKEN:-}" ]]         && echo "SLACK_APP_TOKEN=${SLACK_APP_TOKEN}"
  [[ -n "${SLACK_SIGNING_SECRET:-}" ]]    && echo "SLACK_SIGNING_SECRET=${SLACK_SIGNING_SECRET}"
  [[ -n "${M365_KAWIIL_TENANT_ID:-}" ]]    && echo "M365_KAWIIL_TENANT_ID=${M365_KAWIIL_TENANT_ID}"
  [[ -n "${M365_KAWIIL_CLIENT_ID:-}" ]]    && echo "M365_KAWIIL_CLIENT_ID=${M365_KAWIIL_CLIENT_ID}"
  [[ -n "${M365_KAWIIL_REDIRECT_URI:-}" ]] && echo "M365_KAWIIL_REDIRECT_URI=${M365_KAWIIL_REDIRECT_URI}"
  [[ -n "${M365_YOLTIK_TENANT_ID:-}" ]]    && echo "M365_YOLTIK_TENANT_ID=${M365_YOLTIK_TENANT_ID}"
  [[ -n "${M365_YOLTIK_CLIENT_ID:-}" ]]    && echo "M365_YOLTIK_CLIENT_ID=${M365_YOLTIK_CLIENT_ID}"
  [[ -n "${M365_YOLTIK_REDIRECT_URI:-}" ]] && echo "M365_YOLTIK_REDIRECT_URI=${M365_YOLTIK_REDIRECT_URI}"
  echo "OLLAMA_HOST=http://127.0.0.1:11434"
  echo "OLLAMA_FAST_MODEL=${OLLAMA_FAST_MODEL:-llama3.1:8b}"
  echo "OLLAMA_QUALITY_MODEL=${OLLAMA_QUALITY_MODEL:-gpt-oss:20b}"
  # OLLAMA_DEFAULT_MODEL se mantiene por compat; apunta al rápido (chat del día a día).
  echo "OLLAMA_DEFAULT_MODEL=${OLLAMA_FAST_MODEL:-llama3.1:8b}"
  echo "KAWIIL_AGENTS_URL=http://127.0.0.1:8000"
  [[ -n "${KAWIIL_DISPATCH_TOKEN:-}" ]]   && echo "KAWIIL_DISPATCH_TOKEN=${KAWIIL_DISPATCH_TOKEN}"
} > "$ENV_OUT"
chmod 600 "$ENV_OUT"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$ENV_OUT"
log "openclaw.env generado ($(wc -l < "$ENV_OUT") variables)"

# ── 6) Instalación del binario OpenClaw (opcional) ──────────
OPENCLAW_BIN=""
if command -v openclaw >/dev/null 2>&1; then
  OPENCLAW_BIN=$(command -v openclaw)
  log "OpenClaw ya instalado: $OPENCLAW_BIN"
elif command -v openclaw-gateway >/dev/null 2>&1; then
  OPENCLAW_BIN=$(command -v openclaw-gateway)
  log "OpenClaw-gateway ya instalado: $OPENCLAW_BIN"
else
  log "Instalando OpenClaw vía instalador oficial (openclaw.ai)"
  if curl -fsSL https://openclaw.ai/install.sh | bash 2>&1; then
    log "Instalador oficial corrió OK"
    # Detectar binario después del install
    for cand in openclaw openclaw-gateway /usr/local/bin/openclaw /root/.openclaw/bin/openclaw "$OPENCLAW_HOME/bin/openclaw"; do
      if [[ -x "$cand" ]] || command -v "$cand" >/dev/null 2>&1; then
        OPENCLAW_BIN=$(command -v "$cand" 2>/dev/null || echo "$cand")
        log "Binario detectado: $OPENCLAW_BIN"
        break
      fi
    done
  else
    warn "Instalador oficial falló (openclaw.ai inalcanzable o cambió formato)"
  fi
fi

if [[ -z "$OPENCLAW_BIN" ]]; then
  warn "OpenClaw native NO instalado — el resto del stack sigue (Ollama, Telegram, agents)."
  warn "Reintentar más tarde con: curl -fsSL https://openclaw.ai/install.sh | bash"
  echo "  ✓ install.sh terminó SIN OpenClaw native (env file generado para telegram-bridge)"
  exit 0
fi

# ── 7) systemd unit ────────────────────────────────────────
log "Instalando systemd unit"
sed \
  -e "s|@@SYSTEM_USER@@|${SYSTEM_USER}|g" \
  -e "s|@@OPENCLAW_BIN@@|${OPENCLAW_BIN}|g" \
  -e "s|@@OPENCLAW_HOME@@|${OPENCLAW_HOME}|g" \
  -e "s|@@ENV_FILE@@|${ENV_OUT}|g" \
  "$SCRIPT_DIR/openclaw.service" > /etc/systemd/system/openclaw.service

systemctl daemon-reload
systemctl enable openclaw
systemctl restart openclaw

# ── 8) Verificación ────────────────────────────────────────
sleep 3
if systemctl is-active --quiet openclaw; then
  log "openclaw.service activo"
else
  warn "openclaw.service NO activo — journalctl -u openclaw -n 50"
  systemctl status openclaw --no-pager 2>/dev/null || true
  exit 0   # no fatal — el resto del deploy sigue
fi

for i in 1 2 3 4 5; do
  if curl -fsS "http://127.0.0.1:${OPENCLAW_PORT}/healthz" >/dev/null 2>&1 \
     || curl -fsS "http://127.0.0.1:${OPENCLAW_PORT}/" >/dev/null 2>&1; then
    log "OpenClaw responde en :${OPENCLAW_PORT}"
    exit 0
  fi
  sleep 2
done
warn "OpenClaw no respondió HTTP en 10s — verifica manualmente"
exit 0
