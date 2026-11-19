#!/usr/bin/env bash
# diagnostico-conexion.sh — «no me deja conectar a Donna»: revisa servicio, archivos, puerto local, dominio, certificado y usuarios, y dice qué hacer.
#   bash ~/donna-deploy/cloud/hetzner/tools/diagnostico-conexion.sh
# No imprime secretos.
OC="${OC:-/opt/openclaw}"; DOM="${LOUIS_DOMAIN:-donna.kawiil.mx}"; PORT="${OPENCLAW_PORT:-3000}"; SUDO="${SUDO-sudo}"
P=0; ok(){ printf "  \033[32m✓\033[0m %s\n" "$*"; }; bad(){ printf "  \033[31m✗\033[0m %s\n" "$*"; P=1; }; hint(){ printf "      → %s\n" "$*"; }

echo "1) Servicio"
if systemctl is-active --quiet openclaw-gateway; then ok "openclaw-gateway activo"; else
  bad "openclaw-gateway NO está activo"; hint "mira por qué: sudo journalctl -u openclaw-gateway -n 30 --no-pager"; fi
# NRestarts es un contador HISTÓRICO (no se borra solo): solo es problema si el servicio lleva muy poco corriendo (se está cayendo ahora)
n=$(systemctl show openclaw-gateway -p NRestarts --value 2>/dev/null); pid=$(systemctl show openclaw-gateway -p MainPID --value 2>/dev/null)
up=$(ps -o etimes= -p "${pid:-0}" 2>/dev/null | tr -d ' '); up=${up:-0}
if [[ "${n:-0}" -gt 3 && "$up" -lt 45 ]]; then bad "lleva solo ${up}s corriendo y $n reinicios (se está cayendo al arrancar)"; hint "casi siempre es un archivo sin instalar: ver el punto 2"
elif [[ "${n:-0}" -gt 3 ]]; then ok "estable: lleva $((up/60)) min corriendo (el contador de $n reinicios es histórico; se limpia con: sudo systemctl reset-failed openclaw-gateway)"; fi

echo "2) Archivos instalados en $OC/scripts"
FALTA=0
for f in openclaw_gateway.py louis_core.py app_auth.py louis_push.py app_media.py app_translate.py ws_lite.py donna_voz.py app_chats.py app_trabajos.py app_artifacts.py patio_yoltik.py slack_digest.py; do
  [[ -s "$OC/scripts/$f" ]] || { bad "falta $f"; FALTA=1; }
done
for f in sw.js index.html manifest.webmanifest; do [[ -s "$OC/scripts/app/$f" ]] || { bad "falta app/$f"; FALTA=1; }; done
[[ $FALTA -eq 0 ]] && ok "todos los archivos están"
[[ $FALTA -eq 1 ]] && hint "reinstala todo: cd ~/donna-deploy && git pull origin claude/brave-knuth-8bloma && cd cloud/hetzner/services && sudo install -m 0755 -o polo -g polo *.py /opt/openclaw/scripts/ && sudo install -m 0644 -o polo -g polo app/* /opt/openclaw/scripts/app/ && sudo systemctl restart openclaw-gateway"

echo "3) El servidor responde por dentro (puerto $PORT)"
c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:$PORT/healthz")
[[ "$c" == 200 ]] && ok "salud local: HTTP 200" || { bad "salud local: HTTP $c (no responde)"; hint "reinicia y espera 10 s: sudo systemctl restart openclaw-gateway; sleep 10; vuelve a correr esto"; }
c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:$PORT/app/")
[[ "$c" == 200 ]] && ok "la app carga por dentro: HTTP 200" || bad "la app no carga por dentro: HTTP $c"

echo "4) El dominio $DOM desde internet"
IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null); DNS=$(getent hosts "$DOM" | awk '{print $1}' | head -1)
[[ -n "$DNS" ]] && ok "el dominio apunta a $DNS (este servidor: ${IP:-?})" || { bad "el dominio $DOM no resuelve"; hint "revisa el registro A en GoDaddy hacia ${IP:-la IP del servidor}"; }
[[ -n "$DNS" && -n "$IP" && "$DNS" != "$IP" ]] && { bad "el dominio apunta a $DNS pero este servidor es $IP"; hint "corrige el registro A en GoDaddy"; }
c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://$DOM/app/")
[[ "$c" == 200 ]] && ok "https://$DOM/app/ → HTTP 200" || { bad "https://$DOM/app/ → HTTP $c"; hint "si por dentro sí carga, el problema es Caddy: docker ps | grep -i caddy ; docker logs --tail 30 louis-caddy"; }
if command -v docker >/dev/null 2>&1; then
  $SUDO docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -qi caddy && ok "Caddy (proxy) activo" || { bad "Caddy no está corriendo"; hint "cd /opt/louis && sudo docker compose up -d   (o: sudo docker start louis-caddy)"; }
fi

echo "5) Usuarios y acceso"
NU=$(PUSH_STATE_DIR="$OC/state" python3 -c "import sys; sys.path.insert(0,'$OC/scripts'); import app_auth as A; print(','.join(A.list_users()))" 2>/dev/null)
[[ -n "$NU" ]] && ok "usuarios con contraseña: $NU" || { bad "no hay usuarios con contraseña"; hint "python3 ~/donna-deploy/cloud/hetzner/tools/donna-passwd.py polo"; }
hint "¿olvidaste la contraseña? → python3 ~/donna-deploy/cloud/hetzner/tools/donna-passwd.py polo   (pone una nueva; mínimo 12 caracteres)"
hint "«Demasiados intentos fallidos» → espera 15 minutos o: sudo systemctl restart openclaw-gateway"

echo "6) Lo último que dijo el servidor"
$SUDO journalctl -u openclaw-gateway -n 200 --no-pager 2>/dev/null | grep -i "MÓDULO OPCIONAL FALTANTE\|Traceback\|Error\|ModuleNotFound\|ImportError\|SyntaxError" | tail -6 | cut -c1-220 | sed 's/^/  | /'
echo; [[ $P -eq 0 ]] && echo "TODO BIEN ✅ — si la app aún no conecta: ciérrala por completo y ábrela dos veces; si sigue, borra los datos del sitio en Safari (Ajustes → Safari → Avanzado → Datos de sitios web → donna.kawiil.mx)" || echo "HAY PROBLEMAS ❌ — sigue las flechas →"
