#!/usr/bin/env bash
# mac-heartbeat.sh — Reporta a Hetzner el estado de la Mac cada 30s.
# Envía: timestamp, batería (%), AC power, SSID, hostname, uptime.
# Sube un JSON tiny vía SSH a /opt/openclaw/state/mac_heartbeat.json
# para que Louis sepa si la Mac está prendida y con qué autonomía.
#
# Variables (las pone el plist mac-install-louis-sync.sh):
#   LOUIS_REMOTE_HOST  ej: 204.168.131.21
#   LOUIS_REMOTE_USER  ej: polo
#   LOUIS_SSH_KEY      ej: ~/.ssh/id_ed25519

set -uo pipefail

: "${LOUIS_REMOTE_HOST:?LOUIS_REMOTE_HOST no definido}"
: "${LOUIS_REMOTE_USER:?LOUIS_REMOTE_USER no definido}"
: "${LOUIS_SSH_KEY:?LOUIS_SSH_KEY no definido}"

REMOTE="${LOUIS_REMOTE_USER}@${LOUIS_REMOTE_HOST}"
SSH_OPTS="-i $LOUIS_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=15 -o BatchMode=yes"

# --- Recolecta estado ---
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
HOST=$(hostname -s 2>/dev/null || echo "mac")
UPTIME=$(uptime | awk -F'up ' '{print $2}' | awk -F',' '{print $1}' | xargs)

# Batería en macOS via pmset
BATT_PCT="null"
BATT_AC="false"
BATT_STATE="unknown"
if command -v pmset >/dev/null 2>&1; then
  BATT_RAW=$(pmset -g batt 2>/dev/null || echo "")
  if [[ "$BATT_RAW" == *"AC Power"* ]]; then
    BATT_AC="true"
  fi
  PCT=$(echo "$BATT_RAW" | grep -oE '[0-9]+%' | head -1 | tr -d '%')
  if [[ -n "$PCT" ]]; then
    BATT_PCT="$PCT"
  fi
  if echo "$BATT_RAW" | grep -qi "charging"; then
    BATT_STATE="charging"
  elif echo "$BATT_RAW" | grep -qi "discharging"; then
    BATT_STATE="discharging"
  elif echo "$BATT_RAW" | grep -qi "charged"; then
    BATT_STATE="charged"
  fi
fi

# SSID (red WiFi)
SSID="unknown"
if command -v networksetup >/dev/null 2>&1; then
  WIFI_IFACE=$(networksetup -listallhardwareports 2>/dev/null | awk '/Wi-Fi|AirPort/{getline; print $2}' | head -1)
  if [[ -n "$WIFI_IFACE" ]]; then
    SSID=$(networksetup -getairportnetwork "$WIFI_IFACE" 2>/dev/null | awk -F': ' '{print $2}')
    [[ -z "$SSID" ]] && SSID="unknown"
  fi
fi

# IP pública (rápido, cached por launchd)
PUB_IP="unknown"
if command -v curl >/dev/null 2>&1; then
  PUB_IP=$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || echo "unknown")
fi

# --- JSON payload ---
TMP_JSON=$(mktemp /tmp/mac_heartbeat.XXXXXX.json)
cat > "$TMP_JSON" <<JSON
{
  "ts": "$TS",
  "hostname": "$HOST",
  "uptime": "$UPTIME",
  "battery_pct": $BATT_PCT,
  "on_ac_power": $BATT_AC,
  "battery_state": "$BATT_STATE",
  "ssid": "$SSID",
  "public_ip": "$PUB_IP"
}
JSON

# --- Sube a Hetzner ---
# Usa scp atómico: sube a .tmp, luego mv. Si la conexión falla, la app sigue.
ssh $SSH_OPTS "$REMOTE" "mkdir -p /opt/openclaw/state" 2>/dev/null || {
  rm -f "$TMP_JSON"
  exit 0
}

scp $SSH_OPTS "$TMP_JSON" "$REMOTE:/opt/openclaw/state/mac_heartbeat.json.tmp" 2>/dev/null && \
  ssh $SSH_OPTS "$REMOTE" "mv /opt/openclaw/state/mac_heartbeat.json.tmp /opt/openclaw/state/mac_heartbeat.json"

rm -f "$TMP_JSON"
