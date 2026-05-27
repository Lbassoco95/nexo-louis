# Prompts Cursor — Telegram (versión local sin Claude API)

**Pre-requisito:** OpenClaw + Ollama + gpt-oss:20b funcionando. `openclaw infer model run` debe haber respondido "OK" antes de pegar estos prompts.

---

## Pasos manuales (NO en Cursor — los haces tú)

Cursor no puede hacer clicks en interfaces web. Estos 3 pasos los haces directamente en tu celular o laptop:

### A. Crear el bot en BotFather (3 min)
1. Abre Telegram → busca `@BotFather`.
2. Manda `/newbot`.
3. Nombre: `Yoltik AI` (o el que quieras).
4. Username: `yoltik_ai_bot` (si está tomado: `yoltik_kawiil_bot`).
5. Copia el **token** que te da. Formato: `7891234567:ABCdef-GhiJklmNopqrsTuvwxyz`.
6. Guárdalo en tu password manager.

### B. Crear el grupo privado (2 min)
1. Telegram → Nuevo grupo → nómbralo "Yoltik AI · Polo".
2. Agrega tu bot (busca por username).
3. Toca el bot → "Promote to admin" → solo "Read messages" y "Send messages" como permisos.
4. Manda al grupo: `/start`.

### C. Sacar el chat_id del grupo (2 min)
Necesitamos el ID numérico del grupo para limitar el bot a *solo este grupo*.

```bash
# Desde tu Mac (en la terminal):
BOT_TOKEN="PEGA_AQUÍ_EL_TOKEN_DE_BOTFATHER"
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates" | python3 -m json.tool | grep -A 5 "chat"
```

Verás algo como:
```
"chat": {
    "id": -4521839472,
    "title": "Yoltik AI · Polo",
    "type": "supergroup",
```

Apunta el número `id` (incluye el signo negativo). Eso es tu `chat_id`.

---

## Prompt Cursor — conectar Telegram a OpenClaw

Cuando tengas el TOKEN y el CHAT_ID, pega esto en Cursor:

```
Voy a conectar Telegram al gateway de OpenClaw para acceso móvil. El bot está creado, tengo el TOKEN y el CHAT_ID listos pero NO los voy a poner en este chat — los pegaré en la terminal cuando me indiques.

OBJETIVO:
- OpenClaw escucha mensajes solo del CHAT_ID específico (allowlist).
- El bot responde en español usando el modelo local ollama/gpt-oss:20b.
- Cero llamadas a APIs externas para inferencia.

PASO 1 — Investigar comando o config
=====================================
Antes de configurar, descubre cuál es la forma idiomática en OpenClaw 2026.5.22:

openclaw channel --help 2>/dev/null
openclaw onboard --help 2>/dev/null | grep -iE "(telegram|channel)"
ls ~/.openclaw/channels/ 2>/dev/null

Reporta:
- ¿Hay un subcomando "openclaw channel add telegram"?
- ¿O se configura por archivo en ~/.openclaw/channels/telegram.json5?
- ¿O via openclaw config set channels.telegram.*?

Dame la opción más limpia.

PASO 2 — Configurar Telegram
=============================
Una vez sepas la forma correcta, configura el canal con estos requisitos:

a) Token: PAUSA. Te diré "ahora" y yo pegaré el token en la terminal.
   Usa: read -s -p "Token Telegram: " TG_TOKEN; echo
   
b) Chat ID allowlist: solo el CHAT_ID del grupo (también lo pegaré yo).
   Usa: read -p "Chat ID (con signo): " TG_CHAT_ID

c) Bind: solo este grupo, ningún DM. requireMention si OpenClaw lo soporta.

d) Modelo: ollama/gpt-oss:20b (ya configurado en agents.defaults).

Aplica la config y reinicia el daemon:
openclaw daemon restart

PASO 3 — Verificar daemon procesando Telegram
==============================================
Después del restart:

openclaw status
openclaw logs --tail 50 | grep -iE "(telegram|channel)"

Confirma:
- ¿El canal Telegram aparece como "connected" o "ready"?
- ¿Hay errores tipo "401 unauthorized" (token malo) o "polling failed"?

PASO 4 — Smoke test
====================
Voy a mandar un mensaje desde mi celular al grupo. Antes:

# En la terminal, deja corriendo en background:
openclaw logs --follow &
LOGS_PID=$!

Cuando yo te diga "ya mandé", espera 30 seg (gpt-oss:20b tiene cold start) y revisa:
- ¿El log muestra recepción del mensaje?
- ¿OpenClaw lo asignó al space "general"?
- ¿Ollama recibió la solicitud (busca "ollama/gpt-oss" en logs)?
- ¿Hubo respuesta de vuelta a Telegram?

Si después de 90 seg no hay respuesta en el chat, ejecuta:
ollama ps  # ¿el modelo está cargado?
curl -s http://127.0.0.1:11434/api/tags  # ¿Ollama responde?

Mata el follow:
kill $LOGS_PID 2>/dev/null

Reporta:
1. ¿Llegó el mensaje al gateway?
2. ¿Cuánto tardó la respuesta en aparecer en el chat?
3. ¿Calidad de la respuesta (coherente, en español, sin alucinar)?
```

---

## Si todo funciona

Felicidades — tienes un agente AI 100% local accesible desde tu celular. Lo que ves es:

1. Mandas mensaje desde tu celular en Telegram.
2. Telegram lo entrega al bot.
3. OpenClaw lo recibe en tu Mac.
4. OpenClaw lo pasa a Ollama local.
5. gpt-oss:20b genera respuesta en tu Mac.
6. OpenClaw devuelve la respuesta al chat de Telegram.

Cero datos salieron de tu Mac. Cero API keys. Cero costo recurrente.

---

## Si NO funciona

Pega este prompt de rescate en Cursor:

```
Telegram no responde. Diagnóstico paso a paso:

# 1. ¿Ollama vivo y modelo cargado?
curl -s http://127.0.0.1:11434/api/tags
ollama ps

# 2. ¿OpenClaw daemon vivo?
openclaw status
openclaw doctor

# 3. ¿Token de Telegram correcto?
TG_TOKEN=$(jq -r '.channels.telegram.token // .channels.telegram.botToken // "missing"' ~/.openclaw/openclaw.json 2>/dev/null)
if [[ "$TG_TOKEN" != "missing" ]]; then
  curl -s "https://api.telegram.org/bot${TG_TOKEN}/getMe" | python3 -m json.tool
fi
# Si devuelve {"ok":true,"result":...} → token bien.
# Si devuelve {"ok":false,"description":"Unauthorized"} → token mal.

# 4. ¿Chat ID en allowlist?
jq '.channels.telegram' ~/.openclaw/openclaw.json

# 5. Logs recientes con errores
openclaw logs --tail 200 | grep -iE "(error|fail|denied|unauthor)" | head -20

Reporta qué falló y propón un fix. NO toques nada sin que yo apruebe.
```

---

## Próximo paso después de Telegram funcionando

Pega esto para escribir los system prompts adaptados al modelo 20B:

```
Lee el archivo:
/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/system-prompts-gpt-oss-20b.md

Para cada uno de los 3 system prompts (general, legal, ikan):
1. Extrae el bloque de código entre los marcadores ``` correspondientes.
2. Escríbelo a ~/.openclaw/spaces/<nombre>/system-prompt.md
3. mkdir -p si hace falta. chmod 644.

Después listame los 3 archivos creados con head -5 cada uno.
NO reinicies OpenClaw todavía — antes verifico contigo.
```
