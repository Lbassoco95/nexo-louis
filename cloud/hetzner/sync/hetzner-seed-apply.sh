#!/usr/bin/env bash
# hetzner-seed-apply.sh — Corre EN HETZNER (como root, después de deploy.sh paso [2/6]).
# Descomprime el seed.tar.gz que llegó desde la Mac, lo coloca en /opt/openclaw,
# y los extras (scripts del proyecto) en sus lugares correctos.
#
# Uso: bash sync/hetzner-seed-apply.sh /tmp/louis-seed.tar.gz [/tmp/louis-extras.tar.gz]

set -euo pipefail

SEED="${1:-/tmp/louis-seed.tar.gz}"
EXTRAS="${2:-/tmp/louis-extras.tar.gz}"
SYSTEM_USER="${SYSTEM_USER:-polo}"
TARGET="/opt/openclaw"

log() { printf "  \033[1;36m·\033[0m %s\n" "$*"; }
fail() { printf "  \033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

[[ -f "$SEED" ]]   || fail "No existe $SEED"
[[ -d "$TARGET" ]] || fail "No existe $TARGET — corre primero deploy.sh hasta el paso [2/6]"

# Backup del estado actual por si acaso
if [[ -d "$TARGET/spaces" ]] && [[ -n "$(ls -A "$TARGET/spaces" 2>/dev/null)" ]]; then
  BACKUP="$TARGET/backups/pre-seed-$(date +%Y%m%d%H%M%S).tar.gz"
  mkdir -p "$TARGET/backups"
  log "Respaldando $TARGET/spaces → $BACKUP"
  tar -czf "$BACKUP" -C "$TARGET" spaces 2>/dev/null || true
fi

# Extraer seed. El tarball tiene paths "~/.openclaw/spaces/..." (sin el ~).
# Extraemos a $TARGET (que es /opt/openclaw), strip-components saca ".openclaw"
log "Extrayendo $SEED en $TARGET"
tar -xzf "$SEED" -C / --strip-components=0
# El tarball usa rutas .openclaw/... relativas a HOME del Mac.
# En el VPS queremos que vayan a /opt/openclaw/.
# Tarball estructura: .openclaw/spaces/... → mover a /opt/openclaw/spaces/...
if [[ -d "/.openclaw" ]]; then
  rsync -a /.openclaw/ "$TARGET"/
  rm -rf /.openclaw
fi

# Extraer extras si existen (m365/scripts, nexo-prompts-v2, kawiil_agents.py)
if [[ -f "$EXTRAS" ]]; then
  log "Extrayendo extras $EXTRAS"
  TMP_EX=$(mktemp -d)
  tar -xzf "$EXTRAS" -C "$TMP_EX"

  # m365 scripts van a /opt/openclaw/scripts/m365/
  if [[ -d "$TMP_EX/m365/scripts" ]]; then
    mkdir -p "$TARGET/scripts/m365"
    cp -a "$TMP_EX/m365/scripts/"* "$TARGET/scripts/m365/"
    chmod +x "$TARGET/scripts/m365"/*.py 2>/dev/null || true
    log "m365/scripts → $TARGET/scripts/m365/"
  fi

  # nexo-prompts-v2 → /opt/openclaw/prompts-v2/ (referencia) y telegram-bridge.py a scripts/
  if [[ -d "$TMP_EX/nexo-prompts-v2" ]]; then
    mkdir -p "$TARGET/prompts-v2"
    cp -a "$TMP_EX/nexo-prompts-v2/"* "$TARGET/prompts-v2/"
    # telegram-bridge.py vive en scripts/ para el systemd unit
    if [[ -f "$TMP_EX/nexo-prompts-v2/telegram-bridge.py" ]]; then
      mkdir -p "$TARGET/scripts"
      cp "$TMP_EX/nexo-prompts-v2/telegram-bridge.py" "$TARGET/scripts/telegram-bridge.py"
      chmod +x "$TARGET/scripts/telegram-bridge.py"
      log "telegram-bridge.py → $TARGET/scripts/"
    fi
  fi

  # kawiil_agents.py (CLI cliente) → /opt/openclaw/scripts/
  if [[ -f "$TMP_EX/kawiil-agents/kawiil_agents.py" ]]; then
    cp "$TMP_EX/kawiil-agents/kawiil_agents.py" "$TARGET/scripts/kawiil_agents.py"
    chmod +x "$TARGET/scripts/kawiil_agents.py"
    log "kawiil_agents.py → $TARGET/scripts/"
  fi

  rm -rf "$TMP_EX"
fi

# Logs dir (telegram-bridge escribe ahí)
mkdir -p "$TARGET/logs"

# Whisper model — si el seed no lo trae (lo excluimos), 05-python-audio lo bajó solo.
# Pero el telegram-bridge.py probablemente busca rutas tipo /opt/homebrew/bin/whisper-cli.
# Lo parchemos al vuelo:
BRIDGE="$TARGET/scripts/telegram-bridge.py"
if [[ -f "$BRIDGE" ]]; then
  log "Parchando paths Mac → Linux en telegram-bridge.py"
  # Cambiar candidates Mac (Homebrew) por candidates Linux (/usr/local/bin, /usr/bin)
  sed -i 's|/opt/homebrew/bin/whisper-cli|/usr/local/bin/whisper-cli|g; s|/opt/homebrew/bin/whisper-cpp|/usr/local/bin/whisper-cli|g; s|/opt/homebrew/bin/ffmpeg|/usr/bin/ffmpeg|g' "$BRIDGE"
  # Asegurar que WHISPER_MODEL apunte a /opt/openclaw/whisper-models (donde 05 lo descargó)
  # (no tocar si ya está bien)
fi

# Ownership final — todo a SYSTEM_USER
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$TARGET"
chmod -R go-rwx "$TARGET/credentials" 2>/dev/null || true

# Resumen
echo ""
log "Resumen post-seed:"
echo "    Spaces:      $(find "$TARGET/spaces" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l) carpetas"
echo "    Memorias:    $(find "$TARGET/spaces" -path '*/memory/*' -type f 2>/dev/null | wc -l) archivos"
echo "    Credenciales:$(ls "$TARGET/credentials"/*.env 2>/dev/null | wc -l) .env"
echo "    Tokens M365: $(ls "$TARGET/credentials"/m365-*-tokens.json 2>/dev/null | wc -l) archivos"
echo "    Scripts:     $(ls "$TARGET/scripts"/*.py 2>/dev/null | wc -l) python"

echo ""
echo "  ✓ Seed aplicado en $TARGET"
echo "  ⚠ Siguiente: reinicia OpenClaw para que vea los nuevos prompts:"
echo "    sudo systemctl restart openclaw"
echo "  ⚠ Y arranca el telegram-bridge:"
echo "    sudo systemctl enable --now telegram-bridge"
