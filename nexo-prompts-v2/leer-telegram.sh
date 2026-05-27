#!/usr/bin/env bash
# leer-telegram.sh — Lee mensajes nuevos que Polo le mandó al bot.
#
# Uso:
#   leer-telegram.sh           # imprime mensajes pendientes en JSON simple
#   leer-telegram.sh --pretty  # formato legible
#
# Mantiene un offset persistente en ~/.openclaw/credentials/telegram-offset.txt
# para no leer mensajes repetidos.

set -euo pipefail

CREDS="$HOME/.openclaw/credentials/telegram.env"
OFFSET_FILE="$HOME/.openclaw/credentials/telegram-offset.txt"

if [[ ! -f "$CREDS" ]]; then
  echo "Error: no se encontró $CREDS" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CREDS"

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN no definido}"

# Cargar offset previo
LAST_OFFSET="0"
if [[ -f "$OFFSET_FILE" ]]; then
  LAST_OFFSET="$(cat "$OFFSET_FILE")"
fi

# offset = update_id del último procesado + 1
NEXT_OFFSET=$((LAST_OFFSET + 1))

RESPONSE="$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates?offset=${NEXT_OFFSET}&timeout=2")"

# Parse y extraer
PRETTY="false"
if [[ "${1:-}" == "--pretty" ]]; then
  PRETTY="true"
fi

python3 - <<PYTHON
import json, sys, os

response = json.loads('''$RESPONSE''')
if not response.get("ok"):
    print("ERROR:", response, file=sys.stderr)
    sys.exit(1)

results = response.get("result", [])
if not results:
    print("(sin mensajes nuevos)")
    sys.exit(0)

last_update_id = max(r["update_id"] for r in results)

# Guardar offset
with open("$OFFSET_FILE", "w") as f:
    f.write(str(last_update_id))

# Imprimir
pretty = $([ "$PRETTY" = "true" ] && echo "True" || echo "False")
for r in results:
    msg = r.get("message", {})
    text = msg.get("text", "")
    date = msg.get("date", "")
    if pretty:
        from datetime import datetime
        dt = datetime.fromtimestamp(date).strftime("%Y-%m-%d %H:%M") if date else "?"
        print(f"[{dt}] {text}")
    else:
        print(json.dumps({"date": date, "text": text}))
PYTHON
