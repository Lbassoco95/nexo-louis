#!/usr/bin/env bash
# mac-command-runner.sh — La Mac consulta la cola de comandos en Hetzner cada
# minuto (vía launchd), ejecuta los comandos whitelisted localmente, y escribe
# el resultado de vuelta en Hetzner.
#
# Por qué este patrón: Hetzner NO puede conectarse a la Mac (NAT, sin IP fija).
# Pero la Mac SÍ se conecta a Hetzner. Así que la Mac hace polling de la cola.
#
# Flujo:
#   1. Baja /opt/openclaw/state/mac-commands.jsonl de Hetzner
#   2. Para cada comando con status "pending" que no haya ejecutado ya:
#      - Marca running (resultado parcial a Hetzner)
#      - Ejecuta el script local mapeado
#      - Sube el resultado (done/error + salida) a mac-command-results.jsonl
#   3. Tras un backfill, dispara mac-push-legal.sh para subir la BD actualizada
#
# Variables (las pone el plist mac-install-command-runner.sh):
#   LOUIS_REMOTE_HOST  ej: 204.168.131.21
#   LOUIS_REMOTE_USER  ej: polo
#   LOUIS_SSH_KEY      ej: ~/.ssh/id_ed25519
#
# Comandos locales (puedes sobrescribir con env vars para apuntar a tus scripts):
#   DOF_BACKFILL_CMD       default: cd ~/dof_biblioteca && python3 dof_biblioteca.py backfill
#   DOF_BACKFILL_MES_CMD   default: cd ~/dof_biblioteca && python3 dof_biblioteca.py update
#   SJF_BACKFILL_CMD       default: cd ~/sjf_biblioteca && python3 sjf_biblioteca.py
#   PUSH_LEGAL_SCRIPT      default: ~/.openclaw/scripts/mac-push-legal.sh

set -uo pipefail

: "${LOUIS_REMOTE_HOST:?LOUIS_REMOTE_HOST no definido}"
: "${LOUIS_REMOTE_USER:?LOUIS_REMOTE_USER no definido}"
: "${LOUIS_SSH_KEY:?LOUIS_SSH_KEY no definido}"

REMOTE="${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}"
SSH_OPTS="-i $LOUIS_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes"

QUEUE_REMOTE="/opt/openclaw/state/mac-commands.jsonl"
RESULTS_REMOTE="/opt/openclaw/state/mac-command-results.jsonl"

# Mapeo comando → ejecución local (sobrescribible vía env)
_DOF_PY="${DOF_PY:-$([ -f $HOME/dof_biblioteca/venv/bin/python3 ] && echo $HOME/dof_biblioteca/venv/bin/python3 || echo python3)}"
_SJF_PY="${SJF_PY:-$([ -f $HOME/sjf_biblioteca/venv/bin/python3 ] && echo $HOME/sjf_biblioteca/venv/bin/python3 || echo python3)}"
DOF_BACKFILL_CMD="${DOF_BACKFILL_CMD:-cd $HOME/dof_biblioteca && $_DOF_PY dof_biblioteca.py classify && $_DOF_PY dof_biblioteca.py download-contents --batch 300}"
DOF_BACKFILL_MES_CMD="${DOF_BACKFILL_MES_CMD:-cd $HOME/dof_biblioteca && $_DOF_PY dof_biblioteca.py classify && $_DOF_PY dof_biblioteca.py download-contents --batch 300}"
SJF_BACKFILL_CMD="${SJF_BACKFILL_CMD:-cd $HOME/sjf_biblioteca && $_SJF_PY sjf_biblioteca.py update && $_SJF_PY sjf_biblioteca.py backfill}"
PUSH_LEGAL_SCRIPT="${PUSH_LEGAL_SCRIPT:-$HOME/.openclaw/scripts/mac-push-legal.sh}"

# Registro local de IDs ya ejecutados (para no repetir)
STATE_DIR="$HOME/.openclaw/state"
mkdir -p "$STATE_DIR"
DONE_IDS="$STATE_DIR/mac-command-done-ids.txt"
touch "$DONE_IDS"

LOG_TS() { date -Iseconds; }

# --- 1) Baja la cola ---
QUEUE_LOCAL=$(mktemp /tmp/mac-cmd-queue.XXXXXX)
if ! scp $SSH_OPTS "$REMOTE:$QUEUE_REMOTE" "$QUEUE_LOCAL" 2>/dev/null; then
  # No hay cola todavía o Hetzner inalcanzable — salir limpio
  rm -f "$QUEUE_LOCAL"
  exit 0
fi

# --- helper: empuja un resultado a Hetzner (append atómico) ---
push_result() {
  local json="$1"
  local tmp
  tmp=$(mktemp /tmp/mac-cmd-result.XXXXXX)
  printf '%s\n' "$json" > "$tmp"
  # append remoto: cat local >> remoto vía ssh
  ssh $SSH_OPTS "$REMOTE" "mkdir -p /opt/openclaw/state && cat >> $RESULTS_REMOTE" < "$tmp" 2>/dev/null
  rm -f "$tmp"
}

# --- 2) Procesa cada comando pendiente ---
RAN_BACKFILL=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  # Parse con python (jq puede no estar en la Mac)
  ID=$(printf '%s' "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  CMD=$(printf '%s' "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('comando',''))" 2>/dev/null)
  MES=$(printf '%s' "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('args',{}).get('mes',''))" 2>/dev/null)
  BASH_CMD=$(printf '%s' "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('args',{}).get('bash_cmd',''))" 2>/dev/null)
  [[ -z "$ID" || -z "$CMD" ]] && continue
  # ¿Ya lo ejecuté?
  grep -qxF "$ID" "$DONE_IDS" && continue

  echo "[$(LOG_TS)] Ejecutando $CMD (id=$ID, mes=$MES)"
  # Marca running
  push_result "{\"id\":\"$ID\",\"comando\":\"$CMD\",\"status\":\"running\",\"started_at\":\"$(LOG_TS)\"}"

  OUTPUT=""
  STATUS="done"
  case "$CMD" in
    dof_backfill)
      # Classify primero, luego descarga priorizando entradas recientes (2026+)
      OUT1=$(bash -lc "cd $HOME/dof_biblioteca && $_DOF_PY dof_biblioteca.py classify" 2>&1)
      PATCH=$(mktemp /tmp/dof_patch_XXXXXX)
      cat > "$PATCH" << PYEOF
import importlib.util, sys, os
os.chdir('$HOME/dof_biblioteca')
sys.path.insert(0, '$HOME/dof_biblioteca')
spec = importlib.util.spec_from_file_location('dof', '$HOME/dof_biblioteca/dof_biblioteca.py')
with open(spec.origin) as _f:
    _src = _f.read()
# Inyecta filtro de fecha reciente + ORDER BY para priorizar 2026+
_src = _src.replace(
    "WHERE incluido=1 AND content_downloaded_at IS NULL",
    "WHERE incluido=1 AND content_downloaded_at IS NULL AND fecha>='2026-01-01' ORDER BY fecha DESC"
)
_code = compile(_src, spec.origin, 'exec')
_mod = type(sys)('dof')
_mod.__file__ = spec.origin
exec(_code, _mod.__dict__)
sys.argv = ['dof_biblioteca.py', 'download-contents', '--batch', '400']
_mod.main()
PYEOF
      OUT2=$(bash -lc "$_DOF_PY $PATCH" 2>&1)
      PATCH_EXIT=$?
      rm -f "$PATCH"
      [[ $PATCH_EXIT -ne 0 ]] && STATUS="error"
      OUTPUT="[classify]
$OUT1
[download-contents 2026+]
$OUT2"
      RAN_BACKFILL=1
      ;;
    dof_backfill_mes)
      if [[ -z "$MES" ]]; then
        OUTPUT="ERROR: falta args.mes"; STATUS="error"
      else
        # Classify + descarga solo el mes indicado (ej: 2026-05)
        OUT1=$(bash -lc "cd $HOME/dof_biblioteca && $_DOF_PY dof_biblioteca.py classify" 2>&1)
        PATCH=$(mktemp /tmp/dof_patch_mes_XXXXXX)
        cat > "$PATCH" << PYEOF
import importlib.util, sys, os
os.chdir('$HOME/dof_biblioteca')
sys.path.insert(0, '$HOME/dof_biblioteca')
spec = importlib.util.spec_from_file_location('dof', '$HOME/dof_biblioteca/dof_biblioteca.py')
with open(spec.origin) as _f:
    _src = _f.read()
_src = _src.replace(
    "WHERE incluido=1 AND content_downloaded_at IS NULL",
    "WHERE incluido=1 AND content_downloaded_at IS NULL AND fecha LIKE '$MES%' ORDER BY fecha DESC"
)
_code = compile(_src, spec.origin, 'exec')
_mod = type(sys)('dof')
_mod.__file__ = spec.origin
exec(_code, _mod.__dict__)
sys.argv = ['dof_biblioteca.py', 'download-contents', '--batch', '500']
_mod.main()
PYEOF
        OUT2=$(bash -lc "$_DOF_PY $PATCH" 2>&1)
        PATCH_EXIT=$?
        rm -f "$PATCH"
        [[ $PATCH_EXIT -ne 0 ]] && STATUS="error"
        OUTPUT="[classify]
$OUT1
[download-contents $MES]
$OUT2"
        RAN_BACKFILL=1
      fi
      ;;
    sjf_backfill)
      OUTPUT=$(bash -lc "$SJF_BACKFILL_CMD" 2>&1) || STATUS="error"
      RAN_BACKFILL=1
      ;;
    legal_sync)
      if [[ -f "$PUSH_LEGAL_SCRIPT" ]]; then
        OUTPUT=$(LOUIS_REMOTE_HOST="$LOUIS_REMOTE_HOST" LOUIS_REMOTE_USER="$LOUIS_REMOTE_USER" \
                 LOUIS_SSH_KEY="$LOUIS_SSH_KEY" bash "$PUSH_LEGAL_SCRIPT" 2>&1) || STATUS="error"
      else
        OUTPUT="ERROR: no encuentro $PUSH_LEGAL_SCRIPT"; STATUS="error"
      fi
      ;;
    mac_bash)
      if [[ -z "$BASH_CMD" ]]; then
        OUTPUT="ERROR: falta args.bash_cmd"; STATUS="error"
      else
        echo "[$(LOG_TS)] bash_cmd: $BASH_CMD"
        OUTPUT=$(bash -lc "$BASH_CMD" 2>&1) || STATUS="error"
      fi
      ;;
    *)
      OUTPUT="ERROR: comando no reconocido por el runner: $CMD"; STATUS="error"
      ;;
  esac

  # Trunca salida a 2000 chars y escapa para JSON con python
  RESULT_JSON=$(OUTPUT="$OUTPUT" ID="$ID" CMD="$CMD" STATUS="$STATUS" FIN="$(LOG_TS)" python3 <<'PY'
import os, json
print(json.dumps({
    "id": os.environ["ID"],
    "comando": os.environ["CMD"],
    "status": os.environ["STATUS"],
    "output": os.environ["OUTPUT"][-2000:],
    "finished_at": os.environ["FIN"],
}, ensure_ascii=False))
PY
)
  push_result "$RESULT_JSON"
  echo "$ID" >> "$DONE_IDS"
  echo "[$(LOG_TS)] $CMD (id=$ID) → $STATUS"
done < "$QUEUE_LOCAL"

rm -f "$QUEUE_LOCAL"

# --- 3) Si corrió algún backfill, sube la BD actualizada ---
if [[ "$RAN_BACKFILL" == "1" && -f "$PUSH_LEGAL_SCRIPT" ]]; then
  echo "[$(LOG_TS)] Backfill terminó — empujando BDs a Hetzner"
  LOUIS_REMOTE_HOST="$LOUIS_REMOTE_HOST" LOUIS_REMOTE_USER="$LOUIS_REMOTE_USER" \
    LOUIS_SSH_KEY="$LOUIS_SSH_KEY" bash "$PUSH_LEGAL_SCRIPT" 2>&1 | tail -5
fi

# --- 4) Limpia IDs viejos del registro local (mantén últimos 500) ---
if [[ $(wc -l < "$DONE_IDS") -gt 500 ]]; then
  tail -500 "$DONE_IDS" > "$DONE_IDS.tmp" && mv "$DONE_IDS.tmp" "$DONE_IDS"
fi
