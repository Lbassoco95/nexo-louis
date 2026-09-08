#!/usr/bin/env bash
# fix-audio-transcription.sh — Diagnostica y arregla la transcripción de audio
# (whisper.cpp + ffmpeg + modelo) en Hetzner.
#
# Causas posibles cuando aparece "(error transcribiendo)" en Telegram:
#  1. /usr/local/bin/whisper-cli no existe (no compiló)
#  2. /opt/openclaw/whisper-models/ggml-medium.bin no descargó
#  3. ffmpeg ausente
#  4. whisper-cli falla por libs (libopenblas, libgomp)
#
# Este script:
#   - Verifica cada componente
#   - Recompila whisper.cpp si falta
#   - Re-descarga el modelo si falta o está corrupto
#   - Test end-to-end con un .wav sintético
#
# Correr en Hetzner como root o con sudo:
#   sudo bash fix-audio-transcription.sh

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "  \033[1;36m· %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; }

WHISPER_DIR="/opt/openclaw/whisper-models"
MODEL="$WHISPER_DIR/ggml-medium.bin"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin"
SMALL_MODEL="$WHISPER_DIR/ggml-small.bin"
SMALL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
WHISPER_BIN="/usr/local/bin/whisper-cli"
SYSTEM_USER="${SYSTEM_USER:-polo}"

echo "════════════════════════════════════════"
echo "  Diagnóstico audio transcripción"
echo "════════════════════════════════════════"

# 1) ffmpeg
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg: $(ffmpeg -version | head -1)"
else
  warn "ffmpeg ausente — instalando"
  DEBIAN_FRONTEND=noninteractive apt-get install -yq ffmpeg
  ok "ffmpeg instalado"
fi

# 2) whisper-cli
if [[ -x "$WHISPER_BIN" ]]; then
  ok "whisper-cli presente: $WHISPER_BIN"
  # Verifica que realmente ejecute
  if ! "$WHISPER_BIN" --help >/dev/null 2>&1; then
    warn "whisper-cli existe pero falla al ejecutar (libs?) — recompilando"
    rm -f "$WHISPER_BIN"
  fi
fi

if [[ ! -x "$WHISPER_BIN" ]]; then
  log "Compilando whisper.cpp ESTÁTICO (~3 min) — sin shared libs para evitar libwhisper.so.1 missing"
  apt-get install -yq build-essential cmake git libopenblas-dev >/dev/null
  TMP=$(mktemp -d)
  git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$TMP/whisper.cpp" 2>&1 | tail -3
  cd "$TMP/whisper.cpp"
  # BUILD_SHARED_LIBS=OFF → todo embebido en el binario, no requiere libwhisper.so.1 en runtime
  cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_OPENBLAS=ON \
    >/dev/null
  cmake --build build --target whisper-cli -j"$(nproc)" 2>&1 | tail -3
  install -m 0755 build/bin/whisper-cli "$WHISPER_BIN"
  # Por si en el futuro hay shared libs, copia también al dir estándar y refresca ldconfig
  if find build -name "libwhisper*.so*" 2>/dev/null | head -1 | grep -q .; then
    log "Detecté libs compartidas — copiándolas a /usr/local/lib y ldconfig"
    find build -name "libwhisper*.so*" -exec cp -a {} /usr/local/lib/ \;
    find build -name "libggml*.so*"   -exec cp -a {} /usr/local/lib/ \;
    ldconfig
  fi
  cd /
  rm -rf "$TMP"
  # Verifica que el binario corra sin libs externas
  if ! "$WHISPER_BIN" --help >/dev/null 2>&1; then
    fail "whisper-cli compiló pero NO ejecuta. Verifica: ldd $WHISPER_BIN"
  fi
  ok "whisper-cli instalado en $WHISPER_BIN (verificado runnable)"
fi

# 3) Modelo
mkdir -p "$WHISPER_DIR"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$WHISPER_DIR"

# El telegram-bridge busca medium primero; si tarda mucho, podemos fallback a small.
# Mantenemos medium como default (mejor calidad para español).
need_download=true
if [[ -f "$MODEL" ]]; then
  SIZE_MB=$(du -m "$MODEL" | cut -f1)
  if (( SIZE_MB > 700 )); then
    ok "Modelo medium presente ($SIZE_MB MB)"
    need_download=false
  else
    warn "Modelo medium corrupto/incompleto ($SIZE_MB MB esperaba ~769) — re-descargando"
    rm -f "$MODEL"
  fi
fi

if $need_download; then
  log "Descargando ggml-medium.bin (~769 MB)…"
  curl -fL --progress-bar -o "$MODEL.tmp" "$MODEL_URL" && mv "$MODEL.tmp" "$MODEL"
  chown "$SYSTEM_USER":"$SYSTEM_USER" "$MODEL"
  ok "Modelo medium descargado: $(du -h "$MODEL" | cut -f1)"
fi

# Bonus: small como fallback rápido
if [[ ! -f "$SMALL_MODEL" ]]; then
  log "Bajando ggml-small.bin (~466 MB) como backup rápido"
  curl -fL --progress-bar -o "$SMALL_MODEL.tmp" "$SMALL_URL" && mv "$SMALL_MODEL.tmp" "$SMALL_MODEL"
  chown "$SYSTEM_USER":"$SYSTEM_USER" "$SMALL_MODEL"
  ok "Modelo small descargado: $(du -h "$SMALL_MODEL" | cut -f1)"
else
  ok "Modelo small ya presente"
fi

echo ""
echo "════════════════════════════════════════"
echo "  Test end-to-end"
echo "════════════════════════════════════════"

# 4) Generar un .wav de prueba con voz sintética (espeak-ng) o tono puro como fallback
TMP_DIR=$(mktemp -d)
TEST_WAV="$TMP_DIR/test.wav"

if command -v espeak-ng >/dev/null 2>&1; then
  espeak-ng -v es -w "$TEST_WAV" "Hola Donna, prueba de transcripción audio." 2>/dev/null
elif apt-get install -yq espeak-ng >/dev/null 2>&1 && command -v espeak-ng >/dev/null 2>&1; then
  espeak-ng -v es -w "$TEST_WAV" "Hola Donna, prueba de transcripción audio." 2>/dev/null
else
  # Fallback: tono de 1s para verificar que el binario corre (no producirá texto válido)
  warn "espeak-ng no disponible; usando tono puro (no producirá texto pero verifica pipeline)"
  ffmpeg -y -f lavfi -i "sine=frequency=440:duration=1" -ar 16000 -ac 1 "$TEST_WAV" 2>/dev/null
fi

if [[ ! -f "$TEST_WAV" ]]; then
  fail "No pude generar audio de prueba — skip test"
  exit 1
fi

# Asegura 16 kHz mono
ffmpeg -y -i "$TEST_WAV" -ar 16000 -ac 1 "$TMP_DIR/test16k.wav" 2>/dev/null
log "Audio de prueba: $(du -h "$TMP_DIR/test16k.wav" | cut -f1)"

# Corre whisper
log "Corriendo whisper-cli con modelo medium…"
START=$(date +%s)
if "$WHISPER_BIN" -m "$MODEL" -f "$TMP_DIR/test16k.wav" -l es -otxt -of "$TMP_DIR/transcript" --no-prints 2>"$TMP_DIR/whisper.err"; then
  ELAPSED=$(( $(date +%s) - START ))
  if [[ -f "$TMP_DIR/transcript.txt" ]]; then
    TRANS=$(cat "$TMP_DIR/transcript.txt")
    ok "Whisper corrió OK en ${ELAPSED}s"
    echo "    Transcripción: «$TRANS»"
  else
    warn "Whisper corrió pero no produjo transcript.txt"
  fi
else
  fail "Whisper falló:"
  cat "$TMP_DIR/whisper.err" | head -20
  exit 1
fi

rm -rf "$TMP_DIR"

echo ""
echo "════════════════════════════════════════"
echo "  Reinicio telegram-bridge"
echo "════════════════════════════════════════"

systemctl restart telegram-bridge
sleep 2
if systemctl is-active --quiet telegram-bridge; then
  ok "telegram-bridge active"
else
  fail "telegram-bridge NO active — revisa: journalctl -u telegram-bridge -n 30"
fi

echo ""
ok "Listo. Manda una nota de voz por Telegram para probar end-to-end."
