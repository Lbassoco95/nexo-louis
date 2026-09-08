#!/usr/bin/env bash
# verificar-drift.sh — ¿el server tenía código que el repo NO tiene?
#
# El problema: `self_update.py` deja que Donna edite /opt/openclaw/scripts/ en
# caliente, y esas ediciones nunca vuelven a git. Un deploy las pisa en silencio
# (fue exactamente lo que le pasó a cerebro_kawiil_mcp.py el 7-sep-2026).
#
# Comparar el respaldo contra /opt/louis/services/ NO sirve: ahí ya está la versión
# nueva del repo, así que todo "difiere" y no se distingue una edición viva de un
# cambio legítimo del repo. La comparación correcta es contra la versión del repo
# ANTERIOR al deploy — es decir, la rama base (main).
#
# Uso:  /opt/louis/scripts/verificar-drift.sh /opt/openclaw/.respaldo-AAAAMMDDHHMMSS
#       /opt/louis/scripts/verificar-drift.sh            # usa el respaldo más reciente

set -euo pipefail

FUENTE="${FUENTE:-/opt/louis-src}"
BASE="${BASE:-origin/main}"
SUBDIR="cloud/hetzner/services"
ARCHIVOS=(louis_core.py telegram-bridge.py slack-bridge.py scheduler.py
          openclaw_gateway.py self_update.py browser_runner.py cerebro_kawiil_mcp.py)

log()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

RESPALDO="${1:-}"
if [[ -z "$RESPALDO" ]]; then
  RESPALDO="$(sudo ls -1d /opt/openclaw/.respaldo-* 2>/dev/null | sort | tail -1 || true)"
  [[ -n "$RESPALDO" ]] || fail "No encontré ningún /opt/openclaw/.respaldo-*. Pásalo como argumento."
fi
sudo test -d "$RESPALDO/scripts" || fail "No existe $RESPALDO/scripts"
[[ -d "$FUENTE/.git" ]] || fail "$FUENTE no es un clon de git (ajusta FUENTE=)"

log "Respaldo:  $RESPALDO/scripts"
log "Base repo: $BASE (la versión que había ANTES del deploy)"
sudo git -C "$FUENTE" fetch --quiet origin || warn "no pude hacer fetch; uso lo que haya en local"
echo

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
limpios=0; sucios=0
for f in "${ARCHIVOS[@]}"; do
  if ! sudo git -C "$FUENTE" show "$BASE:$SUBDIR/$f" > "$TMP/$f" 2>/dev/null; then
    warn "$f — no existe en $BASE (archivo nuevo); se omite"
    continue
  fi
  if sudo diff -q "$TMP/$f" "$RESPALDO/scripts/$f" >/dev/null 2>&1; then
    ok "$f — sin ediciones en vivo (el repo era la fuente de verdad)"
    limpios=$((limpios+1))
  else
    n="$(sudo diff "$TMP/$f" "$RESPALDO/scripts/$f" | grep -c '^[<>]' || true)"
    warn "$f — TENÍA ediciones en vivo: $n líneas que el repo no traía"
    printf "      diff completo:\n"
    printf "      sudo git -C %s show %s:%s/%s > /tmp/base-%s\n" "$FUENTE" "$BASE" "$SUBDIR" "$f" "$f"
    printf "      sudo diff /tmp/base-%s %s/scripts/%s\n" "$f" "$RESPALDO" "$f"
    sucios=$((sucios+1))
  fi
done

echo
if [[ $sucios -eq 0 ]]; then
  ok "Ningún archivo traía ediciones en vivo. El deploy no perdió nada."
else
  warn "$sucios archivo(s) con código que el repo no tenía (y $limpios limpios)."
  echo "   Saca el diff de cada uno, decide qué vale y PÓRTALO AL REPO."
  echo "   El respaldo es la única copia: no lo borres hasta terminar."
fi
