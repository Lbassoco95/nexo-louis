#!/usr/bin/env bash
# prune-backups.sh — retención ESCALONADA de los snapshots spaces-YYYYMMDDHHMMSS.tar.gz
#
# Antes: se guardaba TODO durante 14 días (un snapshot cada 5 min = ~4 000 archivos, ~53 GB).
# Ahora (por defecto):
#   · últimas 24 h ............ todos
#   · de 1 a 7 días ........... el más reciente de cada hora
#   · de 7 a 30 días .......... el más reciente de cada día
#   · más de 30 días .......... se borran (para historia larga: copia fuera del servidor, ver docs/runbook.md)
#   · siempre se conservan los N más recientes (--min-keep, 12 por defecto)
# Solo toca archivos con el nombre EXACTO spaces-<14 dígitos>.tar.gz; cualquier otro (pre-seed-*, full-*, *.tgz manuales) se ignora.
#
# Uso:  bash prune-backups.sh [--dry-run] [--emergency] [--dir DIR] [--now EPOCH]
#                             [--keep-all-hours H] [--keep-hourly-days D] [--keep-daily-days D] [--min-keep N]
#   --dry-run    solo muestra qué borraría (NO borra). Úsalo primero.
#   --emergency  política corta para cuando falta disco: 6 h todos, 1 día por hora, 7 días por día.
set -euo pipefail

DIR="${LOUIS_BACKUPS:-/opt/openclaw/backups}"
KEEP_ALL_H=24; KEEP_HOURLY_D=7; KEEP_DAILY_D=30; MIN_KEEP=12
DRY=0; EMERGENCY=0; NOW="$(date +%s)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --emergency) EMERGENCY=1 ;;
    --dir) DIR="$2"; shift ;;
    --now) NOW="$2"; shift ;;
    --keep-all-hours) KEEP_ALL_H="$2"; shift ;;
    --keep-hourly-days) KEEP_HOURLY_D="$2"; shift ;;
    --keep-daily-days) KEEP_DAILY_D="$2"; shift ;;
    --min-keep) MIN_KEEP="$2"; shift ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done
(( EMERGENCY )) && { KEEP_ALL_H=6; KEEP_HOURLY_D=1; KEEP_DAILY_D=7; }

# Salvaguardas: nada de directorios raros ni números que no sean enteros.
[[ -n "$DIR" && "$DIR" != "/" && -d "$DIR" ]] || { echo "directorio no válido: '$DIR'" >&2; exit 2; }
for v in "$KEEP_ALL_H" "$KEEP_HOURLY_D" "$KEEP_DAILY_D" "$MIN_KEEP" "$NOW"; do [[ "$v" =~ ^[0-9]+$ ]] || { echo "valor no numérico: $v" >&2; exit 2; }; done

# Solo los snapshots con nombre exacto, del más nuevo al más viejo (el nombre lleva la fecha: orden lexicográfico = cronológico).
mapfile -t ARCHIVOS < <(find "$DIR" -maxdepth 1 -type f -name 'spaces-*.tar.gz' -printf '%f\n' | grep -E '^spaces-[0-9]{14}\.tar\.gz$' | sort -r || true)
TOTAL=${#ARCHIVOS[@]}
(( TOTAL > 0 )) || exit 0

# Fechas de todos los archivos con UN solo `date` (rápido incluso con miles).
FECHAS=$(for f in "${ARCHIVOS[@]}"; do t="${f:7:14}"; echo "${t:0:4}-${t:4:2}-${t:6:2} ${t:8:2}:${t:10:2}:${t:12:2}"; done)
mapfile -t EPOCHS < <(printf '%s\n' "$FECHAS" | date -f - +%s)

declare -A VISTA_H VISTA_D
BORRAR=(); KEEP=0; BYTES=0
for i in "${!ARCHIVOS[@]}"; do
  f="${ARCHIVOS[$i]}"; t="${f:7:14}"; age=$(( NOW - EPOCHS[i] ))
  if   (( i < MIN_KEEP ));                  then keep=1
  elif (( age < 0 ));                       then keep=1                       # fecha en el futuro: no se toca
  elif (( age < KEEP_ALL_H * 3600 ));       then keep=1
  elif (( age < KEEP_HOURLY_D * 86400 )); then
    k="${t:0:10}"; if [[ -z "${VISTA_H[$k]:-}" ]]; then VISTA_H[$k]=1; keep=1; else keep=0; fi
  elif (( age < KEEP_DAILY_D * 86400 )); then
    k="${t:0:8}";  if [[ -z "${VISTA_D[$k]:-}" ]]; then VISTA_D[$k]=1; keep=1; else keep=0; fi
  else keep=0
  fi
  if (( keep )); then KEEP=$((KEEP+1)); else BORRAR+=("$f"); BYTES=$(( BYTES + $(stat -c %s "$DIR/$f" 2>/dev/null || echo 0) )); fi
done

MB=$(( BYTES / 1048576 ))
if (( DRY )); then
  echo "[prune-backups] SIMULACIÓN en $DIR: de $TOTAL snapshots se conservarían $KEEP y se borrarían ${#BORRAR[@]} (~${MB} MB). No se borró nada."
  exit 0
fi
for f in "${BORRAR[@]}"; do rm -f -- "$DIR/$f"; done
(( ${#BORRAR[@]} > 0 )) && echo "[$(date -Iseconds)] prune-backups: conservados $KEEP, borrados ${#BORRAR[@]} (~${MB} MB liberados)"
exit 0
