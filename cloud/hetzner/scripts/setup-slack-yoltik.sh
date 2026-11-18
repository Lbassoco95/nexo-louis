#!/usr/bin/env bash
# setup-slack-yoltik.sh — guarda los tokens de la app «Donna Yoltik» en el servidor.
#   • xoxp- (User OAuth Token): Donna ve TODO lo que ve Polo en Yoltik (canales, privados, DMs, grupos).
#   • xoxb- (Bot User OAuth Token): opcional; solo canales donde Donna esté invitada.
# Uso:  sudo bash setup-slack-yoltik.sh        (si el pegado no entra: sudo VISIBLE=1 bash setup-slack-yoltik.sh)
# Enter en un campo = conservar lo que ya está. No toca los tokens de Kawiil.
set -uo pipefail
SYSTEM_USER="${SYSTEM_USER:-polo}"; F=/opt/openclaw/credentials/slack.env
[[ -f "$F" ]] || { echo "✗ No existe $F (¿el servidor ya tenía Slack de Kawiil configurado?)"; exit 1; }
TTY=/dev/tty; [[ -r $TTY ]] || TTY=/dev/stdin
pedir() {  # $1=etiqueta $2=variable
  local v
  if [[ "${VISIBLE:-0}" == "1" ]]; then read -rp "$1: " v < "$TTY"; else read -rsp "$1 (no se ve al pegar): " v < "$TTY"; echo; fi
  v="${v//[$'\r\n ']/}"
  [[ -z "$v" ]] && { echo "  · sin cambios"; return; }
  echo "  · recibí ${#v} caracteres"; printf -v "$2" '%s' "$v"
}
cur() { grep -m1 "^$1=" "$F" | cut -d= -f2- | tr -d "\"'"; }
UT=""; BT=""
pedir "User OAuth Token xoxp- (ve TODO lo que ves tú)  [Enter = omitir/conservar]" UT
pedir "Bot  User OAuth Token xoxb- (opcional)           [Enter = omitir/conservar]" BT
[[ -z "$UT" || "$UT" == xoxp-* ]] || { echo "✗ El de usuario debe empezar con xoxp-"; exit 1; }
[[ -z "$BT" || "$BT" == xoxb-* ]] || { echo "✗ El de bot debe empezar con xoxb-"; exit 1; }
KAW_B=$(cur SLACK_BOT_TOKEN); KAW_U=$(cur SLACK_USER_TOKEN)
for t in "$UT" "$BT"; do [[ -z "$t" || ( "$t" != "$KAW_B" && "$t" != "$KAW_U" ) ]] || { echo "✗ Ese token es el de Kawiil, no el de Yoltik."; exit 1; }; done
[[ -n "$UT$BT$(cur SLACK_USER_TOKEN_YOLTIK)$(cur SLACK_BOT_TOKEN_YOLTIK)" ]] || { echo "✗ No hay ningún token (nuevo ni guardado)."; exit 1; }

probar() {  # $1=etiqueta $2=token
  [[ -z "$2" ]] && return 0
  ST="$2" python3 - "$1" <<'PY'
import os, sys
try:
    from slack_sdk import WebClient
except ImportError:
    print("✗ falta slack_sdk (pip3 install --break-system-packages slack-sdk)"); sys.exit(1)
c = WebClient(token=os.environ["ST"]); et = sys.argv[1]
try:
    a = c.auth_test()
except Exception as e:
    print(f"✗ {et}: Slack rechazó el token: {str(e)[:150]}"); sys.exit(1)
print(f"  ✓ {et}: workspace «{a.get('team')}» · identidad: {a.get('user')}")
tipos = "public_channel,private_channel,mpim,im" if et == "usuario" else "public_channel,private_channel"
n, cursor, falt = {}, None, []
try:
    for _ in range(10):
        kw = {"types": tipos, "limit": 200, "exclude_archived": True}
        if cursor: kw["cursor"] = cursor
        r = c.conversations_list(**kw)
        for x in r.get("channels", []):
            if x.get("is_member") or x.get("is_im") or x.get("is_mpim"):
                k = "DM" if x.get("is_im") else "grupo" if x.get("is_mpim") else "privado" if x.get("is_private") else "público"
                n[k] = n.get(k, 0) + 1
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor: break
except Exception as e:
    falt.append(str(e)[:100])
print("    ve:", ", ".join(f"{v} {k}" for k, v in n.items()) if n else "(ninguna conversación todavía)")
if falt: print("    ! error listando conversaciones:", falt[0], "→ revisa los permisos en OAuth & Permissions y reinstala la app")
try: c.users_list(limit=1)
except Exception: print("    ! falta users:read (los nombres saldrán como ids)")
PY
}
OK=0
probar "usuario" "$UT" || OK=1
probar "bot" "$BT" || OK=1
[[ $OK -eq 0 ]] || { echo "✗ No guardé nada: corrige el token y vuelve a correr el script."; exit 1; }

umask 077
cp -a "$F" "$F.bak-yoltik" 2>/dev/null
grep -v '^SLACK_USER_TOKEN_YOLTIK=\|^SLACK_BOT_TOKEN_YOLTIK=' "$F" > "$F.new"
U="${UT:-$(cur SLACK_USER_TOKEN_YOLTIK)}"; B="${BT:-$(cur SLACK_BOT_TOKEN_YOLTIK)}"
[[ -n "$U" ]] && echo "SLACK_USER_TOKEN_YOLTIK=$U" >> "$F.new"
[[ -n "$B" ]] && echo "SLACK_BOT_TOKEN_YOLTIK=$B" >> "$F.new"
mv "$F.new" "$F"; chmod 0600 "$F"; chown "$SYSTEM_USER":"$SYSTEM_USER" "$F"
echo "✓ Guardado en $F (respaldo: $F.bak-yoltik)"
systemctl restart openclaw-gateway scheduler slack-bridge && echo "✓ Servicios reiniciados"
