#!/usr/bin/env bash
# v3 — Rebrand completo a Nexo
# Cambia:
#   1. Nombres de agentes en ~/.openclaw/openclaw.json (Yoltik X → Nexo X)
#   2. AGENTS.md en ambos paths (spaces/* y agents/*/agent/)
#   3. system-prompt.md (por compatibilidad, aunque OpenClaw lee AGENTS.md)
#   4. Borra IDENTITY.md, USER.md, BOOTSTRAP.md en general/ (para rehacer bootstrap)
#   5. Reinicia gateway

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_OC="$HOME/.openclaw"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> 1. Backup de openclaw.json y AGENTS.md"
cp "$HOME_OC/openclaw.json" "$HOME_OC/openclaw.json.backup-$STAMP"
echo "    $HOME_OC/openclaw.json.backup-$STAMP"

for s in general legal ikan; do
  for path in "$HOME_OC/spaces/$s/AGENTS.md" "$HOME_OC/agents/$s/agent/AGENTS.md"; do
    if [[ -f "$path" ]]; then
      cp "$path" "$path.backup-$STAMP"
      echo "    $path.backup-$STAMP"
    fi
  done
done

echo ""
echo "==> 2. Renombrando agentes en openclaw.json (Yoltik X → Nexo X)"
TMP="$(mktemp)"
# walk recorre todo el JSON. Tocamos solo objetos cuyo .name empieza con "Yoltik ".
jq '
  walk(
    if type == "object" and (.name // "" | startswith("Yoltik "))
    then .name = (.name | sub("^Yoltik"; "Nexo"))
    else . end
  )
' "$HOME_OC/openclaw.json" > "$TMP"

# Verificar que jq no rompió el JSON antes de mover
if jq empty "$TMP" 2>/dev/null; then
  mv "$TMP" "$HOME_OC/openclaw.json"
  chmod 600 "$HOME_OC/openclaw.json"
  echo "    OK. Nombres encontrados ahora:"
  grep '"name"' "$HOME_OC/openclaw.json" | sed 's/^/      /' | head -10
else
  echo "    ERROR: jq produjo JSON inválido. Abortando."
  rm -f "$TMP"
  exit 1
fi

echo ""
echo "==> 3. Sobrescribiendo AGENTS.md en ambos paths"
for s in general legal ikan; do
  src_file="$SRC/$s.md"
  if [[ ! -f "$src_file" ]]; then
    echo "    WARN: no encuentro $src_file, salto"
    continue
  fi

  # Path 1: spaces/
  dest1="$HOME_OC/spaces/$s/AGENTS.md"
  cp "$src_file" "$dest1"
  echo "    $dest1"

  # Path 2: agents/
  dest2_dir="$HOME_OC/agents/$s/agent"
  if [[ -d "$dest2_dir" ]]; then
    cp "$src_file" "$dest2_dir/AGENTS.md"
    echo "    $dest2_dir/AGENTS.md"
  else
    mkdir -p "$dest2_dir"
    cp "$src_file" "$dest2_dir/AGENTS.md"
    echo "    $dest2_dir/AGENTS.md (creado)"
  fi

  # Por compatibilidad mantengo también system-prompt.md
  cp "$src_file" "$HOME_OC/spaces/$s/system-prompt.md"
done

echo ""
echo "==> 4. Reset de bootstrap del espacio general"
rm -f "$HOME_OC/spaces/general/IDENTITY.md" \
      "$HOME_OC/spaces/general/USER.md" \
      "$HOME_OC/spaces/general/BOOTSTRAP.md"
echo "    IDENTITY.md, USER.md, BOOTSTRAP.md eliminados"

# También borrar sessions viejas para arrancar con conversación limpia
echo ""
echo "==> 5. Limpiando sessions viejas del agente general"
SESSIONS_DIR="$HOME_OC/agents/general/sessions"
if [[ -d "$SESSIONS_DIR" ]]; then
  # Mover sessions a backup en lugar de borrar (por si Polo quiere recuperar algo)
  mkdir -p "$SESSIONS_DIR/.archive-$STAMP"
  find "$SESSIONS_DIR" -maxdepth 1 -type f -name "*.jsonl" -exec mv {} "$SESSIONS_DIR/.archive-$STAMP/" \;
  find "$SESSIONS_DIR" -maxdepth 1 -type f -name "*.json" -not -name "sessions.json" -not -name ".usage-cost-cache.json" -exec mv {} "$SESSIONS_DIR/.archive-$STAMP/" \; 2>/dev/null || true
  # Reset sessions.json a array vacío
  echo "[]" > "$SESSIONS_DIR/sessions.json"
  echo "    sessions movidas a $SESSIONS_DIR/.archive-$STAMP/"
fi

echo ""
echo "==> 6. Verificación"
echo "--- nombres en openclaw.json ---"
grep '"name"' "$HOME_OC/openclaw.json" | grep -E "Nexo|Yoltik" | sed 's/^/    /' || true
echo "--- general/AGENTS.md (primeras 3 líneas) ---"
head -3 "$HOME_OC/spaces/general/AGENTS.md" | sed 's/^/    /'
echo "--- legal/AGENTS.md (primeras 3 líneas) ---"
head -3 "$HOME_OC/spaces/legal/AGENTS.md" | sed 's/^/    /'
echo "--- ikan/AGENTS.md (primeras 3 líneas) ---"
head -3 "$HOME_OC/spaces/ikan/AGENTS.md" | sed 's/^/    /'

echo ""
echo "==> 7. Reiniciando gateway"
launchctl kickstart -k gui/501/ai.openclaw.gateway
sleep 4

echo ""
echo "==> 8. Verificando que escucha"
if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    OK, gateway escuchando en localhost:3000"
else
  echo "    WARN: gateway aún no escucha, espera unos segundos y revisa con: lsof -iTCP:3000 -sTCP:LISTEN"
fi

echo ""
echo "============================================================"
echo "Listo. Ahora en el iPad:"
echo "  1. Refresca Safari"
echo "  2. El dropdown debe mostrar: Nexo General, Nexo Legal, Nexo Ikán"
echo "  3. Selecciona 'Nexo General'"
echo "  4. Tap '+' para nueva conversación"
echo "  5. Manda 'Hola'"
echo "  6. Debe responder: 'Hola, soy Nexo. Antes de empezar, ¿cómo quieres que te llame?'"
echo "============================================================"
