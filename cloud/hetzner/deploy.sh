#!/usr/bin/env bash
# deploy.sh — Orquestador idempotente. Se corre como root en el VPS.
#
# Uso:
#   ./deploy.sh               # full deploy (bootstrap + services)
#   ./deploy.sh --skip-bootstrap   # solo redeploy de servicios
#   ./deploy.sh --rollback    # detiene servicios sin borrar datos
#
# Se puede correr varias veces. Cada paso verifica si ya está hecho.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Rutas válidas para el repo (legacy /opt/louis o ubicación actual /opt/nexo-louis/cloud/hetzner).
# El runtime usa @@OPENCLAW_HOME@@ → /opt/openclaw, no depende de dónde vive el repo.
_VALID_DIRS=("/opt/louis" "/opt/nexo-louis/cloud/hetzner")
_ok_dir=false
for _d in "${_VALID_DIRS[@]}"; do [[ "$SCRIPT_DIR" == "$_d" ]] && _ok_dir=true; done
if ! $_ok_dir; then
  echo "WARN: directorio inesperado ($SCRIPT_DIR) — continuando de todas formas."
fi

# ── Helpers ────────────────────────────────────────────────────
log()   { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail()  { printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || fail "Corre este script como root (estás como $(whoami))"
}

require_env() {
  [[ -f .env ]] || fail "Falta .env. Cópialo desde .env.example y rellena los secretos."
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  for var in LOUIS_DOMAIN AGENTS_DOMAIN ACME_EMAIL SYSTEM_USER ANTHROPIC_API_KEY; do
    [[ -n "${!var:-}" ]] || fail ".env: variable $var está vacía"
  done
}

# ── Flags ──────────────────────────────────────────────────────
SKIP_BOOTSTRAP=false
ROLLBACK=false
for arg in "$@"; do
  case "$arg" in
    --skip-bootstrap) SKIP_BOOTSTRAP=true ;;
    --rollback)       ROLLBACK=true ;;
    *) fail "Flag desconocido: $arg" ;;
  esac
done

# ── Rollback ───────────────────────────────────────────────────
if $ROLLBACK; then
  require_root
  log "Rollback: deteniendo servicios"
  systemctl stop openclaw 2>/dev/null || true
  docker compose down 2>/dev/null || true
  ok "Servicios detenidos. Datos en /opt/openclaw y volúmenes Docker INTACTOS."
  exit 0
fi

# ── Pre-flight ─────────────────────────────────────────────────
require_root
require_env

OS_ID=$(. /etc/os-release; echo "$ID")
[[ "$OS_ID" == "ubuntu" ]] || warn "OS detectado: $OS_ID (se probó en ubuntu, puede funcionar)"

log "Empezando deploy de Donna en $(hostname) ($(hostname -I | awk '{print $1}'))"

# ── 1) Bootstrap ──────────────────────────────────────────────
if ! $SKIP_BOOTSTRAP; then
  log "[1/8] Bootstrap del sistema (Ollama es el paso más largo: pull de ~13 GB)"
  bash bootstrap/01-harden.sh
  bash bootstrap/02-user.sh
  bash bootstrap/03-docker.sh
  bash bootstrap/04-node.sh
  bash bootstrap/05-python-audio.sh
  bash bootstrap/06-ollama.sh
  ok "Bootstrap completo"
else
  warn "Saltando bootstrap (--skip-bootstrap)"
fi

# ── 2) OpenClaw nativo ────────────────────────────────────────
log "[2/6] Instalando OpenClaw nativo + systemd"
bash openclaw/install.sh
ok "OpenClaw arriba en localhost:${OPENCLAW_PORT:-3000}"

# ── 3) kawiil-agents: clonar / actualizar repo ────────────────
log "[3/6] Preparando código de kawiil-agents"
KAWIIL_REPO_URL="${KAWIIL_REPO_URL:-https://github.com/Lbassoco95/kawiil-agents.git}"
KAWIIL_BRANCH="${KAWIIL_BRANCH:-main}"
mkdir -p /opt/kawiil
KAWIIL_REPO_OK=false
if [[ -d /opt/kawiil/repo/.git ]]; then
  log "Pull de kawiil-agents (existente)"
  git -C /opt/kawiil/repo fetch --quiet origin
  git -C /opt/kawiil/repo reset --hard "origin/${KAWIIL_BRANCH}"
  KAWIIL_REPO_OK=true
else
  log "Clonando kawiil-agents desde $KAWIIL_REPO_URL"
  if git clone --depth 1 -b "$KAWIIL_BRANCH" "$KAWIIL_REPO_URL" /opt/kawiil/repo; then
    KAWIIL_REPO_OK=true
  else
    warn "Repo no accesible. Caddy + OpenClaw siguen; kawiil-agents quedará disabled."
    warn "Configura KAWIIL_REPO_URL (o agrega SSH deploy key) en .env y vuelve a correr."
  fi
fi
if $KAWIIL_REPO_OK; then
  # El Dockerfile vive en /opt/louis pero docker exige que esté dentro del build context.
  # Copiamos al repo con nombre distintivo para no chocar si el repo ya trae un Dockerfile.
  cp "$SCRIPT_DIR/kawiil-agents/Dockerfile" /opt/kawiil/repo/Dockerfile.louis
  chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/kawiil
fi

# ── 4) docker compose: Caddy + kawiil-agents ──────────────────
log "[4/6] Levantando Caddy + kawiil-agents con docker compose"
mkdir -p /opt/louis-data/caddy/{data,config}
# Caddy escribe logs en /data/logs/ dentro del container (=/opt/louis-data/caddy/data/logs en host).
# Caddy NO crea el dir padre; lo creamos a mano.
mkdir -p /opt/louis-data/caddy/data/logs
if $KAWIIL_REPO_OK; then
  docker compose --env-file .env up -d --build
else
  warn "Levantando sólo caddy (kawiil-agents sin repo)"
  docker compose --env-file .env up -d --build caddy
fi
ok "Contenedores levantados"

# ── 5) Sync target (recibe del Mac) ───────────────────────────
log "[5/6] Preparando carpeta de sincronización Mac → Hetzner"
bash sync/hetzner-prepare.sh
ok "Sync target listo en /opt/openclaw-sync"

# ── 6) Cron para aplicar sync ─────────────────────────────────
log "[6/7] Programando cron para aplicar sync cada 5 minutos"
CRON_FILE=/etc/cron.d/louis-sync
cat > "$CRON_FILE" <<EOF
# Aplica delta de /opt/openclaw-sync a /opt/openclaw/spaces cada 5 minutos
*/5 * * * * $SYSTEM_USER bash $SCRIPT_DIR/sync/hetzner-apply.sh >> /var/log/louis-sync.log 2>&1
EOF
chmod 644 "$CRON_FILE"
touch /var/log/louis-sync.log
chown "$SYSTEM_USER":"$SYSTEM_USER" /var/log/louis-sync.log
ok "Cron registrado: $CRON_FILE"

# ── 6b) Copiar scripts de servicios al runtime ─────────────────
# Los .py de cloud/hetzner/services/ (versión repo) van a /opt/openclaw/scripts/
# para que los systemd units los encuentren. Esto sobreescribe versiones viejas
# del seed (Mac) — el repo es source-of-truth para la lógica del bridge.
log "[6b] Copiando servicios (core + bridges + scheduler + gateway + self-update + browser) al runtime"
mkdir -p /opt/openclaw/scripts /opt/openclaw/scripts/m365 /opt/openclaw/logs
# TODOS los .py que el runtime importa o ejecuta. Si falta alguno, las tools que
# dependen de él fallan en silencio (self_update → ImportError; browser_* → error).
for svc in donna_core.py telegram-bridge.py slack-bridge.py scheduler.py \
           openclaw_gateway.py self_update.py browser_runner.py cerebro_kawiil_mcp.py; do
  if [[ -f "services/${svc}" ]]; then
    install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" "services/${svc}" "/opt/openclaw/scripts/${svc}"
  fi
done
if [[ -f "services/m365.py" ]]; then
  install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" "services/m365.py" "/opt/openclaw/scripts/m365/m365.py"
fi
# Seeds que el runtime puede correr/referenciar (idempotentes)
for sc in seed-kawiil-agents.sh import-legal-agents.sh seed-morning-briefing.sh; do
  if [[ -f "scripts/${sc}" ]]; then
    install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" "scripts/${sc}" "/opt/openclaw/scripts/${sc}"
  fi
done
chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/scripts /opt/openclaw/logs
ok "Scripts en /opt/openclaw/scripts/"

# ── 7) Bridges systemd units (Telegram + Slack) ──────────────
log "[7/8] Instalando units (telegram + slack + scheduler + gateway)"
ENV_OUT="/opt/openclaw/openclaw.env"
for unit in telegram-bridge slack-bridge scheduler openclaw-gateway cerebro-kawiil; do
  if [[ -f "services/${unit}.service" ]]; then
    sed \
      -e "s|@@SYSTEM_USER@@|${SYSTEM_USER}|g" \
      -e "s|@@OPENCLAW_HOME@@|/opt/openclaw|g" \
      -e "s|@@ENV_FILE@@|${ENV_OUT}|g" \
      "services/${unit}.service" > "/etc/systemd/system/${unit}.service"
  fi
done

# Cerebro Kawiil: almacén compartido de entregables (Cowork ↔ Donna)
if [[ ! -d /opt/openclaw/entregables ]]; then
  mkdir -p /opt/openclaw/entregables/_briefs
  [[ -f "entregables/README.md" ]] && \
    install -m 0644 -o "$SYSTEM_USER" -g "$SYSTEM_USER" "entregables/README.md" /opt/openclaw/entregables/README.md
  chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/entregables
fi

# Kit de marca (logos Kawiil/Yoltik) para brandear documentos HTML
if [[ -d "assets/brand" ]]; then
  mkdir -p /opt/openclaw/assets
  cp -r assets/brand /opt/openclaw/assets/
  chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/assets
  ok "Kit de marca en /opt/openclaw/assets/brand"
fi

systemctl daemon-reload

# Telegram: arranca si el script existe y hay credencial
if [[ -f /opt/openclaw/scripts/telegram-bridge.py ]]; then
  systemctl enable --now telegram-bridge
  ok "telegram-bridge activo"
else
  warn "telegram-bridge unit registrado pero NO arrancado (falta el seed)"
  warn "Después del seed corre: sudo systemctl enable --now telegram-bridge"
fi

# Slack: arranca si el script existe y hay slack.env con tokens
if [[ -f /opt/openclaw/scripts/slack-bridge.py ]]; then
  if [[ -f /opt/openclaw/credentials/slack.env ]] && grep -qE 'SLACK_BOT_TOKEN="?xoxb' /opt/openclaw/credentials/slack.env 2>/dev/null; then
    systemctl enable --now slack-bridge
    ok "slack-bridge activo"
  else
    warn "slack-bridge.py presente pero falta /opt/openclaw/credentials/slack.env con SLACK_BOT_TOKEN/SLACK_APP_TOKEN"
    warn "Cuando esté el archivo: sudo systemctl enable --now slack-bridge"
  fi
else
  warn "slack-bridge.py aún no copiado al seed — sin arrancar"
fi

# Gateway HTTP de Donna — la cara de louis.kawiil.mx (Caddy → 127.0.0.1:3000).
# Reemplaza al binario oficial de OpenClaw (openclaw.ai responde 403).
if [[ -f /opt/openclaw/scripts/openclaw_gateway.py ]]; then
  systemctl enable --now openclaw-gateway
  if systemctl is-active --quiet openclaw-gateway; then
    ok "openclaw-gateway activo (louis.kawiil.mx → :${OPENCLAW_PORT:-3000})"
  else
    warn "openclaw-gateway no levantó — journalctl -u openclaw-gateway -n 50"
  fi
  # Nota de seguridad (diferida por decisión de Polo): el gateway no exige token
  # por default. Mientras no haya OPENCLAW_GATEWAY_TOKEN, protégelo con Cloudflare Access.
  grep -q '^OPENCLAW_GATEWAY_TOKEN=' "$ENV_OUT" 2>/dev/null \
    || warn "openclaw-gateway SIN token — protégelo con Cloudflare Access antes de exponer DNS"
else
  warn "openclaw_gateway.py no copiado — louis.kawiil.mx devolverá 502"
fi

# Scheduler — motor de tareas repetitivas (recordatorios recurrentes + briefing matutino).
if [[ -f /opt/openclaw/scripts/scheduler.py ]]; then
  systemctl enable --now scheduler
  if systemctl is-active --quiet scheduler; then
    ok "scheduler activo (tareas repetitivas + briefing)"
  else
    warn "scheduler no levantó — journalctl -u scheduler -n 50"
  fi
else
  warn "scheduler.py no copiado — sin recordatorios ni briefing automático"
fi

# Harvester legal SJF — actualiza las tesis del Semanario Judicial (timer diario).
# Causa raíz del estancamiento (jun-2026): no existía timer y el scraper no usaba
# el parámetro ?isSemanal=true. Aquí instalamos el harvester corregido + su timer.
log "[7b] Instalando harvester SJF + timer diario"
mkdir -p /opt/openclaw/legal/sjf
for f in sjf_harvest.py sjf_weekly_summary.py sjf_biblioteca.py sjf_backfill.py; do
  if [[ -f "legal-scrapers/${f}" ]]; then
    install -m 0755 -o "$SYSTEM_USER" -g "$SYSTEM_USER" "legal-scrapers/${f}" "/opt/openclaw/legal/sjf/${f}"
  fi
done
# update diario + resumen semanal (lunes) + backfill histórico (cada 2h)
for unit in sjf-update sjf-weekly sjf-backfill; do
  if [[ -f "services/${unit}.service" && -f "services/${unit}.timer" ]]; then
    sed -e "s|@@SYSTEM_USER@@|${SYSTEM_USER}|g" -e "s|@@OPENCLAW_HOME@@|/opt/openclaw|g" \
        "services/${unit}.service" > "/etc/systemd/system/${unit}.service"
    sed -e "s|@@SYSTEM_USER@@|${SYSTEM_USER}|g" -e "s|@@OPENCLAW_HOME@@|/opt/openclaw|g" \
        "services/${unit}.timer" > "/etc/systemd/system/${unit}.timer"
  fi
done
systemctl daemon-reload
for t in sjf-update.timer sjf-weekly.timer sjf-backfill.timer; do
  if [[ -f "/etc/systemd/system/${t}" ]]; then
    systemctl enable --now "$t"
    systemctl is-active --quiet "$t" && ok "${t} activo" || warn "${t} no levantó — systemctl status ${t}"
  fi
done
ok "SJF: harvester diario (13:30) + backfill histórico (c/2h) + resumen semanal (lun 8:00)"

# ── 8) Seeds: agentes + briefing matutino (idempotentes) ──────
log "[8/8] Sembrando agentes y briefing matutino"
export SYSTEM_USER HOME_OC=/opt/openclaw
# 14 agentes kawiil-* con contexto de negocio
if [[ -f scripts/seed-kawiil-agents.sh ]]; then
  bash scripts/seed-kawiil-agents.sh && ok "Agentes kawiil-* sembrados" \
    || warn "seed-kawiil-agents falló (continúo)"
fi
# Agentes legales (clona anthropics/claude-for-legal — requiere red a github)
if [[ -f scripts/import-legal-agents.sh ]]; then
  bash scripts/import-legal-agents.sh && ok "Agentes legales importados" \
    || warn "import-legal-agents falló (¿sin acceso a github? continúo)"
fi
# Control de alimentación — semilla idempotente de ALIMENTACION.md (no pisa el historial)
if [[ -f "spaces/general/ALIMENTACION.md" ]]; then
  mkdir -p /opt/openclaw/spaces/general
  if [[ ! -f /opt/openclaw/spaces/general/ALIMENTACION.md ]]; then
    install -m 0644 -o "$SYSTEM_USER" -g "$SYSTEM_USER" \
      "spaces/general/ALIMENTACION.md" /opt/openclaw/spaces/general/ALIMENTACION.md
    ok "ALIMENTACION.md sembrado (control de alimentación listo)"
  else
    log "ALIMENTACION.md ya existe — conservo el historial"
  fi
fi
# Briefing matutino 7:00 CDMX recurrente (lo consume el scheduler)
if [[ -f scripts/seed-morning-briefing.sh ]]; then
  bash scripts/seed-morning-briefing.sh && ok "Briefing matutino agendado (7:00 CDMX)" \
    || warn "seed-morning-briefing falló (continúo)"
fi
# Los seeds corren como root; devolvemos la propiedad a $SYSTEM_USER para que el
# scheduler (que corre como $SYSTEM_USER) pueda reescribir la cola al disparar.
chown -R "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/reminders /opt/openclaw/spaces 2>/dev/null || true
ok "Seeds aplicados"

# ── Done ──────────────────────────────────────────────────────
echo ""
ok "Deploy completo."
echo ""
echo "Próximos pasos:"
echo "  1. Apunta DNS de $LOUIS_DOMAIN y $AGENTS_DOMAIN a $(hostname -I | awk '{print $1}')"
echo "  2. Espera ~60s a que Caddy obtenga el certificado TLS"
echo "  3. Corre: ./verify.sh"
echo "  4. Desde tu Mac: cd .../yoltik-ai-setup/cloud/hetzner/sync && ./mac-install.sh $LOUIS_DOMAIN $SYSTEM_USER"
