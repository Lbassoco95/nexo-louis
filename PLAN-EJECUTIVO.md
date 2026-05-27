# Plan Ejecutivo — Yoltik AI nivel "asistente de primer nivel"

**Fecha:** 2026-05-24
**Stack del negocio:** Microsoft 365 (Outlook + Calendar), Slack, Dropbox, archivos locales
**Modelo:** Híbrido — Ollama local + Claude API
**Canal humano:** iMessage (desde tu iPhone)

---

## Arquitectura final

```
┌────────────────────────────────────────────────────────────┐
│  Tu Mac M5/16GB                                            │
│                                                            │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │ Ollama 127.0.0.1 │         │ Claude API       │         │
│  │ gpt-oss:20b      │         │ (Anthropic)      │         │
│  │ (PRIVADO)        │         │ (CALIDAD)        │         │
│  └────────┬─────────┘         └────────┬─────────┘         │
│           │                            │                   │
│           └────────────┬───────────────┘                   │
│                        ▼                                   │
│  ┌──────────────────────────────────────────────┐          │
│  │ OpenClaw Gateway (127.0.0.1:3000)            │          │
│  │                                              │          │
│  │ Agents:                                      │          │
│  │  • general → claude-sonnet (ejecutivo)       │          │
│  │  • legal   → ollama/gpt-oss (privado)        │          │
│  │  • ikan    → ollama/gpt-oss (privado, PLD)   │          │
│  │                                              │          │
│  │ MCPs conectados:                             │          │
│  │  • outlook  → email + calendar (MS Graph)    │          │
│  │  • slack    → enviar mensajes                │          │
│  │  • dropbox  → leer archivos                  │          │
│  │  • filesys  → ~/Documents (local)            │          │
│  │                                              │          │
│  │ Memoria persistente: ~/.openclaw/memory/     │          │
│  └──────────────────┬───────────────────────────┘          │
└────────────────────│───────────────────────────────────────┘
                     ▼
         ┌─────────────────────────┐
         │ iMessage (tu iPhone)    │
         │ Messages.app (tu Mac)   │
         └─────────────────────────┘
```

**Routing inteligente:**
- Cuando hablas desde Messages al agente `general` → usa **Claude Sonnet 4.6** → calidad ejecutiva.
- Cuando trabajas en algo del espacio `legal` o `ikan` → usa **Ollama local** → cero datos a terceros.
- Las acciones (mandar correo, buscar en Dropbox) se ejecutan vía MCP, no por el modelo directamente — el modelo solo decide *qué* hacer; el MCP *hace*.

---

## Roadmap por fases

### ✅ Hecho

- OpenClaw 2026.5.22 instalado, hardened, daemon corriendo.
- validate.sh pasa en verde.
- Ollama + gpt-oss:20b funcionando (smoke test pendiente de confirmar).
- Bundle de scripts listo para reproducir todo.

### 🟢 Fase 1 · HOY — Base ejecutiva (1-2 horas)

**Objetivo:** Tener un asistente ejecutivo que YA conteste con calidad Claude desde iMessage.

| # | Acción | Quién | Tiempo |
|---|---|---|---|
| 1.1 | Smoke test final Ollama (terminar lo que está corriendo) | Cursor | 5 min |
| 1.2 | Agregar **Claude API** como segundo provider | Cursor | 10 min |
| 1.3 | Configurar **3 agents** (general/legal/ikan) cada uno con su modelo | Cursor | 5 min |
| 1.4 | Escribir **system prompts** del archivo `system-prompts-gpt-oss-20b.md` | Cursor | 5 min |
| 1.5 | Permisos macOS para iMessage (Full Disk Access + Automation) | **Tú** | 3 min |
| 1.6 | Instalar **imsg** y configurar canal iMessage | Cursor | 10 min |
| 1.7 | Smoke test desde iPhone | **Tú** + Cursor | 5 min |

**Resultado fin Fase 1:** Mandas iMessage → Claude Sonnet responde con calidad ejecutiva en español mexicano.

### 🟡 Fase 2 · MAÑANA — Outlook (Microsoft 365)

**Objetivo:** Que el agente pueda leer tu correo y agenda, y redactar respuestas.

| # | Acción | Quién | Tiempo |
|---|---|---|---|
| 2.1 | Registrar app en **Azure AD** (Microsoft Entra) para OAuth | **Tú** (en portal.azure.com) | 15 min |
| 2.2 | Obtener client_id, tenant_id, client_secret | **Tú** | 2 min |
| 2.3 | Instalar **outlook-mcp** (server MCP de la comunidad) | Cursor | 5 min |
| 2.4 | OAuth flow para autorizar tu cuenta @kawiil.mx | **Tú** | 2 min |
| 2.5 | Configurar `mcp.servers.outlook` en openclaw.json | Cursor | 5 min |
| 2.6 | Smoke test: "Léeme mi calendario de hoy" | **Tú** | 1 min |
| 2.7 | Smoke test: "Prepara borrador de respuesta al correo de Marco" | **Tú** | 1 min |

**Resultado fin Fase 2:** El agente lee tu inbox de Outlook, ve tu calendario, prepara borradores. NO envía sin tu OK.

### 🟡 Fase 3 · ESTA SEMANA — Slack + Dropbox + archivos locales

**Objetivo:** Completar el "Swiss Army knife" digital.

| # | Acción | Quién | Tiempo |
|---|---|---|---|
| 3.1 | Crear app de Slack en api.slack.com/apps | **Tú** | 10 min |
| 3.2 | Generar tokens (bot + user) con scopes adecuados | **Tú** | 5 min |
| 3.3 | Conectar **@modelcontextprotocol/server-slack** | Cursor | 5 min |
| 3.4 | Generar Dropbox API key en developers.dropbox.com | **Tú** | 10 min |
| 3.5 | Conectar **dropbox-mcp** | Cursor | 5 min |
| 3.6 | Conectar **@modelcontextprotocol/server-filesystem** a `~/Documents/` | Cursor | 5 min |
| 3.7 | Smoke test combinado: "Busca el último contrato del cliente X en Dropbox, mándalo a Pedro por Slack" | **Tú** | 2 min |

**Resultado fin Fase 3:** Asistente con acceso a TODO tu stack. Puede orquestar tareas multi-aplicación con una sola instrucción.

### 🟠 Fase 4 · PRÓXIMA SEMANA — Memoria del negocio y proactividad

**Objetivo:** Que el agente entienda Kawiil/Yoltik/Ikán/Kailash como entendería una secretaria de 5 años.

| # | Acción | Quién | Tiempo |
|---|---|---|---|
| 4.1 | Crear **memoria estructurada** del negocio (Kawiil, Yoltik, Ikán, Kailash, contactos clave, KPIs) | **Tú** + Cursor | 1 h |
| 4.2 | Skill **morning-brief** — digest matutino auto cada día 7am | Cursor | 30 min |
| 4.3 | Skill **meeting-prep** — antes de cada junta, prepara contexto | Cursor | 30 min |
| 4.4 | Skill **pld-monitor** (Ikán) — scrape DOF/CNBV semanal | Cursor | 1 h |
| 4.5 | Skill **end-of-day** — resumen del día, pendientes para mañana | Cursor | 30 min |
| 4.6 | Cron jobs en OpenClaw para los 4 skills | Cursor | 15 min |

**Resultado fin Fase 4:** Agente que ya no esperas que le hables — el viene a ti con la información clave del día.

### 🔵 Fase 5 · CUANDO ESTÉS LISTO — Onboarding directivos + acceso web

| # | Acción | Quién | Tiempo |
|---|---|---|---|
| 5.1 | Cloudflare Tunnel + Access para `ai.kawiil.mx` | Cursor + **Tú** dashboard | 1 h |
| 5.2 | Onboarding directivos: cada uno con su número en allowlist iMessage | **Tú** | 30 min/persona |
| 5.3 | Mac Mini M4 Pro 64GB dedicada (cuando justifique) | **Tú** (compra) | — |
| 5.4 | Migrar todo a Mac Mini, decommission de tu MacBook como bot | Cursor + **Tú** | 2 h |

---

## MCPs que vamos a conectar — referencia técnica

| Integración | MCP server | Tipo | Auth | Repo |
|---|---|---|---|---|
| **Outlook** (mail + calendar) | `outlook-mcp` (comunidad) o `ms365-mcp-server` | stdio | OAuth Azure AD | Buscar el más mantenido al momento de instalar |
| **Slack** | `@modelcontextprotocol/server-slack` | stdio | Bot token + User token | github.com/modelcontextprotocol/servers |
| **Dropbox** | `mcp-server-dropbox` o `@dropbox/mcp-server` | stdio | OAuth Dropbox | Cursor lo descubrirá |
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | stdio | Path allowlist | github.com/modelcontextprotocol/servers — oficial |
| **Memoria** | `@modelcontextprotocol/server-memory` | stdio | Local | github.com/modelcontextprotocol/servers — oficial |
| **Web search** | Ollama Web Search (ya viene bundled) o Brave Search MCP | stdio | API key Brave | Para investigar competencia, regulatorias |

Nota importante: la velocidad de evolución de MCPs es alta. Cuando Cursor llegue a instalar cada uno, le pedirás que verifique el repo más actual en GitHub antes de instalar — para evitar instalar un servidor abandonado.

---

## Decisión clave para Fase 1: API key de Claude

Tienes 3 opciones para arrancar la capa cloud:

| Opción | Costo | Modelos | Tradeoff |
|---|---|---|---|
| **Anthropic Console pay-as-you-go** | ~$3-5/mes por uso ejecutivo medio | Sonnet 4.6, Opus 4.6, Haiku | El más simple, lo recomendado para arrancar. |
| **Claude Pro/Max suscripción + proxy** | $20-200/mes flat | Sonnet/Opus | Si ya tienes Claude Max, puedes usar un proxy tipo `claude-code-proxy` (vimos a alguien hacerlo en los testimonios de openclaw.ai). |
| **OpenRouter (multi-model)** | Variable | Claude + GPT + Gemini + open | Más flexibilidad, paga por token. Más complejo. |

**Mi recomendación: empieza con Anthropic Console pay-as-you-go.** Cap mensual de $100 USD para evitar sorpresas. Después decides si conviene migrar.

---

## Próximo paso INMEDIATO — Prompt Cursor

**Cuando Cursor termine el smoke test de Ollama** (lo de gpt-oss:20b que está corriendo), pega esto:

```
Pasamos a Fase 1.2 del PLAN-EJECUTIVO.md: agregar Claude API como segundo provider para que el space "general" tenga calidad ejecutiva.

Lee primero:
/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/PLAN-EJECUTIVO.md

PASO A — API key
================
Necesito mi API key de Anthropic. Antes de pedírmela:

1. Recuérdame ir a console.anthropic.com → API Keys → Create Key con nombre "yoltik-ai-mac-polo".
2. Recuérdame ir a Settings → Plans & Billing → Usage Limits → poner $100 USD/mes como hard cap.
3. Después yo pego la key en la terminal, no en el chat.

read -s -p "Pega tu API key de Anthropic (no se mostrará): " ANTHROPIC_KEY
echo
# Validar formato
if [[ ! "$ANTHROPIC_KEY" =~ ^sk-ant-[a-zA-Z0-9_-]+$ ]]; then
  echo "Formato inválido. Debe empezar con sk-ant-"; exit 1
fi

# Guardar en ~/.openclaw/.env (perms 600)
mkdir -p ~/.openclaw
if grep -q "^ANTHROPIC_API_KEY=" ~/.openclaw/.env 2>/dev/null; then
  # Reemplazar línea existente
  sed -i.bak "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$ANTHROPIC_KEY|" ~/.openclaw/.env
  rm ~/.openclaw/.env.bak
else
  echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" >> ~/.openclaw/.env
fi
chmod 600 ~/.openclaw/.env

unset ANTHROPIC_KEY

PASO B — Configurar Anthropic como provider
============================================
Aplica esta config con jq (atomic write, backup primero):

cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.backup-pre-anthropic-$(date +%Y%m%d-%H%M%S)

TMP=$(mktemp)
jq '
  (.models //= {}) |
  (.models.providers //= {}) |
  .models.providers.anthropic = {
    apiKey: "ANTHROPIC_API_KEY",
    api: "anthropic"
  }
' ~/.openclaw/openclaw.json > "$TMP" && mv "$TMP" ~/.openclaw/openclaw.json
chmod 600 ~/.openclaw/openclaw.json

PASO C — Configurar 3 agents con routing por modelo
====================================================
TMP=$(mktemp)
jq '
  (.agents //= {}) |
  (.agents.list //= []) |
  .agents.list = [
    {
      id: "general",
      workspace: "~/.openclaw/spaces/general",
      model: { primary: "anthropic/claude-sonnet-4-6" }
    },
    {
      id: "legal",
      workspace: "~/.openclaw/spaces/legal",
      model: { primary: "ollama/gpt-oss:20b" }
    },
    {
      id: "ikan",
      workspace: "~/.openclaw/spaces/ikan",
      model: { primary: "ollama/gpt-oss:20b" }
    }
  ] |
  .agents.defaults = (.agents.defaults // {}) |
  .agents.defaults.model = { primary: "anthropic/claude-sonnet-4-6", fallbacks: ["ollama/gpt-oss:20b"] }
' ~/.openclaw/openclaw.json > "$TMP" && mv "$TMP" ~/.openclaw/openclaw.json
chmod 600 ~/.openclaw/openclaw.json

# Verificar
jq '.models.providers, .agents' ~/.openclaw/openclaw.json

PASO D — Validar y reiniciar
=============================
openclaw config validate
openclaw daemon restart
sleep 5
openclaw status
openclaw models list

# El modelo "anthropic/claude-sonnet-4-6" debe aparecer en la lista.

PASO E — Smoke test del cerebro ejecutivo
==========================================
openclaw infer model run \
  --model "anthropic/claude-sonnet-4-6" \
  --prompt "Eres asistente ejecutivo del CEO de Kawiil. Responde en español mexicano profesional. Solo di OK si me entiendes y puedes operar a este nivel." \
  --json

Reporta:
1. ¿Claude respondió "OK" o equivalente?
2. ¿En español mexicano?
3. ¿Tono profesional?

PASO F — Smoke test del routing
================================
openclaw infer model run \
  --model "ollama/gpt-oss:20b" \
  --prompt "OK si me entiendes." \
  --json

Confirma:
- Claude API → responde rápido (~1-3 seg)
- Ollama local → responde más lento (~10-30 seg)
- Ambos funcionan en paralelo

Después dame el resumen final: ¿está listo para configurar iMessage con Claude detrás?

NO toques iMessage todavía — quiero confirmar el split de modelos primero.
```

---

## Resumen de orden — qué hacer ahora vs después

**AHORA (próximas 2 horas):**
1. Esperar a que Cursor termine el smoke test de Ollama.
2. Pegar el prompt de arriba (agregar Claude API).
3. Cuando ambos modelos pasen smoke, pegar el prompt de iMessage (`prompts-cursor-imessage.md`).
4. Mandar primer mensaje desde tu iPhone.
5. Verificar que el space general usa Claude (más rápido, mejor español).

**Si llega la noche y solo terminas hoy lo de Claude + iMessage:** ya tienes algo útil. Mañana atacas Outlook.

**No quiero que hagas todo en un solo sentón.** El roadmap está partido en fases por una razón: cada fase es valor entregado en sí misma.

---

## Lo que YA tienes vs lo que falta

**Ya tienes:**
- ✅ OpenClaw instalado y hardened
- ✅ Modelo local funcionando
- ✅ Bundle de scripts reproducible
- ✅ Plan de Cloudflare para acceso remoto (sábado)
- ✅ Documento de prompts para Cursor

**Por agregar HOY:**
- 🔲 Claude API como segundo provider (15 min)
- 🔲 3 agents con routing por modelo (incluido en el prompt)
- 🔲 iMessage funcional (15 min)

**Por agregar ESTA SEMANA:**
- 🔲 Outlook MCP (correo + calendario)
- 🔲 Slack MCP
- 🔲 Dropbox MCP
- 🔲 Filesystem MCP
- 🔲 System prompts adaptados
- 🔲 Memoria del negocio
