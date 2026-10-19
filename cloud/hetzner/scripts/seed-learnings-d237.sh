#!/usr/bin/env bash
# seed-learnings-d237.sh — Siembra en LEARNINGS.md las lecciones de la sesión D237/Polos
# (estilo, fuentes, agentes reales, búsqueda web). Idempotente: no duplica entradas.
#
# Uso (en Hetzner):  sudo bash seed-learnings-d237.sh
# Louis las carga como REGLAS VINCULANTES en cada respuesta (ver LEARNINGS.md en el prompt).

set -uo pipefail

SYSTEM_USER="${SYSTEM_USER:-polo}"
FILE="/opt/openclaw/spaces/general/LEARNINGS.md"
FECHA="2026-10-19"
mkdir -p "$(dirname "$FILE")"
touch "$FILE"

add() {  # add <marca-única> <tema> <regla> <contexto>
  local mark="$1" topic="$2" rule="$3" ctx="$4"
  if grep -qF "[seed:$mark]" "$FILE"; then
    echo "  = ya existe: $mark"
    return
  fi
  printf '\n- [%s] [seed:%s] %s: %s\n  Contexto: %s\n' "$FECHA" "$mark" "$topic" "$rule" "$ctx" >> "$FILE"
  echo "  + agregado: $mark"
}

add estilo-corto \
  "Estilo de respuesta" \
  "Conclusión primero, máx. ~8 líneas, sin tablas ni separadores ni títulos en MAYÚSCULAS ni relleno; casi sin emojis; el detalle largo va en documento HTML." \
  "Polo dijo del análisis D237: 'la forma de escribir o dar las respuestas no me gusta'."

add fuentes-citadas \
  "Fuentes de cada dato" \
  "Cita artículo/URL leído en esta conversación; separa Verificado / Inferencia mía / Por confirmar; nunca digas 'completo' o 'definitivo' si no leíste el texto íntegro; cierra con 'Fuentes:' y 'No verifiqué:'." \
  "Polo pidió saber de dónde sacaba Donna las recomendaciones del análisis D237."

add agentes-reales \
  "Asignar solo a agentes y personas reales" \
  "Antes de asignar tareas usa listar_agentes y PEOPLE.md. Hoy existen kawiil-erp, yoltik, vizum, dazon, marketing, investigacion, general, amatl (documentos/contratos), nelli (compliance), tepantli, matiox, rrhh, cultura, comercial. NO existen 'tochtli' ni 'balam'. Investigación = kawiil-investigacion; cumplimiento = kawiil-nelli. Plazos y prioridades son propuesta mía." \
  "En el análisis D237 asigné a Tochtli y Balam, que no son agentes, y fechas como si fueran del decreto."

add calculo-ahorro \
  "Cálculos fiscales" \
  "Ahorro de ISR = deducción × tasa (30% personas morales). Una deducción adicional de 25% sobre un gasto G ahorra G × 25% × 30%, no G × 25%. Muestra la fórmula y marca los ejemplos como ilustrativos." \
  "Error en el ejemplo de ahorro del D237 (\$12,500 en vez de \$3,750 sobre \$50,000)."

add se-vs-sep \
  "Siglas y fechas" \
  "SE = Secretaría de Economía; SEP = Secretaría de Educación Pública. Consulta la hora/fecha actual (reloj) antes de escribir 'hoy' o fechas relativas." \
  "En el D237 escribí 'SEP (Secretaría de Economía)' y 'HOY 18-oct' cuando era 19-oct."

add web-bloqueada \
  "Búsqueda web bloqueada" \
  "Uso web_buscar y web_leer. Si una página bloquea (403, CAPTCHA, vacía) web_leer reintenta con el browser; si dice BLOQUEADO uso navegador_agente. No evado CAPTCHAs: busco otra fuente y digo qué no pude leer. Nunca presento como leído lo que no leí." \
  "Segunda página del decreto D237 con validación de seguridad."

chown "$SYSTEM_USER":"$SYSTEM_USER" "$FILE" 2>/dev/null || true
echo
echo "Tamaño actual de LEARNINGS.md: $(wc -c < "$FILE") bytes (el prompt carga hasta 8000 chars; si se pasa, depurar entradas viejas)."
