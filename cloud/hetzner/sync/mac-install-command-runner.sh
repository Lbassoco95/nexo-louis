#!/usr/bin/env bash
# mac-install-command-runner.sh — Registra el launchd que hace que la Mac
# consulte la cola de comandos en Hetzner cada 60s y ejecute backfills DOF/SJF.
#
# Uso:
#   ./mac-install-command-runner.sh [HOST] [USER] [SSH_KEY]
#   ./mac-install-command-runner.sh 204.168.131.21 polo ~/.ssh/id_ed25519
#
# Si tus scripts de backfill NO están en las rutas por defecto
# (~/dof_biblioteca/backfill.py, ~/sjf_biblioteca/backfill.py), edita el plist
# generado y agrega las env vars DOF_BACKFILL_CMD / SJF_BACKFILL_CMD, o
# expórtalas antes de correr este instalador.

set -euo pipefail

HOST="${1:-204.168.131.21}"
USER_REMOTE="${2:-polo}"
SSH_KEY="${3:-$HOME/.ssh/id_ed25519}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPT="$SCRIPT_DIR/mac-command-runner.sh"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST="$LAUNCH_DIR/ai.kawiil.command-runner.plist"

# launchd no puede ejecutar scripts en ~/Documents/ (TCC). Copiamos a ~/.openclaw/scripts/.
INSTALL_DIR="$HOME/.openclaw/scripts"
RUNNER_SCRIPT="$INSTALL_DIR/mac-command-runner.sh"

[[ -f "$SOURCE_SCRIPT" ]] || { echo "✗ No encuentro $SOURCE_SCRIPT"; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "✗ No encuentro SSH key $SSH_KEY"; exit 1; }

mkdir -p "$INSTALL_DIR" "$LAUNCH_DIR" "$LOG_DIR"

cp "$SOURCE_SCRIPT" "$RUNNER_SCRIPT"
chmod +x "$RUNNER_SCRIPT"
echo "✓ Script copiado a $RUNNER_SCRIPT"

# Permite sobrescribir los comandos de backfill vía env al instalar
DOF_CMD="${DOF_BACKFILL_CMD:-cd \$HOME/dof_biblioteca && python3 dof_biblioteca.py}"
DOF_MES_CMD="${DOF_BACKFILL_MES_CMD:-cd \$HOME/dof_biblioteca && python3 dof_biblioteca.py --mes}"
SJF_CMD="${SJF_BACKFILL_CMD:-cd \$HOME/sjf_biblioteca && python3 sjf_biblioteca.py}"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.kawiil.command-runner</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUNNER_SCRIPT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOUIS_REMOTE_HOST</key><string>$HOST</string>
    <key>LOUIS_REMOTE_USER</key><string>$USER_REMOTE</string>
    <key>LOUIS_SSH_KEY</key><string>$SSH_KEY</string>
    <key>DOF_BACKFILL_CMD</key><string>$DOF_CMD</string>
    <key>DOF_BACKFILL_MES_CMD</key><string>$DOF_MES_CMD</string>
    <key>SJF_BACKFILL_CMD</key><string>$SJF_CMD</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/louis-command-runner.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/louis-command-runner.log</string>
</dict>
</plist>
PLIST

echo "✓ Plist escrito en $PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✓ Cargado en launchd — la Mac revisa la cola cada 60s"
echo ""
echo "IMPORTANTE: verifica que las rutas de backfill sean correctas:"
echo "  DOF: $DOF_CMD"
echo "  SJF: $SJF_CMD"
echo "Si tus scripts viven en otro lado, edita $PLIST y recarga."
echo ""
echo "Ver el log en vivo:"
echo "  tail -f $LOG_DIR/louis-command-runner.log"
echo ""
echo "Detener:"
echo "  launchctl unload $PLIST"
