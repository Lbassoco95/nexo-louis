#!/usr/bin/env bash
# install-m365-scripts.sh
# Copia los 5 scripts M365 a ~/.openclaw/spaces/general/scripts/
# Registra los tools en AGENTS.md de Louis.

set -euo pipefail

SRC="$HOME/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/m365/scripts"
DEST="$HOME/.openclaw/spaces/general/scripts"
HOME_OC="$HOME/.openclaw"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"

echo "==> Copiando scripts M365 a $DEST"
for s in oauth-kawiil.sh m365-token.sh m365-leer-correos.sh m365-calendario.sh m365-mandar-correo.sh; do
  if [[ -f "$SRC/$s" ]]; then
    cp "$SRC/$s" "$DEST/$s"
    chmod +x "$DEST/$s"
    echo "    $DEST/$s"
  fi
done

echo ""
echo "==> Registrando tools en AGENTS.md de Louis"
AGENTS_PATH="$HOME_OC/spaces/general/AGENTS.md"
AGENTS_PATH2="$HOME_OC/agents/general/agent/AGENTS.md"

# Backup
for p in "$AGENTS_PATH" "$AGENTS_PATH2"; do
  [[ -f "$p" ]] && cp "$p" "$p.backup-$STAMP"
done

if ! grep -q "TOOLS: Microsoft 365 (Outlook + Calendar)" "$AGENTS_PATH" 2>/dev/null; then
  cat >> "$AGENTS_PATH" <<'NEXOEOF'

# TOOLS: Microsoft 365 (Outlook + Calendar)

Tienes acceso a Microsoft Graph para Kawiil (tenant Bassoco Vega Salas Morales Servicios Empresariales). Los scripts viven en `~/.openclaw/spaces/general/scripts/`.

## Lectura de correos

```bash
~/.openclaw/spaces/general/scripts/m365-leer-correos.sh kawiil unread 10
~/.openclaw/spaces/general/scripts/m365-leer-correos.sh kawiil all 20
```

Args: tenant ("kawiil" o "yoltik" cuando se configure), filter ("unread"|"all"), limit. Devuelve lista con remitente, fecha, asunto, preview. Usa cuando Polo te pida "léeme correos", "qué hay en mi inbox", "correos urgentes hoy".

## Lectura de calendario

```bash
~/.openclaw/spaces/general/scripts/m365-calendario.sh kawiil hoy
~/.openclaw/spaces/general/scripts/m365-calendario.sh kawiil manana
~/.openclaw/spaces/general/scripts/m365-calendario.sh kawiil semana
```

Devuelve eventos con horario (TZ Mexico), título, ubicación, organizador, asistentes. Usa cuando Polo te pida "agenda de hoy", "qué tengo mañana", "juntas esta semana".

## Mandar correo

```bash
~/.openclaw/spaces/general/scripts/m365-mandar-correo.sh kawiil "destino@ej.com" "Asunto" "Cuerpo del mensaje"
```

**REGLA INVIOLABLE**: NUNCA mandes correo sin antes mostrarle a Polo el borrador completo (destinatario, asunto, cuerpo) y obtener "confirmo" explícito. El patrón es:

1. Polo: "Mándale a Marco que el sprint queda para el viernes"
2. Tú: "Borrador para Marco. Asunto: Reagenda sprint a viernes. Cuerpo: [...]. ¿Confirmas envío?"
3. Polo: "confirmo"
4. Tú: ejecutas el script y reportas "Enviado a marco@... a las HH:MM."

NUNCA mandes sin confirmación. NUNCA respondas automáticamente a un correo entrante. Si Polo dice "respóndele que sí", muestra borrador primero.

## Tokens / autorización

Los access tokens se refrescan solos cada vez que invocas un script (vía `m365-token.sh` interno). Si los tokens están vencidos sin posibilidad de refresh (refresh_token expiró tras 90 días), el script falla con mensaje claro y Polo tendrá que correr de nuevo `oauth-kawiil.sh`.

## Cuando Yoltik tenant esté listo

Mismos scripts pero primer arg "yoltik" en lugar de "kawiil". Las credenciales irán en `~/.openclaw/credentials/m365-yoltik.env` y `~/.openclaw/credentials/m365-yoltik-tokens.json`.
NEXOEOF
  cp "$AGENTS_PATH" "$AGENTS_PATH2"
  echo "    AGENTS.md actualizado con tools M365"
else
  echo "    AGENTS.md ya tiene la sección M365, no se duplicó"
fi

echo ""
echo "==> Reiniciando gateway"
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
sleep 4

echo ""
echo "============================================================"
echo "Scripts M365 instalados. Siguiente paso CRÍTICO:"
echo ""
echo "  Correr el OAuth handshake para autenticarte con Microsoft."
echo "  Esto abre Safari, inicias sesión con leo.bassoco@kawiil.mx,"
echo "  apruebas los permisos. Sólo se hace UNA vez."
echo ""
echo "  Comando:"
echo "    bash $DEST/oauth-kawiil.sh"
echo ""
echo "  Después de eso, Louis ya puede leer/mandar correos y leer calendario."
echo "============================================================"
