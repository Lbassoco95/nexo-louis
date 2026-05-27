# Prompts Cursor — iMessage (canal de mensajería elegido)

**Decisión:** iMessage en modo BÁSICO (SIP encendido). Texto + media + allowlist. Sin reacciones/edit/unsend para mantener SIP encendido y conservar las protecciones nativas de macOS.

**Pre-requisito:** OpenClaw + Ollama + modelo local funcionando. `openclaw infer model run` ya respondió "OK".

---

## FASE 1 · Permisos macOS (3 min, manual)

Antes de instalar imsg, necesitas dar 3 permisos a macOS. Hazlo manualmente, Cursor no puede:

### A. Verifica que Messages.app está signed in
1. Abre **Messages.app** en tu Mac.
2. Si pide login con Apple ID → entra con el mismo Apple ID que tu iPhone.
3. Configuración → Messages → debe estar `[email/teléfono]` listado bajo "You can be reached for messages at".
4. Manda un iMessage a tu propio número desde Messages.app → debe llegar.

Si esto no funciona, **detente aquí** — el resto no funcionará sin Messages.app activo. Arregla Messages primero (Apple ID, iCloud, doble factor, etc.).

### B. Full Disk Access
1. Configuración del Sistema → Privacidad y Seguridad → Full Disk Access.
2. Activa el toggle para **Terminal** (o iTerm si usas iTerm) y **Cursor**.
3. Si Terminal o Cursor no aparecen: presiona `+` y agrégalos desde Aplicaciones.
4. Cierra y vuelve a abrir Terminal/Cursor.

Por qué: imsg necesita leer `~/Library/Messages/chat.db` para detectar mensajes entrantes. macOS protege ese path con Full Disk Access.

### C. Automation: permitir controlar Messages
1. Configuración del Sistema → Privacidad y Seguridad → Automation.
2. Bajo Terminal (o Cursor) → activa el toggle para **Messages**.
3. Si no ves Messages bajo Terminal, no te preocupes — aparecerá cuando imsg intente enviar el primer mensaje y te saltará un prompt.

Por qué: imsg envía mensajes salientes via AppleScript/Automation a Messages.app.

---

## FASE 2 · Prompt Cursor — Instalación y config

Cuando los 3 permisos estén listos, pega esto en Cursor:

```
Voy a configurar iMessage como canal de mensajería para OpenClaw. Modo BÁSICO (SIP encendido, sin private API). Ya tengo:
- Messages.app signed in y funcional
- Full Disk Access activado para Terminal y Cursor
- Automation: Terminal → Messages activado

PASO 1 — Verificar pre-requisitos automáticamente
==================================================
Corre estos checks y reporta:

# Messages.app instalado y signed in
defaults read com.apple.iChat 2>/dev/null | grep -i Account | head -5 || echo "no Messages account info"

# Existe chat.db con permisos de lectura
ls -la ~/Library/Messages/chat.db 2>&1 | head -3

# Probar acceso (esto valida Full Disk Access)
sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(*) FROM chat;" 2>&1 | head -3

Si el último comando devuelve un número entero → Full Disk Access bien configurado.
Si devuelve "unable to open database" o "operation not permitted" → falta Full Disk Access. Para todo y avísame.

PASO 2 — Instalar imsg
=======================
brew tap steipete/tap 2>/dev/null || true
brew install steipete/tap/imsg

# Verificar instalación
imsg --version
imsg rpc --help 2>&1 | head -10

# Probar conexión a Messages
imsg chats --limit 1 2>&1 | head -10

Si el último comando lista al menos un chat → todo OK.
Si pide permisos (popup de macOS) → autoríza y vuelve a correr.
Si devuelve error de permisos → falta Full Disk Access o Automation.

PASO 3 — Pedirme el número de iPhone para allowlist
====================================================
PAUSA. NO escribas mi número en el chat. Pide que yo lo pegue en la terminal:

read -p "Número de iPhone con código país (ej +5215512345678): " MY_PHONE
echo "Número recibido: ${MY_PHONE:0:5}...${MY_PHONE: -4}"

Valida que tiene formato +<digitos>: 
if [[ ! "$MY_PHONE" =~ ^\+[0-9]{10,15}$ ]]; then
  echo "Formato inválido"; exit 1
fi

Continúa solo si el formato es válido.

PASO 4 — Configurar el canal iMessage
======================================
Aplica esta config al openclaw.json (usa jq + atomic write, NO sobrescribas todo el archivo):

# Backup primero
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.backup-pre-imessage-$(date +%Y%m%d-%H%M%S)

# Detectar la ruta correcta del cliPath
IMSG_PATH=$(command -v imsg)
echo "imsg path: $IMSG_PATH"

# Aplicar config con jq
TMP=$(mktemp)
jq --arg cliPath "$IMSG_PATH" \
   --arg dbPath "$HOME/Library/Messages/chat.db" \
   --arg myPhone "$MY_PHONE" '
  (.channels //= {}) |
  .channels.imessage = {
    enabled: true,
    cliPath: $cliPath,
    dbPath: $dbPath,
    dmPolicy: "allowlist",
    allowFrom: [$myPhone],
    groupPolicy: "disabled"
  }
' ~/.openclaw/openclaw.json > "$TMP" && mv "$TMP" ~/.openclaw/openclaw.json
chmod 600 ~/.openclaw/openclaw.json

# Verificar que aplicó
jq '.channels.imessage' ~/.openclaw/openclaw.json

PASO 5 — Validar config y reiniciar
=====================================
openclaw config validate 2>&1 | head -20
openclaw daemon restart
sleep 5
openclaw status

# Confirmar que el canal está activo
openclaw channels status --probe 2>&1 | tail -20

Buscamos: en la salida debe aparecer "imessage" como "works" o "ready" o "available: true".

Si dice "imsg not found" → revisa $PATH del daemon.
Si dice "permission denied" → falta Full Disk Access en el contexto del daemon (corre `imsg chats --limit 1` para forzar el prompt de permisos).

PASO 6 — Smoke test desde mi iPhone
====================================
Antes de avisarme:

# Deja siguiendo los logs en background
openclaw logs --follow 2>&1 | grep -iE "(imessage|imsg|message)" &
LOGS_PID=$!

Cuando yo te diga "ya mandé el mensaje", espera 60 segundos (gpt-oss:20b tiene cold start) y reporta:

1. ¿El log muestra recepción del mensaje?
2. ¿Hubo pairing pending? Si sí, corre:
   openclaw pairing list imessage
   openclaw pairing approve imessage <CODE>
3. ¿OpenClaw lo asignó al space "general" y al modelo ollama/<modelo>?
4. ¿Hubo respuesta enviada de vuelta a iMessage?

Mata el follow:
kill $LOGS_PID 2>/dev/null

Reporta el resultado.
```

---

## FASE 3 · Smoke test desde tu iPhone

Cuando Cursor termine el Paso 5 y te diga "listo, manda mensaje":

1. Abre **Messages** en tu iPhone.
2. Manda un iMessage a **tu propio número** (que es lo mismo que abrir tu chat contigo mismo).
   - Si tu iPhone y Mac usan el mismo Apple ID, ese mensaje llega también a tu Mac via iMessage.
3. Mensaje sugerido: *"Hola, ¿qué tal? Confirma que recibes."*
4. Avísale a Cursor "ya mandé" para que revise los logs.
5. **Espera hasta 90 segundos** para la primera respuesta (cold start del modelo).

**Primera vez:** verás un código de pairing en los logs. Cursor te lo aprueba automáticamente con `openclaw pairing approve imessage <CODE>`.

**Segunda vez:** ya queda paired, los mensajes responden de inmediato (~5-20 seg con gpt-oss:20b).

---

## FASE 4 · Si algo falla — prompts de rescate

### R1 — "Full Disk Access denied" o sqlite3 chat.db falla

```
sqlite3 ~/Library/Messages/chat.db dijo "operation not permitted". Necesito otorgar Full Disk Access en el contexto correcto.

# Detecta qué proceso corre OpenClaw (LaunchAgent, manual, Terminal hijo, etc.)
ps -ax | grep openclaw | grep -v grep

# El proceso padre necesita FDA. Si es bash/Terminal → da FDA a Terminal.
# Si es launchd → da FDA al "openclaw" binary (Configuración → Full Disk Access → + → /usr/local/bin/openclaw o $(which openclaw))

# Forzar prompts de permisos via una operación que requiera ambos:
imsg chats --limit 1
# macOS te pedirá Full Disk Access primero, después Automation. Acepta ambos.

# Verifica
imsg chats --limit 1 | head -5

Cuando esto funcione, vuelve al PASO 5 del flujo principal.
```

### R2 — Channel "imessage" no aparece en status --probe

```
El canal iMessage no aparece como activo. Diagnóstica:

# 1. ¿Está habilitado en el config?
jq '.channels.imessage' ~/.openclaw/openclaw.json

# 2. ¿imsg está en PATH del proceso?
which imsg
ls -la $(jq -r '.channels.imessage.cliPath' ~/.openclaw/openclaw.json)

# 3. ¿Errores en logs del daemon?
openclaw logs --tail 200 | grep -iE "(imessage|imsg|chat.db)" | tail -30

# 4. ¿El daemon se reinició después de cambiar el config?
openclaw status | head -10

Posibles fixes:
- cliPath incorrecto → corregir con: openclaw config set channels.imessage.cliPath "$(which imsg)"
- Daemon viejo → openclaw daemon restart
- Validation failed → openclaw doctor

NO cambies cosas grandes sin avisarme.
```

### R3 — Mensaje llega pero no hay respuesta

```
iMessage llegó al gateway pero no respondió. Diagnóstica:

# 1. ¿Pairing pending?
openclaw pairing list imessage

# 2. ¿El allowlist permite mi número?
jq '.channels.imessage.allowFrom' ~/.openclaw/openclaw.json
# Mi número debe estar ahí EXACTO con +52.

# 3. ¿OpenClaw enrutó al agente correcto?
openclaw logs --tail 200 | grep -iE "(routed|dispatch|agent)" | tail -10

# 4. ¿Ollama recibió la llamada?
openclaw logs --tail 200 | grep -iE "(ollama|provider)" | tail -10

# 5. ¿Modelo local respondió?
ollama ps
curl -s http://127.0.0.1:11434/api/tags

Reporta diagnóstico antes de tocar nada.
```

### R4 — Respuesta tarda más de 2 minutos

Probablemente cold start severo o swap a disco (16GB RAM con gpt-oss:20b está al filo). Pega:

```
La respuesta de gpt-oss:20b se tarda demasiado. Diagnóstica si el problema es:
(a) cold start (primera vez = lento, después rápido)
(b) swap a disco (RAM insuficiente)
(c) algo más

# RAM en uso vs libre
vm_stat | head -10
top -l 1 -n 0 | grep PhysMem

# ¿Modelo cargado?
ollama ps

# Test directo a Ollama, sin OpenClaw
time curl -s -X POST http://127.0.0.1:11434/api/generate \
  -d '{"model":"gpt-oss:20b","prompt":"Hola","stream":false}' \
  --max-time 120

Si el test directo tarda <30s y el iMessage tarda 2min → problema de OpenClaw routing.
Si el test directo tarda 60-90s → cold start normal, dale tiempo.
Si el test directo tarda >120s → swap. Considera bajarte a qwen3:14b:

ollama pull qwen3:14b
openclaw config set agents.defaults.model.primary "ollama/qwen3:14b"
openclaw daemon restart

Reporta los tiempos antes de cambiar modelo.
```

---

## Próximos pasos después de iMessage funcionando

1. **Escribir system prompts adaptados** (Prompt en `system-prompts-gpt-oss-20b.md`):
   - Pega ese prompt en Cursor para que escriba los 3 system prompts.
2. **Agregar directivos al allowlist** (cuando estés listo):
   ```
   openclaw config set channels.imessage.allowFrom '["+5215512345678","+5215587654321"]'
   openclaw daemon restart
   ```
3. **Habilitar grupos** (opcional, si quieres un "directorio Kawiil" en iMessage):
   ```
   openclaw config set channels.imessage.groupPolicy "allowlist"
   openclaw config set channels.imessage.groups '{"*": {"requireMention": true}}'
   ```
4. **Setup Cloudflare Tunnel** (sábado): expone el dashboard web para uso desde laptops/celulares de directivos, complementario a iMessage.

---

## Resumen de la división de trabajo

| Paso | Quién | Tiempo |
|---|---|---|
| A · Verificar Messages.app | **Tú** | 1 min |
| B · Full Disk Access | **Tú** | 1 min |
| C · Automation Messages | **Tú** (auto-prompt) | <1 min |
| 1 · Pre-requisitos check | Cursor | 30 seg |
| 2 · Instalar imsg | Cursor | 1-2 min |
| 3 · Pasar número (terminal) | **Tú** | 10 seg |
| 4 · Aplicar config | Cursor | 30 seg |
| 5 · Validar y reiniciar | Cursor | 30 seg |
| 6 · Smoke test (mandar mensaje) | **Tú** | 30 seg |
| Aprobar pairing | Cursor | 10 seg |
| Respuesta del modelo | Espera | 30-90 seg primera vez |

**Total: ~10-15 min activos, ~85% automatizado por Cursor.**
