#!/usr/bin/env bash
# Instala la versión v3 del prompt de Nexo General (con briefing diario + agenda).
# Crea AGENDA.md con los pendientes que Polo tiene para mañana lunes 25 mayo 2026.
# NO toca legal ni ikan (ya están bien).

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_OC="$HOME/.openclaw"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> 1. Backup del AGENTS.md actual de Nexo General"
for path in "$HOME_OC/spaces/general/AGENTS.md" "$HOME_OC/agents/general/agent/AGENTS.md"; do
  if [[ -f "$path" ]]; then
    cp "$path" "$path.backup-$STAMP"
    echo "    $path.backup-$STAMP"
  fi
done

echo ""
echo "==> 2. Instalando prompt v3 (con briefing + agenda)"
cp "$SRC/general-v3.md" "$HOME_OC/spaces/general/AGENTS.md"
cp "$SRC/general-v3.md" "$HOME_OC/agents/general/agent/AGENTS.md"
cp "$SRC/general-v3.md" "$HOME_OC/spaces/general/system-prompt.md"
echo "    AGENTS.md actualizados en ambos paths + system-prompt.md"

echo ""
echo "==> 3. Instalando AGENDA.md inicial"
# Si ya existe AGENDA.md, hacer backup primero
if [[ -f "$HOME_OC/spaces/general/AGENDA.md" ]]; then
  cp "$HOME_OC/spaces/general/AGENDA.md" "$HOME_OC/spaces/general/AGENDA.md.backup-$STAMP"
  echo "    Backup del AGENDA.md previo: $HOME_OC/spaces/general/AGENDA.md.backup-$STAMP"
fi
cp "$SRC/AGENDA-template.md" "$HOME_OC/spaces/general/AGENDA.md"
echo "    AGENDA.md instalado con pendientes del lunes 25 mayo"

echo ""
echo "==> 4. Verificación"
echo "    AGENTS.md primeras líneas:"
head -3 "$HOME_OC/spaces/general/AGENTS.md" | sed 's/^/      /'
echo ""
echo "    AGENDA.md primeras 5 líneas:"
head -5 "$HOME_OC/spaces/general/AGENDA.md" | sed 's/^/      /'

echo ""
echo "==> 5. Reiniciando gateway"
launchctl kickstart -k gui/501/ai.openclaw.gateway
sleep 4

if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    OK, gateway escuchando"
else
  echo "    WARN: gateway aún no escucha. Espera y revisa con: lsof -iTCP:3000 -sTCP:LISTEN"
fi

echo ""
echo "============================================================"
echo "Listo. Mañana lunes cuando abras Nexo en el iPad:"
echo "  1. Tap '+' para nueva conversación"
echo "  2. Manda 'Hola' o 'buenos días'"
echo "  3. Louis (Nexo) debe saludarte por nombre, decirte la fecha,"
echo "     y darte briefing de los pendientes de hoy."
echo ""
echo "Si quieres probarlo ya, hazlo desde el iPad."
echo "============================================================"
