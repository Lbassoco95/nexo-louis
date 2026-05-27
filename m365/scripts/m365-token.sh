#!/usr/bin/env bash
# m365-token.sh — devuelve un access_token válido de Microsoft Graph para tenant Kawiil.
# Refresca automáticamente si está expirado o cerca de expirar (margen 5 min).
# Uso: ACCESS_TOKEN="$(./m365-token.sh kawiil)"

set -euo pipefail

TENANT_KEY="${1:-kawiil}"  # kawiil o yoltik
CREDS_FILE="$HOME/.openclaw/credentials/m365-${TENANT_KEY}.env"
TOKENS_FILE="$HOME/.openclaw/credentials/m365-${TENANT_KEY}-tokens.json"

if [[ ! -f "$CREDS_FILE" ]]; then
  echo "Error: no encontré $CREDS_FILE. Corre oauth-${TENANT_KEY}.sh primero." >&2
  exit 1
fi
if [[ ! -f "$TOKENS_FILE" ]]; then
  echo "Error: no encontré $TOKENS_FILE. Corre oauth-${TENANT_KEY}.sh primero." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CREDS_FILE"

# Variables convertidas: M365_KAWIIL_CLIENT_ID, etc.
PREFIX="M365_$(echo "$TENANT_KEY" | tr '[:lower:]' '[:upper:]')"
CLIENT_ID="$(eval echo "\$${PREFIX}_CLIENT_ID")"
TENANT_ID="$(eval echo "\$${PREFIX}_TENANT_ID")"

# Leer tokens actuales y verificar expiración
python3 - "$TOKENS_FILE" <<'PYTHON'
import sys, json, time
path = sys.argv[1]
with open(path) as f:
    d = json.load(f)
issued = d.get("__issued_at", 0)
expires_in = d.get("expires_in", 0)
now = int(time.time())
# refrescar si quedan menos de 5 min
if (issued + expires_in - now) > 300 and d.get("access_token"):
    print(d["access_token"])
    sys.exit(0)
sys.exit(42)  # señal: hay que refrescar
PYTHON

# Si llegamos acá, no había token o estaba por expirar — el python anterior salió 42
if [[ $? -ne 0 ]]; then
  : # caer al refresh
fi

# Refresh
REFRESH_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("refresh_token",""))' "$TOKENS_FILE")"
if [[ -z "$REFRESH_TOKEN" ]]; then
  echo "Error: no hay refresh_token en $TOKENS_FILE" >&2
  exit 1
fi

TOKEN_URL="https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token"
RESPONSE="$(curl -sS -X POST "$TOKEN_URL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "scope=Mail.ReadWrite Mail.Send Calendars.ReadWrite User.Read offline_access" \
  --data-urlencode "refresh_token=${REFRESH_TOKEN}" \
  --data-urlencode "grant_type=refresh_token")"

# Guardar nuevos tokens con timestamp
python3 - "$TOKENS_FILE" <<PYTHON
import json, sys, time
data = json.loads('''$RESPONSE''')
if not data.get("access_token"):
    print("Refresh falló:", data, file=sys.stderr)
    sys.exit(1)
data["__issued_at"] = int(time.time())
with open(sys.argv[1], "w") as f:
    json.dump(data, f)
print(data["access_token"])
PYTHON
