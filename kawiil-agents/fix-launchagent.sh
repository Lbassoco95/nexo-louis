#!/usr/bin/env bash
# fix-launchagent.sh — Corrige el LaunchAgent de kawiil-agents para que cargue .env

set -euo pipefail

REPO="$HOME/Documents/Claude/Projects/kawiil-stack/kawiil-agents"
SERVER="$REPO/server"
PLIST="$HOME/Library/LaunchAgents/ai.kawiil.agents.plist"
WRAPPER="$SERVER/start.sh"
PYTHON_BIN="$REPO/.venv/bin/python3"

echo "==> 1. Creando wrapper que source .env antes de uvicorn"
cat > "$WRAPPER" <<'BASHEOF'
#!/usr/bin/env bash
# Wrapper que carga .env y luego arranca uvicorn.
set -a
# shellcheck disable=SC1091
source "$(dirname "$0")/.env"
set +a

# Cambiar a directorio del server (para que `app:app` resuelva)
cd "$(dirname "$0")"

exec "$REPO_PYTHON" -m uvicorn app:app --host 127.0.0.1 --port 8000
BASHEOF

# Sustituir REPO_PYTHON con el binario real
sed -i '' "s|\$REPO_PYTHON|$PYTHON_BIN|g" "$WRAPPER"
chmod +x "$WRAPPER"
echo "    $WRAPPER"

echo ""
echo "==> 2. Actualizando LaunchAgent plist para usar el wrapper"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.kawiil.agents</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$WRAPPER</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SERVER</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$HOME/.openclaw/logs/kawiil-agents.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.openclaw/logs/kawiil-agents.stderr.log</string>
</dict>
</plist>
EOF
echo "    $PLIST"

echo ""
echo "==> 3. Recargando LaunchAgent"
launchctl bootout "gui/$(id -u)/ai.kawiil.agents" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 5

echo ""
echo "==> 4. Verificación"
if lsof -iTCP:8000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    Server escuchando en localhost:8000"
  echo ""
  echo "    /api/agents:"
  curl -sS http://localhost:8000/api/agents | python3 -m json.tool 2>&1 | head -50
else
  echo "    WARN: server no escucha aún. Espera 5s más y revisa:"
  echo "    lsof -iTCP:8000 -sTCP:LISTEN"
  echo "    tail -30 $HOME/.openclaw/logs/kawiil-agents.stderr.log"
fi
