# Runbook — Donna en Hetzner

Comandos del día 2: logs, reinicios, backups, restore, debug.

## Logs

| Servicio | Comando |
|---|---|
| OpenClaw | `journalctl -u openclaw -f` |
| Caddy | `docker compose -f /opt/louis/docker-compose.yml logs -f caddy` |
| kawiil-agents | `docker compose -f /opt/louis/docker-compose.yml logs -f kawiil-agents` |
| Sync apply (cron) | `tail -f /var/log/louis-sync.log` |
| Acceso Caddy Donna | `tail -f /opt/louis-data/caddy/data/logs/louis.log` |
| Acceso Caddy Agents | `tail -f /opt/louis-data/caddy/data/logs/agents.log` |
| Auth SSH (fail2ban) | `tail -f /var/log/auth.log` |
| Sync push (Mac) | `tail -f ~/Library/Logs/louissync.log` |

## Reinicios

```bash
# OpenClaw (cambios en system-prompt o env)
sudo systemctl restart openclaw

# Caddy (cambios en Caddyfile)
cd /opt/louis && docker compose restart caddy

# kawiil-agents (después de git pull en /opt/kawiil/repo)
cd /opt/louis && docker compose up -d --build kawiil-agents

# Todo de un golpe
cd /opt/louis && sudo ./deploy.sh --skip-bootstrap
```

## Recargar config sin downtime

OpenClaw:
```bash
sudo systemctl reload openclaw     # SIGHUP, sin perder conexiones
```

Caddy:
```bash
cd /opt/louis && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## Backups

Snapshots automáticos cada vez que el cron aplica un delta:

- Ubicación: `/opt/openclaw/backups/spaces-YYYYMMDDHHMMSS.tar.gz`
- Retención: 14 días (los más viejos se borran solos)

Backup manual completo (antes de un cambio grande):

```bash
sudo tar -czf /opt/openclaw/backups/full-$(date +%Y%m%d%H%M).tar.gz \
  -C /opt openclaw
```

Copia los backups fuera del VPS (ej. a S3 / Wasabi / iCloud):

```bash
# Ejemplo con rclone (configura primero con `rclone config`)
rclone copy /opt/openclaw/backups remote:louis-backups --include "spaces-*.tar.gz"
```

## Restore

```bash
# Lista snapshots
ls -lh /opt/openclaw/backups/

# Detén OpenClaw
sudo systemctl stop openclaw

# Restaura
sudo rm -rf /opt/openclaw/spaces
sudo tar -xzf /opt/openclaw/backups/spaces-20260525123000.tar.gz -C /opt/openclaw/
sudo chown -R polo:polo /opt/openclaw/spaces

# Arranca
sudo systemctl start openclaw
```

## Pausar / Reanudar sync Mac → Hetzner

Pausar (en la Mac):
```bash
launchctl unload ~/Library/LaunchAgents/ai.kawiil.louissync.plist
```

Reanudar:
```bash
launchctl load ~/Library/LaunchAgents/ai.kawiil.louissync.plist
```

Forzar un push manual:
```bash
LOUIS_REMOTE_HOST=louis.kawiil.mx \
LOUIS_REMOTE_USER=polo \
LOUIS_SSH_KEY=~/.ssh/louis_sync \
  bash ~/Documents/Claude/Projects/Yoltik\ Desarrollos/yoltik-ai-setup/cloud/hetzner/sync/mac-push.sh
```

## Borrar algo en Hetzner que ya borraste en la Mac

El cron de Hetzner usa `rsync --update` (no `--delete`) deliberadamente,
para no perder cosas accidentalmente. Si quieres propagar un borrado:

```bash
# En Hetzner, una vez:
sudo rm /opt/openclaw/spaces/general/memory/archivo-que-quieres-borrar.md
sudo systemctl restart openclaw
```

(O, si confías plenamente en el Mac como fuente de verdad, edita
`sync/hetzner-apply.sh` y añade `--delete` al rsync — pero entonces un
borrado accidental en la Mac también se propaga.)

## Renovar certificado TLS (automático, pero por si acaso)

Caddy renueva automáticamente. Para forzar:

```bash
cd /opt/louis
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --force
```

Ver fecha del cert:
```bash
echo | openssl s_client -connect louis.kawiil.mx:443 -servername louis.kawiil.mx 2>/dev/null \
  | openssl x509 -noout -dates
```

## Update de OpenClaw

```bash
# Cambia OPENCLAW_VERSION en .env (ej. 2026.5.30) o déjalo en `latest`
cd /opt/louis
sudo ./deploy.sh --skip-bootstrap
# El install.sh hace npm install -g; systemctl restart openclaw lo levanta nuevo
```

## Update de kawiil-agents

```bash
cd /opt/kawiil/repo
sudo -u polo git pull
cd /opt/louis
docker compose up -d --build kawiil-agents
```

O simplemente `sudo ./deploy.sh --skip-bootstrap` que lo hace todo.

## Rollback total

```bash
cd /opt/louis
sudo ./deploy.sh --rollback   # detiene servicios, no toca datos
```

Para volver a encender: `sudo ./deploy.sh --skip-bootstrap`.

## Cloudflare Access (opcional, recomendado para uso exclusivo de Polo)

Si quieres que solo tu identidad de Cloudflare pueda abrir la UI de OpenClaw,
sin exponerla al mundo:

1. En Cloudflare DNS → louis.kawiil.mx → "Proxied" (nube naranja).
2. Cloudflare Zero Trust → Applications → Add → Self-hosted.
3. Application domain: `louis.kawiil.mx`.
4. Policy: require email = `lbassoco@kawiil.mx`.
5. Caddy queda igual; Cloudflare hace el gate antes de llegar al VPS.

Los bots de Telegram/Slack y la API de kawiil-agents NO deben quedar detrás
de Access (siguen webhooks/HTTP de servicios). Por eso `agents.kawiil.mx`
queda público (autenticado por token).

## Caddy en bucle de reinicio (`Restarting`) → cae TODO el proxy

Síntoma: `docker ps` muestra `louis-caddy ... Restarting`, y los dominios
públicos no responden (`curl https://cerebro.kawiil-central.mx/health` da
`Couldn't connect`), aunque el servicio interno sí responde
(`curl http://127.0.0.1:4040/health` → OK). Como Caddy carga toda la config de
golpe, **un solo bloque roto tira todos los sitios** (Donna, Cerebro, agents).

Diagnóstico:
```bash
sudo docker logs --tail 40 louis-caddy
```

Causa más común — **`TABLERO_HASH` mal escapado**. El hash bcrypt trae `$`
(`$2a$14$...`); docker-compose los interpola y se los come, dejando un valor
corrupto. En los logs verás:
```
http_basic: base64-decoding password: illegal base64 data at input byte 45
```
y warnings tipo `WARN The "hP" variable is not set`.

Fix:
```bash
# Ver qué valor recibió realmente el contenedor:
sudo docker inspect louis-caddy --format '{{range .Config.Env}}{{println .}}{{end}}' | grep TABLERO_HASH

# En /opt/louis/.env, escapa CADA `$` como `$$` (comillas simples para que el
# shell no toque los `$`):
sudo sed -i 's|^TABLERO_HASH=.*|TABLERO_HASH=$$2a$$14$$<resto-del-bcrypt>|' /opt/louis/.env

# Recrea Caddy y verifica:
cd /opt/louis && sudo docker compose up -d caddy
sudo docker inspect louis-caddy --format '{{range .Config.Env}}{{println .}}{{end}}' | grep TABLERO_HASH
# Debe mostrar `$` sencillos: TABLERO_HASH=$2a$14$... (bcrypt completo)
```
Caddy detecta el prefijo `$2a$` y usa bcrypt directo; si los `$` faltan, intenta
base64 y truena. Ver también el comentario en `docker-compose.yml` y `.env.example`.

## Cuando algo se rompe

1. `./verify.sh` — primera línea de diagnóstico.
2. Logs del servicio sospechoso (tabla arriba).
3. `systemctl status openclaw --no-pager -l`
4. `docker compose ps` y `docker compose logs --tail 100`
5. Estado de disco: `df -h /opt`
6. Estado de memoria: `free -m` (si <100 MB libre, considera CPX21 más grande)

Si todo falla y necesitas Donna YA, **enciende la Mac** y la versión local
sigue funcionando — Hetzner no es destructivo de la Mac, son entornos
paralelos con la Mac como fuente de verdad del contenido.
