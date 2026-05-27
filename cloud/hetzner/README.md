# Louis en Hetzner — paquete `clone & run`

Deploy de Louis (Nexo Personal de Polo) en un VPS de Hetzner. Objetivo: que Louis siga
respondiendo Telegram/Slack/iMessage **aunque la Mac esté apagada**, con la Mac
empujando memoria (`AGENDA.md`, prompts, recuerdos) hacia el cloud cada 5 minutos.

## Arquitectura

```
                       Internet
                          │
                          ▼
                ┌──────────────────┐
                │  Caddy (host:443)│  ← auto-TLS Let's Encrypt
                │  louis.kawiil.mx │
                │  agents.kawiil.mx│
                └────────┬─────────┘
                         │
        ┌────────────────┼──────────────────┐
        ▼                                   ▼
┌──────────────────┐               ┌──────────────────┐
│ OpenClaw         │               │ kawiil-agents    │
│ (systemd nativo) │               │ (docker compose) │
│ localhost:3000   │               │ localhost:8000   │
│ /opt/openclaw    │               │ /opt/kawiil      │
└──────────────────┘               └──────────────────┘
        │                                   │
        ├── Anthropic Claude Sonnet 4.6 ────┘
        ├── Telegram polling
        ├── Slack Socket Mode
        └── M365 Graph (Outlook + Calendar)

         ▲
         │ rsync vía SSH cada 5 min
         │ (sólo memoria + prompts, sin secretos)
         │
    ┌────┴────┐
    │   Mac   │  ← fuente de verdad del contenido
    │ de Polo │  ← los secretos viven sólo en Hetzner
    └─────────┘
```

## Por qué OpenClaw nativo y no en Docker

OpenClaw 2026.5.22 es un instalador oficial (`https://openclaw.ai/install.sh`)
diseñado para correr nativo en Linux con systemd. Meterlo en Docker añade riesgo
sin beneficio (la app es node + sqlite, no necesita aislamiento). Caddy y
`kawiil-agents` sí van en docker-compose porque tienen dependencias más estables.

## Pre-requisitos

1. VPS Hetzner Cloud (CX22 mínimo, **CPX21 recomendado** = 3 vCPU / 4 GB RAM / 80 GB) con Ubuntu 24.04 LTS recién provisionado.
2. Acceso SSH como `root` con tu llave pública.
3. Dominio (ej. `kawiil.mx`) con permiso para crear subdominios `louis` y `agents` apuntando a la IP del VPS.
4. Todos los tokens listos en algún lado (Anthropic, Telegram, Slack, M365). Ver [docs/secrets.md](docs/secrets.md) para cómo extraerlos de la Mac.

## Pasos (≈ 30 min total)

```bash
# 1) Desde tu Mac, sube este paquete al VPS
ssh root@TU_IP_HETZNER 'mkdir -p /opt/louis'
rsync -avz ./ root@TU_IP_HETZNER:/opt/louis/

# 2) SSH al VPS
ssh root@TU_IP_HETZNER
cd /opt/louis

# 3) Edita .env con tus secretos
cp .env.example .env
nano .env          # pega tokens; guarda con Ctrl+O, salir Ctrl+X

# 4) Ejecuta el deploy (idempotente, puedes correrlo de nuevo)
./deploy.sh

# 5) Verifica salud
./verify.sh
```

Cuando `verify.sh` reporte todo OK, mándale un mensaje a Louis por Telegram —
debe responder desde Hetzner sin que la Mac esté encendida.

## Sincronización Mac → Hetzner

Una vez Louis está vivo en Hetzner, en tu **Mac** corres:

```bash
cd ~/Documents/Claude/Projects/Yoltik\ Desarrollos/yoltik-ai-setup/cloud/hetzner/sync
./mac-install.sh louis.kawiil.mx polo
```

Eso registra un `launchd` job que cada 5 minutos empuja:

- `~/.openclaw/spaces/general/AGENDA.md`
- `~/.openclaw/spaces/*/memory/`
- `~/.openclaw/spaces/*/system-prompt.md`

a `/opt/openclaw-sync/` en Hetzner. Un cron en Hetzner aplica el delta a
`/opt/openclaw/spaces/` (con respaldo `.bak.YYYYMMDDHHMMSS` por si rompe algo).

Los **secretos no se sincronizan**: viven solo en `.env` del VPS. La Mac queda
como fuente de verdad del contenido (memoria), Hetzner queda como fuente de
verdad de los tokens y de Louis vivo.

## Capas de seguridad

- SSH solo por llave, root login deshabilitado, `polo` con sudo.
- `ufw` permitiendo 22, 80, 443. Nada más.
- `fail2ban` con jail SSH.
- `unattended-upgrades` para parches automáticos.
- Caddy auto-TLS Let's Encrypt.
- OpenClaw y kawiil-agents bound a `127.0.0.1` — solo accesibles vía Caddy.
- Acceso a la URL pública protegido por Cloudflare Access (paso opcional documentado en runbook).

## Archivos

```
hetzner/
├── README.md                     ← estás aquí
├── deploy.sh                     ← orquestador (bootstrap + servicios)
├── verify.sh                     ← health-check
├── .env.example                  ← plantilla de secretos
├── docker-compose.yml            ← Caddy + kawiil-agents
├── Caddyfile                     ← reverse proxy + TLS
├── bootstrap/
│   ├── 01-harden.sh             ← ufw, fail2ban, swap, unattended-upgrades
│   ├── 02-user.sh               ← crea usuario polo + SSH key + lock root
│   ├── 03-docker.sh             ← Docker Engine + compose plugin
│   └── 04-node.sh               ← Node.js 22 LTS para OpenClaw
├── openclaw/
│   ├── install.sh               ← descarga e instala OpenClaw nativo
│   └── openclaw.service         ← systemd unit (Restart=always)
├── kawiil-agents/
│   └── Dockerfile               ← imagen FastAPI (uvicorn)
├── sync/
│   ├── mac-install.sh           ← se corre en Mac: instala launchd + SSH key
│   ├── mac-push.sh              ← el rsync que ejecuta el launchd
│   ├── mac-launchd.plist        ← plantilla del launchd
│   ├── rsync-files.txt          ← include/exclude del rsync
│   ├── hetzner-prepare.sh       ← se corre en Hetzner: prepara /opt/openclaw-sync
│   └── hetzner-apply.sh         ← se corre en Hetzner por cron: aplica delta
└── docs/
    ├── runbook.md               ← día 2: logs, reinicios, restore, backups
    └── secrets.md               ← cómo extraer tokens desde la Mac
```

## Costos esperados

- Hetzner CPX21: €7.05 / mes (≈ $7.50 USD)
- Anthropic API: $30–60 USD/mes (igual que hoy)
- Dominio: ya lo tienes
- Total marginal de la migración: **~$7.50 USD/mes**

## Si algo sale mal

Lee [docs/runbook.md](docs/runbook.md). En particular:
- Logs OpenClaw: `journalctl -u openclaw -f`
- Logs Caddy: `docker compose logs -f caddy`
- Logs kawiil: `docker compose logs -f kawiil-agents`
- Rollback completo: `./deploy.sh --rollback` (deja servicios apagados pero conserva datos)

Cualquier problema con la sincronización Mac → Hetzner aparece en
`~/Library/Logs/louissync.log` en la Mac y en `/var/log/louis-sync.log`
en Hetzner.
