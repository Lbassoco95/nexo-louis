#!/usr/bin/env bash
# m365-calendario.sh — Lista eventos del calendario.
# Uso:
#   m365-calendario.sh kawiil hoy
#   m365-calendario.sh kawiil semana
#   m365-calendario.sh kawiil manana

set -euo pipefail

TENANT="${1:-kawiil}"
RANGE="${2:-hoy}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN="$("$SCRIPT_DIR/m365-token.sh" "$TENANT")"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: no obtuve access_token" >&2
  exit 1
fi

# Calcular fechas en TZ Mexico/CDMX
read START END <<<"$(python3 - "$RANGE" <<'PYTHON'
import sys
from datetime import datetime, timedelta, timezone
range_ = sys.argv[1]
# Hora local Mexico = UTC-6 (sin DST)
TZ = timezone(timedelta(hours=-6))
now = datetime.now(TZ)
today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
today_end = today_start + timedelta(days=1)

if range_ == "hoy":
    s, e = today_start, today_end
elif range_ == "manana":
    s, e = today_start + timedelta(days=1), today_start + timedelta(days=2)
elif range_ == "semana":
    s, e = today_start, today_start + timedelta(days=7)
elif range_ == "mes":
    s, e = today_start, today_start + timedelta(days=30)
else:
    s, e = today_start, today_end

# Microsoft Graph espera ISO 8601 con timezone
print(s.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), end=" ")
print(e.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
PYTHON
)"

URL="https://graph.microsoft.com/v1.0/me/calendarview?startDateTime=${START}&endDateTime=${END}&\$orderby=start/dateTime&\$top=50&\$select=subject,start,end,location,bodyPreview,organizer,attendees,isOnlineMeeting,onlineMeeting"

RESP="$(curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  -H "Prefer: outlook.timezone=\"Central Standard Time (Mexico)\"" \
  "$URL")"

python3 - <<PYTHON
import json, sys
d = json.loads('''$RESP''')
if "error" in d:
    print("ERROR:", d["error"], file=sys.stderr)
    sys.exit(1)

events = d.get("value", [])
if not events:
    print("(sin eventos en este rango)")
    sys.exit(0)

for e in events:
    start = e.get("start", {}).get("dateTime", "?")[:16].replace("T", " ")
    end = e.get("end", {}).get("dateTime", "?")[:16].replace("T", " ")[-5:]
    subj = e.get("subject", "(sin título)")
    loc = (e.get("location", {}) or {}).get("displayName", "") or ""
    org = (e.get("organizer", {}) or {}).get("emailAddress", {}).get("name", "?")
    online = "🎥 " if e.get("isOnlineMeeting") else ""
    print(f"[{start} → {end}] {online}{subj}")
    if loc:
        print(f"   📍 {loc}")
    print(f"   👤 {org}")
    attendees = e.get("attendees", [])
    if attendees:
        names = [a.get("emailAddress", {}).get("name", "?") for a in attendees[:5]]
        print(f"   👥 {', '.join(names)}{'...' if len(attendees) > 5 else ''}")
    print()
PYTHON
