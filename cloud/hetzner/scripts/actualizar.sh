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

# ── 3) Deploy (instala scripts + units, hace daemon-reload) ─────────────────
log "Corriendo deploy.sh --skip-bootstrap"
( cd "$PAQUETE" && sudo ./deploy.sh --skip-bootstrap )

# ── 4) Reiniciar de verdad ──────────────────────────────────────────────────
# deploy.sh usa `enable --now`, que NO reinicia lo que ya está corriendo.
log "Reiniciando servicios (para que cargue el código nuevo)"
sudo systemctl daemon-reload
for s in "${SERVICIOS[@]}"; do
  if systemctl list-unit-files | grep -q "^${s}.service"; then
    sudo systemctl restart "$s" && ok "$s reiniciado" || printf "  ! %s no reinició\n" "$s"
  fi
done

# ── 5) Verificar ────────────────────────────────────────────────────────────
log "Estado"
for s in "${SERVICIOS[@]}"; do
  printf "  %-20s %s\n" "$s" "$(systemctl is-active "$s" 2>/dev/null || echo '?')"
done

if [[ -f "$PAQUETE/scripts/test-comprension.py" ]]; then
  log "Pruebas de comprensión"
  python3 "$PAQUETE/scripts/test-comprension.py" || fail "Las pruebas fallaron — revisa antes de dar por bueno el deploy."
fi

echo
ok "Listo. Mándale un mensaje a Donna por Telegram para confirmar que responde."
echo "   Si algo salió mal:  sudo journalctl -u telegram-bridge -n 50 --no-pager"
