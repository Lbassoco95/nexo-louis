# Cursor Prompts — Implementación Yoltik AI

**Cómo usar este documento:**
1. Abre Cursor en modo Agent (Cmd+L → asegúrate que dice "Agent", no "Ask").
2. Verifica que Cursor tiene permiso de ejecutar terminal (Settings → Features → Terminal).
3. Copia el prompt completo del paso que sigas.
4. Pégalo en el chat de Cursor.
5. Lee lo que Cursor te diga antes de aprobar cada acción.

**Importante:** Algunos pasos son **manuales fuera de Cursor** (Cloudflare dashboard, Telegram BotFather, console.anthropic.com). Cursor no puede hacer clicks en interfaces web — esos los haces tú. Está marcado claramente abajo.

---

## FASE 0 · Setup inicial de Cursor (5 min)

### Prompt 0.1 — Abrir el proyecto y leer contexto

Pega esto en Cursor:

```
Abre el folder "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup" como workspace.

Lee los siguientes archivos y dame un resumen de 5 líneas de qué hace cada uno:
- CHECKLIST-POLO.md
- hoy/install-yoltik-ai.sh
- hoy/validate.sh
- hoy/killswitch.sh
- fin-de-semana/setup-cloudflare-tunnel.sh
- fin-de-semana/waf-rules.txt

Después dime: ¿está todo presente y ejecutable? ¿Detectas algo que falte o que pueda fallar en mi Mac (Apple Silicon, macOS 14 o 15)? Si encuentras algo, lístalo pero NO hagas cambios todavía.
```

**Cursor te dirá:** resumen + flags si detecta problemas. **No avances al siguiente prompt** hasta que Cursor confirme que todo está OK.

---

## FASE 1 · HOY — Instalación base (~30 min)

### Prompt 1.1 — Verificar prerrequisitos en mi Mac

```
Antes de instalar OpenClaw, verifica desde la terminal que mi Mac tiene los prerrequisitos. Corre estos comandos uno por uno y dime el resultado:

1. uname -a
2. sw_vers
3. command -v brew || echo "brew no instalado"
4. command -v node && node --version || echo "node no instalado"
5. df -h ~ | tail -1
6. fdesetup status

Después de correrlos, dame un diagnóstico:
- ¿Mac compatible (macOS 13+, arquitectura ARM o Intel)?
- ¿Brew y Node 22+ presentes?
- ¿Espacio suficiente (>5GB libres en ~)?
- ¿FileVault encendido?

Si algo falta, dime cómo arreglarlo PERO no corras nada todavía.
```

### Prompt 1.2 — Correr el instalador

```
Abre una terminal en el folder "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy".

Haz ejecutables los scripts:
chmod +x *.sh

Después corre el instalador:
bash install-yoltik-ai.sh

El script es interactivo. Cuando te pida confirmación, contesta "y". Cuando te pida la API key de Anthropic, PAUSA y avísame para que yo la pegue (la tengo lista). Cuando OpenClaw pregunte si instala el daemon, di "yes".

Reporta cada paso conforme el script avanza. Si algo falla, NO intentes arreglarlo solo — copia el error y avísame.
```

**⚠ Acción manual en este paso:**
- Antes de correr este prompt, tener lista tu API key de Anthropic (console.anthropic.com → API keys → Create).
- Cuando Cursor pause para que pegues, pégala TÚ en la terminal (no en el chat de Cursor — la API key es secreto).

### Prompt 1.3 — Validar instalación

```
La instalación de OpenClaw terminó. Corre el validador de seguridad:

bash "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy/validate.sh"

Léeme el output completo y dime:
1. ¿Cuántos checks pasaron, cuántos warning, cuántos failed?
2. Si hay fallos: ¿son críticos o cosméticos?
3. ¿Es seguro continuar al setup de Telegram?

NO intentes "arreglar" warnings solo — solo dime cuáles son.
```

### Prompt 1.4 — Crear estructura de spaces con system prompts

```
OpenClaw está instalado. Ahora quiero configurar los 3 spaces (general, legal, ikan) con system prompts apropiados.

Para cada uno, crea o actualiza el archivo:
~/.openclaw/spaces/<nombre>/system-prompt.md

Con el siguiente contenido:

--- general/system-prompt.md ---
Eres el asistente ejecutivo del directorio de Kawiil. Tus usuarios son C-level: hablan español mexicano, valoran respuestas concisas, y manejan información estratégica y operativa.

Reglas:
- Responde en español mexicano, tono profesional pero cercano.
- NUNCA accedas a archivos en ~/.openclaw/spaces/legal/ ni ~/.openclaw/spaces/ikan/ — esos son espacios separados.
- Cuando un directivo te pida ejecutar una acción que modifica archivos, pregunta confirmación primero.
- Si la consulta involucra datos PLD/clientes Ikán o contratos, redirígelo al espacio correspondiente.

Contexto Kawiil: empresa mexicana de software, opera productos Yoltik (Yoltik Pagos/Kailash, Ikán para cumplimiento PLD). Polo es el CEO.

--- legal/system-prompt.md ---
Eres el asistente legal del directorio de Kawiil. Manejas contratos, NDAs, finanzas y RH.

Reglas:
- Tono formal pero accesible. Español mexicano.
- Nunca des consejo legal vinculante — eres apoyo, no abogado. Recomienda revisión humana cuando aplique.
- Antes de firmar/ejecutar cualquier acción sobre un contrato, requiere "CONFIRMO" explícito del usuario.
- Si detectas información PLD o datos de clientes Ikán en un documento, NO los procesas aquí; redirige al espacio ikan.
- Usa el playbook de Kawiil para revisión de NDAs (GREEN/YELLOW/RED).

--- ikan/system-prompt.md ---
Eres el asistente del producto Ikán (cumplimiento PLD bajo LFPIORPI). Operas EXCLUSIVAMENTE sobre plantillas, políticas y metodología — NO sobre expedientes KYC reales hasta que se autorice la fase 2.

Reglas:
- Solo lectura: NO escribas archivos ni envíes datos externos.
- Si te piden datos de un cliente real (CURP, RFC, monto), responde: "Esa información debe consultarse en el sistema Ikán, no aquí."
- Conoces la metodología EBR FIATCOIN (5 elementos, escala 15-39, niveles KYC N1/N2/N3, umbrales LFPIORPI XVI).
- Hablas con asesores PLD certificados, no con cualquier usuario.

Después de crear los tres archivos, lístame su contenido para que verifique.
```

---

## FASE 2 · HOY — Telegram bot (~20 min)

### ⚠ Pasos manuales (fuera de Cursor)

**Cursor NO puede hacer estos clicks:**

1. **Telegram → BotFather:**
   - Abre Telegram (app o web.telegram.org).
   - Busca `@BotFather`.
   - Manda `/newbot`.
   - Nombre: `Yoltik AI` (o el que quieras).
   - Username: `yoltik_ai_bot` (si está tomado, prueba `yoltik_kawiil_bot`).
   - **Copia el token** que te da (formato `1234567890:ABCdef...`).

2. **Crea el grupo privado:**
   - En Telegram → Nuevo grupo → "Yoltik AI · Polo".
   - Agrega al bot (busca por su username).
   - Manda al bot un mensaje al grupo `/promote` para hacerlo admin (opcional, ayuda con algunas acciones).

### Prompt 2.1 — Conectar Telegram a OpenClaw vía API

```
Ya tengo el token de mi bot de Telegram (lo pegaré cuando me lo pidas).

Quiero conectar Telegram como canal en OpenClaw. Pero NO quiero usar el UI web de OpenClaw — quiero hacerlo por línea de comandos para que quede documentado.

1. Investiga: ¿OpenClaw expone un comando CLI para agregar canales? Algo como "openclaw channel add telegram --token=..." o un archivo de config tipo ~/.openclaw/channels/telegram.yaml.

Corre:
openclaw --help
openclaw channel --help 2>/dev/null || true
ls ~/.openclaw/channels/ 2>/dev/null || echo "no channels dir"

Dime qué opciones encuentras.

2. Si hay comando CLI: úsalo. Pausa para que yo pegue el token.
3. Si solo hay config file: créalo con el formato que vea en la doc oficial (busca en docs.openclaw.ai si dudas). Pausa para que pegue el token.
4. Si solo hay UI: abre http://127.0.0.1:3000 y dime cómo navegar.

Reinicia el daemon al final: openclaw daemon restart
```

### Prompt 2.2 — Primer test desde el celular

```
Voy a mandar un mensaje desde mi celular al grupo de Telegram donde está el bot.

Antes de que lo haga, prepara la terminal para mostrarme en TIEMPO REAL las acciones del agente:

tail -f ~/.openclaw/logs/audit.log

Cuando yo te avise que mandé el mensaje (algo como "Hola, ¿qué puedes hacer?"), espera 10 segundos y léeme las últimas líneas del audit log. Confirma:
1. ¿El mensaje llegó al gateway?
2. ¿OpenClaw lo asignó a un space (cuál)?
3. ¿Se procesó con Claude?
4. ¿Hubo respuesta de vuelta al canal?

Si algo falla, dime el error exacto.
```

---

## FASE 3 · FIN DE SEMANA — Cloudflare Tunnel (~1 hora)

### ⚠ Pre-requisito manual

Antes de correr cualquier prompt de esta fase:
1. `kawiil.mx` debe estar en Cloudflare DNS (`dig NS kawiil.mx` debe responder con `*.cloudflare.com`).
2. Activar Cloudflare Zero Trust: `one.dash.cloudflare.com` → wizard.

### Prompt 3.1 — Setup del túnel

```
Quiero conectar Cloudflare Tunnel para exponer OpenClaw como ai.kawiil.mx.

Prerrequisito que ya verifiqué: kawiil.mx está en Cloudflare (corre "dig NS kawiil.mx" y confírmame que ves cloudflare.com en la respuesta).

Después corre:
bash "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/fin-de-semana/setup-cloudflare-tunnel.sh"

Este script:
- Hace login a Cloudflare (abre browser, autorizo yo).
- Crea el túnel "yoltik-ai".
- Genera ~/.cloudflared/config.yml con mis valores.
- Crea el DNS record ai.kawiil.mx.
- Instala como servicio.

Cuando termine, verifica con:
cloudflared tunnel info yoltik-ai
curl -I https://ai.kawiil.mx

NO abras ai.kawiil.mx en el browser todavía — falta configurar Access. Solo confírmame que el túnel está conectado.
```

### Prompt 3.2 — Verificar que NO esté expuesto sin auth

```
Antes de configurar Cloudflare Access en el dashboard, quiero confirmar que ai.kawiil.mx redirige a una página de login de Cloudflare, NO directamente a OpenClaw.

Corre:
curl -sI https://ai.kawiil.mx | head -20

Si ves un redirect (302/301) a *.cloudflareaccess.com → bien, Access está activo.
Si ves un 200 con HTML de OpenClaw → MAL, ai.kawiil.mx está expuesto. Para todo:
  bash "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy/killswitch.sh"

Dame el resultado y dime cómo proceder.
```

### ⚠ Pasos manuales en Cloudflare dashboard

**Cursor NO puede hacer estos:**

1. **Conectar Google Workspace:**
   - one.dash.cloudflare.com → Settings → Authentication → Login methods → Add → Google Workspace.
   - Te pedirá autorizar con un super-admin de tu Google Workspace.

2. **Crear la aplicación:**
   - Access → Applications → Add an application → Self-hosted.
   - Subdomain: `ai`, Domain: `kawiil.mx`, Session: 8h.

3. **Policy "Directivos":**
   - Action: Allow.
   - Include: Emails ending in `@kawiil.mx`.
   - Require: Authentication method = "Any MFA".

4. **Policy "Block default":**
   - Action: Block.
   - Include: Everyone.
   - Orden: debajo de "Directivos".

### Prompt 3.3 — WAF rules

```
Lee el archivo:
/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/fin-de-semana/waf-rules.txt

Para cada una de las 5 reglas, genera un texto bonito y formateado que pueda copiar-pegar en el dashboard de Cloudflare, separado por delimitadores claros. Inclúyelo en un solo bloque que pueda mandar a un archivo, así:

bash -c 'cat > ~/Desktop/waf-paste-ready.txt' << "EOF"
[reglas formateadas]
EOF

Después abre el archivo con: open ~/Desktop/waf-paste-ready.txt

Esto me deja un PDF / texto plano fácil de copiar mientras hago clicks en Cloudflare.
```

### Prompt 3.4 — Validación full post-Cloudflare

```
Ya configuré Access + WAF en Cloudflare. Quiero validar el setup completo.

Corre estos checks y reporta cada uno:

1. # Túnel activo
cloudflared tunnel info yoltik-ai

2. # Gateway sigue solo en localhost
lsof -iTCP:3000 -sTCP:LISTEN

3. # Cloudflare contesta con redirect a Access
curl -sI https://ai.kawiil.mx | grep -iE "(location|cf-)"

4. # Puerto 3000 NO accesible desde IP pública (encuentra mi IP pública y prueba)
PUBLIC_IP=$(curl -s ifconfig.me)
nmap -p 3000 -Pn $PUBLIC_IP

5. # OpenClaw audit logs no muestran requests no autenticadas
tail -50 ~/.openclaw/logs/audit.log | grep -i unauth | head

6. # Re-correr el validador local
bash "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy/validate.sh"

Al final dame un veredicto: ¿es seguro invitar al primer directivo a probar la URL?
```

---

## FASE 4 · SEMANA SIGUIENTE — Skills custom

Aquí es donde Cursor da más valor: vas a escribir código real para los skills propios.

### Prompt 4.1 — Investigar la API de skills de OpenClaw

```
Voy a crear skills custom para OpenClaw. Antes de escribir código, investiga su API:

1. Lee la documentación oficial en https://docs.openclaw.ai (o el equivalente que encuentres).
2. Si OpenClaw está instalado, inspecciona un skill ya presente:
   ls ~/.openclaw/skills/
   cat ~/.openclaw/skills/*/manifest.* 2>/dev/null | head -100
   cat ~/.openclaw/skills/*/index.* 2>/dev/null | head -100

3. Resúmeme:
   - ¿Qué archivos componen un skill? (manifest, code, deps)
   - ¿Qué lenguaje? (TS / JS / Python)
   - ¿Cómo se registran herramientas/tools?
   - ¿Cómo se autentica un skill cuando llama APIs externas?
   - ¿Hay un comando para validar un skill antes de instalarlo?

Después dame un esqueleto MÍNIMO de un "hello world" skill con manifest + 1 tool que regresa un string fijo. Lo usaremos como base.
```

### Prompt 4.2 — Skill: morning-brief

```
Crea un skill OpenClaw llamado "morning-brief" en:
~/.openclaw/skills-dev/morning-brief/

El skill debe:
1. Ejecutarse a las 7am cada día laborable (lunes-viernes).
2. Pull de Gmail (últimas 24h) — los emails con label "Importante" o "Inbox" sin leer.
3. Pull de Google Calendar — eventos del día.
4. Pull opcional de Slack (canal #yoltik si existe) — últimos 20 mensajes con menciones a "@Polo" o "@directorio".
5. Pasar todo a Claude Sonnet 4.6 con un prompt que devuelva:
   - Top 3 cosas que requieren mi atención hoy.
   - 1-2 líneas por evento del calendario con contexto.
   - Cualquier mención a PLD, Ikán, Kailash, o stakeholders clave.
6. Enviar el resumen al grupo de Telegram "Yoltik AI · Polo".

Stack: TypeScript, fetch nativo, googleapis npm package, @anthropic-ai/sdk.

Después de crear los archivos, dame:
1. Lista de los archivos creados.
2. El comando para instalar dependencias.
3. El comando para registrar el skill en OpenClaw (añadirlo a la allowlist).
4. Cómo probarlo manualmente antes de programarlo.

NO corras el skill todavía — primero lo reviso.
```

### Prompt 4.3 — Skill: pld-monitor

```
Crea un skill OpenClaw llamado "pld-monitor" en:
~/.openclaw/skills-dev/pld-monitor/

Contexto: estoy construyendo Ikán, un producto de cumplimiento PLD bajo la Ley Federal para la Prevención e Identificación de Operaciones con Recursos de Procedencia Ilícita (LFPIORPI) — específicamente para Actividades Vulnerables del sector XVI (esquemas de monedas virtuales). Necesito monitorear cambios regulatorios.

El skill debe:
1. Cada lunes a las 9am, scrape:
   - DOF (diariooficial.gob.mx) — buscar entradas que mencionen "LFPIORPI", "Actividades Vulnerables", "monedas virtuales", "FIATCOIN", "SHCP", "CNBV", "UIF".
   - CNBV (cnbv.gob.mx/normatividad) — sección de avisos recientes.
   - SAT (sat.gob.mx) — comunicados PLD.
2. Comparar con el snapshot anterior en ~/.openclaw/skills-data/pld-monitor/last-snapshot.json.
3. Si hay cambios, pasarlos a Claude para que:
   - Resuma qué cambió.
   - Evalúe impacto sobre Ikán/Yoltik (alto/medio/bajo).
   - Sugiera acciones (revisar políticas, alertar a clientes, etc.).
4. Mandar el resumen al grupo "Yoltik AI · Polo" con tags [PLD-ALERTA] o [PLD-INFO].

Stack: TypeScript, playwright o cheerio para scrape, @anthropic-ai/sdk.

Importante:
- Respeta robots.txt y rate limits.
- Logguea cada scrape a ~/.openclaw/logs/pld-monitor.log.
- Si el scrape falla, mándame alerta al chat pidiendo que revise manualmente.

Crea los archivos, dame instrucciones de instalación. NO ejecutes todavía.
```

### Prompt 4.4 — Skill: contract-review

```
Crea un skill OpenClaw llamado "contract-review" en:
~/.openclaw/skills-dev/contract-review/

El skill debe:
1. Activarse cuando le mandan un PDF al chat de Telegram (en el grupo legal).
2. Extraer texto del PDF (pdf-parse o similar).
3. Pasarlo a Claude con el playbook de Kawiil para revisión de contratos/NDAs.
4. Devolver:
   - Tipo de documento (NDA, MSA, SOW, DPA, otro).
   - Clasificación rápida (GREEN/YELLOW/RED siguiendo el playbook de triage-nda).
   - Top 5 cláusulas que requieren atención (con cita exacta del texto).
   - Recomendación: firmar / negociar / escalar a abogado externo.
5. Guardar el análisis en ~/.openclaw/spaces/legal/reviews/<fecha>-<hash>.md.

Stack: TypeScript, pdf-parse, @anthropic-ai/sdk.

Para el playbook, lee mi skill ya existente en Cowork:
- legal:triage-nda (busca su SKILL.md en el sistema, probablemente en /var/folders/.../plugins/.../skills/triage-nda/SKILL.md).

Usa sus heurísticas como base.

Crea los archivos, dame instrucciones. NO ejecutes.
```

---

## FASE 5 · TROUBLESHOOTING — Prompts de rescate

### T1 — Instalación falló a mitad

```
La instalación de OpenClaw falló. Necesito diagnóstico.

1. Pega exactamente el error que viste (copia desde la terminal).
2. Corre estos diagnósticos:
   - command -v node && node --version
   - command -v openclaw && openclaw --version
   - ls -la ~/.openclaw/
   - cat ~/.openclaw/openclaw.json 2>/dev/null | head -30
   - tail -50 ~/.openclaw/logs/install.log 2>/dev/null
3. Identifica el punto exacto donde falló (qué paso del install-yoltik-ai.sh).
4. Propón un fix mínimo. NO toques nada sin que yo apruebe.

Si el error involucra "sharp" o "libvips", la solución conocida es:
SHARP_IGNORE_GLOBAL_LIBVIPS=1 npm install -g openclaw@latest
```

### T2 — Gateway no responde

```
El UI de OpenClaw en http://127.0.0.1:3000 no carga. Diagnóstica:

1. lsof -iTCP:3000 -sTCP:LISTEN
   ¿Hay algo escuchando ahí?

2. ps aux | grep -i openclaw | grep -v grep
   ¿El daemon corre?

3. tail -50 ~/.openclaw/logs/daemon.log 2>/dev/null
   tail -50 ~/.openclaw/logs/gateway.log 2>/dev/null
   ¿Hay errores recientes?

4. cat ~/.openclaw/openclaw.json | jq .gateway
   ¿Bind sigue en 127.0.0.1?

Dame diagnóstico antes de intentar arreglar. Soluciones más comunes:
- Daemon no corriendo: openclaw daemon start
- Puerto ocupado: cambiar port en config a 3001
- Config corrupto: restaurar de ~/.openclaw/openclaw.json.backup-*
```

### T3 — Telegram no responde

```
El bot de Telegram no contesta mis mensajes. Diagnóstica:

1. ¿El daemon procesa el canal?
   tail -100 ~/.openclaw/logs/audit.log | grep -i telegram

2. ¿El token es válido?
   TOKEN=$(grep -i token ~/.openclaw/channels/telegram.* 2>/dev/null | head -1)
   curl "https://api.telegram.org/bot${TOKEN##*=}/getMe"

3. ¿OpenClaw hace polling?
   ps aux | grep -E "(openclaw|telegram)" | grep -v grep

4. ¿Mi chat_id está en allowlist?
   cat ~/.openclaw/channels/telegram.* | grep -i allowed

Soluciones comunes:
- Bot no admin en el grupo: hazlo admin
- Token equivocado: regenera en @BotFather
- chat_id no en allowlist: actualizar config con tu chat_id (lo sacas de getUpdates)
```

### T4 — Cloudflare devuelve 502

```
ai.kawiil.mx devuelve 502 Bad Gateway. Diagnóstica:

1. ¿OpenClaw corre?
   curl -I http://127.0.0.1:3000 (debe ser 200, 401 o 302)

2. ¿cloudflared corre y conectado?
   ps aux | grep cloudflared | grep -v grep
   cloudflared tunnel info yoltik-ai

3. ¿Logs de cloudflared?
   tail -50 ~/.cloudflared/cloudflared.log
   (busca "error" o "refused")

4. ¿El config apunta al puerto correcto?
   cat ~/.cloudflared/config.yml

Causas típicas:
- OpenClaw caído: reiniciarlo
- cloudflared con config viejo: restart del servicio
- Firewall de macOS bloqueando localhost: revisar Configuración → Privacidad → Firewall
```

### T5 — Killswitch / incidente

```
SOSPECHO ACTIVIDAD SOSPECHOSA. Ejecuta inmediatamente:

bash "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy/killswitch.sh"

Después:
1. Léeme las últimas 100 líneas del audit log que el killswitch capturó.
2. Identifica acciones inusuales (escrituras a paths no estándar, exec de comandos raros, llamadas a IPs externas).
3. Lista los timestamps sospechosos.
4. NO intentes "limpiar" nada — solo diagnóstico.

Después de leer, dime los pasos para:
a) Rotar API keys.
b) Revocar el túnel.
c) Restaurar desde backup limpio.
```

---

## Apéndice · Convenciones para todos los prompts

- Si Cursor te pregunta "¿quieres que ejecute esto?" antes de un comando — di **sí** solo si entiendes el comando.
- Si Cursor te ofrece "auto-run all" — di **NO**. Quieres ver cada paso para esta infra.
- Si Cursor sugiere modificar el config de OpenClaw fuera de mis scripts — di **espera, déjame revisar primero**. Pide que te explique qué cambia y por qué.
- Para cualquier acción destructiva (rm -rf, sudo, reset) — pausa, lee el comando completo, y solo aprueba si tiene sentido.

---

## Orden estricto recomendado para HOY

1. **Prompt 0.1** → confirma que archivos existen.
2. **Prompt 1.1** → verifica prerrequisitos.
3. *(Manual)* → ten API key de Anthropic lista.
4. **Prompt 1.2** → instala.
5. **Prompt 1.3** → valida.
6. **Prompt 1.4** → crea system prompts de spaces.
7. *(Manual)* → BotFather, crea bot y grupo.
8. **Prompt 2.1** → conecta Telegram.
9. **Prompt 2.2** → primer test desde celular.

Si esto cierra en verde, das por terminado el día y el resto es para el fin de semana.

---

## Si Cursor se confunde o se va por ramas raras

Pégale esto:

```
Volvamos al plan. Estás trabajando en la implementación de Yoltik AI siguiendo el archivo:
/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/cursor-prompts.md

El paso actual es [PROMPT_X.Y]. Concéntrate solo en ese paso. No introduzcas cambios fuera de su scope. Reporta el resultado y espera mi siguiente prompt.
```
