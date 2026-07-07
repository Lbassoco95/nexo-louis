#!/usr/bin/env bash
# redeploy.sh — Despliegue ligero: copia los servicios .py del repo al runtime
# (/opt/openclaw/scripts) y reinicia los servicios. NO toca datos (spaces/, entregables/,
# credenciales). Idempotente. Correr DESPUÉS de un `git pull`.
#
# Uso (como root, desde el repo):
#   sudo bash cloud/hetzner/redeploy.sh
#
# A diferencia de deploy.sh (orquestador completo con bootstrap/Caddy/seeds), esto solo
# actualiza la lógica de los servicios — el caso del día a día.
set -euo pipefail

USER_OWN="${SYSTEM_USER:-polo}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/services" && pwd)"
DST=/opt/openclaw/scripts

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "Corre como root (sudo)."
mkdir -p "$DST" "$DST/m365"

SERVICES=(louis_core.py telegram-bridge.py slack-bridge.py scheduler.py
          openclaw_gateway.py self_update.py browser_runner.py cerebro_kawiil_mcp.py)

# 1) Gate de compilación — si algún .py no compila, abortamos ANTES de copiar nada.
log "Verificando que los servicios compilan"
for svc in "${SERVICES[@]}"; do
  [[ -f "$SRC/$svc" ]] || continue
  python3 -m py_compile "$SRC/$svc" || fail "$svc no compila — abortando sin tocar el runtime."
done
[[ -f "$SRC/m365.py" ]] && { python3 -m py_compile "$SRC/m365.py" || fail "m365.py no compila."; }
ok "Todo compila"

# 2) Copiar al runtime
log "Copiando servicios a $DST"
for svc in "${SERVICES[@]}"; do
  [[ -f "$SRC/$svc" ]] && install -m 0755 -o "$USER_OWN" -g "$USER_OWN" "$SRC/$svc" "$DST/$svc" && ok "$svc"
done
[[ -f "$SRC/m365.py" ]] && install -m 0755 -o "$USER_OWN" -g "$USER_OWN" "$SRC/m365.py" "$DST/m365/m365.py"

# 3) Actualizar archivos .service si cambiaron y recargar systemd
log "Verificando archivos .service"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SERVICE_RELOAD=0
for svc_tmpl in "$SRC"/*.service; do
  unit_name="$(basename "$svc_tmpl")"
  dest="/etc/systemd/system/$unit_name"
  if [[ -f "$dest" ]]; then
    # Parchear MemoryMax si el template tiene un valor diferente
    tmpl_mm=$(grep -oP 'MemoryMax=\K\S+' "$svc_tmpl" 2>/dev/null || true)
    live_mm=$(grep -oP 'MemoryMax=\K\S+' "$dest" 2>/dev/null || true)
    if [[ -n "$tmpl_mm" && "$tmpl_mm" != "$live_mm" ]]; then
      sed -i "s/MemoryMax=${live_mm}/MemoryMax=${tmpl_mm}/" "$dest"
      ok "$unit_name: MemoryMax actualizado ($live_mm → $tmpl_mm)"
      _SERVICE_RELOAD=1
    fi
    # Parchear variables Environment= si cambiaron (ej. SJF_BACKFILL_FLOOR)
    while IFS= read -r env_line; do
      kv="${env_line#Environment=}"
      env_key="${kv%%=*}"
      tmpl_val="${kv#*=}"
      live_val=$(grep -oP "Environment=${env_key}=\K\S+" "$dest" 2>/dev/null || true)
      if [[ -n "$live_val" && "$live_val" != "$tmpl_val" ]]; then
        sed -i "s|Environment=${env_key}=${live_val}|Environment=${env_key}=${tmpl_val}|" "$dest"
        ok "$unit_name: ${env_key} actualizado ($live_val → $tmpl_val)"
        _SERVICE_RELOAD=1
      fi
    done < <(grep '^Environment=' "$svc_tmpl" 2>/dev/null || true)
  fi
done
# Parchear .timer si cambiaron (no tienen placeholders — copia directa)
for t_tmpl in "$SRC"/*.timer; do
  t_name="$(basename "$t_tmpl")"
  t_dest="/etc/systemd/system/$t_name"
  if [[ -f "$t_dest" ]]; then
    if ! diff -q "$t_tmpl" "$t_dest" >/dev/null 2>&1; then
      install -m 0644 "$t_tmpl" "$t_dest"
      ok "$t_name: timer actualizado"
      _SERVICE_RELOAD=1
    fi
  fi
done
# Instalar units que están en el repo pero no en el servidor (con sustitución de placeholders)
OPENCLAW_HOME="$(dirname "$DST")"
for t_tmpl in "$SRC"/*.timer; do
  t_name="$(basename "$t_tmpl")"
  t_dest="/etc/systemd/system/$t_name"
  if [[ ! -f "$t_dest" ]]; then
    svc_name="${t_name%.timer}.service"
    svc_src="$SRC/$svc_name"
    svc_dest="/etc/systemd/system/$svc_name"
    if [[ -f "$svc_src" && ! -f "$svc_dest" ]]; then
      sed "s|@@SYSTEM_USER@@|${USER_OWN}|g; s|@@OPENCLAW_HOME@@|${OPENCLAW_HOME}|g" \
        "$svc_src" > "$svc_dest"
      chmod 0644 "$svc_dest"
      ok "$svc_name: instalado (nuevo)"
    fi
    install -m 0644 "$t_tmpl" "$t_dest"
    systemctl enable --now "$t_name" && ok "$t_name: instalado y activado"
    _SERVICE_RELOAD=1
  fi
done
if [[ $_SERVICE_RELOAD -eq 1 ]]; then
  systemctl daemon-reload && ok "systemd daemon-reload"
fi

# 3b) Actualizar scrapers legales si existen en el repo
SCRAPERS_SRC="$REPO_ROOT/legal-scrapers"
DOF_DST=/opt/openclaw/legal/dof
SJF_DST=/opt/openclaw/legal/sjf
if [[ -d "$SCRAPERS_SRC" ]]; then
  log "Actualizando scrapers legales"
  for f in "$SCRAPERS_SRC"/*.py; do
    fname="$(basename "$f")"
    python3 -m py_compile "$f" 2>/dev/null || { warn "$fname no compila — omito"; continue; }
    if [[ "$fname" == dof_* ]]; then
      [[ -d "$DOF_DST" ]] && install -m 0755 -o "$USER_OWN" -g "$USER_OWN" "$f" "$DOF_DST/$fname" && ok "dof/$fname"
    elif [[ "$fname" == sjf_* ]]; then
      [[ -d "$SJF_DST" ]] && install -m 0755 -o "$USER_OWN" -g "$USER_OWN" "$f" "$SJF_DST/$fname" && ok "sjf/$fname"
    fi
  done
fi

# 4) Reiniciar servicios (solo los que existen/están activos)
log "Reiniciando servicios"
for unit in telegram-bridge slack-bridge scheduler openclaw-gateway cerebro-kawiil; do
  if systemctl list-unit-files "${unit}.service" >/dev/null 2>&1 && \
     systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl restart "$unit" && ok "↻ $unit" || warn "$unit no reinició — journalctl -u $unit -n 30"
  else
    warn "$unit no instalado, omito"
  fi
done

echo ""
ok "Redeploy completo."
echo "Verifica:  systemctl is-active telegram-bridge cerebro-kawiil scheduler"
