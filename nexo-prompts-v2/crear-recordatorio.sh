#!/usr/bin/env bash
# crear-recordatorio.sh — Crea un Reminder en iCloud que sincroniza al iPhone.
#
# Uso:
#   crear-recordatorio.sh "Llamar a Marco sobre CORS" "2026-05-25 09:00"
#   crear-recordatorio.sh "Revisar agenda" "2026-05-25 07:00" "Trabajo"
#
# Args:
#   $1 = texto del recordatorio (obligatorio)
#   $2 = fecha hora "YYYY-MM-DD HH:MM" (obligatorio)
#   $3 = nombre de lista (opcional, default "Recordatorios")
#
# Requiere: macOS, app Reminders configurada con iCloud.

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Error: faltan argumentos." >&2
  echo "Uso: $0 \"texto\" \"YYYY-MM-DD HH:MM\" [lista]" >&2
  exit 1
fi

TEXT="$1"
DATETIME="$2"
LIST="${3:-Recordatorios}"

# Validar formato de fecha
if ! [[ "$DATETIME" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}$ ]]; then
  echo "Error: formato de fecha debe ser YYYY-MM-DD HH:MM (ej. 2026-05-25 09:00)" >&2
  exit 1
fi

# Convertir a formato AppleScript date (DD/MM/YYYY HH:MM:SS)
YEAR="${DATETIME:0:4}"
MONTH="${DATETIME:5:2}"
DAY="${DATETIME:8:2}"
HOUR="${DATETIME:11:2}"
MIN="${DATETIME:14:2}"

# AppleScript necesita la fecha en el formato local. Usamos date components.
osascript <<APPLESCRIPT
tell application "Reminders"
    -- Obtén la lista (si no existe usa la primera)
    set targetList to missing value
    repeat with l in lists
        if name of l is "$LIST" then
            set targetList to l
            exit repeat
        end if
    end repeat
    if targetList is missing value then
        set targetList to first list
    end if

    -- Construye fecha
    set targetDate to (current date)
    set year of targetDate to $YEAR
    set month of targetDate to $MONTH
    set day of targetDate to $DAY
    set hours of targetDate to $HOUR
    set minutes of targetDate to $MIN
    set seconds of targetDate to 0

    -- Crea el reminder
    tell targetList
        make new reminder with properties {name:"$TEXT", remind me date:targetDate}
    end tell
end tell
APPLESCRIPT

if [[ $? -eq 0 ]]; then
  echo "OK: Reminder creado para $DATETIME en lista \"$LIST\""
  echo "  Texto: $TEXT"
  echo "  Sincronizará a tu iPhone/iPad vía iCloud automáticamente."
else
  echo "ERROR: no se pudo crear el reminder. Verifica que la app Reminders esté abierta y conectada a iCloud."
  exit 1
fi
