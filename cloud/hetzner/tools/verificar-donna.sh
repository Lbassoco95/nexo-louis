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
  if systemctl is-active --quiet "$s" 2>/dev/null; then ok "$s activo"; else bad "$s NO activo (journalctl -u $s -n 30)"; fi
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
