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
  # Manifiesto de hashes de lo que ESTE script instaló la última vez. Donna puede
  # editar su propio código en caliente (self_update.py → editar_mi_codigo), y esas
  # ediciones NO vuelven a git: si el archivo en disco no coincide con lo que dejamos
  # la vez pasada, fue editado en vivo y sobrescribirlo pierde el cambio. Se avisa
  # antes de pisarlo (el respaldo de arriba lo hace recuperable).
  local manifiesto="$oc/.deploy-manifest.json"
  local nuevo_manifiesto=""
  local n=0 drift=0 cambiados=0 faltantes=0
  # Misma lista que deploy.sh, MÁS donna_html.py: `donna_core` lo importa para el
  # HTML interactivo (el formato por defecto de Kawiil) y deploy.sh nunca lo copió,
  # así que en el runtime podía quedar una versión vieja o ninguna.
  for svc in donna_core.py donna_html.py telegram-bridge.py slack-bridge.py scheduler.py \
             openclaw_gateway.py self_update.py browser_runner.py cerebro_kawiil_mcp.py; do
    # Un archivo de la lista que NO está en el paquete es un ERROR, no algo que
    # saltarse en silencio: así se ocultó que la lista decía `louis_core.py` después
    # del renombre a `donna_core.py`. El script reportó "8 instalados" y dejó el core
    # del runtime sin actualizar — los arreglos no llegaron a producción.
    if [[ ! -f "$PAQUETE/services/$svc" ]]; then
      printf "  \033[1;31m✗ %s no existe en %s/services — la lista está desfasada\033[0m\n" "$svc" "$PAQUETE"
      faltantes=$((faltantes+1))
      continue
    fi
    local h_nuevo h_disco h_previo
    h_nuevo="$(sha256sum "$PAQUETE/services/$svc" | cut -c1-16)"
    h_disco="$(sudo sha256sum "$oc/scripts/$svc" 2>/dev/null | cut -c1-16 || echo '')"
    h_previo="$(sudo grep -o "\"$svc\": *\"[0-9a-f]*\"" "$manifiesto" 2>/dev/null \
                | grep -o '[0-9a-f]\{16\}' | head -1 || echo '')"
    if [[ -n "$h_disco" && -n "$h_previo" && "$h_disco" != "$h_previo" ]]; then
      printf "  \033[1;33m! %s fue EDITADO EN VIVO desde el último deploy\033[0m\n" "$svc"
      printf "      el cambio no está en git; lo estás pisando. Compáralo con:\n"
      printf "      sudo diff %s/scripts/%s %s/services/%s\n" "$bkp" "$svc" "$PAQUETE" "$svc"
      drift=$((drift+1))
    fi
    [[ "$h_disco" != "$h_nuevo" ]] && cambiados=$((cambiados+1))
    sudo install -m 0755 -o "$su" -g "$su" "$PAQUETE/services/$svc" "$oc/scripts/$svc"
    nuevo_manifiesto+="  \"$svc\": \"$h_nuevo\",\n"
    n=$((n+1))
  done
  printf '{\n%s  "_deploy": "%s"\n}\n' "$nuevo_manifiesto" "$(date -Iseconds)" \
    | sudo tee "$manifiesto" >/dev/null
  [[ $drift -gt 0 ]] && printf "  \033[1;33m! %d archivo(s) con ediciones en vivo pisadas — respaldo en %s\033[0m\n" "$drift" "$bkp"
  printf "  (%d de %d archivos cambiaron respecto a lo que había en disco)\n" "$cambiados" "$n"
  if [[ $faltantes -gt 0 ]]; then
    fail "$faltantes archivo(s) de la lista no existen en el paquete. NO se instaló todo:
 arregla la lista en este script antes de dar el deploy por bueno."
  fi
  # Huérfanos del renombre louis_*→donna_*: si quedan en el runtime confunden a
  # cualquiera que audite, y un import equivocado los volvería a usar.
  for viejo in louis_core.py louis_html.py; do
    if sudo test -f "$oc/scripts/$viejo"; then
      sudo mv "$oc/scripts/$viejo" "$bkp/$viejo"
      printf "  · %s era un huérfano del renombre → movido al respaldo\n" "$viejo"
    fi
  done
  # ── Scrapers legales ──────────────────────────────────────────────────────
  # deploy.sh los instala, pero SIEMPRE corremos --solo-servicios (que lo salta),
  # así que sin esto el runtime en /opt/openclaw/legal/ se queda con la versión
  # vieja para siempre. Justo lo que pasó con el arreglo del backfill del SJF: el
  # rsync llega a /opt/louis/legal-scrapers/, no al runtime.
  local nleg=0
  for par in \
      "sjf_harvest.py:sjf" "sjf_weekly_summary.py:sjf" "sjf_biblioteca.py:sjf" \
      "sjf_backfill.py:sjf" "dof_biblioteca.py:dof" "dof_daily_summary.py:dof" \
      "legal_digest.py:." "estado_legal.py:."; do
    local arch="${par%%:*}" sub="${par##*:}"
    if [[ ! -f "$PAQUETE/legal-scrapers/$arch" ]]; then
      printf "  \033[1;31m✗ legal-scrapers/%s no existe — la lista está desfasada\033[0m\n" "$arch"
      faltantes=$((faltantes+1))
      continue
    fi
    sudo mkdir -p "$oc/legal/$sub"
    sudo install -m 0755 -o "$su" -g "$su" "$PAQUETE/legal-scrapers/$arch" "$oc/legal/$sub/$arch"
    nleg=$((nleg+1))
  done
  ok "$nleg scrapers legales instalados en $oc/legal/"

  # Anti-desfase: cada ExecStart de las units que apunte a $oc/legal/ tiene que
  # existir en disco. Es la comprobación que habría cachado que --solo-servicios
  # nunca instalaba estos archivos, en vez de descubrirlo por un acervo incompleto.
  local huerfanos=0
  while read -r destino; do
    [[ -z "$destino" ]] && continue
    if ! sudo test -f "$destino"; then
      printf "  \033[1;31m✗ una unit ejecuta %s y ese archivo NO existe\033[0m\n" "$destino"
      huerfanos=$((huerfanos+1))
    fi
  done < <(grep -h "^ExecStart=" "$PAQUETE"/services/*.service 2>/dev/null \
           | grep -o "@@OPENCLAW_HOME@@/legal/[A-Za-z0-9_/.-]*\.py" \
           | sed "s|@@OPENCLAW_HOME@@|$oc|" | sort -u)
  if [[ $huerfanos -gt 0 ]]; then
    fail "$huerfanos unit(s) apuntan a scripts que no existen en el runtime.
 Agrégalos a la lista de scrapers legales en este script."
  fi

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
# cerebro-kawiil se reinicia también: importa donna_core, así que sin reinicio se
# queda con el core viejo en memoria. Antes se omitía por miedo a perder ediciones
# en vivo; ahora el manifiesto de hashes ya avisa de eso antes de pisar nada.
for s in "${SERVICIOS[@]}" cerebro-kawiil; do
  # Preguntar al DISCO, no parsear `systemctl list-unit-files`: ese grep se saltó
  # scheduler y openclaw-gateway sin decir nada, y quedaron corriendo el core viejo
  # en memoria mientras el script reportaba éxito.
  if [[ -f "/etc/systemd/system/${s}.service" ]]; then
    sudo systemctl restart "$s" && ok "$s reiniciado" \
      || printf "  ! %s NO reinició — sudo systemctl status %s\n" "$s" "$s"
  else
    printf "  · %s.service no está instalado (se omite)\n" "$s"
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

# cerebro-kawiil NO se reinicia automáticamente: es el MCP que comparte memoria con
# Cowork, y reiniciarlo aplica la versión del repo. Si el archivo en disco cambió,
# hay que decirlo — el servicio sigue corriendo el código anterior en memoria.
# El script no puede dar el deploy por bueno solo porque los comandos no fallaron:
# hay que comprobar que cada servicio arrancó DESPUÉS de que se escribió el core.
# Un servicio con timestamp anterior está corriendo el código viejo en memoria.
log "Verificando que cada servicio cargó el código nuevo"
_core="/opt/openclaw/scripts/donna_core.py"
if sudo test -f "$_core"; then
  _mtime="$(sudo stat -c %Y "$_core")"
  _viejos=0
  for s in "${SERVICIOS[@]}" cerebro-kawiil; do
    systemctl is-active --quiet "$s" 2>/dev/null || continue
    _ts="$(systemctl show -p ActiveEnterTimestampMonotonic --value "$s" 2>/dev/null || echo 0)"
    _arranque="$(date -d "$(systemctl show -p ActiveEnterTimestamp --value "$s" 2>/dev/null)" +%s 2>/dev/null || echo 0)"
    if [[ "$_arranque" -gt 0 && "$_arranque" -lt "$_mtime" ]]; then
      printf "  \033[1;31m✗ %s arrancó ANTES del core nuevo — corre el viejo en memoria\033[0m\n" "$s"
      _viejos=$((_viejos+1))
    else
      ok "$s cargó el código nuevo"
    fi
  done
  [[ $_viejos -gt 0 ]] && fail "$_viejos servicio(s) no cargaron el código nuevo. Corre:
   sudo systemctl restart ${SERVICIOS[*]} cerebro-kawiil"
fi

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
