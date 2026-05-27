#!/usr/bin/env bash
# oauth-kawiil.sh — Authorization Code Flow + PKCE para tenant Kawiil.
# Corre UNA SOLA VEZ. Abre Safari, autorizas con tu cuenta @kawiil.mx,
# y guarda access_token + refresh_token en ~/.openclaw/credentials/m365-kawiil-tokens.json
#
# Después, todos los scripts m365-*.sh leen ese archivo y refrescan el access_token solos.

set -euo pipefail

CREDS_FILE="$HOME/.openclaw/credentials/m365-kawiil.env"
TOKENS_FILE="$HOME/.openclaw/credentials/m365-kawiil-tokens.json"

if [[ ! -f "$CREDS_FILE" ]]; then
  echo "Error: no encontré $CREDS_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CREDS_FILE"

: "${M365_KAWIIL_CLIENT_ID:?}"
: "${M365_KAWIIL_TENANT_ID:?}"
: "${M365_KAWIIL_REDIRECT_URI:?}"

# === PKCE: generar verifier + challenge ===
# Generar 32 bytes aleatorios, codificar base64url
CODE_VERIFIER="$(openssl rand -base64 64 | tr -d '\n=' | tr '/+' '_-' | cut -c1-128)"
CODE_CHALLENGE="$(printf '%s' "$CODE_VERIFIER" | openssl dgst -sha256 -binary | openssl base64 | tr -d '\n=' | tr '/+' '_-')"

STATE="$(openssl rand -hex 16)"

SCOPES_ENCODED="Mail.ReadWrite%20Mail.Send%20Calendars.ReadWrite%20User.Read%20offline_access"

AUTH_URL="https://login.microsoftonline.com/${M365_KAWIIL_TENANT_ID}/oauth2/v2.0/authorize?\
client_id=${M365_KAWIIL_CLIENT_ID}&\
response_type=code&\
redirect_uri=$(printf '%s' "$M365_KAWIIL_REDIRECT_URI" | sed 's|:|%3A|g; s|/|%2F|g')&\
scope=${SCOPES_ENCODED}&\
state=${STATE}&\
code_challenge=${CODE_CHALLENGE}&\
code_challenge_method=S256&\
response_mode=query&\
prompt=select_account"

# === Levantar servidor HTTP local para recibir el callback ===
PORT=8765
TMP_FILE="$(mktemp)"

echo ""
echo "==> Iniciando servidor local en localhost:${PORT} para recibir callback OAuth..."

# Python one-liner: HTTP server que escucha UNA request, guarda query string, muestra mensaje, sale.
python3 - <<PYTHON &
import http.server, socketserver, urllib.parse, threading, sys, time

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass  # silenciar
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        # Guardar a archivo
        with open("$TMP_FILE", "w") as f:
            for k, v in qs.items():
                f.write(f"{k}={v[0]}\n")
        # Responder HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"""<!DOCTYPE html><html><head><title>Listo</title></head><body style='font-family:sans-serif;padding:40px;'><h2>Autorizacion recibida</h2><p>Ya puedes cerrar esta pestana y volver a la terminal.</p></body></html>""")
        # Apagar servidor
        threading.Thread(target=self.server.shutdown, daemon=True).start()

with socketserver.TCPServer(("127.0.0.1", $PORT), H) as srv:
    srv.serve_forever()
PYTHON
SERVER_PID=$!

sleep 1

# === Abrir Safari con la URL ===
echo "==> Abriendo Safari con la URL de autorización..."
echo "    Autentica con leo.bassoco@kawiil.mx y aprueba los permisos."
echo ""
open "$AUTH_URL"

# Esperar que Polo autorice (server termina solo)
echo "    Esperando que completes el login en Safari..."
wait $SERVER_PID 2>/dev/null || true

# === Leer code del callback ===
if [[ ! -s "$TMP_FILE" ]]; then
  echo "ERROR: no recibí callback. ¿Cerraste Safari sin autorizar?" >&2
  rm -f "$TMP_FILE"
  exit 1
fi

# shellcheck disable=SC1090
source <(awk '{print $0}' "$TMP_FILE" | sed 's/^/CALLBACK_/')

if [[ "${CALLBACK_state:-}" != "$STATE" ]]; then
  echo "ERROR: state no coincide. Posible ataque CSRF." >&2
  rm -f "$TMP_FILE"
  exit 1
fi

if [[ -z "${CALLBACK_code:-}" ]]; then
  echo "ERROR: no recibí code. ${CALLBACK_error:-}: ${CALLBACK_error_description:-}" >&2
  rm -f "$TMP_FILE"
  exit 1
fi

rm -f "$TMP_FILE"

echo ""
echo "==> Code recibido. Intercambiando por tokens..."

# === Intercambiar code por tokens ===
TOKEN_URL="https://login.microsoftonline.com/${M365_KAWIIL_TENANT_ID}/oauth2/v2.0/token"

RESPONSE="$(curl -sS -X POST "$TOKEN_URL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "client_id=${M365_KAWIIL_CLIENT_ID}" \
  --data-urlencode "scope=Mail.ReadWrite Mail.Send Calendars.ReadWrite User.Read offline_access" \
  --data-urlencode "code=${CALLBACK_code}" \
  --data-urlencode "redirect_uri=${M365_KAWIIL_REDIRECT_URI}" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code_verifier=${CODE_VERIFIER}")"

if echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("access_token") else 1)'; then
  # OK, guardar
  echo "$RESPONSE" > "$TOKENS_FILE"
  chmod 600 "$TOKENS_FILE"
  EXPIRES="$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("expires_in", "?"))')"
  echo ""
  echo "OK. Tokens guardados en $TOKENS_FILE"
  echo "    access_token: válido por $EXPIRES segundos"
  echo "    refresh_token: válido ~90 días (auto-rotación al usar)"
  echo ""
  echo "Ya puedes usar los scripts:"
  echo "  m365-leer-correos.sh"
  echo "  m365-mandar-correo.sh"
  echo "  m365-calendario.sh"
else
  echo "ERROR al intercambiar code por token:" >&2
  echo "$RESPONSE" | python3 -m json.tool >&2
  exit 1
fi
