#!/usr/bin/env bash
# hetzner-apply.sh — Cron job en Hetzner (cada 5 min).
# Aplica el delta de /opt/openclaw-sync/spaces/ → /opt/openclaw/spaces/
# con respaldo previo y locking para no atropellarse a sí mismo.
#
# Estrategia:
#  1) Si /opt/openclaw-sync/spaces no tiene nada nuevo desde el último apply, no hace nada.
#  2) Snapshot diferencial a /opt/openclaw/backups/spaces-YYYYMMDDHHMMSS.tar.gz (retiene 14 días).
#  3) rsync con --update (no sobreescribe archivos que ya son más nuevos en destino).
#  4) Si rsync falla, restaura el último snapshot.

set -euo pipefail

SRC="/opt/openclaw-sync/spaces"
DST="/opt/openclaw/spaces"
BACKUPS="/opt/openclaw/backups"
LOCK="/var/lock/louis-sync.lock"
MARKER="/opt/openclaw-sync/.last-apply"

mkdir -p "$BACKUPS"

# ── Lock: si ya hay un apply corriendo, sal silenciosamente ──
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] another apply in progress — skip"
  exit 0
fi

# ── Verificación: hay algo que aplicar ───────────────────────
if [[ ! -d "$SRC" ]]; then
  echo "[$(date -Iseconds)] $SRC no existe — skip"
  exit 0
fi

# Última mtime de cualquier archivo dentro de SRC
LATEST_MTIME=$(find "$SRC" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 || echo 0)
LAST_APPLY_MTIME=0
[[ -f "$MARKER" ]] && LAST_APPLY_MTIME=$(stat -c %Y "$MARKER" 2>/dev/null || echo 0)

# Comparación de enteros (truncado para sortear floats)
if (( ${LATEST_MTIME%.*} <= LAST_APPLY_MTIME )); then
  # sin cambios — log silencioso
  exit 0
fi

echo "[$(date -Iseconds)] aplicando delta de Mac → OpenClaw"

# ── Snapshot de destino ──────────────────────────────────────
TS=$(date +%Y%m%d%H%M%S)
SNAPSHOT="$BACKUPS/spaces-${TS}.tar.gz"
if [[ -d "$DST" ]] && [[ -n "$(ls -A "$DST" 2>/dev/null)" ]]; then
  tar -czf "$SNAPSHOT" -C "$(dirname "$DST")" "$(basename "$DST")" 2>/dev/null || true
  echo "[$(date -Iseconds)] snapshot: $SNAPSHOT"
fi

# ── Rsync con safety: --update (no degrada timestamps más nuevos en DST) ──
# NO --delete aquí — el Mac es la fuente de "contenido nuevo", no la fuente
# autoritativa de borrados. Si Polo borra algo en la Mac y quiere borrarlo
# en Hetzner, lo hace explícitamente con el flag --delete (ver runbook).
if rsync -a --update "$SRC/" "$DST/"; then
  touch "$MARKER"
  echo "[$(date -Iseconds)] apply OK"
else
  echo "[$(date -Iseconds)] rsync FAILED — restaurando snapshot" >&2
  if [[ -f "$SNAPSHOT" ]]; then
    rm -rf "$DST"
    tar -xzf "$SNAPSHOT" -C "$(dirname "$DST")"
    echo "[$(date -Iseconds)] restored from $SNAPSHOT" >&2
  fi
  exit 1
fi

# ── Recargar OpenClaw para que vea cambios en system-prompt / config ──
# OpenClaw lee archivos on-demand para AGENDA.md y memorias, pero el
# system-prompt.md se cachea por sesión. Reiniciar el servicio es barato.
# El usuario polo tiene sudo NOPASSWD para estos comandos (ver hetzner-prepare.sh).
if systemctl is-active --quiet openclaw; then
  sudo systemctl reload openclaw 2>/dev/null || sudo systemctl restart openclaw 2>/dev/null || \
    echo "[$(date -Iseconds)] WARN: no pude recargar openclaw (revisa sudoers /etc/sudoers.d/louis-sync)"
fi

# ── Pruning de backups (>14 días) ────────────────────────────
find "$BACKUPS" -name 'spaces-*.tar.gz' -mtime +14 -delete 2>/dev/null || true
