#!/usr/bin/env bash
# install-telegram-bridge.sh — Convierte Telegram en canal completo para Louis.
#   1. Instala whisper-cpp + ffmpeg via brew
#   2. Descarga modelo whisper medium (~769 MB)
#   3. Copia telegram-bridge.py a ~/.openclaw/
#   4. Crea LaunchAgent que arranca el bridge al boot
#   5. Carga el LaunchAgent (arranca el bridge)
#   6. Manda mensaje de prueba

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_OC="$HOME/.openclaw"
STAMP="$(date +%Y%m%d-%H%M%S)"

# === Colores ===
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

log()  { printf "%s\n" "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { printf "%s\n" "${GREEN}✓${NC} $*"; }
warn() { printf "%s\n" "${YELLOW}⚠${NC} $*"; }
err()  { printf "%s\n" "${RED}✗${NC} $*" >&2; }

# === Verificaciones previas ===
log "Verificando que Telegram esté configurado..."
if [[ ! -f "$HOME_OC/credentials/telegram.env" ]]; then
  err "No encontré $HOME_OC/credentials/telegram.env"
  err "Corre primero: bash install-telegram.sh"
  exit 1
fi
ok "Telegram credentials OK"

log "Verificando que Anthropic API key esté en ~/.openclaw/.env..."
if ! grep -q "ANTHROPIC_API_KEY" "$HOME_OC/.env" 2>/dev/null; then
  warn "No encontré ANTHROPIC_API_KEY en $HOME_OC/.env"
  warn "El bridge la va a buscar también en la variable de entorno."
  warn "Si falla, agrégala manualmente al .env y reinicia el bridge."
fi

# === Paso 1: Homebrew ===
log "Paso 1/7: Verificando Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
  err "Homebrew no está instalado. Instálalo desde https://brew.sh primero."
  exit 1
fi
ok "Homebrew presente"

# === Paso 2: ffmpeg ===
log "Paso 2/7: Instalando ffmpeg (si no está)..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  brew install ffmpeg
else
  ok "ffmpeg ya instalado: $(ffmpeg -version 2>&1 | head -1)"
fi

# === Paso 3: whisper-cpp ===
log "Paso 3/7: Instalando whisper-cpp (si no está)..."
if ! command -v whisper-cli >/dev/null 2>&1 && ! command -v whisper-cpp >/dev/null 2>&1; then
  brew install whisper-cpp
fi

WHISPER_BIN=""
for cand in /opt/homebrew/bin/whisper-cli /opt/homebrew/bin/whisper-cpp /usr/local/bin/whisper-cli /usr/local/bin/whisper-cpp; do
  if [[ -x "$cand" ]]; then
    WHISPER_BIN="$cand"
    break
  fi
done

if [[ -z "$WHISPER_BIN" ]]; then
  err "whisper-cpp instalado pero no encontré el binario en paths esperados."
  exit 1
fi
ok "whisper binary: $WHISPER_BIN"

# === Paso 4: Modelo whisper medium ===
log "Paso 4/7: Descargando modelo whisper 'medium' (~769 MB, puede tardar)..."
WHISPER_DIR="$HOME_OC/whisper-models"
mkdir -p "$WHISPER_DIR"
MODEL_PATH="$WHISPER_DIR/ggml-medium.bin"

if [[ -f "$MODEL_PATH" ]] && [[ "$(stat -f%z "$MODEL_PATH" 2>/dev/null || stat -c%s "$MODEL_PATH" 2>/dev/null)" -gt 500000000 ]]; then
  ok "Modelo medium ya descargado en $MODEL_PATH"
else
  log "    Descargando de Hugging Face..."
  curl -L --progress-bar -o "$MODEL_PATH" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin"
  ok "Modelo descargado"
fi

# === Paso 5: Copiar bridge ===
log "Paso 5/7: Instalando telegram-bridge.py en $HOME_OC/"
cp "$SRC/telegram-bridge.py" "$HOME_OC/telegram-bridge.py"
chmod +x "$HOME_OC/telegram-bridge.py"
ok "Bridge instalado en $HOME_OC/telegram-bridge.py"

# === Paso 6: LaunchAgent ===
log "Paso 6/7: Creando LaunchAgent ai.openclaw.telegram-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/ai.openclaw.telegram-bridge.plist"

# Detectar python3
PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="/usr/bin/python3"
fi
ok "Python: $PYTHON_BIN"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.openclaw.telegram-bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$HOME_OC/telegram-bridge.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME_OC/logs/telegram-bridge.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME_OC/logs/telegram-bridge.stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF
ok "Plist creado: $PLIST_PATH"

# === Paso 7: Cargar LaunchAgent ===
log "Paso 7/7: Cargando LaunchAgent (arranca el bridge)..."
# Si ya está cargado, descargarlo primero
launchctl bootout "gui/$(id -u)/ai.openclaw.telegram-bridge" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
sleep 2

# Verificar
if launchctl print "gui/$(id -u)/ai.openclaw.telegram-bridge" >/dev/null 2>&1; then
  ok "Bridge corriendo. Logs en $HOME_OC/logs/telegram-bridge.log"
else
  err "Bridge no quedó corriendo. Revisa logs:"
  err "    tail -50 $HOME_OC/logs/telegram-bridge.stderr.log"
fi

# === Mensaje de prueba ===
echo ""
log "Mandando mensaje de prueba a tu Telegram..."
sleep 3
bash "$HOME_OC/spaces/general/scripts/enviar-telegram.sh" \
  "🚀 Bridge de Telegram activo. Ya puedes mandarme texto o audio desde aquí — yo proceso todo en tu Mac, transcribo voz con Whisper local, y respondo. Pruébame: mándame un mensaje o un audio."

echo ""
cat <<EOF
${GREEN}═══════════════════════════════════════════════════════════════════${NC}
${GREEN}  Bridge instalado y corriendo${NC}
${GREEN}═══════════════════════════════════════════════════════════════════${NC}

Lo que cambió:
  • Puedes platicar con Louis 100% desde Telegram (texto + audio)
  • Audios se transcriben localmente con Whisper medium en español
  • Louis tiene acceso a tu memoria (AGENDA, USER, LEARNINGS, etc.) y tools
  • El bridge corre como LaunchAgent — arranca al boot, auto-restart si crashea

Pruebas que sugiero:
  1. Manda un mensaje de texto al bot
     "Buenas Louis, ¿qué tengo pendiente?"

  2. Manda un audio (hold para grabar) al bot
     "Recuérdame mañana a las nueve llamar a Marco"

  3. Pregunta algo que requiera memoria
     "¿Cómo te dije que prefería que respondieras?"

Logs y debug:
  tail -f $HOME_OC/logs/telegram-bridge.log

Apagar el bridge temporalmente:
  launchctl bootout gui/$(id -u)/ai.openclaw.telegram-bridge

Volverlo a arrancar:
  launchctl bootstrap gui/$(id -u) $PLIST_PATH

Para reiniciar tras un cambio en el bridge:
  launchctl kickstart -k gui/$(id -u)/ai.openclaw.telegram-bridge

PENDIENTE DE SEGURIDAD: cuando termines validación, rota:
  • Telegram bot token (di '/revoke' a @BotFather, actualiza telegram.env)
  • Anthropic API key (la regeneras en console.anthropic.com)
${GREEN}═══════════════════════════════════════════════════════════════════${NC}
EOF
