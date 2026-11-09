#!/usr/bin/env bash
# Pruebas de la retención de respaldos y del script de sync (carpetas temporales; no toca nada real).
#   bash cloud/hetzner/tests/test_respaldos.sh
set -uo pipefail
export TZ=UTC
AQUI="$(cd "$(dirname "$0")" && pwd)"
PRUNE="$AQUI/../sync/prune-backups.sh"
APPLY="$AQUI/../sync/hetzner-apply.sh"
FALLOS=0; PRUEBAS=0
ok()   { PRUEBAS=$((PRUEBAS+1)); echo "  ✓ $1"; }
mal()  { PRUEBAS=$((PRUEBAS+1)); FALLOS=$((FALLOS+1)); echo "  ✗ $1"; }
igual(){ if [[ "$2" == "$3" ]]; then ok "$1"; else mal "$1 (esperado '$3', obtenido '$2')"; fi; }
entre(){ if (( $2 >= $3 && $2 <= $4 )); then ok "$1 ($2)"; else mal "$1 ($2 fuera de $3..$4)"; fi; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── Poda escalonada ────────────────────────────────────────────────────────────────
echo "== prune-backups.sh"
NOW=$(date -d "2026-11-09 00:00:00" +%s)
B="$TMP/b"; mkdir -p "$B"
# un snapshot cada 5 min durante 40 días (11 520 archivos) + archivos que NO deben tocarse
{ for ((k=0; k<11520; k++)); do echo "@$((NOW - k*300))"; done; } | date -f - +%Y%m%d%H%M%S | while read -r t; do : > "$B/spaces-$t.tar.gz"; done
: > "$B/pre-seed-20260525170622.tar.gz"; : > "$B/full-202605251200.tar.gz"; : > "$B/spaces-manual-20260101-000000.tgz"; : > "$B/spaces-20260101.tar.gz"
igual "se generaron 11 520 snapshots" "$(find "$B" -name 'spaces-??????????????.tar.gz' | wc -l)" "11520"

bash "$PRUNE" --dry-run --dir "$B" --now "$NOW" | grep -q "SIMULACIÓN" && ok "--dry-run avisa que es simulación" || mal "--dry-run sin aviso"
igual "--dry-run no borra nada" "$(find "$B" -name 'spaces-??????????????.tar.gz' | wc -l)" "11520"

bash "$PRUNE" --dir "$B" --now "$NOW" >/dev/null
RESTAN=$(find "$B" -name 'spaces-??????????????.tar.gz' | wc -l)
entre "quedan ~455 snapshots (antes 11 520)" "$RESTAN" 440 470
# últimas 24 h: todos
RECIENTES=0; for ((k=0; k<288; k++)); do t=$(date -d "@$((NOW - k*300))" +%Y%m%d%H%M%S); [[ -f "$B/spaces-$t.tar.gz" ]] && RECIENTES=$((RECIENTES+1)); done
igual "las últimas 24 h se conservan completas (288)" "$RECIENTES" "288"
# de 2 a 6 días atrás: exactamente uno por hora
MAL_H=0; for d in 2026-11-03 2026-11-04 2026-11-05 2026-11-06 2026-11-07; do for h in $(seq -w 0 23); do n=$(ls "$B" | grep -c "^spaces-${d//-/}${h}[0-9]\{4\}\.tar\.gz$"); [[ "$n" == "1" ]] || MAL_H=$((MAL_H+1)); done; done
igual "de 2 a 6 días: exactamente 1 por hora" "$MAL_H" "0"
# de 8 a 28 días atrás: exactamente uno por día
MAL_D=0; for k in $(seq 8 28); do d=$(date -d "@$((NOW - k*86400))" +%Y%m%d); n=$(ls "$B" | grep -c "^spaces-${d}[0-9]\{6\}\.tar\.gz$"); [[ "$n" == "1" ]] || MAL_D=$((MAL_D+1)); done
igual "de 8 a 28 días: exactamente 1 por día" "$MAL_D" "0"
igual "nada de más de 30 días" "$(find "$B" -name 'spaces-2026100[0-9]*.tar.gz' -o -name 'spaces-202609*.tar.gz' | wc -l)" "0"
[[ -f "$B/pre-seed-20260525170622.tar.gz" && -f "$B/full-202605251200.tar.gz" && -f "$B/spaces-manual-20260101-000000.tgz" && -f "$B/spaces-20260101.tar.gz" ]] && ok "archivos con otros nombres intactos" || mal "se tocó un archivo ajeno"
bash "$PRUNE" --dir "$B" --now "$NOW" >/dev/null; igual "es idempotente (segunda pasada no cambia nada)" "$(find "$B" -name 'spaces-??????????????.tar.gz' | wc -l)" "$RESTAN"

# --min-keep protege los más recientes aunque la política los borraría
B2="$TMP/b2"; mkdir -p "$B2"; for k in $(seq 0 99); do : > "$B2/spaces-$(date -d "@$((NOW - k*300 - 90*86400))" +%Y%m%d%H%M%S).tar.gz"; done
bash "$PRUNE" --dir "$B2" --now "$NOW" --min-keep 12 >/dev/null
igual "--min-keep conserva los 12 más recientes aunque sean viejísimos" "$(ls "$B2" | wc -l)" "12"
# modo emergencia: política más corta
B3="$TMP/b3"; mkdir -p "$B3"; { for ((k=0; k<11520; k++)); do echo "@$((NOW - k*300))"; done; } | date -f - +%Y%m%d%H%M%S | while read -r t; do : > "$B3/spaces-$t.tar.gz"; done
bash "$PRUNE" --emergency --dir "$B3" --now "$NOW" >/dev/null
entre "emergencia deja pocos (72 + 18 + 6)" "$(ls "$B3" | wc -l)" 80 120
[[ -f "$B3/spaces-$(date -d "@$NOW" +%Y%m%d%H%M%S).tar.gz" ]] && ok "emergencia conserva el más reciente" || mal "emergencia borró el más reciente"
# entradas inválidas
bash "$PRUNE" --dir "/" >/dev/null 2>&1; igual "rechaza el directorio raíz" "$?" "2"
bash "$PRUNE" --dir "$B" --min-keep abc >/dev/null 2>&1; igual "rechaza números inválidos" "$?" "2"
bash "$PRUNE" --dir "$TMP/no-existe" >/dev/null 2>&1; igual "rechaza un directorio inexistente" "$?" "2"

# ── hetzner-apply.sh (rsync real) ─────────────────────────────────────────────────
echo "== hetzner-apply.sh"
command -v rsync >/dev/null || { echo "  (sin rsync: se omiten estas pruebas)"; echo; echo "$((PRUEBAS-FALLOS))/$PRUEBAS pruebas OK"; exit $FALLOS; }
S="$TMP/sync/spaces"; D="$TMP/dst/spaces"; BK="$TMP/bk"; MK="$TMP/sync/.last-apply"; mkdir -p "$S" "$D" "$BK"
export LOUIS_SYNC_SRC="$S" LOUIS_SYNC_DST="$D" LOUIS_BACKUPS="$BK" LOUIS_SYNC_LOCK="$TMP/lock" LOUIS_SYNC_MARKER="$MK"
snaps() { find "$BK" -name 'spaces-??????????????.tar.gz' | wc -l; }
correr() { bash "$APPLY" 2>&1; }

echo "contenido v1" > "$S/agenda.md"; touch -d "5 minutes ago" "$S/agenda.md"; echo "viejo" > "$D/existente.md"
SALIDA=$(correr); igual "1ª corrida con contenido nuevo: 1 snapshot" "$(snaps)" "1"
grep -q "apply OK" <<<"$SALIDA" && ok "aplicó el delta" || mal "no aplicó: $SALIDA"
igual "el contenido llegó al destino" "$(cat "$D/agenda.md" 2>/dev/null)" "contenido v1"
[[ -f "$MK" ]] && ok "dejó el marcador" || mal "sin marcador"

sleep 1.1; SALIDA=$(correr); igual "2ª corrida sin nada nuevo: no hay snapshot nuevo" "$(snaps)" "1"
[[ -z "$SALIDA" ]] && ok "y no escribe en el log" || mal "escribió algo: $SALIDA"

# solo cambia la fecha (mismo contenido): antes generaba snapshot cada 5 min
touch -d "+1 minute" "$S/agenda.md"; SALIDA=$(correr)
igual "solo cambió la fecha: NO hay snapshot" "$(snaps)" "1"
grep -q "aplicando" <<<"$SALIDA" && mal "aplicó por una simple fecha" || ok "no aplica por una simple fecha"
sleep 1.1; SALIDA=$(correr); [[ -z "$SALIDA" ]] && ok "y el marcador evita revisarlo otra vez" || mal "volvió a trabajar: $SALIDA"

# cambio real de contenido
sleep 1.1; echo "contenido v2" > "$S/agenda.md"; touch -d "+2 minutes" "$S/agenda.md"; sleep 1.1
SALIDA=$(correr); igual "cambio real: un snapshot más" "$(snaps)" "2"
igual "y el destino se actualizó" "$(cat "$D/agenda.md")" "contenido v2"
# archivo nuevo
sleep 1.1; echo "otro" > "$S/nuevo.md"; touch -d "+3 minutes" "$S/nuevo.md"; correr >/dev/null
igual "archivo nuevo: otro snapshot" "$(snaps)" "3"; [[ -f "$D/nuevo.md" ]] && ok "y llegó al destino" || mal "no llegó"

# origen VACÍO: el caso real de louis-prod (el sync desde la Mac dejó de entregar archivos). Antes: error de aritmética en
# cada corrida + un snapshot completo cada 5 minutos para siempre. Ahora: no hace nada y no escribe nada en el log.
S2="$TMP/vacio/spaces"; D2="$TMP/vacio-dst/spaces"; BK2="$TMP/vacio-bk"; mkdir -p "$S2" "$D2" "$BK2"; echo "estable" > "$D2/a.md"
SALIDA=$(LOUIS_SYNC_SRC="$S2" LOUIS_SYNC_DST="$D2" LOUIS_BACKUPS="$BK2" LOUIS_SYNC_MARKER="$TMP/vacio/.last-apply" LOUIS_SYNC_LOCK="$TMP/lock-vacio" bash "$APPLY" 2>&1)
igual "origen vacío: ningún snapshot" "$(find "$BK2" -name 'spaces-*.tar.gz' | wc -l)" "0"
[[ -z "$SALIDA" ]] && ok "origen vacío: no escribe nada en el log (antes: error de aritmética cada 5 min)" || mal "origen vacío escribió: $SALIDA"
for i in 1 2 3; do LOUIS_SYNC_SRC="$S2" LOUIS_SYNC_DST="$D2" LOUIS_BACKUPS="$BK2" LOUIS_SYNC_MARKER="$TMP/vacio/.last-apply" LOUIS_SYNC_LOCK="$TMP/lock-vacio" bash "$APPLY" >/dev/null 2>&1; sleep 0.2; done
igual "origen vacío, 4 corridas seguidas: siguen sin snapshots" "$(find "$BK2" -name 'spaces-*.tar.gz' | wc -l)" "0"
igual "origen vacío: el destino no se toca" "$(cat "$D2/a.md")" "estable"

# freno de espacio
sleep 1.1; echo "v3" > "$S/agenda.md"; touch -d "+4 minutes" "$S/agenda.md"
SALIDA=$(LOUIS_FAKE_FREE_GB=1 correr)
igual "sin espacio (1 GB): no hay snapshot" "$(snaps)" "3"
igual "ni se aplicó el cambio" "$(cat "$D/agenda.md")" "contenido v2"
grep -q "NO se aplica" <<<"$SALIDA" && ok "lo dice en el log" || mal "sin aviso: $SALIDA"
SALIDA=$(LOUIS_FAKE_FREE_GB=5 correr)
grep -q "poda de emergencia" <<<"$SALIDA" && ok "con poco espacio (5 GB) poda en emergencia" || mal "sin poda: $SALIDA"
igual "y aun así aplica (hay lo mínimo)" "$(cat "$D/agenda.md")" "v3"
igual "se reintentó el cambio que quedó pendiente (snapshot nuevo)" "$(snaps)" "4"

echo; echo "$((PRUEBAS-FALLOS))/$PRUEBAS pruebas OK"
exit $FALLOS
