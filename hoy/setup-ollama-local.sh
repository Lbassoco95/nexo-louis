#!/usr/bin/env bash
# setup-ollama-local.sh
# Instala Ollama, detecta hardware, recomienda y baja un modelo apropiado,
# y conecta OpenClaw para usarlo como provider local (sin Anthropic, sin OpenAI).
#
# Uso: bash setup-ollama-local.sh
# Prerequisito: OpenClaw ya instalado (haber corrido install-yoltik-ai.sh).

set -euo pipefail

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

log()  { printf "%s\n" "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { printf "%s\n" "${GREEN}✓${NC} $*"; }
warn() { printf "%s\n" "${YELLOW}⚠${NC} $*"; }
err()  { printf "%s\n" "${RED}✗${NC} $*" >&2; }

# --- Pre-flight ---
if [[ "$(uname)" != "Darwin" ]]; then
  err "Solo macOS por ahora."; exit 1
fi

if ! command -v openclaw >/dev/null 2>&1; then
  err "OpenClaw no instalado. Corre install-yoltik-ai.sh primero."
  exit 1
fi

log "Yoltik AI — Setup Ollama local (sin Anthropic)"
log ""

# --- 1. Detectar hardware ---
log "Paso 1/5: Detectando hardware..."
TOTAL_RAM_BYTES=$(sysctl -n hw.memsize)
TOTAL_RAM_GB=$((TOTAL_RAM_BYTES / 1024 / 1024 / 1024))
CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "desconocido")
ARCH=$(uname -m)

ok "RAM total: ${TOTAL_RAM_GB} GB"
ok "Chip: ${CHIP} (${ARCH})"

# --- 2. Recomendar modelo según RAM ---
log "Paso 2/5: Eligiendo modelo según tu RAM..."

if [[ $TOTAL_RAM_GB -ge 48 ]]; then
  RECOMMENDED_MODEL="qwen3:32b"
  ALT_MODEL="llama3.3"
  RECOMMENDED_REASON="Tienes RAM de sobra. qwen3:32b da excelente tool calling y razonamiento. llama3.3 (70B) es alternativa más fuerte pero más lenta."
elif [[ $TOTAL_RAM_GB -ge 24 ]]; then
  RECOMMENDED_MODEL="qwen3:32b"
  ALT_MODEL="gpt-oss:20b"
  RECOMMENDED_REASON="Sweet spot: qwen3:32b cabe y da el mejor tool calling open weight. Si va lento, baja a gpt-oss:20b."
elif [[ $TOTAL_RAM_GB -ge 16 ]]; then
  RECOMMENDED_MODEL="gpt-oss:20b"
  ALT_MODEL="qwen3:14b"
  RECOMMENDED_REASON="gpt-oss:20b es la mejor opción que cabe holgadamente. qwen3:14b si quieres más velocidad."
elif [[ $TOTAL_RAM_GB -ge 8 ]]; then
  RECOMMENDED_MODEL="gemma4"
  ALT_MODEL="qwen3:4b"
  RECOMMENDED_REASON="Limitado pero funcional para piloto. gemma4 es el default de OpenClaw. Considera actualizar a una Mac con más RAM para producción."
else
  err "RAM insuficiente (${TOTAL_RAM_GB} GB). Ollama necesita ≥8 GB libres."
  err "Te recomiendo usar una Mac Mini con al menos 16 GB."
  exit 1
fi

ok "Modelo recomendado: ${GREEN}$RECOMMENDED_MODEL${NC}"
ok "Alternativa: $ALT_MODEL"
log ""
log "Razón: $RECOMMENDED_REASON"
log ""

read -r -p "Usar el recomendado ($RECOMMENDED_MODEL)? [Y/n/<otro>] " choice
case "$choice" in
  ""|y|Y|yes|YES|si|SI|sí|SÍ) MODEL="$RECOMMENDED_MODEL" ;;
  n|N|no|NO) MODEL="$ALT_MODEL" ;;
  *) MODEL="$choice" ;;
esac
log "Modelo elegido: $MODEL"
log ""

# --- 3. Instalar Ollama ---
log "Paso 3/5: Instalando Ollama..."
if ! command -v ollama >/dev/null 2>&1; then
  brew install ollama
else
  ok "Ollama ya instalado: $(ollama --version 2>&1 | head -1)"
fi

# Arrancar el daemon como servicio (auto-start al boot)
if ! pgrep -x ollama >/dev/null 2>&1; then
  log "Arrancando Ollama daemon..."
  brew services start ollama
  sleep 3
fi

# Verificar
if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  ok "Ollama daemon responde en 127.0.0.1:11434"
else
  err "Ollama no responde. Intenta: brew services restart ollama"
  exit 1
fi

# --- 4. Bajar el modelo ---
log "Paso 4/5: Descargando modelo $MODEL (esto puede tardar 5-30 min según tamaño)..."
log "Estás bajando pesos open weight. Cero costo, todo se queda en tu disco."
log ""

# Si ya está pulled, skip
if ollama list 2>/dev/null | grep -q "^$MODEL"; then
  ok "Modelo $MODEL ya estaba pulled"
else
  ollama pull "$MODEL"
fi

# Smoke test del modelo solo (sin OpenClaw)
log "Smoke test del modelo (sin OpenClaw)..."
if echo "Responde solo 'OK'." | ollama run "$MODEL" --hidethinking 2>/dev/null | head -5 | grep -q .; then
  ok "Modelo responde correctamente"
else
  warn "El modelo está pulled pero el smoke test fue ambiguo. Continúo de todas formas."
fi

# --- 5. Conectar OpenClaw a Ollama ---
log "Paso 5/5: Conectando OpenClaw a Ollama..."

# 5a. Setear la env var en el shell actual y persistir
export OLLAMA_API_KEY="ollama-local"
SHELL_RC=""
if [[ "$SHELL" == *zsh ]]; then SHELL_RC="$HOME/.zshrc"; fi
if [[ "$SHELL" == *bash ]]; then SHELL_RC="$HOME/.bash_profile"; fi
if [[ -n "$SHELL_RC" ]]; then
  if ! grep -q "OLLAMA_API_KEY" "$SHELL_RC" 2>/dev/null; then
    echo '' >> "$SHELL_RC"
    echo '# Yoltik AI — provider local' >> "$SHELL_RC"
    echo 'export OLLAMA_API_KEY="ollama-local"' >> "$SHELL_RC"
    ok "OLLAMA_API_KEY añadido a $SHELL_RC"
  else
    ok "OLLAMA_API_KEY ya en $SHELL_RC"
  fi
fi

# 5b. También ponerlo en ~/.openclaw/.env para que el daemon lo vea
mkdir -p "$HOME/.openclaw"
ENV_FILE="$HOME/.openclaw/.env"
if [[ ! -f "$ENV_FILE" ]] || ! grep -q "OLLAMA_API_KEY" "$ENV_FILE"; then
  echo 'OLLAMA_API_KEY=ollama-local' >> "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "OLLAMA_API_KEY guardado en $ENV_FILE (perms 600)"
fi

# 5c. Setear el modelo en OpenClaw
log "Configurando OpenClaw para usar ollama/$MODEL..."
if openclaw models set "ollama/$MODEL" 2>/dev/null; then
  ok "Modelo set: ollama/$MODEL"
else
  warn "Comando 'openclaw models set' no disponible o falló. Edita manualmente:"
  warn "  openclaw config set agents.defaults.model.primary \"ollama/$MODEL\""
  openclaw config set agents.defaults.model.primary "ollama/$MODEL" 2>/dev/null || true
fi

# 5d. Reiniciar el daemon para que tome la nueva config
log "Reiniciando daemon de OpenClaw..."
openclaw daemon restart 2>/dev/null || openclaw restart 2>/dev/null || true
sleep 5

# --- Verificación final ---
log ""
log "Verificación final..."

# ¿OpenClaw ve el modelo?
if openclaw models list --provider ollama 2>/dev/null | grep -q "$MODEL"; then
  ok "OpenClaw lista el modelo: ollama/$MODEL"
else
  warn "No pude confirmar el modelo en OpenClaw. Corre: openclaw models list --provider ollama"
fi

# Smoke test end-to-end (OpenClaw → Ollama → modelo)
log "Smoke test end-to-end (OpenClaw → Ollama)..."
SMOKE_RESULT=$(openclaw infer model run \
  --local \
  --model "ollama/$MODEL" \
  --prompt "Responde solo con 'OK' si recibes este mensaje." \
  --json 2>&1 | tail -20 || echo "FAIL")

if echo "$SMOKE_RESULT" | grep -iq "ok"; then
  ok "Smoke test PASÓ. OpenClaw está hablando con tu modelo local."
else
  warn "Smoke test ambiguo. Output:"
  echo "$SMOKE_RESULT"
fi

cat <<EOF

${GREEN}═══════════════════════════════════════════════════${NC}
${GREEN}  OpenClaw + Ollama configurado en local${NC}
${GREEN}═══════════════════════════════════════════════════${NC}

Lo que tienes ahora:
  • Modelo: ollama/$MODEL (corre en tu Mac, sin red externa)
  • Ollama daemon: 127.0.0.1:11434 (auto-start al boot)
  • OpenClaw gateway: ver tu config (3000 o 18789)
  • Provider: ollama (auto-discovery activado)
  • Cero llamadas a Anthropic/OpenAI

Próximos pasos:

1. Abre el Web Control UI:
   open http://127.0.0.1:18789  # o el puerto que use tu instancia
   (el token está en ~/.openclaw/gateway-token.txt)

2. Conecta Telegram (sin necesidad de API keys de Claude):
   Sigue las instrucciones en CHECKLIST-POLO.md sección C.

3. Si quieres probar otro modelo más adelante:
   ollama pull gpt-oss:20b
   openclaw models set ollama/gpt-oss:20b
   openclaw daemon restart

4. Para ver lo que el agente hace:
   tail -f ~/.openclaw/logs/*.log

EOF

ok "Listo. Tu laboratorio personal AI está 100% en tu Mac."
