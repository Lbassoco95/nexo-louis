#!/usr/bin/env bash
# mac-update-bridges.sh — Hot-update de los bridges en Hetzner SIN re-bootstrap.
#
# Sube:
#   - services/louis_core.py         → /opt/openclaw/scripts/
#   - services/telegram-bridge.py    → /opt/openclaw/scripts/
#   - services/slack-bridge.py       → /opt/openclaw/scripts/
#   - services/m365.py               → /opt/openclaw/scripts/m365/
#   - services/slack-bridge.service  → /etc/systemd/system/ (renderizando placeholders)
#   - services/telegram-bridge.service → /etc/systemd/system/ (idem)
#
# Instala slack_bolt + slack_sdk vía pip si faltan.
# Reinicia telegram-bridge y arranca slack-bridge si hay credenciales.
#
# Uso:
#   ./mac-update-bridges.sh                       # default 204.168.131.21
#   ./mac-update-bridges.sh louis.kawiil.mx       # con dominio o IP

set -euo pipefail

HOST="${1:-204.168.131.21}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
SERVICES="$PKG_DIR/services"
SCRIPTS_DIR="$PKG_DIR/scripts"

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SCP_OPTS="-o StrictHostKeyChecking=accept-new"

ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# Pre-flight
[[ -d "$SERVICES" ]] || fail "No encontré $SERVICES"
for f in louis_core.py telegram-bridge.py slack-bridge.py m365.py scheduler.py self_update.py openclaw_gateway.py browser_runner.py slack-bridge.service telegram-bridge.service scheduler.service openclaw-gateway.service; do
  [[ -f "$SERVICES/$f" ]] || fail "Falta $SERVICES/$f"
done

# Detectar usuario SSH
REMOTE_USER=""
for u in polo root; do
  if ssh $SSH_OPTS "${u}@${HOST}" 'echo ok' >/dev/null 2>&1; then
    REMOTE_USER="$u"
    break
  fi
done
[[ -n "$REMOTE_USER" ]] || fail "No puedo SSH a $HOST (probé root y polo)"
REMOTE="${REMOTE_USER}@${HOST}"
SUDO=""
[[ "$REMOTE_USER" == "polo" ]] && SUDO="sudo "
ok "SSH a $REMOTE OK"

# ── 1) Subir archivos a /tmp ──────────────────────────────────
log "[1/5] Subiendo .py + .service a /tmp en $HOST"
ssh $SSH_OPTS "$REMOTE" "rm -f /tmp/louis_core.py /tmp/telegram-bridge.py /tmp/slack-bridge.py /tmp/m365.py /tmp/scheduler.py /tmp/openclaw_gateway.py /tmp/browser_runner.py /tmp/telegram-bridge.service /tmp/slack-bridge.service /tmp/scheduler.service /tmp/openclaw-gateway.service /tmp/import-legal-agents.sh /tmp/slack.env 2>/dev/null || true"
scp $SCP_OPTS -q \
  "$SERVICES/louis_core.py" \
  "$SERVICES/telegram-bridge.py" \
  "$SERVICES/slack-bridge.py" \
  "$SERVICES/m365.py" \
  "$SERVICES/scheduler.py" \
  "$SERVICES/self_update.py" \
  "$SERVICES/openclaw_gateway.py" \
  "$SERVICES/browser_runner.py" \
  "$SERVICES/telegram-bridge.service" \
  "$SERVICES/slack-bridge.service" \
  "$SERVICES/scheduler.service" \
  "$SERVICES/openclaw-gateway.service" \
  "$REMOTE":/tmp/
# Sube todos los .sh de scripts/ (no solo import-legal-agents.sh)
if [[ -d "$SCRIPTS_DIR" ]]; then
  for s in "$SCRIPTS_DIR"/*.sh; do
    [[ -f "$s" ]] && scp $SCP_OPTS -q "$s" "$REMOTE":/tmp/"$(basename "$s")"
  done
fi

# Si tenemos slack.env local con tokens válidos, subirlo también
LOCAL_SLACK_ENV="$HOME/.openclaw/credentials/slack.env"
if [[ -f "$LOCAL_SLACK_ENV" ]] && grep -qE "SLACK_BOT_TOKEN=\"?xoxb" "$LOCAL_SLACK_ENV" 2>/dev/null; then
  log "    Detecté slack.env local con tokens — subiendo a /tmp/slack.env"
  scp $SCP_OPTS -q "$LOCAL_SLACK_ENV" "$REMOTE":/tmp/slack.env
fi

ok "Archivos en /tmp del VPS"

# ── 2) Instalar archivos en runtime ───────────────────────────
log "[2/5] Instalando archivos en /opt/openclaw/scripts/ y /etc/systemd/"
ssh $SSH_OPTS "$REMOTE" "${SUDO}bash -s" <<'REMOTE_INSTALL'
set -e
SYSTEM_USER="${SUDO_USER:-polo}"
[[ "$SYSTEM_USER" == "root" ]] && SYSTEM_USER="polo"

mkdir -p /opt/openclaw/scripts /opt/openclaw/scripts/m365 /opt/openclaw/logs /opt/openclaw/credentials /opt/openclaw/reminders /opt/openclaw/spaces/general/agents /opt/openclaw/legal/sjf/logs /opt/openclaw/legal/dof/logs /opt/openclaw/state /opt/openclaw/projects /opt/openclaw/mac/Projects /opt/openclaw/code-backups/kawiil-os

# Pre-crear logs para evitar PermissionError la primera vez que el unit arranca
# (systemd con StandardOutput=append crea el archivo como root, después Python
# corre como polo y no puede escribir encima)
for logf in telegram-bridge.log slack-bridge.log scheduler.log openclaw-gateway.log; do
  touch /opt/openclaw/logs/$logf
  chown $SYSTEM_USER:$SYSTEM_USER /opt/openclaw/logs/$logf
done
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/louis_core.py       /opt/openclaw/scripts/louis_core.py
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/telegram-bridge.py  /opt/openclaw/scripts/telegram-bridge.py
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/slack-bridge.py     /opt/openclaw/scripts/slack-bridge.py
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/m365.py             /opt/openclaw/scripts/m365/m365.py
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/scheduler.py        /opt/openclaw/scripts/scheduler.py
install -m 0644 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/self_update.py      /opt/openclaw/scripts/self_update.py
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/openclaw_gateway.py /opt/openclaw/scripts/openclaw_gateway.py
install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/browser_runner.py   /opt/openclaw/scripts/browser_runner.py
mkdir -p /opt/openclaw/code-backups
chown "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/code-backups
# Instala todos los .sh que hayan llegado a /tmp (vienen de scripts/)
for s in /tmp/import-legal-agents.sh /tmp/import-kawiil-agents.sh /tmp/fix-audio-transcription.sh /tmp/setup-kawiil-central-creds.sh /tmp/update-kawiil-db-url.sh /tmp/install-playwright.sh /tmp/install-bitwarden.sh /tmp/install-1password.sh /tmp/backup-spaces-agents.sh /tmp/seed-kawiil-agents.sh; do
  [[ -f "$s" ]] && install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" "$s" "/opt/openclaw/scripts/$(basename "$s")"
done
# Limpia el script viejo si quedó del deploy previo
rm -f /opt/openclaw/scripts/clone-kawiil-central.sh 2>/dev/null || true

# Sudoers para que el user pueda reiniciar SOLO los 3 servicios de Louis sin password
# (necesario para reiniciar_mi_servicio del self-update)
cat > /etc/sudoers.d/louis-self-update <<EOF
# Permite a $SYSTEM_USER reiniciar servicios de Louis sin password (self-update)
$SYSTEM_USER ALL=(root) NOPASSWD: /bin/systemctl restart telegram-bridge
$SYSTEM_USER ALL=(root) NOPASSWD: /bin/systemctl restart slack-bridge
$SYSTEM_USER ALL=(root) NOPASSWD: /bin/systemctl restart scheduler
EOF
chmod 0440 /etc/sudoers.d/louis-self-update
# Validar sintaxis sudoers
visudo -c -f /etc/sudoers.d/louis-self-update >/dev/null && echo "  ✓ sudoers self-update OK" || {
  echo "  ✗ sudoers self-update INVÁLIDO — removiendo"
  rm -f /etc/sudoers.d/louis-self-update
}
chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/scripts /opt/openclaw/logs /opt/openclaw/reminders /opt/openclaw/spaces/general/agents /opt/openclaw/legal /opt/openclaw/state /opt/openclaw/projects /opt/openclaw/mac /opt/openclaw/code-backups

# Slack credentials si vinieron
if [[ -f /tmp/slack.env ]]; then
  install -m 0600 -o "$SYSTEM_USER" -g "$SYSTEM_USER" /tmp/slack.env /opt/openclaw/credentials/slack.env
  rm -f /tmp/slack.env
  echo "  ✓ slack.env sincronizado a /opt/openclaw/credentials/"
fi

# Renderizar y poner systemd units
for unit in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  if [[ -f "/tmp/${unit}.service" ]]; then
    sed \
      -e "s|@@SYSTEM_USER@@|${SYSTEM_USER}|g" \
      -e "s|@@OPENCLAW_HOME@@|/opt/openclaw|g" \
      -e "s|@@ENV_FILE@@|/opt/openclaw/openclaw.env|g" \
      "/tmp/${unit}.service" > "/etc/systemd/system/${unit}.service"
  fi
done
systemctl daemon-reload
echo "  ✓ Archivos instalados"
REMOTE_INSTALL
ok "Archivos instalados en runtime"

# ── 3) Instalar slack_bolt si falta ───────────────────────────
log "[3/5] Asegurando slack_bolt + slack_sdk (pip)"
ssh $SSH_OPTS "$REMOTE" "${SUDO}bash -c '
if python3 -c \"import slack_bolt\" 2>/dev/null; then
  echo \"  slack_bolt ya presente\"
else
  pip3 install --quiet --break-system-packages slack-bolt slack-sdk
  echo \"  slack_bolt instalado\"
fi
'"
ok "slack_bolt OK"

# ── 4) Reiniciar telegram-bridge y arrancar slack-bridge ──────
log "[4/5] Reiniciando telegram-bridge + arrancando slack-bridge (si hay creds)"
ssh $SSH_OPTS "$REMOTE" "${SUDO}bash -s" <<'REMOTE_RESTART'
set -e
systemctl restart telegram-bridge
sleep 2
echo "  telegram-bridge: $(systemctl is-active telegram-bridge)"

if [[ -f /opt/openclaw/credentials/slack.env ]] && grep -qE 'SLACK_BOT_TOKEN="?xoxb' /opt/openclaw/credentials/slack.env 2>/dev/null && grep -qE 'SLACK_APP_TOKEN="?xapp' /opt/openclaw/credentials/slack.env 2>/dev/null; then
  systemctl enable slack-bridge >/dev/null 2>&1 || true
  systemctl restart slack-bridge
  sleep 2
  echo "  slack-bridge:    $(systemctl is-active slack-bridge)"
else
  echo "  slack-bridge:    NO arrancado — falta /opt/openclaw/credentials/slack.env con SLACK_BOT_TOKEN (xoxb-) y SLACK_APP_TOKEN (xapp-)"
fi

# Scheduler — siempre arranca (no necesita creds extra, usa telegram.env)
systemctl enable scheduler >/dev/null 2>&1 || true
systemctl restart scheduler
sleep 2
echo "  scheduler:       $(systemctl is-active scheduler)"

# OpenClaw Gateway — siempre arranca (envuelve louis_core)
# Apaga el legacy openclaw.service si quedó dando lata
if systemctl list-unit-files openclaw.service >/dev/null 2>&1; then
  systemctl disable openclaw >/dev/null 2>&1 || true
  systemctl stop openclaw 2>/dev/null || true
fi
systemctl enable openclaw-gateway >/dev/null 2>&1 || true
systemctl restart openclaw-gateway
sleep 2
echo "  openclaw-gateway: $(systemctl is-active openclaw-gateway)"
# Health check inline
curl -fsS --max-time 3 http://127.0.0.1:3000/healthz >/dev/null 2>&1 \
  && echo "    /healthz OK" \
  || echo "    /healthz NO responde (revisa: journalctl -u openclaw-gateway -n 30)"
REMOTE_RESTART
ok "Servicios reiniciados"

# ── 5) Health check ───────────────────────────────────────────
log "[5/5] Health check"
ssh $SSH_OPTS "$REMOTE" "
echo '--- Servicios activos ---'
systemctl is-active telegram-bridge slack-bridge scheduler ollama openclaw-gateway 2>&1 | paste <(echo -e 'telegram-bridge\nslack-bridge\nscheduler\nollama\nopenclaw-gateway') -
echo ''
echo '--- OpenClaw gateway ---'
curl -fsS --max-time 5 http://127.0.0.1:3000/v1/status 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print('agents_count',d.get('agents_count')); print('fast',d.get('models',{}).get('ollama_fast')); print('routing',d.get('routing'))\" 2>/dev/null || echo '(gateway status no disponible)'
python3 -c \"
import sys
sys.path.insert(0,'/opt/openclaw/scripts')
import louis_core as c
assert c.should_deterministic_operational_response('hola')
print('routing smoke OK')
\" 2>/dev/null || echo '(louis_core smoke falló)'
echo ''
echo '--- Últimas 10 líneas del log de telegram-bridge ---'
${SUDO}tail -n 10 /opt/openclaw/logs/telegram-bridge.log 2>/dev/null || echo '(sin log todavía)'
echo ''
echo '--- Últimas 10 líneas del log de slack-bridge ---'
${SUDO}tail -n 10 /opt/openclaw/logs/slack-bridge.log 2>/dev/null || echo '(sin log todavía)'
echo ''
echo '--- Últimas 10 líneas del log de scheduler ---'
${SUDO}tail -n 10 /opt/openclaw/logs/scheduler.log 2>/dev/null || echo '(sin log todavía)'
"

echo ""
ok "Update completo."
cat <<EOF

Pruébalo:
  1. Telegram: manda foto con caption — debe analizarla con vision
  2. Slack DM: pega imagen o nota de voz — debe procesarla
  3. Recordatorio proactivo:
       'recuérdame en 2 minutos que esto funcionó'
     Espera 2 min — Louis te lo debe mandar solo.
  4. Telegram rápido: 'hola' o '¿qué tengo urgente?' → briefing <3s (determinístico)
  5. Telegram status: '/status' → servicios + agentes sin LLM
  6. Agentes: 'lista mis agentes' (/sonnet) o POST /v1/agents/{name}
  7. .env recomendado en VPS:
       OLLAMA_FAST_MODEL=llama3.1:8b
       OLLAMA_QUALITY_MODEL=gpt-oss:20b
       OLLAMA_CHAT_TIMEOUT=45

Si algo falla:
  ssh $REMOTE 'journalctl -u telegram-bridge -u slack-bridge -u scheduler -f'

EOF
