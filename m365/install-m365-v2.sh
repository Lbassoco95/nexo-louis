#!/usr/bin/env bash
# install-m365-v2.sh
# Reemplaza los scripts .sh viejos con el cliente Python unificado m365.py
# Actualiza AGENTS.md de Louis para que use los comandos correctos.

set -euo pipefail

SRC="$HOME/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/m365/scripts"
DEST="$HOME/.openclaw/spaces/general/scripts"
HOME_OC="$HOME/.openclaw"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"

echo "==> Instalando m365.py unificado"
cp "$SRC/m365.py" "$DEST/m365.py"
chmod +x "$DEST/m365.py"
echo "    $DEST/m365.py"

echo "==> Instalando oauth-kawiil.py"
cp "$SRC/oauth-kawiil.py" "$DEST/oauth-kawiil.py"
chmod +x "$DEST/oauth-kawiil.py"
echo "    $DEST/oauth-kawiil.py"

echo ""
echo "==> Borrando los .sh viejos que fallaban con OData"
rm -f "$DEST/m365-token.sh" \
      "$DEST/m365-leer-correos.sh" \
      "$DEST/m365-calendario.sh" \
      "$DEST/m365-mandar-correo.sh" \
      "$DEST/oauth-kawiil.sh"
echo "    OK"

echo ""
echo "==> Actualizando AGENTS.md con los comandos Python correctos"
AGENTS_PATH="$HOME_OC/spaces/general/AGENTS.md"
AGENTS_PATH2="$HOME_OC/agents/general/agent/AGENTS.md"

# Backup
for p in "$AGENTS_PATH" "$AGENTS_PATH2"; do
  [[ -f "$p" ]] && cp "$p" "$p.backup-$STAMP"
done

# Quitar la sección vieja "TOOLS: Microsoft 365" si existe (entre el header y EOF/sección siguiente)
python3 - "$AGENTS_PATH" <<'PYTHON'
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()

# Buscar la sección vieja y removerla hasta el siguiente "# " o fin de archivo
import re
pattern = re.compile(r'\n# TOOLS: Microsoft 365.*?(?=\n# [A-Z]|\Z)', re.DOTALL)
new = pattern.sub('', content)
with open(path, "w") as f:
    f.write(new)
PYTHON

# Agregar la nueva sección
cat >> "$AGENTS_PATH" <<'NEXOEOF'

# TOOLS: Microsoft 365 (Outlook + Calendar) — v2 Python

Tienes acceso a Microsoft Graph para el tenant Kawiil vía un cliente Python unificado en `~/.openclaw/spaces/general/scripts/m365.py`.

## Comandos disponibles

### Leer correos
```bash
python3 ~/.openclaw/spaces/general/scripts/m365.py leer-correos kawiil unread 10
python3 ~/.openclaw/spaces/general/scripts/m365.py leer-correos kawiil all 20
```
Args: tenant (kawiil|yoltik cuando se configure), filtro (unread|all), límite. Cuando Polo pida "léeme correos", "qué hay en mi inbox", "correos urgentes hoy".

### Leer calendario
```bash
python3 ~/.openclaw/spaces/general/scripts/m365.py calendario kawiil hoy
python3 ~/.openclaw/spaces/general/scripts/m365.py calendario kawiil manana
python3 ~/.openclaw/spaces/general/scripts/m365.py calendario kawiil semana
```
Devuelve eventos con horario (TZ Mexico), asistentes, ubicación, organizador. Cuando Polo pida agenda.

### Mandar correo
```bash
python3 ~/.openclaw/spaces/general/scripts/m365.py mandar-correo kawiil "destinatario@ejemplo.com" "Asunto" "Cuerpo"
python3 ~/.openclaw/spaces/general/scripts/m365.py mandar-correo kawiil "a@ej.com,b@ej.com" "Asunto" "Cuerpo" "cc@ej.com" "html"
```

**REGLA INVIOLABLE**: NUNCA mandes correo sin antes mostrar a Polo el borrador completo (destinatario, asunto, cuerpo) y obtener "confirmo" explícito. Patrón:

1. Polo: "Mándale a Marco que el sprint queda para el viernes"
2. Tú: "Borrador. To: marco@... · Asunto: ... · Cuerpo: ... · ¿Confirmas envío?"
3. Polo: "confirmo"
4. Tú: ejecutas el script y reportas "Enviado a marco@... a las HH:MM."

NUNCA mandes sin confirmación. NUNCA respondas automáticamente a correo entrante. Si Polo dice "respóndele que sí", muestra borrador primero.

### Verificar token (debug)
```bash
python3 ~/.openclaw/spaces/general/scripts/m365.py token kawiil
```
Imprime un access_token válido. Refresca automáticamente si expira.

## Cuando algo falla

- Si script devuelve "no encontré tokens", el OAuth no se hizo. Decirle a Polo: corre `python3 ~/.openclaw/spaces/general/scripts/oauth-kawiil.py`.
- Si script devuelve "refresh falló", el refresh_token expiró (>90 días sin uso). Mismo remedio: rerun oauth.
- Si script devuelve "401 Unauthorized", problemas con permisos. Verificar admin consent en Entra ID.

## Yoltik tenant

Cuando Polo registre la app en tenant Yoltik, mismo cliente con `yoltik` en lugar de `kawiil` como primer arg.
NEXOEOF

cp "$AGENTS_PATH" "$AGENTS_PATH2"
echo "    AGENTS.md actualizado en ambos paths"

echo ""
echo "==> Reiniciando gateway"
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
sleep 4

echo ""
echo "============================================================"
echo "Cliente m365.py instalado. Pruebas:"
echo ""
echo "  # Correos no leídos"
echo "  python3 ~/.openclaw/spaces/general/scripts/m365.py leer-correos kawiil unread 5"
echo ""
echo "  # Agenda de hoy"
echo "  python3 ~/.openclaw/spaces/general/scripts/m365.py calendario kawiil hoy"
echo ""
echo "  # Agenda de mañana"
echo "  python3 ~/.openclaw/spaces/general/scripts/m365.py calendario kawiil manana"
echo "============================================================"
