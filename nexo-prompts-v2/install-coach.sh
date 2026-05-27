#!/usr/bin/env bash
# install-coach.sh — Convierte a Louis (Nexo) en coach ejecutivo:
#   1. Instala system prompt v4 con modo coach + memoria estructurada
#   2. Crea archivos de memoria: PROJECTS.md, PEOPLE.md, LEARNINGS.md, JOURNAL.md, IMPORTANT.md
#   3. Instala script crear-recordatorio.sh para push notifications via iCloud
#   4. Reinicia gateway

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_OC="$HOME/.openclaw"
SPACE="$HOME_OC/spaces/general"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> 1. Backup AGENTS.md actual"
for path in "$SPACE/AGENTS.md" "$HOME_OC/agents/general/agent/AGENTS.md"; do
  if [[ -f "$path" ]]; then
    cp "$path" "$path.backup-$STAMP"
    echo "    $path.backup-$STAMP"
  fi
done

echo ""
echo "==> 2. Instalando system prompt v4 (modo coach)"
cp "$SRC/general-v4.md" "$SPACE/AGENTS.md"
cp "$SRC/general-v4.md" "$HOME_OC/agents/general/agent/AGENTS.md"
cp "$SRC/general-v4.md" "$SPACE/system-prompt.md"
echo "    OK"

echo ""
echo "==> 3. Instalando archivos de memoria estructurada"
for f in PROJECTS.md PEOPLE.md LEARNINGS.md JOURNAL.md IMPORTANT.md; do
  if [[ -f "$SPACE/$f" ]]; then
    cp "$SPACE/$f" "$SPACE/$f.backup-$STAMP"
    echo "    backup previo: $SPACE/$f.backup-$STAMP"
  fi
  cp "$SRC/memory-files/$f" "$SPACE/$f"
  echo "    instalado: $SPACE/$f"
done

echo ""
echo "==> 4. Instalando script crear-recordatorio.sh"
mkdir -p "$SPACE/scripts"
cp "$SRC/crear-recordatorio.sh" "$SPACE/scripts/crear-recordatorio.sh"
chmod +x "$SPACE/scripts/crear-recordatorio.sh"
echo "    $SPACE/scripts/crear-recordatorio.sh (ejecutable)"

echo ""
echo "==> 5. Verificación de Reminders (iCloud)"
# Test rápido: abrir Reminders para asegurar que arranca
osascript -e 'tell application "Reminders" to activate' 2>/dev/null && echo "    OK, app Reminders accesible"
echo "    Asegúrate que la app Reminders esté logueada con tu Apple ID e iCloud sincronizando."

echo ""
echo "==> 6. Test del script de recordatorio (crea uno de prueba para mañana 9am)"
TEST_DATE="$(date -v+1d +%Y-%m-%d) 09:00"
if "$SPACE/scripts/crear-recordatorio.sh" "Prueba de Louis - puedes borrar este recordatorio" "$TEST_DATE" 2>&1; then
  echo "    OK, reminder de prueba creado para $TEST_DATE"
  echo "    Revisa tu app Reminders en iPhone/iPad en unos segundos."
else
  echo "    WARN: el script falló. Revisa permisos de macOS (Privacy → Reminders → Terminal/iTerm)"
fi

echo ""
echo "==> 7. Reiniciando gateway"
launchctl kickstart -k gui/501/ai.openclaw.gateway
sleep 4

if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    OK, gateway escuchando"
else
  echo "    WARN: gateway aún no escucha. Espera y verifica con: lsof -iTCP:3000 -sTCP:LISTEN"
fi

echo ""
echo "============================================================"
echo "Louis ahora es coach ejecutivo. Lo que cambió:"
echo ""
echo "  • Memoria estructurada activa:"
echo "    - PROJECTS.md, PEOPLE.md, LEARNINGS.md, JOURNAL.md, IMPORTANT.md"
echo "    - Louis lee y actualiza solo según lo que descubre"
echo ""
echo "  • Push notifications via iCloud Reminders:"
echo "    - Diles 'recuérdame X mañana a las 9am' y crea un Reminder"
echo "    - Te llega al iPhone como notificación nativa"
echo ""
echo "  • Comportamientos nuevos:"
echo "    - Triage al inicio (urgente, estancado, importante)"
echo "    - Descompone tareas complejas antes de ejecutar"
echo "    - Aprende preferencias y las guarda en LEARNINGS.md"
echo "    - Persigue pendientes estancados"
echo "    - Cierre de día con resumen + agenda para mañana"
echo ""
echo "PRUEBA AHORA en el iPad:"
echo "  1. Refresca Safari"
echo "  2. Nueva conversación (+)"
echo "  3. Manda: 'Hola Louis, recuérdame mañana a las 8am revisar mis pendientes'"
echo "  4. Louis debe crear el Reminder y confirmar"
echo "  5. Verifica que llegue al iPhone"
echo "============================================================"
