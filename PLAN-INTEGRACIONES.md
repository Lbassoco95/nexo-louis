# Plan de integraciones — Nexo + Microsoft 365 + Kawiil Central

**Fecha:** 2026-05-24
**Pre-requisitos:** Fase 1 completa (Claude API + Ollama + iMessage funcionando).

---

## Replanteamiento basado en descubrimiento de Kawiil Central

**Kawiil Central NO es un dashboard pasivo. Es una plataforma activa de IA colaborativa con:**

- Supabase project ID `qppfampapbxdgednkofc`
- Edge Function `ai-chat` multimodal
- Memoria de equipo `/team/` con vector embeddings
- Proyectos IA compartidos (RLS por organización + membresía)
- Slack ya integrado (notificaciones + eventos)
- Sistema de compliance
- Stack Vite + React + Supabase

**Implicación estratégica:** Nexo y Kawiil Central deben ser piezas complementarias del ecosistema Kawiil, no productos que compiten.

**División de responsabilidades:**

| Capa | Nexo (esta plataforma) | Kawiil Central |
|---|---|---|
| Interfaz humana | iMessage personal del directivo | Web app del equipo |
| Modelo | Híbrido (Ollama local + Claude API) | OpenAI/Claude vía Edge Function |
| Memoria | Personal del directivo (~/.openclaw/memory) | Compartida del equipo (`/team/` en Supabase) |
| Tools | MCPs (Outlook, Slack, Dropbox, FS, Kawiil Central) | Sus propias capabilities |
| Audiencia | Un directivo | Todo el equipo |
| Datos sensibles | Quedan en local cuando aplica | Quedan en Supabase con RLS |

**Nexo consulta a Kawiil Central, no la reemplaza.** Cuando le preguntes a Nexo "¿qué está pasando con el proyecto X?", Nexo va a Kawiil Central, busca en su memoria de equipo / proyectos IA, y trae el contexto. Después razona y te responde.

---

## Arquitectura completa actualizada

```
                  ┌──────────────────────────┐
                  │  iPhone (iMessage)       │
                  │  Polo → Nexo             │
                  └──────────┬───────────────┘
                             │
                  ┌──────────▼───────────────┐
                  │  Mac M5/16GB             │
                  │  OpenClaw Gateway        │
                  │                          │
                  │  Agents:                 │
                  │  • general → Claude API  │
                  │  • legal   → Ollama      │
                  │  • ikan    → Ollama      │
                  └──────────┬───────────────┘
                             │
                ┌────────────┼────────────┬────────────┐
                ▼            ▼            ▼            ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
        │ Outlook  │  │ Calendar │  │  Slack   │  │ Kawiil       │
        │ (MS      │  │ (MS      │  │ (MCP /   │  │ Central      │
        │ Graph)   │  │ Graph)   │  │ API)     │  │ (Supabase)   │
        └──────────┘  └──────────┘  └──────────┘  └──────────────┘
                                                          │
                                            ┌─────────────┴──────────┐
                                            │ • ai_projects          │
                                            │ • shared_memories      │
                                            │ • document_chunks      │
                                            │ • chat_messages        │
                                            │ • compliance tables    │
                                            │ • Edge fn ai-chat      │
                                            └────────────────────────┘
```

---

## Fase 2 — Microsoft 365 (Outlook + Calendar)

**Stack confirmado:** M365 Business Standard / Premium con admin access.
**Auth:** OAuth via Microsoft Entra ID (antes Azure AD).
**API:** Microsoft Graph.

### Paso 2.1 — Registrar app en Entra ID (~15 min, **tú** en portal)

1. Entra a [entra.microsoft.com](https://entra.microsoft.com) con cuenta admin de Kawiil.
2. **App registrations → New registration:**
   - Name: `Nexo (Polo)`
   - Supported account types: **Accounts in this organizational directory only** (single tenant — Kawiil)
   - Redirect URI: dejar en blanco por ahora (lo agregamos abajo)
3. Después del registro, copia:
   - **Application (client) ID** (UUID)
   - **Directory (tenant) ID** (UUID)
4. **Certificates & secrets → New client secret:**
   - Description: `nexo-polo-mac-1`
   - Expires: 24 months
   - Copia el **Value** del secret (solo se muestra una vez)
5. **API permissions → Add a permission → Microsoft Graph → Delegated:**
   - `Mail.Read`
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `Calendars.Read`
   - `Calendars.ReadWrite`
   - `User.Read`
   - `offline_access` (importante: para refresh tokens)
6. **Grant admin consent for Kawiil** (botón en la misma página).
7. **Authentication → Add a platform → Mobile and desktop applications:**
   - Custom redirect URI: `http://localhost:8765/callback`
8. Anota los 3 secretos en un lugar seguro (password manager):
   - Client ID
   - Tenant ID
   - Client Secret

### Paso 2.2 — Instalar MCP server de Microsoft 365 (~5 min, **Cursor**)

Hay varios MCPs comunitarios. Cursor investigará el más mantenido al momento de instalar. Candidatos conocidos:
- `softgridinc-pte-ltd/mcp-microsoft-office` (NPM, mantenido)
- `softgridinc-pte-ltd/mcp-server-microsoft365`
- Custom con `@modelcontextprotocol/sdk`

**Prompt Cursor (Paso 2.2):**

```
Vamos a conectar Microsoft 365 (Outlook + Calendar) a Nexo.

PASO A — Investigar MCP server vigente
=======================================
1. Busca en GitHub MCP servers para Microsoft 365 / Graph:
   - https://github.com/modelcontextprotocol/servers (oficiales)
   - "ms365-mcp", "microsoft-graph-mcp", "outlook-mcp"
2. Reporta cuál tiene:
   - Más estrellas / commits recientes (últimos 30 días)
   - Soporte para Mail (read/send) + Calendar (read/write)
   - OAuth delegated con refresh tokens
3. Si encuentras 2-3 opciones, dame pros/contras y déjame elegir.

NO instales nada todavía — quiero ver primero.

PASO B — Cuando elija, instalar y configurar
=============================================
[Cursor te guiará según el MCP elegido]

PASO C — Credenciales (NO en chat)
====================================
Te voy a pasar 3 valores en Terminal vía read -s:
- Tenant ID
- Client ID
- Client Secret

Configura ~/.openclaw/.env con:
- MS_TENANT_ID
- MS_CLIENT_ID
- MS_CLIENT_SECRET

chmod 600 ~/.openclaw/.env

PASO D — Configurar MCP server en openclaw.json
=================================================
Agrega bajo mcp.servers.outlook (o el nombre que use el MCP elegido).
Backup primero. jq atomic write. Mostrar diff antes de aplicar.

PASO E — OAuth flow
====================
El primer arranque del MCP abrirá un browser para que apruebe scopes con tu cuenta @kawiil.mx.
Pausa para que yo dé el consentimiento manualmente.

PASO F — Smoke tests
=====================
1. "Léeme el subject de mis últimos 3 correos sin leer"
2. "¿Qué tengo agendado para mañana?"
3. "Prepara un borrador de respuesta al último correo de [nombre]"

NO envíes nada todavía — solo lee y redacta.

Reporta resultados.
```

### Paso 2.3 — System prompt update (Cursor, 2 min)

Cuando Outlook responda, agregar al system prompt de `general`:
```
HERRAMIENTAS NUEVAS:
- outlook.mail: lee, busca y redacta correos. NO envía sin "CONFIRMO".
- outlook.calendar: lee eventos, propone bloques disponibles. NO agenda sin "CONFIRMO".

REGLAS:
- Antes de enviar correo: muestra to/cc/subject/body y pide "CONFIRMO".
- Antes de agendar: muestra fecha/hora/invitados/asunto y pide "CONFIRMO".
- Si recibes "modifica X" después de mostrar el borrador, edita y vuelve a confirmar.
```

---

## Fase 2.5 — Kawiil Central (Supabase) — la pieza estratégica

**Decisión clave de seguridad:** ¿Nexo solo LEE de Kawiil Central, o también ESCRIBE?

**Mi recomendación inicial:** Empezar **solo lectura** durante 1-2 semanas. Después decidir si vale la pena darle write.

Ventajas de solo-lectura:
- Si Nexo se equivoca, no rompe nada en Kawiil Central.
- Validamos cobertura de queries antes de darle poder de modificar.
- Polo controla manualmente las acciones de escritura desde la web app.

### Paso 2.5.1 — Crear API key de solo lectura en Supabase (~5 min, **tú**)

1. Entra a [supabase.com/dashboard/project/qppfampapbxdgednkofc](https://supabase.com/dashboard/project/qppfampapbxdgednkofc).
2. **Settings → API:**
   - Anota `URL` (debe ser `https://qppfampapbxdgednkofc.supabase.co`).
   - Anota el `anon` key (público, RLS lo limita).
3. **NO uses el `service_role` key todavía** — esa key bypassa RLS y puede leer/escribir todo. La guardamos para después si la necesitamos.
4. Verifica que las **RLS policies** estén activas en las tablas que Nexo va a leer:
   - `ai_projects` → policy: usuario debe ser dueño o miembro.
   - `ai_project_shared_memories` → policy: solo proyectos accesibles.
   - `document_chunks` → policy: por organización.

### Paso 2.5.2 — Crear un user JWT para Nexo (~10 min, **tú** + Cursor)

Para que Nexo "actúe como Polo" al consultar Kawiil Central, necesita un JWT generado para tu usuario:

**Opción A — Sesión persistente:**
1. Inicia sesión en Kawiil Central web normalmente.
2. Abre DevTools → Application → LocalStorage / Cookies.
3. Encuentra el JWT de Supabase (`sb-qppfampapbxdgednkofc-auth-token`).
4. Cópialo (es largo).
5. En Terminal:
   ```bash
   read -s -p "Pega JWT de Kawiil Central: " KAWIIL_JWT
   echo "KAWIIL_USER_JWT=$KAWIIL_JWT" >> ~/.openclaw/.env
   ```

**Opción B (mejor, más limpio):**
Crear un user "Nexo Bot" en Kawiil Central con email tipo `nexo-bot@kawiil.mx` y darle membresía a los proyectos que quieres que vea. Después generas un JWT específico para ese user. Esto es más limpio que reusar tu sesión personal.

**Recomiendo Opción B**, pero Opción A funciona para arranque rápido.

### Paso 2.5.3 — MCP Supabase o REST tool (~10 min, **Cursor**)

**Prompt Cursor (Kawiil Central):**

```
Vamos a conectar Nexo con Kawiil Central (plataforma propia en Supabase).

CONTEXTO:
- Supabase project ID: qppfampapbxdgednkofc
- URL: https://qppfampapbxdgednkofc.supabase.co
- Tiene tablas: ai_projects, ai_project_members, ai_project_shared_memories, document_chunks, chat_messages
- Edge Function: ai-chat
- RLS activo — Nexo accederá con JWT de usuario, NO con service_role
- Modo: SOLO LECTURA al principio

PASO A — Investigar MCP Supabase
=================================
Busca en GitHub MCPs de Supabase:
- @modelcontextprotocol/server-postgres (oficial — funciona con cualquier Postgres incluyendo Supabase)
- supabase-mcp (de la comunidad)
- Otros

Reporta la mejor opción con:
- Solo SELECT (no UPDATE/DELETE/INSERT) — debe ser configurable
- Soporte para JWT auth contra REST API de Supabase
- Mantenimiento activo

PASO B — Decidir entre 2 caminos
=================================
Camino 1: PostgREST directo (más simple)
- Llamar al REST API auto-generado de Supabase con curl/fetch
- Skill custom muy ligera
- Scopes: solo SELECT, queries específicas

Camino 2: MCP Postgres oficial
- Cliente Postgres tradicional contra Supabase
- Requiere conexión directa a la DB (más permisos)

Recomienda y dame pros/contras.

PASO C — Implementar el elegido
================================
Cuando elija, implementa con:
- SUPABASE_URL=https://qppfampapbxdgednkofc.supabase.co (en .env)
- SUPABASE_ANON_KEY=<lo que yo te pase>
- KAWIIL_USER_JWT=<lo que yo te pase>

Modo: solo lectura. Validar que no hay queries de UPDATE/DELETE/INSERT habilitados.

PASO D — Smoke tests
======================
1. Lista mis proyectos IA en Kawiil Central:
   "¿Qué proyectos tengo en Kawiil Central?"
   Esperado: llamada a get_my_ai_projects() o SELECT de ai_projects donde soy miembro.

2. Búsqueda en memoria de equipo:
   "Busca en la memoria de equipo lo que sabemos del cliente FIATCOIN"
   Esperado: query a ai_project_shared_memories y/o document_chunks con embedding search.

3. Historial de chat:
   "¿Qué dijo el equipo en el chat de Ikán esta semana?"
   Esperado: SELECT chat_messages filtrado por proyecto y fecha.

Reporta cada smoke test. Si algo falla, NO intentes arreglarlo solo — dime el error.
```

### Paso 2.5.4 — Actualizar system prompt de Nexo

Agregar al system prompt de `general`:
```
HERRAMIENTA NUEVA: kawiil-central
- Es la plataforma de IA colaborativa del grupo Kawiil.
- Tiene proyectos compartidos, memoria de equipo, historial de chats del equipo, document chunks con embeddings.
- ÚSALA cuando el usuario pregunte algo que probablemente está documentado por el equipo (proyectos, clientes, decisiones, status).
- ANTES de inventar contexto, consulta Kawiil Central.
- Modo solo lectura: NO intentes escribir, modificar ni borrar nada ahí.
- Si encuentras información, cita la fuente: "Según la memoria de equipo en Kawiil Central [proyecto X]..."
```

---

## Orden de ejecución sugerido

### HOY (lo que falta)
1. **Rotar API key de Claude** (3 min, ya te lo dije).
2. **Full Disk Access** Terminal + Cursor.
3. **Correr `setup-imessage.sh`** + número.
4. **Smoke test desde iPhone** → primera respuesta de Nexo.

### MAÑANA (en este orden)
1. Registrar app en Entra ID (15 min).
2. Pegar a Cursor el prompt Paso 2.2 (Outlook MCP).
3. OAuth flow + smoke tests Outlook.
4. Actualizar system prompt con tools Outlook.

### PASADO MAÑANA
1. Crear JWT de Kawiil Central (opción A rápida o B limpia).
2. Pegar a Cursor el prompt Paso 2.5.3.
3. Smoke tests Kawiil Central.
4. Actualizar system prompt con tool kawiil-central.

### DESPUÉS (esta semana)
- Slack (probablemente puede aprovechar la integración que Kawiil Central ya tiene)
- Dropbox MCP
- Filesystem MCP a `~/Documents/`

---

## Resumen — qué necesito de ti AHORA mismo

Solo para hoy (orden estricto):

| # | Acción | Quién | Tiempo |
|---|---|---|---|
| 1 | Rotar Claude API key (revoke + create + `setup-anthropic-key.sh`) | **Tú** + Terminal | 3 min |
| 2 | Full Disk Access para Terminal y Cursor + cerrarlos y reabrirlos | **Tú** | 2 min |
| 3 | `sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(*) FROM chat;"` debe devolver número | **Tú** verifica | 30 seg |
| 4 | `bash setup-imessage.sh` y pegar tu número en Terminal | **Tú** | 2 min |
| 5 | iMessage desde iPhone a tu propio número: "Hola Nexo" | **Tú** | 1 min |
| 6 | `openclaw pairing approve imessage <CODE>` | **Tú** o Cursor | 30 seg |
| 7 | Esperar respuesta en español MX | Nexo | 5 seg |

**Cuando suceda el paso 7**, ya tienes Fase 1 completa. Avísame con un "ya estoy hablando con Nexo" y arrancamos Fase 2 (Microsoft).
