#!/usr/bin/env bash
# mac-install-projects-sync.sh — Registra el launchd que sincroniza
# ~/Documents/Claude/Projects/ Mac → Hetzner cada 30 min.
#
# Uso:
#   ./mac-install-projects-sync.sh [HOST] [USER] [SSH_KEY]
#   ./mac-install-projects-sync.sh 204.168.131.21 polo ~/.ssh/id_ed25519
#
# IMPORTANTE (macOS/TCC): como la carpeta está en ~/Documents, launchd necesita
# "Acceso a disco completo" para /bin/bash. El script lo recuerda al final.

set -euo pipefail

HOST="${1:-204.168.131.21}"
USER_REMOTE="${2:-polo}"
SSH_KEY="${3:-$HOME/.ssh/id_ed25519}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPT="$SCRIPT_DIR/mac-push-projects.sh"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST="$LAUNCH_DIR/ai.kawiil.projects-sync.plist"

INSTALL_DIR="$HOME/.openclaw/scripts"
PUSH_SCRIPT="$INSTALL_DIR/mac-push-projects.sh"

[[ -f "$SOURCE_SCRIPT" ]] || { echo "✗ No encuentro $SOURCE_SCRIPT"; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "✗ No encuentro SSH key $SSH_KEY"; exit 1; }

mkdir -p "$INSTALL_DIR" "$LAUNCH_DIR" "$LOG_DIR"

cp "$SOURCE_SCRIPT" "$PUSH_SCRIPT"
chmod +x "$PUSH_SCRIPT"
echo "✓ Script copiado a $PUSH_SCRIPT"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.kawiil.projects-sync</string>
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
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/louis-projects-sync.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/louis-projects-sync.log</string>
</dict>
</plist>
PLIST

echo "✓ Plist escrito en $PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✓ Cargado en launchd — corre cada 30 min (+ inmediato ahora)"
echo ""
echo "⚠️  IMPORTANTE — Acceso a disco completo (TCC):"
echo "   La carpeta está en ~/Documents, que macOS protege. Si el log muestra"
echo "   'Operation not permitted', ve a:"
echo "   Ajustes del sistema → Privacidad y seguridad → Acceso a disco completo"
echo "   y agrega /bin/bash (botón +, Cmd+Shift+G, escribe /bin/bash)."
echo ""
echo "Ver el log:   tail -f $LOG_DIR/louis-projects-sync.log"
echo "Forzar corrida: LOUIS_REMOTE_HOST=$HOST LOUIS_REMOTE_USER=$USER_REMOTE LOUIS_SSH_KEY=$SSH_KEY bash $PUSH_SCRIPT"
echo "Detener:      launchctl unload $PLIST"
