#!/usr/bin/env bash
# actualizar.sh — trae los cambios de GitHub y los pone VIVOS en Hetzner.
#
# Por qué existe: /opt/louis NO es un clon de git (es un destino de rsync), así que
# `cd /opt/louis && git pull` falla con "not a git repository". Y deploy.sh usa
# `systemctl enable --now`, que no reinicia un servicio que ya está corriendo: sin
# el restart explícito el código nuevo no carga.
#
# Uso (como polo, que tiene sudo sin contraseña):
#   /opt/louis/scripts/actualizar.sh                       # rama main
#   /opt/louis/scripts/actualizar.sh <rama>                 # otra rama
#   FUENTE=/ruta/al/repo /opt/louis/scripts/actualizar.sh   # sin clonar (repo ya en disco)
#
# Es idempotente: puedes correrlo de nuevo sin miedo.

set -euo pipefail

# --solo-servicios: NO corre deploy.sh. Instala nada más los .py y las units, que
# es lo único que cambia al actualizar la lógica del bot. Útil (y más seguro) en un
# server que ya está en producción: deploy.sh exige LOUIS_DOMAIN/AGENTS_DOMAIN/
# ACME_EMAIL —que solo sirven para Caddy y el TLS— y además toca Caddy, docker,
# cron y los seeds. Nada de eso cambia por actualizar tres archivos de Python.
SOLO_SERVICIOS=false
ARGS=()
for a in "$@"; do
  case "$a" in
    --solo-servicios) SOLO_SERVICIOS=true ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

RAMA="${1:-main}"
# El repo es PRIVADO: por HTTPS pediría token. Se intenta SSH (deploy key del
# server) y solo si eso falla se prueba HTTPS.
REPO_SSH="${REPO_SSH:-git@github.com:Lbassoco95/nexo-louis.git}"
REPO_HTTPS="${REPO_HTTPS:-https://github.com/Lbassoco95/nexo-louis.git}"
FUENTE="${FUENTE:-/opt/louis-src}"
PAQUETE="/opt/louis"
SERVICIOS=(telegram-bridge slack-bridge scheduler openclaw-gateway)

log() { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
fail(){ printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

[[ $EUID -eq 0 ]] && fail "No lo corras como root: córrelo como polo (usa sudo internamente)."
sudo -n true 2>/dev/null || fail "sudo pide contraseña. Revisa /etc/sudoers.d/polo (debe decir NOPASSWD: ALL)."

# ── 1) Traer el código ──────────────────────────────────────────────────────
if [[ -d "$FUENTE/.git" ]]; then
  log "Actualizando $FUENTE (rama $RAMA)"
  sudo git -C "$FUENTE" fetch --prune origin
  sudo git -C "$FUENTE" checkout "$RAMA"
  sudo git -C "$FUENTE" reset --hard "origin/$RAMA"
elif [[ -d "$FUENTE" ]]; then
  log "Usando $FUENTE tal cual (no es repo git, no se toca)"
else
  log "Clonando en $FUENTE (rama $RAMA) — intento SSH primero"
  if ! sudo git clone --branch "$RAMA" "$REPO_SSH" "$FUENTE" 2>/dev/null; then
    log "SSH no funcionó; intentando HTTPS"
    sudo git clone --branch "$RAMA" "$REPO_HTTPS" "$FUENTE" || fail \
"No pude clonar (el repo es privado y este server no tiene credencial de GitHub).
 Dos salidas:
   a) Registra una deploy key del server en GitHub:
        sudo ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ''
        sudo cat /root/.ssh/id_ed25519.pub
      → pégala en github.com/Lbassoco95/nexo-louis → Settings → Deploy keys → Add
      → vuelve a correr este script.
   b) Empuja el código desde tu Mac (el flujo del README) y luego:
        # en la Mac:
        rsync -avz --exclude='.git' --exclude='.env' \\
          ~/ruta/al/repo/cloud/hetzner/ polo@204.168.131.21:/opt/louis/
        # en el server:
        cd /opt/louis && sudo ./deploy.sh --skip-bootstrap
        sudo systemctl daemon-reload
        sudo systemctl restart telegram-bridge slack-bridge scheduler openclaw-gateway"
  fi
fi
ok "Código en $FUENTE ($(sudo git -C "$FUENTE" rev-parse --short HEAD 2>/dev/null || echo 'sin git'))"

[[ -d "$FUENTE/cloud/hetzner" ]] || fail "No encuentro $FUENTE/cloud/hetzner"

# ── 2) Sincronizar el paquete a /opt/louis ──────────────────────────────────
# SIN --delete a propósito: /opt/louis/.env tiene los secretos y NO está en el
# repo (está en .gitignore). Con --delete se borraría y deploy.sh dejaría de correr.
log "Sincronizando a $PAQUETE (preservando .env)"
sudo rsync -a --exclude='.git' --exclude='.env' \
  "$FUENTE/cloud/hetzner/" "$PAQUETE/"
[[ -f "$PAQUETE/.env" ]] || fail "Se perdió $PAQUETE/.env — restáuralo antes de seguir (deploy.sh lo exige)."
ok "Paquete sincronizado, .env intacto"

# ── 3) Instalar al runtime ──────────────────────────────────────────────────
instalar_solo_servicios() {
  # Los valores de sustitución se sacan de la unit YA INSTALADA y funcionando, no
  # del .env: así no dependemos de variables que solo le importan a Caddy.
  local unit_viva="/etc/systemd/system/telegram-bridge.service"
  local su oc envf
  su="$(sudo sed -n 's/^User=//p' "$unit_viva" 2>/dev/null | head -1)"
  envf="$(sudo sed -n 's/^EnvironmentFile=//p' "$unit_viva" 2>/dev/null | head -1)"
  su="${su:-polo}"
  oc="/opt/openclaw"                      # deploy.sh lo tiene hardcodeado igual
  envf="${envf:-$oc/openclaw.env}"
  log "Instalando como usuario '$su' (env: $envf)"

  # Respaldo de lo que vamos a sobrescribir — para volver atrás en un comando.
  local bkp="/opt/openclaw/.respaldo-$(date +%Y%m%d%H%M%S)"
  sudo mkdir -p "$bkp"
  sudo cp -a "$oc/scripts" "$bkp/scripts" 2>/dev/null || true
  ok "Respaldo del runtime anterior en $bkp"

  sudo mkdir -p "$oc/scripts/m365" "$oc/logs"
  local n=0
  for svc in louis_core.py telegram-bridge.py slack-bridge.py scheduler.py \
             openclaw_gateway.py self_update.py browser_runner.py cerebro_kawiil_mcp.py; do
    if [[ -f "$PAQUETE/services/$svc" ]]; then
      sudo install -m 0755 -o "$su" -g "$su" "$PAQUETE/services/$svc" "$oc/scripts/$svc"
      n=$((n+1))
    fi
  done
  [[ -f "$PAQUETE/services/m365.py" ]] && \
    sudo install -m 0755 -o "$su" -g "$su" "$PAQUETE/services/m365.py" "$oc/scripts/m365/m365.py"
  for sc in seed-kawiil-agents.sh import-legal-agents.sh seed-morning-briefing.sh; do
    [[ -f "$PAQUETE/scripts/$sc" ]] && \
      sudo install -m 0755 -o "$su" -g "$su" "$PAQUETE/scripts/$sc" "$oc/scripts/$sc"
  done
  sudo chown -R "$su":"$su" "$oc/scripts" "$oc/logs"
  ok "$n scripts instalados en $oc/scripts/"

  # Units (.service y .timer) con los placeholders sustituidos, igual que deploy.sh
  local u=0
  for unit in telegram-bridge slack-bridge scheduler openclaw-gateway cerebro-kawiil \
              dof-daily dof-daily-tarde dof-harvest dof-contents dof-pdfs \
              legal-digest legal-estado sjf-update sjf-weekly; do
    for ext in service timer; do
      if [[ -f "$PAQUETE/services/${unit}.${ext}" ]]; then
        sudo cp -a "/etc/systemd/system/${unit}.${ext}" "$bkp/" 2>/dev/null || true
        sudo sed -e "s|@@SYSTEM_USER@@|${su}|g" \
                 -e "s|@@OPENCLAW_HOME@@|${oc}|g" \
                 -e "s|@@ENV_FILE@@|${envf}|g" \
                 "$PAQUETE/services/${unit}.${ext}" \
          | sudo tee "/etc/systemd/system/${unit}.${ext}" >/dev/null
        u=$((u+1))
      fi
    done
  done
  ok "$u units actualizadas en /etc/systemd/system/"
}

if $SOLO_SERVICIOS; then
  instalar_solo_servicios
else
  log "Corriendo deploy.sh --skip-bootstrap"
  if ! ( cd "$PAQUETE" && sudo ./deploy.sh --skip-bootstrap ); then
    printf "\n"
    fail "deploy.sh falló (típicamente por una variable de .env que solo usa Caddy).
 Si el server YA está en producción y solo quieres actualizar la lógica del bot,
 corre esto — instala los .py y las units sin tocar Caddy/docker/cron/seeds:
   $0 $RAMA --solo-servicios"
  fi
fi

# ── 4) Reiniciar de verdad ──────────────────────────────────────────────────
# deploy.sh usa `enable --now`, que NO reinicia lo que ya está corriendo.
log "Reiniciando servicios (para que cargue el código nuevo)"
sudo systemctl daemon-reload
for s in "${SERVICIOS[@]}"; do
  if systemctl list-unit-files | grep -q "^${s}.service"; then
    sudo systemctl restart "$s" && ok "$s reiniciado" || printf "  ! %s no reinició\n" "$s"
  fi
done
# Los .timer cambiaron (zona horaria explícita y se quitó Persistent de los que
# notifican). Un daemon-reload no recalcula el próximo disparo: hay que reiniciarlos.
for t in dof-daily dof-daily-tarde legal-digest legal-estado sjf-update sjf-weekly \
         dof-harvest dof-contents dof-pdfs; do
  if [[ -f "/etc/systemd/system/${t}.timer" ]]; then
    sudo systemctl restart "${t}.timer" 2>/dev/null && ok "${t}.timer reprogramado" \
      || printf "  ! %s.timer NO reinició — sudo systemctl status %s.timer\n" "$t" "$t"
  else
    printf "  · %s.timer no está instalado (se omite)\n" "$t"
  fi
done

# ── 5) Verificar ────────────────────────────────────────────────────────────
log "Estado"
for s in "${SERVICIOS[@]}"; do
  # OJO: `is-active` sale con código != 0 para cualquier estado que no sea "active"
  # (activating, failed…). Con `|| echo '?'` se imprimía el estado Y un '?' aparte.
  est="$(systemctl is-active "$s" 2>/dev/null)" || true
  printf "  %-20s %s\n" "$s" "${est:-desconocido}"
  if [[ "$est" != "active" ]]; then
    printf "      ↳ %s\n" "$(systemctl is-failed "$s" 2>/dev/null || true)"
    printf "      ↳ revisa: sudo journalctl -u %s -n 30 --no-pager\n" "$s"
  fi
done

log "Próximos disparos (verifica la HORA: debe ser CDMX, no UTC)"
systemctl list-timers --all --no-pager 2>/dev/null | head -1 || true
systemctl list-timers --all --no-pager 2>/dev/null | grep -E "dof-|legal-|sjf-" || true
echo "  (hora del server: $(date '+%H:%M %Z')  ·  CDMX: $(TZ=America/Mexico_City date '+%H:%M'))"

if [[ -f "$PAQUETE/scripts/test-comprension.py" ]]; then
  log "Pruebas de comprensión"
  python3 "$PAQUETE/scripts/test-comprension.py" || fail "Las pruebas fallaron — revisa antes de dar por bueno el deploy."
fi

echo
ok "Listo. Mándale un mensaje a Donna por Telegram para confirmar que responde."
echo "   Si algo salió mal:  sudo journalctl -u telegram-bridge -n 50 --no-pager"
