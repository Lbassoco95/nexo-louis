#!/usr/bin/env bash
# actualizar-donna.sh — actualiza Donna SIN cortar una grabación en curso.
#
# Uso (en el servidor, como polo):
#   bash ~/donna-deploy/cloud/hetzner/tools/actualizar-donna.sh            # actualiza si NO hay grabación; si la hay, no toca nada y te lo dice
#   bash ~/donna-deploy/cloud/hetzner/tools/actualizar-donna.sh --esperar  # si hay grabación, espera a que termine y entonces actualiza solo
#   bash ~/donna-deploy/cloud/hetzner/tools/actualizar-donna.sh --forzar   # actualiza YA aunque haya grabación (la corta y se reconecta; solo por urgencia)
#
# Cómo sabe que hay una grabación: mira si en los últimos 2 minutos llegó audio, texto de rescate o un autoguardado de Escuchar, o si hay una revisión de voces
# procesándose (base de datos, solo lectura). Reiniciar a media revisión la pierde (35–40 min de trabajo y su costo en OpenAI).
# Si hay grabación NO instala nada (ni pantalla ni servidor): una pantalla nueva con un servidor viejo se desajusta. Todo se instala junto, sin grabación en curso.
# Después de instalar reinicia los servicios y corre la verificación.
set -u
REPO="${REPO:-$HOME/donna-deploy}"
SRC="$REPO/cloud/hetzner/services"
DEST="${DEST:-/opt/openclaw/scripts}"
DB="${DONNA_DB:-/opt/openclaw/state/donna.db}"
SUDO="${SUDO-sudo}"
OWNER="${OWNER:-polo}"
SERVICIOS="${SERVICIOS:-openclaw-gateway scheduler telegram-bridge}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
ESPERA="${ESPERA:-30}"
MODO=normal
for a in "$@"; do
  case "$a" in
    --esperar) MODO=esperar ;;
    --forzar) MODO=forzar ;;
    *) echo "Opción desconocida: $a (usa --esperar o --forzar)"; exit 2 ;;
  esac
done

grabando() {   # 0 = hay una grabación en curso
  python3 - "$DB" <<'PY'
import sqlite3, sys, time
try:
    c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
except sqlite3.Error:
    sys.exit(1)
import json, os
t = time.time() - 120
for q in ("SELECT COUNT(*) FROM escucha_audio WHERE estado='grabando' AND actualizado>?",
          "SELECT COUNT(*) FROM escucha_rescate WHERE actualizado>?",
          "SELECT COUNT(*) FROM escucha_borradores WHERE actualizado>?"):
    try:
        if c.execute(q, (t,)).fetchone()[0]:
            sys.exit(0)
    except sqlite3.Error:
        pass
try:                                                          # una revisión de voces EN CURSO (su proceso sigue vivo): reiniciar la pierde (35–40 min y su costo). Una huérfana (el proceso ya no existe) no cuenta.
    for (datos,) in c.execute("SELECT datos FROM junta_voces WHERE estado='procesando' AND ts>?", (time.time() - 1800,)):
        pid = json.loads(datos or "{}").get("pid")
        if pid and os.path.exists("/proc/%d" % int(pid)):
            sys.exit(0)
except sqlite3.Error:
    pass
sys.exit(1)
PY
}

if [ -z "${NO_GIT:-}" ]; then
  (cd "$REPO" && git pull --ff-only) || { echo "✗ git pull falló; no instalo nada."; exit 1; }
fi

if [ "$MODO" != forzar ] && grabando; then
  if [ "$MODO" = esperar ]; then
    echo "🎙️ Hay una grabación o una revisión de voces en curso. Espero a que termine para actualizar (reviso cada ${ESPERA} s; Ctrl+C cancela)…"
    libres=0
    while [ "$libres" -lt 3 ]; do
      if grabando; then libres=0; else libres=$((libres + 1)); fi
      sleep "$ESPERA"
    done
    echo "✓ La grabación terminó. Actualizo."
  else
    echo "🎙️ Hay una grabación o una revisión de voces en curso: NO instalo ni reinicio nada para no cortarla."
    echo "   El código nuevo ya está descargado. Cuando termines y guardes la junta, corre:"
    echo "     bash $0"
    echo "   o corre ahora   bash $0 --esperar   y se actualiza solo al terminar."
    exit 0
  fi
fi

$SUDO install -m 0755 -o "$OWNER" -g "$OWNER" "$SRC"/*.py "$DEST"/ || { echo "✗ no pude instalar el servidor"; exit 1; }
$SUDO install -m 0644 -o "$OWNER" -g "$OWNER" "$SRC"/app/* "$DEST"/app/ || { echo "✗ no pude instalar la pantalla"; exit 1; }
$SUDO $SYSTEMCTL restart $SERVICIOS || { echo "✗ no pude reiniciar los servicios"; exit 1; }
echo "✓ Instalado y reiniciado. Cierra la app por completo y ábrela de nuevo para cargar la versión nueva."
if [ -z "${NO_VERIFY:-}" ]; then
  bash "$REPO/cloud/hetzner/tools/verificar-donna.sh" | tail -5
fi
