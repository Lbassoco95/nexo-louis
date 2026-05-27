#!/usr/bin/env bash
# mac-install-louis-sync.sh — Registra DOS launchd agents en la Mac:
#
#   1) ai.kawiil.heartbeat   → cada 30s sube /opt/openclaw/state/mac_heartbeat.json
#                              (Louis sabe si tu Mac está prendida y con qué batería)
#
#   2) ai.kawiil.louis-sync  → cada 5 min rsync bidireccional Mac↔Hetzner
#                              (Projects/ y .openclaw/spaces/, sin --delete)
#
# Uso:
#   ./mac-install-louis-sync.sh [HOST] [USER] [SSH_KEY]
#   ./mac-install-louis-sync.sh 204.168.131.21 polo ~/.ssh/id_ed25519
#
# Idempotente: re-ejecutar simplemente recarga los plists.

set -euo pipefail

HOST="${1:-204.168.131.21}"
USER_REMOTE="${2:-polo}"
SSH_KEY="${3:-$HOME/.ssh/id_ed25519}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
INSTALL_DIR="$HOME/.openclaw/scripts"

mkdir -p "$INSTALL_DIR" "$LAUNCH_DIR" "$LOG_DIR"

# Copiar scripts a ~/.openclaw/scripts/ (launchd NO puede correr cosas en ~/Documents/ por TCC)
for src in mac-heartbeat.sh mac-sync-bidirectional.sh; do
  cp "$SCRIPT_DIR/$src" "$INSTALL_DIR/$src"
  chmod +x "$INSTALL_DIR/$src"
done
echo "✓ Scripts copiados a $INSTALL_DIR/"

[[ -f "$SSH_KEY" ]] || { echo "✗ No encuentro SSH key $SSH_KEY"; exit 1; }

# ============================================================
# Plist 1: heartbeat (cada 30s)
# ============================================================
HEARTBEAT_PLIST="$LAUNCH_DIR/ai.kawiil.heartbeat.plist"
cat > "$HEARTBEAT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.kawiil.heartbeat</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$INSTALL_DIR/mac-heartbeat.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOUIS_REMOTE_HOST</key><string>$HOST</string>
    <key>LOUIS_REMOTE_USER</key><string>$USER_REMOTE</string>
    <key>LOUIS_SSH_KEY</key><string>$SSH_KEY</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/louis-heartbeat.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/louis-heartbeat.log</string>
</dict>
</plist>
PLIST
echo "✓ Plist heartbeat → $HEARTBEAT_PLIST"

# ============================================================
# Plist 2: sync bidireccional (cada 5 min)
# ============================================================
SYNC_PLIST="$LAUNCH_DIR/ai.kawiil.louis-sync.plist"
cat > "$SYNC_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.kawiil.louis-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$INSTALL_DIR/mac-sync-bidirectional.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOUIS_REMOTE_HOST</key><string>$HOST</string>
    <key>LOUIS_REMOTE_USER</key><string>$USER_REMOTE</string>
    <key>LOUIS_SSH_KEY</key><string>$SSH_KEY</string>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/louis-sync.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/louis-sync.log</string>
</dict>
</plist>
PLIST
echo "✓ Plist sync → $SYNC_PLIST"

# Reload ambos
for plist in "$HEARTBEAT_PLIST" "$SYNC_PLIST"; do
  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
done
echo "✓ Cargados en launchd — heartbeat cada 30s, sync cada 5 min"
echo ""
echo "Logs:"
echo "  tail -f $LOG_DIR/louis-heartbeat.log"
echo "  tail -f $LOG_DIR/louis-sync.log"
echo ""
echo "Forzar heartbeat ahora:"
echo "  LOUIS_REMOTE_HOST=$HOST LOUIS_REMOTE_USER=$USER_REMOTE LOUIS_SSH_KEY=$SSH_KEY bash $INSTALL_DIR/mac-heartbeat.sh"
echo ""
echo "Detener:"
echo "  launchctl unload $HEARTBEAT_PLIST $SYNC_PLIST"
