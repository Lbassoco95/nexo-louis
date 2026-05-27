#!/usr/bin/env bash
# install-telegram.sh — Conecta Louis (Nexo) con Telegram para chat libre.
#   1. Guarda credenciales en ~/.openclaw/credentials/telegram.env
#   2. Copia scripts enviar-telegram.sh y leer-telegram.sh a ~/.openclaw/spaces/general/scripts/
#   3. Manda un mensaje de prueba a tu Telegram
#   4. Actualiza AGENTS.md de Louis para que conozca los nuevos tools
#   5. Reinicia gateway

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_OC="$HOME/.openclaw"
SPACE="$HOME_OC/spaces/general"
SCRIPTS_DIR="$SPACE/scripts"
CREDS_DIR="$HOME_OC/credentials"
STAMP="$(date +%Y%m%d-%H%M%S)"

# === Credenciales recibidas de Polo ===
TELEGRAM_BOT_TOKEN="8790887727:AAGzvcsj-2rhp_3245uhdjAfjbJmHAGhk-Y"
TELEGRAM_CHAT_ID="7241883999"

echo "==> 1. Guardando credenciales en $CREDS_DIR/telegram.env"
mkdir -p "$CREDS_DIR"
cat > "$CREDS_DIR/telegram.env" <<EOF
TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID"
EOF
chmod 600 "$CREDS_DIR/telegram.env"
echo "    OK (perms 600 — solo tú puedes leerlo)"

echo ""
echo "==> 2. Copiando scripts a $SCRIPTS_DIR/"
mkdir -p "$SCRIPTS_DIR"
cp "$SRC/enviar-telegram.sh" "$SCRIPTS_DIR/enviar-telegram.sh"
cp "$SRC/leer-telegram.sh"   "$SCRIPTS_DIR/leer-telegram.sh"
chmod +x "$SCRIPTS_DIR/enviar-telegram.sh" "$SCRIPTS_DIR/leer-telegram.sh"
echo "    OK (ejecutables)"

echo ""
echo "==> 3. Mensaje de prueba a tu Telegram"
TEST_MSG="Hola Polo, soy Louis. Acabamos de conectar Telegram. A partir de ahora te puedo escribir aquí cuando haya algo importante. Confirma con 'recibido' si te llegó este mensaje."
if "$SCRIPTS_DIR/enviar-telegram.sh" "$TEST_MSG"; then
  echo "    Revisa tu Telegram en el iPhone."
else
  echo "    ERROR: no se pudo enviar el mensaje. Revisa el bot token y chat_id."
  exit 1
fi

echo ""
echo "==> 4. Actualizando AGENTS.md de Louis para registrar los nuevos tools"
AGENTS_PATH="$SPACE/AGENTS.md"
AGENTS_PATH2="$HOME_OC/agents/general/agent/AGENTS.md"

# Backup
for p in "$AGENTS_PATH" "$AGENTS_PATH2"; do
  [[ -f "$p" ]] && cp "$p" "$p.backup-$STAMP"
done

# Agregar sección de Telegram al final del AGENTS.md si no está ya
if ! grep -q "TOOL: enviar-telegram" "$AGENTS_PATH" 2>/dev/null; then
  cat >> "$AGENTS_PATH" <<'NEXOEOF'

# TOOL: enviar-telegram

Tienes acceso a un script que manda mensajes de Telegram al iPhone de Polo:

```bash
~/.openclaw/spaces/general/scripts/enviar-telegram.sh "Texto del mensaje"
```

Flags opcionales:
- `--markdown` para formato con negritas (*texto*), itálicas (_texto_), código (`texto`)
- `--html` para HTML
- `--silent` para mandar sin sonido (no interrumpe a Polo)

Úsalo cuando:
- Polo te diga "mándame X por Telegram" o "avísame"
- Detectes algo URGENTE durante triage matutino
- Necesites confirmar que algo está hecho cuando Polo no está en chat web
- Recordatorios programados que requieran intervención inmediata (vs Reminders de iCloud que son alarmas pasivas)

Reglas:
1. NO mandes spam — máximo 3-4 mensajes por día salvo emergencia real.
2. Para recordatorios futuros (no inmediatos), prefiere `crear-recordatorio.sh` (iCloud Reminders) sobre Telegram.
3. Si Polo dice "no me molestes" o "después", respétalo y no escribas hasta que él lo retome.
4. Cuando mandes algo importante, después de enviarlo guarda referencia en JOURNAL.md.

# TOOL: leer-telegram

Polo puede escribirte al bot de Telegram cuando esté lejos de la Mac/iPad. Para leer esos mensajes:

```bash
~/.openclaw/spaces/general/scripts/leer-telegram.sh --pretty
```

Devuelve mensajes nuevos (no re-lee viejos — mantiene offset). Si no hay nada, dice "(sin mensajes nuevos)".

Úsalo cuando:
- Sea inicio de conversación nueva en webchat (haz polling al iniciar para ver si Polo te dejó algo)
- Polo te diga "leíste lo que te mandé" o similar

Cuando proceses un mensaje de Telegram, trátalo como una instrucción de Polo y actúa en consecuencia:
- "agrega comprar X a mi agenda" → actualiza AGENDA.md
- "recuérdame mañana 9am llamar a Marco" → usa crear-recordatorio.sh
- "qué tengo pendiente" → responde por Telegram con resumen breve
NEXOEOF
  # Sincronizar al segundo path
  cp "$AGENTS_PATH" "$AGENTS_PATH2"
  echo "    AGENTS.md actualizado en ambos paths"
else
  echo "    AGENTS.md ya tiene la sección de Telegram, no se duplicó"
fi

echo ""
echo "==> 5. Reiniciando gateway"
launchctl kickstart -k gui/501/ai.openclaw.gateway
sleep 4
if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    OK, gateway escuchando"
else
  echo "    WARN: gateway aún no escucha, espera y revisa con: lsof -iTCP:3000 -sTCP:LISTEN"
fi

echo ""
echo "============================================================"
echo "Telegram conectado. Pruebas que puedes hacer:"
echo ""
echo "  1. Verifica que llegó el mensaje de prueba a tu Telegram."
echo ""
echo "  2. Manualmente desde terminal (prueba rápida):"
echo "       $SCRIPTS_DIR/enviar-telegram.sh \"Otro mensaje desde Mac\""
echo ""
echo "  3. Desde el iPad (vía Louis):"
echo "       Refresca Safari → conversación con Nexo General"
echo "       Manda: 'Louis, mándame por Telegram la lista de pendientes de hoy'"
echo "       Louis debe usar el tool y confirmar."
echo ""
echo "  4. Bidireccional (Polo → Louis vía Telegram):"
echo "       Manda al bot en Telegram: 'agrega revisar Outlook a mi agenda'"
echo "       Después en el iPad pídele a Louis: 'lee mis mensajes de Telegram'"
echo "       Louis debe leer el mensaje y procesarlo."
echo ""
echo "RECORDATORIO DE SEGURIDAD:"
echo "  El bot token quedó pegado en chat (y en este script)."
echo "  Cuando quieras rotarlo: dile a BotFather '/revoke', te da uno nuevo,"
echo "  actualiza $CREDS_DIR/telegram.env y reinicia gateway."
echo "============================================================"
