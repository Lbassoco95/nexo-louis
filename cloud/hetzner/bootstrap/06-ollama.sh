#!/usr/bin/env bash
# 06-ollama.sh — Instala Ollama + descarga gpt-oss:20b para uso local privado.
#
# Arquitectura de modelos en Louis:
#   - Día a día (orquestación, clasificación, recordatorios): OpenClaw routea a Ollama (local, gratis)
#   - Deep learning local / contenido privado (legal, ikan): Ollama gpt-oss:20b (8B parámetros activos)
#   - Razonamiento ejecutivo fuerte: Claude Sonnet 4.6 vía Anthropic API
#
# Ollama escucha en 127.0.0.1:11434 (no público). OpenClaw lo invoca como tool.
# Modelo gpt-oss:20b: ~13 GB en RAM activa (MoE — 20B params total, 8B activos por inferencia).
#
# Requisitos: CPX42 o mayor (16 GB+ RAM). En CPX32 (8 GB) NO arranca.
set -euo pipefail

log()  { printf "  \033[1;36m·\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }

SYSTEM_USER="${SYSTEM_USER:-polo}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gpt-oss:20b}"

# --- RAM check ----------------------------------------------
TOTAL_MB=$(grep MemTotal /proc/meminfo | awk '{print int($2/1024)}')
if (( TOTAL_MB < 14000 )); then
  warn "RAM total ${TOTAL_MB} MB — gpt-oss:20b necesita >= 14 GB. Saltando install."
  warn "Sube el VPS a CPX42 (16 GB) o más y vuelve a correr deploy.sh."
  exit 0
fi
log "RAM disponible: ${TOTAL_MB} MB ✓"

# --- Instalar Ollama ----------------------------------------
if command -v ollama >/dev/null 2>&1; then
  log "Ollama ya instalado ($(ollama --version 2>&1 | head -1))"
else
  log "Instalando Ollama (oficial)"
  curl -fsSL https://ollama.ai/install.sh | sh
fi

# El instalador oficial deja un systemd unit ollama.service activo, listening en 127.0.0.1:11434
log "Verificando ollama.service"
systemctl enable --now ollama 2>/dev/null || true
sleep 2

# Forzar bind a localhost (no exponer al mundo)
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_NUM_PARALLEL=1"
EOF
systemctl daemon-reload
systemctl restart ollama
sleep 3

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  warn "Ollama no responde en 127.0.0.1:11434 — revisa: journalctl -u ollama -n 30"
  exit 1
fi
log "Ollama escuchando en 127.0.0.1:11434"

# --- Descargar modelo (puede tardar 5-15 min, ~13 GB) -------
if ollama list 2>/dev/null | grep -qE "^${OLLAMA_MODEL%:*}\s+${OLLAMA_MODEL#*:}"; then
  log "Modelo $OLLAMA_MODEL ya descargado — skip"
else
  log "Descargando $OLLAMA_MODEL (~13 GB, 5-15 min según red)"
  ollama pull "$OLLAMA_MODEL"
fi

# --- Smoke test ---------------------------------------------
log "Smoke test (genera ~50 tokens)"
RESPONSE=$(curl -sS -X POST http://127.0.0.1:11434/api/generate \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$OLLAMA_MODEL\",\"prompt\":\"Responde en una sola línea: ¿quién eres?\",\"stream\":false,\"options\":{\"num_predict\":50}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response','(vacío)'))" 2>/dev/null || echo "(error)")
log "Modelo responde: ${RESPONSE:0:120}"

echo "  ✓ Ollama + $OLLAMA_MODEL listos"
