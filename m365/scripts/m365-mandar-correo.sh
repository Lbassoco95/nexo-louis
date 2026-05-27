#!/usr/bin/env bash
# m365-mandar-correo.sh — Manda un correo desde la cuenta del tenant.
# Uso:
#   m365-mandar-correo.sh kawiil "destinatario@ej.com" "Asunto" "Cuerpo del mensaje"
#   m365-mandar-correo.sh kawiil "a@ej.com,b@ej.com" "Asunto" "Cuerpo" "cc@ej.com" "html|text"
#
# Por seguridad: solo manda, no incluye attachments. Para attachments hay que extenderlo.

set -euo pipefail

TENANT="${1:?Uso: $0 <tenant> <to> <subject> <body> [cc] [contentType]}"
TO="${2:?destinatario requerido}"
SUBJECT="${3:?subject requerido}"
BODY="${4:?body requerido}"
CC="${5:-}"
CONTENT_TYPE="${6:-text}"  # text o html

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN="$("$SCRIPT_DIR/m365-token.sh" "$TENANT")"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: no obtuve access_token" >&2
  exit 1
fi

# Construir JSON del mensaje
PAYLOAD="$(python3 - <<PYTHON
import json, sys
to_list = "$TO".split(",")
cc_list = "$CC".split(",") if "$CC" else []

msg = {
    "message": {
        "subject": "$SUBJECT",
        "body": {
            "contentType": "Text" if "$CONTENT_TYPE" == "text" else "HTML",
            "content": '''$BODY'''
        },
        "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to_list if a.strip()],
    },
    "saveToSentItems": True
}
if cc_list and any(c.strip() for c in cc_list):
    msg["message"]["ccRecipients"] = [{"emailAddress": {"address": c.strip()}} for c in cc_list if c.strip()]

print(json.dumps(msg))
PYTHON
)"

RESP_CODE="$(curl -sS -o /tmp/m365-send-resp.json -w "%{http_code}" \
  -X POST "https://graph.microsoft.com/v1.0/me/sendMail" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")"

if [[ "$RESP_CODE" == "202" ]] || [[ "$RESP_CODE" == "200" ]]; then
  echo "OK: correo enviado a $TO"
  echo "    Subject: $SUBJECT"
  rm -f /tmp/m365-send-resp.json
else
  echo "ERROR: HTTP $RESP_CODE" >&2
  cat /tmp/m365-send-resp.json >&2
  echo "" >&2
  rm -f /tmp/m365-send-resp.json
  exit 1
fi
