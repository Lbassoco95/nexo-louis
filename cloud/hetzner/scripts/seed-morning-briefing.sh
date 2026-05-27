#!/usr/bin/env bash
# seed-morning-briefing.sh — Agenda briefing matutino 7:00 CDMX (recurrente daily).
# Uso en VPS: sudo bash /opt/openclaw/scripts/seed-morning-briefing.sh

set -euo pipefail

HOME_OC="${HOME_OC:-/opt/openclaw}"
QUEUE="$HOME_OC/reminders/queue.jsonl"
MARKER='__morning_briefing__'

mkdir -p "$(dirname "$QUEUE")"
touch "$QUEUE"

if grep -q "$MARKER" "$QUEUE" 2>/dev/null; then
  echo "✓ Ya existe entrada de briefing matutino en $QUEUE"
  grep "$MARKER" "$QUEUE" || true
  exit 0
fi

fire_at=$(python3 -c "
from datetime import datetime, timedelta, timezone
tz = timezone(timedelta(hours=-6))
now = datetime.now(tz)
target = now.replace(hour=7, minute=0, second=0, microsecond=0)
if target <= now:
    target += timedelta(days=1)
print(target.isoformat())
")

created_at=$(python3 -c "
from datetime import datetime, timezone, timedelta
print(datetime.now(timezone(timedelta(hours=-6))).isoformat())
")

entry="{\"id\":\"morning-brief\",\"fire_at\":\"${fire_at}\",\"message\":\"${MARKER}\",\"channel\":\"telegram\",\"mode\":\"briefing\",\"recurrence\":\"daily\",\"created_at\":\"${created_at}\",\"source\":\"system\"}"

echo "$entry" >> "$QUEUE"
chmod 600 "$QUEUE" 2>/dev/null || true
echo "✓ Briefing matutino agendado — primer fire_at: $fire_at"
echo "  Queue: $QUEUE"
