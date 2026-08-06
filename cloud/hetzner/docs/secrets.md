# Migración de secretos: Mac → .env de Hetzner

Antes de correr `deploy.sh`, debes llenar `.env` con los tokens que ya tienes
en tu Mac. Esta nota documenta de dónde sacar cada uno.

> **Nunca** subas `.env` a git, ni lo pegues en chat. Cópialo por scp/rsync
> directo al VPS y bórralo del trayecto cuando termines.

## Mapa rápido

| Variable en .env | De dónde sacarlo |
|---|---|
| `ANTHROPIC_API_KEY` | `~/.openclaw/credentials/anthropic.env` o `~/.openclaw/openclaw.env` |
| `TELEGRAM_BOT_TOKEN` | `~/.openclaw/credentials/telegram.env` |
| `TELEGRAM_ALLOWED_CHAT_ID` | `~/.openclaw/credentials/telegram.env` |
| `SLACK_BOT_TOKEN` | `~/.openclaw/credentials/slack.env` |
| `SLACK_APP_TOKEN` | `~/.openclaw/credentials/slack.env` |
| `SLACK_SIGNING_SECRET` | `~/.openclaw/credentials/slack.env` |
| `MS_KAWIIL_TENANT_ID` | `~/.openclaw/credentials/m365-kawiil.env` |
| `MS_KAWIIL_CLIENT_ID` | `~/.openclaw/credentials/m365-kawiil.env` |
| `MS_KAWIIL_CLIENT_SECRET` | `~/.openclaw/credentials/m365-kawiil.env` |
| `MS_KAWIIL_REFRESH_TOKEN` | `~/.openclaw/credentials/m365-kawiil.env` |
| `MS_YOLTIK_TENANT_ID` | `~/.openclaw/credentials/m365-yoltik.env` |
| `MS_YOLTIK_CLIENT_ID` | `~/.openclaw/credentials/m365-yoltik.env` |
| `MS_YOLTIK_CLIENT_SECRET` | `~/.openclaw/credentials/m365-yoltik.env` |
| `MS_YOLTIK_REFRESH_TOKEN` | `~/.openclaw/credentials/m365-yoltik.env` |
| `SUPABASE_URL` | Supabase dashboard → Project settings → API (proyecto `qppfampapbxdgednkofc`) |
| `SUPABASE_SERVICE_KEY` | Supabase dashboard → API → `service_role` key |
| `SUPABASE_ANON_KEY` | Supabase dashboard → API → `anon` key |
| `KAWIIL_DISPATCH_TOKEN` | `~/.openclaw/credentials/kawiil-agents.env` |
| `KAWIIL_*ORG_ID` | `~/.openclaw/credentials/kawiil-agents.env` |
| `MAC_SYNC_SSH_PUB_KEY` | Output de `mac-install.sh` (ver más abajo) |

## Extraer todo de la Mac de un golpe

```bash
# En la Mac:
cd ~/.openclaw/credentials
ls -la
```

Cada archivo `*.env` está en formato `KEY=VALUE`. Para imprimir todo y
copiarlo manualmente:

```bash
for f in ~/.openclaw/credentials/*.env; do
  echo "# === $f ==="
  cat "$f"
  echo ""
done
```

## Generar `.env` parcial automáticamente

Hay un helper opcional. Desde la Mac:

```bash
cd ~/Documents/Claude/Projects/Yoltik\ Desarrollos/yoltik-ai-setup/cloud/hetzner
cat > /tmp/louis-env-draft <<EOF
# Generado $(date -Iseconds) — copia a /opt/louis/.env en Hetzner

DONNA_DOMAIN=donna.kawiil.mx
AGENTS_DOMAIN=agents.kawiil.mx
ACME_EMAIL=lbassoco@kawiil.mx
SYSTEM_USER=polo
SYSTEM_USER_SSH_KEY=$(cat ~/.ssh/id_ed25519.pub 2>/dev/null || cat ~/.ssh/id_rsa.pub 2>/dev/null)

ANTHROPIC_API_KEY=$(grep -h ANTHROPIC_API_KEY ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

TELEGRAM_BOT_TOKEN=$(grep -h TELEGRAM_BOT_TOKEN ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
TELEGRAM_ALLOWED_CHAT_ID=$(grep -h TELEGRAM_ALLOWED_CHAT_ID ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

SLACK_BOT_TOKEN=$(grep -h SLACK_BOT_TOKEN ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
SLACK_APP_TOKEN=$(grep -h SLACK_APP_TOKEN ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
SLACK_SIGNING_SECRET=$(grep -h SLACK_SIGNING_SECRET ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

MS_TENANT_ID=$(grep -h MS_TENANT_ID ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
MS_CLIENT_ID=$(grep -h MS_CLIENT_ID ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
MS_CLIENT_SECRET=$(grep -h MS_CLIENT_SECRET ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
MS_REFRESH_TOKEN=$(grep -h MS_REFRESH_TOKEN ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

SUPABASE_URL=https://qppfampapbxdgednkofc.supabase.co
SUPABASE_SERVICE_KEY=$(grep -h SUPABASE_SERVICE_KEY ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
SUPABASE_ANON_KEY=$(grep -h SUPABASE_ANON_KEY ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

KAWIIL_DISPATCH_TOKEN=$(grep -h KAWIIL_DISPATCH_TOKEN ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
KAWIIL_ORG_ID=$(grep -h '^KAWIIL_ORG_ID' ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
KAWIIL_KAWIIL_ORG_ID=$(grep -h KAWIIL_KAWIIL_ORG_ID ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
KAWIIL_YOLTIK_ORG_ID=$(grep -h KAWIIL_YOLTIK_ORG_ID ~/.openclaw/credentials/*.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

OPENCLAW_PORT=3000
OPENCLAW_VERSION=latest

# Esto se rellena después con mac-install.sh
MAC_SYNC_SSH_PUB_KEY=
EOF

# Subir al VPS y borrar local
scp /tmp/louis-env-draft root@TU_IP_HETZNER:/opt/louis/.env
rm /tmp/louis-env-draft
```

Después, en el VPS, revisa que todos los valores se copiaron bien:

```bash
ssh root@TU_IP_HETZNER 'cat /opt/louis/.env | grep -v "^#" | grep -v "^$" | awk -F= "{print \$1\"=\"length(\$2)\" chars\"}"'
```

(Imprime el tamaño de cada valor sin revelar el secreto — útil para
verificar que ninguno quedó vacío.)

## Particularidades

### MS_REFRESH_TOKEN
Tu refresh token está atado a un `client_id` específico que ya autorizaste
en la app de Entra ID (ver `nexo_m365_setup` en memoria). Migrar a Hetzner
**no requiere re-OAuth**: el mismo refresh_token funciona desde cualquier IP
porque Microsoft no fija IP en los tokens. Si por algún motivo el token
expira o se invalida (ocurre tras ~90 días sin uso), reautoriza con:

```bash
# En tu Mac, scripts existentes:
cd ~/Documents/Claude/Projects/Yoltik\ Desarrollos/yoltik-ai-setup/m365
./oauth-bootstrap.sh
# Copia el nuevo refresh_token al .env de Hetzner
```

### SLACK_APP_TOKEN (Socket Mode)
Socket Mode no requiere IP fija, así que tu app de Slack actual funciona
sin cambios desde Hetzner. **Importante**: no corras dos socket clients
al mismo tiempo (Mac + Hetzner) con el mismo App Token — Slack desconecta
al más viejo. Cuando estés listo para el cutover, detén el OpenClaw de la
Mac (`launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist`).

### TELEGRAM_BOT_TOKEN (Polling)
Igual que Slack: solo un proceso puede hacer polling al mismo tiempo. El
Telegram API responde `409 Conflict` si dos procesos intentan al mismo
tiempo. Detén el de la Mac antes de validar el de Hetzner.

### MAC_SYNC_SSH_PUB_KEY
Esta NO la rellenas de un archivo existente. Se genera con
`sync/mac-install.sh` en la Mac, que crea `~/.ssh/louis_sync` dedicado.
Después de correr el script, pegas la pubkey en `/opt/louis/.env` del VPS
y vuelves a correr `bash sync/hetzner-prepare.sh` allá.

## Cutover seguro (sin perder mensajes)

1. Deploy en Hetzner pero **deja apagados** Telegram y Slack en `.env`
   (comenta esas líneas con `#` y reinicia OpenClaw).
2. Verifica que Caddy + OpenClaw + kawiil-agents responden.
3. Detén los canales en la Mac:
   ```bash
   launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist
   ```
4. Re-activa los tokens en `.env` de Hetzner, reinicia OpenClaw.
5. Manda un mensaje de prueba por Telegram desde tu iPhone. Debe responder
   Donna desde Hetzner.
6. Si todo OK, vuelve a encender el de la Mac SOLO si lo quieres como backup
   en caliente — pero **con tokens diferentes** (otro bot de Telegram de
   pruebas, otro app de Slack). Compartir tokens entre 2 hosts no funciona.

El sync Mac → Hetzner sigue corriendo siempre que la Mac esté encendida,
así que la memoria/AGENDA se mantiene en paralelo aun si solo Hetzner
responde a humanos.
