#!/usr/bin/env bash
# install-kawiil-agents-local.sh
# Levanta el server FastAPI de kawiil-agents en localhost:8000 con .env real.
# Crea venv, instala requirements, arranca con uvicorn.

set -euo pipefail

REPO="$HOME/Documents/Claude/Projects/kawiil-stack/kawiil-agents"
SERVER="$REPO/server"
CREDS_DIR="$HOME/.openclaw/credentials"

if [[ ! -d "$REPO" ]]; then
  echo "ERROR: no encontré $REPO. ¿Clonaste el repo?" >&2
  exit 1
fi

# === Leer Anthropic key de openclaw .env ===
ANTHROPIC_KEY=""
if [[ -f "$HOME/.openclaw/.env" ]]; then
  ANTHROPIC_KEY="$(grep -E '^ANTHROPIC_API_KEY' "$HOME/.openclaw/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')"
fi

if [[ -z "$ANTHROPIC_KEY" ]]; then
  echo "ERROR: no encontré ANTHROPIC_API_KEY en ~/.openclaw/.env" >&2
  echo "       Pégala manualmente cuando edites $SERVER/.env" >&2
  ANTHROPIC_KEY="sk-ant-PEGAR-AQUI"
fi

# === Generar KAWIIL_DISPATCH_TOKEN nuevo si no existe ===
if [[ -f "$CREDS_DIR/kawiil-agents.env" ]]; then
  # shellcheck disable=SC1090
  source "$CREDS_DIR/kawiil-agents.env"
fi
if [[ -z "${KAWIIL_DISPATCH_TOKEN:-}" ]]; then
  KAWIIL_DISPATCH_TOKEN="$(openssl rand -hex 32)"
fi

# === Supabase service_role key: reusar la existente o pedirla (NUNCA hardcodear) ===
# La key vive solo en local con perms 600 y NO se commitea. Si rotaste la key en
# Supabase, borra ~/.openclaw/credentials/kawiil-agents.env y vuelve a correr esto.
# (El source de arriba ya la trae como $SUPABASE_SERVICE_KEY si el creds file existe.)
if [[ -z "${SUPABASE_SERVICE_KEY:-}" ]] && [[ -f "$HOME/.openclaw/.env" ]]; then
  SUPABASE_SERVICE_KEY="$(grep -E '^SUPABASE_SERVICE_KEY' "$HOME/.openclaw/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')"
fi
if [[ -z "${SUPABASE_SERVICE_KEY:-}" ]]; then
  echo "Necesito la service_role key de Supabase (proyecto qppfampapbxdgednkofc)."
  echo "En Supabase Studio → Project Settings → API → service_role secret."
  read -rsp "Supabase service_role key (sb_secret_... o eyJ...): " SUPABASE_SERVICE_KEY
  echo ""
fi
if [[ -z "$SUPABASE_SERVICE_KEY" ]]; then
  echo "ERROR: sin SUPABASE_SERVICE_KEY no puedo configurar kawiil-agents." >&2
  exit 1
fi

echo "==> 1. Guardando credenciales en $CREDS_DIR/kawiil-agents.env"
mkdir -p "$CREDS_DIR"
cat > "$CREDS_DIR/kawiil-agents.env" <<EOF
# Kawiil HQ Agents server — credenciales
# Service role key de Supabase qppfampapbxdgednkofc (servidor local, NO compartir)
SUPABASE_URL="https://qppfampapbxdgednkofc.supabase.co"
SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY"
ANTHROPIC_API_KEY="$ANTHROPIC_KEY"
AGENT_ORG_ID="00000000-0000-0000-0000-000000000001"
DATA_ORG_ID="a0000000-0000-0000-0000-000000000001"
KAWIIL_DISPATCH_TOKEN="$KAWIIL_DISPATCH_TOKEN"
KAWIIL_AGENTS_URL="http://localhost:8000"
EOF
chmod 600 "$CREDS_DIR/kawiil-agents.env"
echo "    OK (perms 600)"

echo ""
echo "==> 2. Creando .env del server"
cat > "$SERVER/.env" <<EOF
SUPABASE_URL=https://qppfampapbxdgednkofc.supabase.co
SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
AGENT_ORG_ID=00000000-0000-0000-0000-000000000001
DATA_ORG_ID=a0000000-0000-0000-0000-000000000001
KAWIIL_DISPATCH_TOKEN=$KAWIIL_DISPATCH_TOKEN
EOF
chmod 600 "$SERVER/.env"
echo "    $SERVER/.env"

echo ""
echo "==> 3. Creando venv Python en $REPO/.venv"
if [[ ! -d "$REPO/.venv" ]]; then
  python3 -m venv "$REPO/.venv"
fi
# shellcheck disable=SC1091
source "$REPO/.venv/bin/activate"
echo "    Python: $(which python3)"

echo ""
echo "==> 4. Instalando dependencias"
pip install --upgrade pip --quiet
pip install -r "$SERVER/requirements.txt" --quiet
# Add uvicorn si no está
pip install uvicorn[standard] python-dotenv --quiet
echo "    OK"

echo ""
echo "==> 5. Test rápido de import"
cd "$SERVER"
python3 -c "import app; print('OK — app importa correctamente')" 2>&1 | head -10

echo ""
echo "==> 6. Configurando LaunchAgent para auto-arranque"
PLIST="$HOME/Library/LaunchAgents/ai.kawiil.agents.plist"
PYTHON_BIN="$REPO/.venv/bin/python3"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.kawiil.agents</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>app:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8000</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SERVER</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$SERVER</string>
        <key>PATH</key>
        <string>$REPO/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
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
echo "==> 7. Cargando LaunchAgent (server arranca)"
launchctl bootout "gui/$(id -u)/ai.kawiil.agents" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 5

echo ""
echo "==> 8. Verificación"
if lsof -iTCP:8000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    OK, server escuchando en localhost:8000"
  echo ""
  echo "    Endpoints:"
  curl -sS http://localhost:8000/docs -o /dev/null -w "    /docs:          HTTP %{http_code}\n"
  curl -sS http://localhost:8000/api/agents -o /dev/null -w "    /api/agents:    HTTP %{http_code}\n"
else
  echo "    WARN: server no escucha. Logs:"
  tail -20 "$HOME/.openclaw/logs/kawiil-agents.stderr.log" 2>/dev/null
fi

echo ""
echo "============================================================"
echo "kawiil-agents corriendo en localhost:8000"
echo ""
echo "  • Dashboard:  http://localhost:8000/docs (Swagger)"
echo "  • Lista agentes:  curl http://localhost:8000/api/agents"
echo "  • Dispatch:  POST /api/tasks/dispatch con header X-Kawiil-Dispatch-Token"
echo ""
echo "  Token dispatch (guárdalo, lo necesitará Louis):"
echo "    $KAWIIL_DISPATCH_TOKEN"
echo ""
echo "  Logs en vivo:"
echo "    tail -f $HOME/.openclaw/logs/kawiil-agents.stderr.log"
echo ""
echo "  Reiniciar tras cambios:"
echo "    launchctl kickstart -k gui/$(id -u)/ai.kawiil.agents"
echo "============================================================"
