#!/usr/bin/env bash
# 06-ollama.sh — Instala Ollama + dos modelos locales:
#
#   • RÁPIDO   (default chat): llama3.1:8b  (~4.7 GB) — respuestas ágiles (~2-5s).
#   • CALIDAD  (/oss, legal):  gpt-oss:20b  (~13 GB)  — razonamiento profundo, lento en CPU.
#
# Arquitectura de modelos en Donna:
#   - Chat día a día / saludos / clasificación → Ollama RÁPIDO (local, gratis, snappy)
#   - Contenido privado profundo (/oss, legal, ikan) → Ollama CALIDAD gpt-oss:20b
#   - Razonamiento ejecutivo + tools → Claude Sonnet 4.6 (Anthropic API)
#
# Ollama escucha en 127.0.0.1:11434 (no público). Los bridges lo invocan.
#
# Degradación por RAM:
#   - El modelo RÁPIDO se descarga SIEMPRE (cabe hasta en 8 GB).
#   - El modelo CALIDAD (gpt-oss:20b) solo si hay >= 14 GB. Si no, Donna sigue
#     funcional con el rápido y /oss cae al rápido con aviso.
#   - OLLAMA_MAX_LOADED_MODELS=1 → Ollama nunca tiene los dos modelos en RAM a la
#     vez (descarga uno antes de cargar el otro). Esto evita OOM en CPX42 (16 GB):
#     normalmente reside el 8b (~5 GB); al usar /oss se desaloja y carga el 20b.
set -euo pipefail

log()  { printf "  \033[1;36m·\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }

SYSTEM_USER="${SYSTEM_USER:-polo}"
OLLAMA_FAST_MODEL="${OLLAMA_FAST_MODEL:-llama3.1:8b}"
OLLAMA_QUALITY_MODEL="${OLLAMA_QUALITY_MODEL:-gpt-oss:20b}"
QUALITY_MIN_MB="${QUALITY_MIN_MB:-14000}"

TOTAL_MB=$(grep MemTotal /proc/meminfo | awk '{print int($2/1024)}')
log "RAM total: ${TOTAL_MB} MB"

# --- Instalar Ollama (sin gate de RAM: el rápido cabe siempre) ----
if command -v ollama >/dev/null 2>&1; then
  log "Ollama ya instalado ($(ollama --version 2>&1 | head -1))"
else
  log "Instalando Ollama (oficial)"
  curl -fsSL https://ollama.ai/install.sh | sh
fi

# El instalador oficial deja un systemd unit ollama.service listening en 127.0.0.1:11434
systemctl enable --now ollama 2>/dev/null || true
sleep 2

# Override: bind localhost + un solo modelo cargado a la vez (anti-OOM)
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_NUM_PARALLEL=1"
# Mantiene 1 modelo en RAM. Al cambiar entre llama3.1:8b y gpt-oss:20b,
# desaloja el anterior antes de cargar el nuevo → nunca suma 8b+20b (evita OOM).
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF
systemctl daemon-reload
systemctl restart ollama
sleep 3

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  warn "Ollama no responde en 127.0.0.1:11434 — revisa: journalctl -u ollama -n 30"
  exit 1
fi
log "Ollama escuchando en 127.0.0.1:11434"

# --- Helper: pull idempotente (coincidencia exacta por nombre) ----
pull_model() {
  local m="$1"
  if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$m"; then
    log "Modelo $m ya descargado — skip"
  else
    log "Descargando $m … (puede tardar varios minutos según red)"
    ollama pull "$m"
  fi
}

# --- 1) Modelo RÁPIDO (siempre) -----------------------------------
pull_model "$OLLAMA_FAST_MODEL"

# --- 2) Modelo CALIDAD (solo si hay RAM) --------------------------
if (( TOTAL_MB >= QUALITY_MIN_MB )); then
  pull_model "$OLLAMA_QUALITY_MODEL"
else
  warn "RAM ${TOTAL_MB} MB < ${QUALITY_MIN_MB} MB — NO descargo $OLLAMA_QUALITY_MODEL."
  warn "Donna queda funcional con $OLLAMA_FAST_MODEL. Para el modelo de calidad (/oss),"
  warn "sube el VPS a CPX42 (16 GB)+ y vuelve a correr ./deploy.sh."
fi

# --- 3) Smoke test del modelo rápido ------------------------------
log "Smoke test ($OLLAMA_FAST_MODEL, ~50 tokens)"
RESPONSE=$(curl -sS -X POST http://127.0.0.1:11434/api/generate \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$OLLAMA_FAST_MODEL\",\"prompt\":\"Responde en una sola línea: ¿quién eres?\",\"stream\":false,\"options\":{\"num_predict\":50}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response','(vacío)'))" 2>/dev/null || echo "(error)")
log "Modelo rápido responde: ${RESPONSE:0:120}"

if (( TOTAL_MB >= QUALITY_MIN_MB )); then
  echo "  ✓ Ollama listo — rápido: $OLLAMA_FAST_MODEL · calidad: $OLLAMA_QUALITY_MODEL"
else
  echo "  ✓ Ollama listo — rápido: $OLLAMA_FAST_MODEL (sin modelo de calidad: RAM insuficiente)"
fi
