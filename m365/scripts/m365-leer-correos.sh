#!/usr/bin/env bash
# m365-leer-correos.sh — Lee correos del inbox.
# Uso:
#   m365-leer-correos.sh kawiil [unread|all] [limit]
#   m365-leer-correos.sh kawiil unread 10
#   m365-leer-correos.sh kawiil all 20

set -euo pipefail

TENANT="${1:-kawiil}"
FILTER="${2:-unread}"  # unread o all
LIMIT="${3:-10}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN="$("$SCRIPT_DIR/m365-token.sh" "$TENANT")"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: no obtuve access_token" >&2
  exit 1
fi

QUERY="\$top=${LIMIT}&\$orderby=receivedDateTime desc&\$select=subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments"

if [[ "$FILTER" == "unread" ]]; then
  QUERY="${QUERY}&\$filter=isRead eq false"
fi

URL="https://graph.microsoft.com/v1.0/me/messages?${QUERY}"

RESP="$(curl -sS -H "Authorization: Bearer $TOKEN" -H "Accept: application/json" "$URL")"

python3 - <<PYTHON
import json, sys
d = json.loads('''$RESP''')
if "error" in d:
    print("ERROR:", d["error"], file=sys.stderr)
    sys.exit(1)

msgs = d.get("value", [])
if not msgs:
    print("(sin correos no leídos)" if "$FILTER" == "unread" else "(sin correos)")
    sys.exit(0)

for m in msgs:
    fr = m.get("from", {}).get("emailAddress", {})
    fr_str = f"{fr.get('name','?')} <{fr.get('address','?')}>"
    date = m.get("receivedDateTime", "?")
    subj = m.get("subject", "(sin asunto)")
    preview = (m.get("bodyPreview", "") or "")[:200].replace("\n", " ")
    attach = "📎" if m.get("hasAttachments") else "  "
    unread = "•" if not m.get("isRead") else " "
    print(f"{unread} {attach} [{date[:16]}] {fr_str}")
    print(f"      {subj}")
    print(f"      {preview}")
    print()
PYTHON
