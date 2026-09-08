#!/usr/bin/env bash
# configurar-slack.sh — carga las credenciales de Slack y levanta el bridge.
#
# Los tokens se piden con `read -rs`: NO se hacen eco en pantalla y NO quedan en
# ~/.bash_history (que es lo que pasaría al escribirlos en un `echo ... >` o en un
# `nano` es seguro pero manual). El archivo queda 0600 del usuario del servicio.
#
# Uso:  /opt/louis/scripts/configurar-slack.sh

set -euo pipefail

CREDS="/opt/openclaw/credentials/slack.env"
SERVICIO="slack-bridge"

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

[[ $EUID -eq 0 ]] && fail "Córrelo como polo, no como root (usa sudo internamente)."

# Usuario dueño del runtime, tomado de la unit que ya está instalada.
SU="$(sudo sed -n 's/^User=//p' /etc/systemd/system/${SERVICIO}.service 2>/dev/null | head -1)"
SU="${SU:-polo}"

# ── 1) Dependencias de Python ───────────────────────────────────────────────
# Si faltan, el bridge muere en el import ANTES de leer los tokens y el síntoma
# se confunde con "faltan credenciales".
log "Verificando slack_bolt / slack_sdk"
if ! python3 -c "import slack_bolt, slack_sdk" 2>/dev/null; then
  warn "slack_bolt no está instalado. Instalando…"
  sudo pip install --break-system-packages slack-bolt slack-sdk \
    || fail "No pude instalar slack-bolt. Instálalo y vuelve a correr esto."
fi
ok "slack_bolt disponible"

# ── 2) Tokens ───────────────────────────────────────────────────────────────
cat <<'AYUDA'

De dónde salen (api.slack.com/apps → tu app):
  • SLACK_BOT_TOKEN  (xoxb-…)  OAuth & Permissions → Bot User OAuth Token
  • SLACK_APP_TOKEN  (xapp-…)  Basic Information → App-Level Tokens
                               → Generate Token con el scope connections:write
                               (y Settings → Socket Mode debe estar ENCENDIDO)

No se van a ver en pantalla mientras los escribes. Pega y dale Enter.

AYUDA

read -rsp "SLACK_BOT_TOKEN (xoxb-…): " BOT; echo
read -rsp "SLACK_APP_TOKEN (xapp-…): " APP; echo
[[ -n "$BOT" && -n "$APP" ]] || fail "Alguno vino vacío."
[[ "$BOT" == xoxb-* ]] || warn "El bot token no empieza con 'xoxb-' — revisa que no sea el de usuario (xoxp-)."
[[ "$APP" == xapp-* ]] || warn "El app token no empieza con 'xapp-' — ese es el App-Level Token, no el bot."

read -rp "Tu user ID de Slack para los recordatorios por DM (U…, opcional): " DM || true

# ── 3) Escribir el archivo ──────────────────────────────────────────────────
if sudo test -f "$CREDS"; then
  BKP="${CREDS}.bak.$(date +%Y%m%d%H%M%S)"
  sudo cp -a "$CREDS" "$BKP"
  ok "Respaldo del anterior en $BKP"
fi

sudo install -d -m 700 -o "$SU" -g "$SU" "$(dirname "$CREDS")"
# El heredoc entra por stdin: los tokens no aparecen en la línea de comando ni en ps.
printf 'SLACK_BOT_TOKEN=%s\nSLACK_APP_TOKEN=%s\n%s' "$BOT" "$APP" \
  "$([[ -n "${DM:-}" ]] && printf 'SLACK_DEFAULT_DM_USER=%s\n' "$DM")" \
  | sudo tee "$CREDS" >/dev/null
sudo chmod 600 "$CREDS"
sudo chown "$SU":"$SU" "$CREDS"
unset BOT APP
ok "Escrito $CREDS (0600, dueño $SU)"

# ── 4) Levantar ─────────────────────────────────────────────────────────────
# reset-failed es necesario: con RestartPreventExitStatus=78 el servicio quedó en
# 'failed' a propósito y systemd no lo reintenta hasta que se limpia el estado.
log "Levantando $SERVICIO"
sudo systemctl reset-failed "$SERVICIO" 2>/dev/null || true
sudo systemctl enable "$SERVICIO" >/dev/null 2>&1 || true
sudo systemctl restart "$SERVICIO"
sleep 6

EST="$(systemctl is-active "$SERVICIO" 2>/dev/null)" || true
printf "\n  %s: %s\n" "$SERVICIO" "${EST:-desconocido}"
if [[ "$EST" == "active" ]]; then
  N="$(systemctl show -p NRestarts --value "$SERVICIO" 2>/dev/null || echo 0)"
  ok "Arriba (reinicios desde el reset: $N). Mándale un DM al bot en Slack para probar."
else
  warn "No levantó. Las últimas líneas del log:"
  sudo tail -n 25 /opt/openclaw/logs/slack-bridge.log 2>/dev/null \
    || sudo journalctl -u "$SERVICIO" -n 25 --no-pager
fi

cat <<'NOTA'

OJO — un solo Socket Mode por App Token: si el OpenClaw de tu Mac sigue corriendo
con el MISMO xapp-, Slack desconecta al más viejo y los dos se pelean. Si es el
caso, apaga el de la Mac:
  launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist
NOTA
