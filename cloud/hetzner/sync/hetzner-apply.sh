#!/usr/bin/env bash
# hetzner-apply.sh — Cron job en Hetzner (cada 5 min).
# Aplica el delta de /opt/openclaw-sync/spaces/ → /opt/openclaw/spaces/
# con respaldo previo y locking para no atropellarse a sí mismo.
#
# Estrategia:
#  1) Si /opt/openclaw-sync/spaces no tiene nada nuevo desde el último apply, no hace nada.
#  2) Snapshot (tar.gz completo de spaces) a /opt/openclaw/backups/spaces-YYYYMMDDHHMMSS.tar.gz, SOLO si hay cambios
#     reales de contenido; retención escalonada en prune-backups.sh y freno de espacio (ver más abajo).
#  3) rsync con --update (no sobreescribe archivos que ya son más nuevos en destino).
#  4) Si rsync falla, restaura el último snapshot.

set -euo pipefail

# Rutas: los valores por defecto son los de producción; las variables LOUIS_* existen para poder probar con carpetas temporales.
SRC="${LOUIS_SYNC_SRC:-/opt/openclaw-sync/spaces}"
DST="${LOUIS_SYNC_DST:-/opt/openclaw/spaces}"
BACKUPS="${LOUIS_BACKUPS:-/opt/openclaw/backups}"
LOCK="${LOUIS_SYNC_LOCK:-/var/lock/louis-sync.lock}"
MARKER="${LOUIS_SYNC_MARKER:-/opt/openclaw-sync/.last-apply}"
PRUNE="$(cd "$(dirname "$0")" && pwd)/prune-backups.sh"
# Freno de espacio (GB libres en el disco de los respaldos): por debajo del primero se poda en modo emergencia;
# por debajo del segundo NO se aplica nada (sin snapshot no hay forma segura de revertir) hasta que haya espacio.
MIN_FREE_GB="${LOUIS_MIN_FREE_GB:-8}"
HARD_FREE_GB="${LOUIS_HARD_FREE_GB:-3}"

libre_gb() {  # LOUIS_FAKE_FREE_GB solo para pruebas
  if [[ -n "${LOUIS_FAKE_FREE_GB:-}" ]]; then echo "$LOUIS_FAKE_FREE_GB"; else df --output=avail -BG "$BACKUPS" | tail -1 | tr -dc '0-9'; fi
}

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

# ── ¿Hay cambios REALES de contenido? ────────────────────────
# Una fecha de modificación más nueva no es un cambio: sin esta revisión se hacía un snapshot completo, un rsync y un
# reload cada 5 minutos aunque no hubiera nada nuevo. Con --checksum se compara el contenido; si rsync falla, se
# asume que sí hay cambios (comportamiento de antes).
if SIMULADO=$(rsync -a --update --checksum -i -n "$SRC/" "$DST/" 2>/dev/null); then
  CAMBIOS=$(printf '%s\n' "$SIMULADO" | grep -cE '^(>f|cd|cL|\*deleting)' || true)
else
  CAMBIOS=1
fi
if (( CAMBIOS == 0 )); then
  touch "$MARKER"   # solo cambiaron fechas: se da por aplicado, sin snapshot ni reload
  exit 0
fi

# ── Freno de espacio ──────────────────────────────────────────
mkdir -p "$BACKUPS"
LIBRE=$(libre_gb)
if (( LIBRE < MIN_FREE_GB )); then
  echo "[$(date -Iseconds)] WARN: quedan ${LIBRE} GB libres (< ${MIN_FREE_GB}); poda de emergencia de snapshots"
  [[ -f "$PRUNE" ]] && bash "$PRUNE" --emergency --dir "$BACKUPS" || true
  LIBRE=$(libre_gb)
  if (( LIBRE < HARD_FREE_GB )); then
    echo "[$(date -Iseconds)] ERROR: solo ${LIBRE} GB libres (< ${HARD_FREE_GB}) tras podar; NO se aplica el delta hasta liberar espacio" >&2
    exit 0   # sin tocar el marcador: se reintenta en la siguiente corrida
  fi
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

# ── Pruning de backups: retención escalonada (ver prune-backups.sh) ──
if [[ -f "$PRUNE" ]]; then
  bash "$PRUNE" --dir "$BACKUPS" || true
else
  find "$BACKUPS" -name 'spaces-*.tar.gz' -mtime +14 -delete 2>/dev/null || true
fi
