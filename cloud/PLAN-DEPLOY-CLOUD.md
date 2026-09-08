# Plan: deploy de Donna + kawiil-agents en cloud (no depender de Mac)

Objetivo: Donna y el server kawiil-agents corren en cloud 24/7. Mac apagada, dormida o sin internet → Donna sigue respondiendo Telegram/Slack.

## Inventario actual (qué corre dónde)

| Componente | Hoy | Próximo |
|---|---|---|
| **OpenClaw gateway** (Donna core) | Mac LaunchAgent localhost:3000 | VPS Linux con auto-restart |
| **kawiil-agents FastAPI** | Mac LaunchAgent localhost:8000 | Render.com / Fly.io |
| **Supabase qppfampapbxdgednkofc** | Ya en cloud (Supabase managed) | Sin cambio |
| **Telegram bot** | Polling vía gateway en Mac | Polling vía gateway en VPS |
| **Slack bot** | Socket Mode vía gateway en Mac | Socket Mode vía gateway en VPS |
| **Whisper transcripción** | Pendiente — usaría Mac | API OpenAI Whisper en cloud |
| **iCloud Reminders** | Requiere Mac (AppleScript) | Se mantiene en Mac como fallback, o sustituye por Telegram alarm |
| **m365.py (Outlook/Calendar)** | Mac LaunchAgent | Cloud — solo refresh_token migra |

## Opciones de cloud, comparadas

| Proveedor | Pros | Contras | Costo |
|---|---|---|---|
| **Render.com** | Free tier 750h, deploy desde GitHub, fácil envs | Cold start ~30s en free, RAM 512MB | $0-7 USD/mes |
| **Fly.io** | Generosos free, edge locations, fly machines suspend/resume | Setup más técnico (Dockerfile, fly.toml) | $0-5 USD/mes |
| **Railway** | UI muy amigable, $5 USD crédito mensual | Plan free terminó (ahora pago desde inicio) | $5-10 USD/mes |
| **DigitalOcean Droplet** | Control total Linux, IP fija | Tú administras OS, parches, backups | $4-12 USD/mes |
| **AWS EC2 t4g.nano** | ARM económico, integración AWS | Más complejo, costos crecen rápido | $3-8 USD/mes |
| **Hetzner CX11** | Mejor relación precio/perf en EU | Latencia a Mexico ~150ms | €4 EUR/mes ≈ $4 USD |

## Recomendación

**Fase A — esta semana**: Render.com para kawiil-agents (solo el server FastAPI).
- Free tier suficiente para empezar.
- Push a GitHub → Render auto-build → URL pública `kawiil-agents.onrender.com`.
- Variables de entorno se cargan desde dashboard Render.
- Donna (gateway en Mac) llama a esa URL en lugar de localhost:8000.
- **Ventaja**: si Mac se apaga, los 7 agentes siguen disponibles. Donna los puede invocar cuando Mac vuelva.

**Fase B — próxima semana**: Migrar OpenClaw gateway a VPS (DigitalOcean o Hetzner $4-5 USD/mes).
- OpenClaw soporta install en Linux.
- Misma config + tokens + canales (Telegram, Slack, M365) — solo cambiar paths y reinstalar.
- Mac queda como cliente que se conecta al gateway en VPS (similar a como hoy iPad se conecta).
- Donna vive en cloud, Mac es solo un canal más (WebChat).

**Fase C — futuro**: Considerar VPS dedicado para Whisper (si seguimos audio) y Mac Mini headless para iCloud Reminders + tareas que requieren macOS APIs.

## Costos estimados después de migrar

- Render free → $0-7
- VPS gateway → $4-5
- Supabase → $0 (sigues en free tier)
- Anthropic Claude → ~$30-60/mes (igual que hoy)
- Telegram → $0
- Slack → $0
- iCloud → $0 (cuenta personal)

**Total mensual estimado**: ~$35-70 USD/mes. Ya estás gastando lo de Anthropic, los $5-15 de infra es marginal.

## Pasos prácticos (cuando estés listo)

### Render kawiil-agents (1-2 horas)

1. En `kawiil-agents/server/`, agregar `render.yaml` o configurar desde dashboard
2. Crear cuenta en render.com con tu GitHub
3. New → Web Service → connect repo Lbassoco95/kawiil-agents → root dir `server`
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
6. Environment vars: las mismas del `.env` local (SUPABASE_URL, SERVICE_KEY, ANTHROPIC_API_KEY, ORG IDs, DISPATCH_TOKEN)
7. Deploy. Render asigna URL pública.
8. Actualizar Donna tool para apuntar a esa URL en lugar de localhost:8000

### VPS OpenClaw gateway (2-3 horas)

1. Provision Droplet/Server (Ubuntu 22.04 LTS, 1 vCPU / 1GB RAM mínimo)
2. SSH + setup básico (firewall ufw, fail2ban, swap)
3. Install Node.js 22
4. `curl -fsSL https://openclaw.ai/install.sh | bash`
5. Copiar config de Mac (`~/.openclaw/openclaw.json` + credentials)
6. Re-registrar canales (los tokens son los mismos)
7. Apuntar dominio (ej. `louis.kawiil.mx` con Cloudflare Tunnel o Cloudflare DNS + nginx)
8. Activar Cloudflare Access para que solo Polo pueda accederlo

## Trade-offs honestos

**Pro de migrar a cloud:**
- Donna nunca duerme
- No depende de Mac, energía eléctrica, internet de casa
- Puede agregar más usuarios fácilmente (futuro Nexo Equipo)

**Contra:**
- Costo recurrente $10-20/mes adicional
- Más superficie de ataque (servidor expuesto a internet)
- iCloud Reminders deja de funcionar (porque AppleScript requiere macOS)
- Whisper local pierde sentido (mejor pagar API)

## ¿Qué hacer hoy?

Decisión más simple: **mantén Mac corriendo por ahora**, agendamos Render kawiil-agents para esta semana, y VPS gateway la próxima.

Si quieres acelerar, lo más impactante primero es subir kawiil-agents a Render (1-2h) — eso ya libera 50% de la dependencia de Mac, porque los agentes especialistas viven en cloud y Donna los invoca desde cualquier lugar.
