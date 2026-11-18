#!/usr/bin/env bash
# setup-slack-yoltik.sh — guarda el token xoxb- de la app «Donna Yoltik» (workspace de Yoltik) en el servidor.
# Uso:  sudo bash setup-slack-yoltik.sh        (si el pegado no entra: sudo VISIBLE=1 bash setup-slack-yoltik.sh)
# No toca el token de Kawiil: agrega SLACK_BOT_TOKEN_YOLTIK a /opt/openclaw/credentials/slack.env.
set -uo pipefail
SYSTEM_USER="${SYSTEM_USER:-polo}"; F=/opt/openclaw/credentials/slack.env
[[ -f "$F" ]] || { echo "✗ No existe $F (¿el servidor ya tenía Slack de Kawiil configurado?)"; exit 1; }
TTY=/dev/tty; [[ -r $TTY ]] || TTY=/dev/stdin
if [[ "${VISIBLE:-0}" == "1" ]]; then read -rp "Token xoxb- de Donna Yoltik: " T < "$TTY"
else read -rsp "Token xoxb- de Donna Yoltik (no se ve al pegar): " T < "$TTY"; echo; fi
T="${T//[$'\r\n ']/}"
[[ -n "$T" ]] && echo "  · recibí ${#T} caracteres" || { echo "✗ No recibí nada (prueba: sudo VISIBLE=1 bash $0)"; exit 1; }
[[ "$T" == xoxb-* ]] || { echo "✗ Debe empezar con xoxb- (es el «Bot User OAuth Token», no el xapp- ni el xoxp-)"; exit 1; }
KAW=$(grep -m1 '^SLACK_BOT_TOKEN=' "$F" | cut -d= -f2- | tr -d "\"'")
[[ "$T" != "$KAW" ]] || { echo "✗ Ese es el token de Kawiil, no el de Yoltik."; exit 1; }

echo "Probando contra Slack…"
ST="$T" python3 - <<'PY' || exit 1
import os, sys
try:
    from slack_sdk import WebClient
except ImportError:
    print("✗ falta slack_sdk (pip3 install --break-system-packages slack-sdk)"); sys.exit(1)
c = WebClient(token=os.environ["ST"])
try:
    a = c.auth_test()
except Exception as e:
    print("✗ Slack rechazó el token:", str(e)[:150]); sys.exit(1)
print(f"  ✓ workspace: {a.get('team')}  ·  bot: {a.get('user')}")
falt = []
for nombre, fn in (("channels:read", lambda: c.conversations_list(types="public_channel", limit=1)),
                   ("groups:read",   lambda: c.conversations_list(types="private_channel", limit=1)),
                   ("users:read",    lambda: c.users_list(limit=1))):
    try: fn()
    except Exception as e: falt.append(nombre)
if falt: print("  ! faltan permisos:", ", ".join(falt), "→ agrégalos en OAuth & Permissions y reinstala la app"); 
n = 0
try:
    cs = c.conversations_list(types="public_channel,private_channel", limit=200).get("channels", [])
    n = sum(1 for x in cs if x.get("is_member"))
    print(f"  · Donna está invitada en {n} canal(es)" + ("" if n else "  → en cada canal escribe /invite @Donna"))
except Exception: pass
PY

umask 077
cp -a "$F" "$F.bak-yoltik" 2>/dev/null
grep -v '^SLACK_BOT_TOKEN_YOLTIK=' "$F" > "$F.new"; echo "SLACK_BOT_TOKEN_YOLTIK=$T" >> "$F.new"
mv "$F.new" "$F"; chmod 0600 "$F"; chown "$SYSTEM_USER":"$SYSTEM_USER" "$F"
echo "✓ Guardado SLACK_BOT_TOKEN_YOLTIK en $F (respaldo: $F.bak-yoltik)"
systemctl restart openclaw-gateway scheduler slack-bridge && echo "✓ Servicios reiniciados"
