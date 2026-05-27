#!/usr/bin/env bash
# Instala los system prompts v2 (Nexo) en los 3 espacios de OpenClaw.
# Resetea archivos de bootstrap del espacio general.
# Reinicia el gateway.

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.openclaw/spaces"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> Backup de prompts actuales"
for s in general legal ikan; do
  if [[ -f "$DEST/$s/system-prompt.md" ]]; then
    cp "$DEST/$s/system-prompt.md" "$DEST/$s/system-prompt.md.backup-$STAMP"
    echo "    backup: $DEST/$s/system-prompt.md.backup-$STAMP"
  fi
done

echo ""
echo "==> Copiando prompts v2 (Nexo)"
cp "$SRC/general.md" "$DEST/general/system-prompt.md"
cp "$SRC/legal.md"   "$DEST/legal/system-prompt.md"
cp "$SRC/ikan.md"    "$DEST/ikan/system-prompt.md"
echo "    copiados a $DEST/{general,legal,ikan}/system-prompt.md"

echo ""
echo "==> Reset de bootstrap del espacio general"
rm -f "$DEST/general/IDENTITY.md" \
      "$DEST/general/USER.md" \
      "$DEST/general/BOOTSTRAP.md"
echo "    IDENTITY.md, USER.md, BOOTSTRAP.md eliminados"

echo ""
echo "==> Verificación"
echo "--- general ---"
head -3 "$DEST/general/system-prompt.md"
echo "--- legal ---"
head -3 "$DEST/legal/system-prompt.md"
echo "--- ikan ---"
head -3 "$DEST/ikan/system-prompt.md"

echo ""
echo "==> Reiniciando gateway"
launchctl kickstart -k gui/501/ai.openclaw.gateway
sleep 3

echo ""
echo "==> Verificando que escucha"
lsof -iTCP:3000 -sTCP:LISTEN || echo "WARN: gateway no escucha aún, espera unos segundos más"

echo ""
echo "Listo. Ve al iPad, refresca la página, asegúrate de estar en el espacio 'general' y manda 'Hola'."
