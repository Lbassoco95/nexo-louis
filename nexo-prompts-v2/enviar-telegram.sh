#!/usr/bin/env bash
# enviar-telegram.sh — Louis usa este tool para mandar mensajes de Telegram a Polo.
#
# Uso:
#   enviar-telegram.sh "Hola Polo, recordatorio: junta en 30 min"
#   enviar-telegram.sh "*Importante:* Marco confirmó CORS" --markdown
#   enviar-telegram.sh "Aviso sigiloso" --silent
#
# Args:
#   $1 = texto del mensaje (obligatorio)
#   --markdown      formatea con Markdown V2 (negritas, links, código)
#   --html          formatea con HTML
#   --silent        sin sonido en el iPhone (notificación silenciosa)
#
# Credenciales: lee ~/.openclaw/credentials/telegram.env

set -euo pipefail

CREDS="$HOME/.openclaw/credentials/telegram.env"

if [[ ! -f "$CREDS" ]]; then
  echo "Error: no se encontró $CREDS" >&2
  echo "Ejecuta install-telegram.sh primero." >&2
  exit 1
fi

# Cargar credenciales
# shellcheck disable=SC1090
source "$CREDS"

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN no definido en $CREDS}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID no definido en $CREDS}"

if [[ $# -lt 1 ]]; then
  echo "Error: falta el texto del mensaje." >&2
  echo "Uso: $0 \"texto\" [--markdown|--html] [--silent]" >&2
  exit 1
fi

MESSAGE="$1"
shift

PARSE_MODE=""
DISABLE_NOTIF="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --markdown) PARSE_MODE="MarkdownV2" ;;
    --html)     PARSE_MODE="HTML" ;;
    --silent)   DISABLE_NOTIF="true" ;;
    *) echo "Flag desconocido: $1" >&2; exit 1 ;;
  esac
  shift
done

# Construir el POST
ARGS=(-X POST
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
      -d "chat_id=${TELEGRAM_CHAT_ID}"
      --data-urlencode "text=${MESSAGE}"
      -d "disable_notification=${DISABLE_NOTIF}")

if [[ -n "$PARSE_MODE" ]]; then
  ARGS+=(-d "parse_mode=${PARSE_MODE}")
fi

RESPONSE="$(curl -s "${ARGS[@]}")"
OK="$(echo "$RESPONSE" | python3 -c 'import sys, json; d=json.load(sys.stdin); print(d.get("ok"))' 2>/dev/null || echo "false")"

if [[ "$OK" == "True" ]]; then
  echo "OK: mensaje enviado a Telegram."
else
  echo "ERROR enviando mensaje:" >&2
  echo "$RESPONSE" >&2
  exit 1
fi
