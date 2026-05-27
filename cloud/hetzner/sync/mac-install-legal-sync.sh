#!/usr/bin/env bash
# mac-install-legal-sync.sh — Registra el launchd que sincroniza las BDs de
# SJF + DOF Mac → Hetzner cada 15 min.
#
# Uso:
#   ./mac-install-legal-sync.sh [HOST] [USER] [SSH_KEY]
#   ./mac-install-legal-sync.sh 204.168.131.21 polo ~/.ssh/id_ed25519

set -euo pipefail

HOST="${1:-204.168.131.21}"
USER_REMOTE="${2:-polo}"
SSH_KEY="${3:-$HOME/.ssh/id_ed25519}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPT="$SCRIPT_DIR/mac-push-legal.sh"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST="$LAUNCH_DIR/ai.kawiil.legal-sync.plist"

# launchd no puede ejecutar scripts en ~/Documents/ (TCC bloquea acceso).
# Copiamos el script a ~/.openclaw/scripts/ que es accesible.
INSTALL_DIR="$HOME/.openclaw/scripts"
PUSH_SCRIPT="$INSTALL_DIR/mac-push-legal.sh"

[[ -f "$SOURCE_SCRIPT" ]] || { echo "✗ No encuentro $SOURCE_SCRIPT"; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "✗ No encuentro SSH key $SSH_KEY"; exit 1; }

mkdir -p "$INSTALL_DIR" "$LAUNCH_DIR" "$LOG_DIR"

# Copiar script a ubicación accesible para launchd
cp "$SOURCE_SCRIPT" "$PUSH_SCRIPT"
chmod +x "$PUSH_SCRIPT"
echo "✓ Script copiado a $PUSH_SCRIPT"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.kawiil.legal-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PUSH_SCRIPT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOUIS_REMOTE_HOST</key><string>$HOST</string>
    <key>LOUIS_REMOTE_USER</key><string>$USER_REMOTE</string>
    <key>LOUIS_SSH_KEY</key><string>$SSH_KEY</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/louis-legal-sync.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/louis-legal-sync.log</string>
</dict>
</plist>
PLIST

echo "✓ Plist escrito en $PLIST"

# Reload
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✓ Cargado en launchd — corre cada 15 min (+ inmediato ahora)"
echo ""
echo "Ver el log en vivo:"
echo "  tail -f $LOG_DIR/louis-legal-sync.log"
echo ""
echo "Forzar una corrida manual:"
echo "  LOUIS_REMOTE_HOST=$HOST LOUIS_REMOTE_USER=$USER_REMOTE LOUIS_SSH_KEY=$SSH_KEY bash $PUSH_SCRIPT"
echo ""
echo "Detener:"
echo "  launchctl unload $PLIST"
