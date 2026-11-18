#!/usr/bin/env bash
# verificar-donna.sh — diagnóstico de Donna en UN comando. Correr en el servidor:
#     bash /opt/louis/tools/verificar-donna.sh
# No imprime secretos (solo si existen). Sale con código 1 si algo falla.
OC="${OC:-/opt/openclaw}"; URL="${DONNA_URL:-http://127.0.0.1:3000}"; SUDO="${SUDO-sudo}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAIL=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$*"; FAIL=1; }
info() { printf "  · %s\n" "$*"; }
cnt()  { $SUDO grep -c "$1" "$2" 2>/dev/null || true; }

echo "1) Servicios"
for s in openclaw-gateway telegram-bridge scheduler slack-bridge; do
  if systemctl is-active --quiet "$s" 2>/dev/null; then ok "$s activo"
  elif [[ "$s" == "telegram-bridge" && -e "$OC/state/telegram_off" ]]; then info "telegram-bridge apagado a propósito (existe $OC/state/telegram_off)"
  else bad "$s NO activo (journalctl -u $s -n 30)"; fi
done

echo "2) Código instalado en $OC/scripts (versión reciente)"
[[ "$(cnt '_MESES_NUM_ES' "$OC/scripts/louis_core.py")" -ge 1 ]] && ok "arreglo de fechas" || bad "falta el arreglo de fechas → reinstalar louis_core.py"
[[ "$(cnt 'APP_HISTORY_FILE' "$OC/scripts/openclaw_gateway.py")" -ge 1 ]] && ok "historial de la app" || bad "falta el historial de la app → reinstalar openclaw_gateway.py"
[[ "$(cnt 'haiku-fallback' "$OC/scripts/louis_core.py")" -ge 1 ]] && ok "respaldo de conversación (Haiku)" || bad "falta el respaldo de conversación → reinstalar louis_core.py"

echo "3) Llaves (solo si existen, nunca el valor)"
DS=0
for f in "$OC/.env" "$OC/openclaw.env"; do
  n=$(cnt '^DEEPSEEK_API_KEY=.' "$f"); [[ "${n:-0}" -ge 1 ]] && DS=1
done
[[ $DS -eq 1 ]] && ok "DEEPSEEK_API_KEY presente" || bad "NO hay DEEPSEEK_API_KEY: el chat normal no puede responder (cae al respaldo)"
n=$(cnt '^ANTHROPIC_API_KEY=.' "$OC/openclaw.env"); [[ "${n:-0}" -ge 1 ]] && ok "ANTHROPIC_API_KEY presente" || info "sin ANTHROPIC_API_KEY en openclaw.env (puede estar en $OC/.env)"
TOKEN=$($SUDO grep -m1 '^OPENCLAW_GATEWAY_TOKEN=' "$OC/openclaw.env" 2>/dev/null | cut -d= -f2-)
[[ -n "$TOKEN" ]] && ok "token del gateway presente" || bad "no hay OPENCLAW_GATEWAY_TOKEN"

echo "3b) Kawiil Central (de aquí salen las cifras de tareas y proyectos)"
kc_has() { local v="$1" f n; for f in "$OC/openclaw.env" "$OC/.env" "$OC/credentials/kawiil-central.env"; do
             n=$(cnt "^$v=." "$f"); [[ "${n:-0}" -ge 1 ]] && return 0; done; return 1; }
for v in KAWIIL_CENTRAL_SUPABASE_URL KAWIIL_CENTRAL_SUPABASE_KEY KAWIIL_CENTRAL_DATABASE_URL; do
  if kc_has "$v"; then ok "$v presente"
  else bad "$v NO está en ningún archivo → sudo bash /opt/louis/scripts/setup-kawiil-central-creds.sh"; fi
done
python3 -c "import psycopg2" 2>/dev/null && ok "psycopg2 instalado" \
  || bad "falta psycopg2 (sin él no hay consultas SQL) → pip3 install --break-system-packages psycopg2-binary"
if [[ -n "$TOKEN" ]]; then
  KC=$(curl -s --max-time 40 -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"args":{}}' \
        "$URL/v1/tools/kawiil_central_estado" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("result",""))' 2>/dev/null)
  [[ -n "$KC" ]] && echo "$KC" | sed 's/^/  | /'
  if [[ -z "$KC" ]]; then bad "la consulta a Kawiil Central no devolvió nada"
  elif echo "$KC" | grep -q "✗\|❌"; then bad "Kawiil Central NO responde completo (revisa los ✗ de arriba): Donna no puede dar cifras reales"
  else ok "Kawiil Central responde con datos reales"; fi
fi

echo "3c) Otras integraciones (informativo: · = no configurada en este servidor)"
for v in TELEGRAM_BOT_TOKEN SLACK_BOT_TOKEN M365_KAWIIL_CLIENT_ID OP_SERVICE_ACCOUNT_TOKEN BITWARDEN_ENV_FILE PLAYWRIGHT_BROWSERS_PATH; do
  n=$(cnt "^$v=." "$OC/openclaw.env"); [[ "${n:-0}" -ge 1 ]] && ok "$v" || info "$v: no configurada"
done

echo "3d) Acceso a la app (usuario y contraseña)"
NU=$(PUSH_STATE_DIR="$OC/state" python3 -c "import sys; sys.path.insert(0,'$OC/scripts'); import app_auth as A; print(len(A.list_users()))" 2>/dev/null)
if [[ -z "$NU" ]]; then bad "no está instalado app_auth.py en $OC/scripts (la app aún pide token)"
elif [[ "$NU" -ge 1 ]]; then ok "$NU usuario(s) con contraseña (se guardan solo como hash)"
else bad "no hay ningún usuario con contraseña → python3 /opt/louis/tools/donna-passwd.py polo"; fi

echo "3e) Tableros (artefactos)"
[[ "$(cnt 'crear_artefacto' "$OC/scripts/louis_core.py")" -ge 1 && -f "$OC/scripts/app_artifacts.py" ]] \
  && ok "tableros instalados (app_artifacts.py + herramientas)" || bad "faltan los tableros → instalar app_artifacts.py y louis_core.py"
[[ "$(cnt 'v1/app/artefacto' "$OC/scripts/openclaw_gateway.py")" -ge 1 ]] && ok "endpoints de tableros" || bad "gateway sin endpoints de tableros → reinstalar openclaw_gateway.py"

echo "4) Conversación real (¿Ya estás ahí?)"
if [[ -n "$TOKEN" ]]; then
  ERR=$(mktemp); OUT=$(DONNA_URL="$URL" DONNA_TOKEN="$TOKEN" python3 "$HERE/donna-chat.py" "¿Ya estás ahí?" 2>"$ERR")
  MODEL=$(sed 's/\x1b\[[0-9;]*m//g' "$ERR" | grep -o 'modelo: [^)]*' | head -1); rm -f "$ERR"
  info "respuesta (primeros 300 caracteres): $(echo "$OUT" | head -c 300 | tr '\n' ' ')"
  info "${MODEL:-modelo: (no reportado)}"
  if [[ -z "$OUT" ]]; then bad "sin respuesta"
  elif echo "$OUT" | grep -q "⚠️"; then bad "el servidor devolvió un ERROR (ver registro, paso 6)"
  elif [[ -z "$MODEL" ]]; then bad "la respuesta no terminó bien (no llegó el cierre con el modelo)"
  elif echo "$OUT" | grep -q "VENCE / CON HORA\|Para HOY"; then bad "respondió con el BRIEFING en vez de conversar"
  elif echo "$MODEL" | grep -q "chat-error"; then bad "ningún modelo respondió (DeepSeek y el respaldo fallaron)"
  elif echo "$MODEL" | grep -q "fallback"; then bad "respondió con el RESPALDO (${MODEL#modelo: }): DeepSeek no está respondiendo"
  else ok "respuesta conversacional (${MODEL#modelo: })"; fi
fi

echo "5) Historial y aprendizaje"
H="$OC/spaces/general/app-history.jsonl"
[[ -s "$H" ]] && ok "app-history.jsonl con $(wc -l < "$H") mensajes (alimenta la memoria nocturna de las 23:00)" || bad "no existe app-history.jsonl (el chat de la app no se está guardando)"
[[ -f "$OC/state/last-memory-distill.json" ]] && info "última destilación: $($SUDO grep -o '"date": *"[^"]*"\|"resumen": *"[^"]*"' "$OC/state/last-memory-distill.json" | tr '\n' ' ')"

echo "6) Lo que dijo el modelo en los últimos intentos (registro del gateway)"
$SUDO grep -E "DeepSeek|Haiku de respaldo|Sin modelo|fallback|chat stream falló" "$OC/logs/openclaw-gateway.log" 2>/dev/null | tail -8 | cut -c1-230 | sed 's/^/  | /'

echo; [[ $FAIL -eq 0 ]] && echo "TODO BIEN ✅" || { echo "HAY PROBLEMAS ❌ (revisa los ✗ de arriba)"; exit 1; }
