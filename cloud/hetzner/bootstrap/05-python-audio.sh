#!/usr/bin/env bash
# 05-python-audio.sh — Dependencias del telegram-bridge: Python 3.12, ffmpeg, whisper.cpp
# whisper.cpp se compila desde source (apt no lo trae). ffmpeg desde apt.
set -euo pipefail

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }

log "Instalando Python 3.12 + ffmpeg + libs de audio"
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
  python3 python3-pip python3-venv \
  ffmpeg \
  build-essential cmake git

# Python deps que el telegram-bridge + slack-bridge necesitan (viven en el host, no Docker)
log "Instalando deps Python globales (--break-system-packages para Ubuntu 24)"
pip3 install --quiet --break-system-packages \
  anthropic \
  python-telegram-bot==21.6 \
  requests \
  slack-bolt \
  slack-sdk

# whisper.cpp — compilar binario whisper-cli y dejarlo en /usr/local/bin
if [[ ! -x /usr/local/bin/whisper-cli ]]; then
  log "Compilando whisper.cpp (~3 min en CPX21)"
  TMP=$(mktemp -d)
  git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$TMP/whisper.cpp"
  cd "$TMP/whisper.cpp"
  cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build build --target whisper-cli -j"$(nproc)" >/dev/null
  install -m 0755 build/bin/whisper-cli /usr/local/bin/whisper-cli
  cd /
  rm -rf "$TMP"
  log "whisper-cli instalado en /usr/local/bin/whisper-cli"
else
  log "whisper-cli ya instalado — skip"
fi

# Modelo whisper (medium, ~769 MB) — sólo si no existe
WHISPER_DIR="/opt/openclaw/whisper-models"
MODEL="$WHISPER_DIR/ggml-medium.bin"
if [[ ! -f "$MODEL" ]]; then
  log "Descargando modelo whisper medium (~769 MB)"
  mkdir -p "$WHISPER_DIR"
  curl -fL -o "$MODEL" \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
  chown -R "${SYSTEM_USER:-polo}":"${SYSTEM_USER:-polo}" "$WHISPER_DIR"
  log "Modelo guardado en $MODEL"
else
  log "Modelo whisper ya presente — skip"
fi

echo "  ✓ Audio stack listo"
