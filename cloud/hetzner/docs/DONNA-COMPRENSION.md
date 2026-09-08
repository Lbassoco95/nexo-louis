# Donna: nombre de marca + arreglo de comprensión

Fecha: 2026-09-07

Dos cambios que salieron del mismo caso real en Telegram.

## El caso

Polo pegó el recordatorio del vencimiento y cerró con una pregunta:

> de este vencimiento ⏰ 🔴 HOY VENCE — Oficio CNBV 411-2/1364/2026 (Sylon Asesores).
> Enviar el correo a dllamas@cnbv.gob.mx … Guardar el correo de acuse con el folio:
> es la única constancia de **presentación** en tiempo.
> **lo agregaste a kawiil central para que llevemos el seguimiento?**

El bot contestó con un PPTX titulado *"Análisis legal — de este vencimiento ⏰ 🔴 HOY
VENCE — Oficio CNBV 41"*. Nunca contestó la pregunta.

### Por qué

`needs_doc_sonnet()` pedía solo que en CUALQUIER parte del mensaje hubiera un tipo de
documento **y** un verbo de pedido:

| Señal | Qué hizo match | Dónde venía realmente |
|---|---|---|
| tipo de documento | `presentación` | "constancia de **presentación** en tiempo" (trámite legal) |
| verbo de pedido | `Enviar` | "**Enviar** el correo a dllamas@cnbv.gob.mx" (instrucción del trámite) |

Las dos palabras estaban a ~400 caracteres de distancia y ninguna era una orden para
Donna: eran parte del contexto pegado. Ya dentro del flujo de documento,
`_es_analisis_legal()` matcheaba con solo ver `CNBV`, así que salió como "análisis
legal", y `_doc_tipo_de_mensaje()` eligió PPTX por la misma palabra `presentación`.

## Arreglo (comprensión)

En `services/louis_core.py`:

1. **Sentidos no documentales** (`_limpiar_sentidos_no_documentales`): se borran del
   mensaje frases como "constancia / acuse / fecha / plazo de presentación" y
   "presentación en tiempo" antes de buscar el tipo de documento. Se aplica también en
   `_doc_tipo_de_mensaje()` para que no elija PPTX.
2. **Preguntas de seguimiento** (`es_pregunta_de_seguimiento`): "¿lo agregaste a kawiil
   central?", "¿ya lo guardaste?", "¿lo registraste?" ya no disparan generación de
   archivo — hay que contestarlas. Requiere signo de interrogación, para no confundir la
   orden ("agrégalo a kawiil central") con la pregunta. Válvula de escape: si Polo nombra
   un formato explícito ("¿me lo pasas **en PDF**?"), sí es pedido de documento.
3. **Cercanía verbo↔tipo** (`_doc_verbo_pegado_al_tipo`, ventana de 45 caracteres): el
   verbo tiene que estar pegado al tipo. "hazme un informe" sí; "Enviar el correo…" en la
   línea 2 y "presentación" en la línea 8, no.
4. **`_es_analisis_legal`**: mencionar CNBV / IMPI / amparo ya no basta. La señal fuerte
   ahora exige además que el mensaje pida un entregable.
5. **Verbos acentuados**: `prepárame`, `ármame`, `conviérteme` — antes no matcheaban
   (`prepara\w*` no cubre `prepára`), así que pedidos legítimos se caían.
6. **System prompt** — sección `# COMPRENSIÓN DEL MENSAJE`: separa el contexto pegado de
   la petición real, contesta la pregunta con el hecho verificado, acusa el vencimiento en
   una línea, y si no distingue qué quiere Polo, pregunta en vez de generar el archivo.
7. **`generar_documento`** (descripción de la tool) y la red de seguridad #2 del bridge
   (`_promete_documento`) también quedan bloqueadas en preguntas de seguimiento.

Si el detector rápido falla y devuelve `False` en un pedido real, no se pierde nada: el
modelo sigue teniendo la tool `generar_documento` en el chat normal. El error caro es el
contrario (mandar un PPTX basura), y ése es el que se cerró.

### Pruebas

```bash
python3 cloud/hetzner/scripts/test-comprension.py
```

32 casos, sin red ni credenciales: el mensaje del CNBV, preguntas de seguimiento,
contexto pegado, y los pedidos de documento que deben seguir funcionando.

## Arreglo (nombre)

El asistente se presenta y firma como **Donna** (antes "Louis"), en línea con el Cerebro
Kawiil que ya usa ese nombre del lado de Cowork:

- identidad del system prompt (femenino: "asistente ejecutiva"), briefing diario,
  módulo de memoria, generador de documentos;
- autoría de los entregables: pie de PDF/DOCX/PPTX/HTML, chat embebido
  ("Pregúntale a Donna"), pie del briefing, `preparado_por` de los briefs del Cerebro;
- dashboard de agentes del gateway y descripciones de tools;
- adjunto HTML de Telegram: `donna_<fecha>.html`.

`ASSISTANT_NAME` en `louis_core.py` es el único lugar que define el nombre para lo que se
imprime en entregables. Si Polo le dice "Louis", el prompt indica que responda normal pero
que se presente como Donna.

**A propósito NO se renombraron** (renombrarlos rompe units, logs, rutas o el sync):
`louis_core.py`, `louis_html.py`, los servicios systemd, `/opt/louis`, las variables
`LOUIS_*`, la carpeta `_Louis-Generados` y el handle real de Slack `Louis-Nexo` / `@Louis`.

**Pendiente manual:** el nombre que se ve arriba del chat de Telegram lo define BotFather,
no el código → `@BotFather` → `/setname` → elegir el bot → "Donna". Igual `/setdescription`
y `/setabouttext` si dicen Louis. En Slack, el nombre de la app se cambia en
api.slack.com → Basic Information → App Name (y ahí sí habría que actualizar el handle en
las descripciones de las tools de Slack).

## Deploy

```bash
# en Hetzner, como root
cd /opt/louis && git pull && ./deploy.sh --skip-bootstrap
systemctl restart telegram-bridge slack-bridge openclaw-gateway
```

---

# Segunda ronda (7-sep, tarde): acciones fabricadas, formatos y horarios

De la conversación de las 6:54–6:57pm salieron tres fallas más. Ninguna era falta de
inteligencia del modelo: eran instrucciones contradictorias y plomería.

## A. Decía "agendado" y el calendario quedaba vacío

Donna escribió *"✅ Correcto. Agendando los 4 eventos de MAÑANA en Microsoft Calendar…"*
y *"Evento agendado en Microsoft Calendar (Kawiil)"*. No se creó nada.

Causas, en orden de culpa:

1. **La descripción de la tool le ordenaba preguntar.** `m365_crear_evento` decía
   literal: *"Crea evento en calendario. Confirma fecha/hora con Polo antes."* Polo ya
   había dado las 4 horas exactas y ella igual preguntó *"¿Confirmo que los meto al
   calendario de Kawiil?"* — obedeciendo la tool. De ahí el bucle infinito de
   confirmación.
2. **Nada verificaba la afirmación.** Ya existía una red anti-fabricación para
   recordatorios (`_REMINDER_CLAIM_RE` → obliga a llamar `agendar_recordatorio`), pero
   no para eventos ni para notas. Afirmar era gratis.
3. **`_es_stall` no veía el gerundio.** Detectaba "voy a crear" pero no "Agendando…",
   y tampoco el "¿Confirmo que…?".

Arreglos:

- `m365_crear_evento` ahora dice lo contrario: si Polo dio día y hora, **ejecuta sin
  preguntar** (el dato ES la confirmación), una llamada por evento, `tenant` = kawiil por
  default, 30 min si no hay fin, y prohibido decir "agendado" sin OK de la tool. Incluye
  la distinción recordatorio ≠ evento.
- `_CLAIM_NETS`: tabla de (afirmación, tools que la vuelven verdad, corrección). Cubre
  eventos (`m365_crear_evento`) y notas/memoria (`append_to_memory`, …). Si el turno
  afirma sin haber llamado ninguna, `_accion_fabricada()` lo empuja a ejecutarla de
  verdad — una vez por familia, para no ciclar el loop.
- `_es_stall` ahora cubre gerundios de acción (agendando, creando, registrando,
  guardando, bloqueando…) y el bucle de confirmación (`¿confirmo`, `¿procedo`,
  `¿los meto`, `¿quieres que lo…`).
- System prompt, sección `# ACCIONES: EJECUTA, NO ANUNCIES`: nunca afirmar sin OK de la
  tool; no pedir permiso dos veces; un ítem = una llamada; reportar lo que devolvió la
  tool (incluidos los fallos parciales); recordatorio ≠ evento; "anótalo" = tool de
  memoria de verdad.

## B. Unos mensajes con un formato y otros con otro

El prompt y el conversor se contradecían:

| El prompt le pedía | Lo que `format_for_telegram` hace |
|---|---|
| "Negrita: `*una sola*`, NO `**dos**`" | `*x*` → **cursiva**; `**x**` → negrita |
| "NO uses headers `#`" | `# x` → negrita |

Cuando obedecía el prompt, los títulos salían en cursiva; cuando escribía su Markdown
natural, salían bien. Mismo bot, dos estilos. Arreglado: el prompt de Telegram (y el de
Slack, que tenía el mismo desfase) ahora pide **Markdown estándar**, que es lo que los
conversores entienden, y advierte de no mezclar `*x*` con `**x**`.

Segunda causa: el troceado a 4000 caracteres cortaba a ciegas y partía los tags
(`<b>` en un chunk, `</b>` en el otro) → Telegram devolvía 400 y **ese** chunk caía a
texto plano, así que un mensaje largo salía mitad con formato y mitad en crudo.
`_chunk_html()` ahora corta en salto de línea.

## C. Mensajes a todas horas

Cuatro causas distintas:

1. ~~**`sjf-weekly.timer` sin zona horaria** disparaba a las 2:00am.~~
   **CORRECCIÓN (verificado en el server):** era falso. `louis-prod` ya tiene su hora
   local en **CST** (`America/Mexico_City`), así que un `OnCalendar` sin zona ya
   disparaba en hora CDMX — `systemctl list-timers` lo confirma:
   `sjf-weekly LAST Mon 2026-09-07 08:00:00 CST`. Poner la zona explícita **sigue
   valiendo** (así el horario no depende de la zona del server: un
   `timedatectl set-timezone UTC` movería todos los avisos 6 horas en silencio), pero
   NO era una causa de los mensajes a deshoras.
2. ~~**`Persistent=true` en los timers que notifican** — comprobada con el 20:20 de
   `legal-estado`.~~
   **CORRECCIÓN 2 (y van dos):** también falsa. El `legal-estado.timer` REAL de
   producción (rama de agosto) es `OnCalendar=*-*-* 00/4:20:00 America/Mexico_City` —
   **cada 4 horas a las :20**: 00:20, 04:20, 08:20, 12:20, 16:20, 20:20. El disparo de
   las 20:20 era una corrida normal, no un catch-up. (Lo que yo comparaba era contra el
   `Mon,Fri 09:15` de `main`, que llevaba meses sin ser lo que corría.)
   Quitar `Persistent` de los timers que notifican sigue siendo defendible —un boletín
   perdido no debería dispararse al arrancar a cualquier hora— pero **no está
   comprobado** que haya causado ningún mensaje a deshoras.

   **La causa más probable, ahora sí leyendo la unit correcta:** ese timer dispara a las
   **00:20 y 04:20**, y `estado_legal.py` decide si manda (schedule adaptativo: diario
   las primeras 2 semanas, cada 3 días los días 15-35, semanal después). Un reporte a
   medianoche o a las 4am es *por diseño*. Y la ventana de silencio que se agregó al
   scheduler **no lo cubre**: los timers de systemd mandan por su propio camino, sin
   pasar por la cola de recordatorios.
   Verificar con: `journalctl -u legal-estado --since "7 days ago"`.
   Arreglo natural: `08/4:20` (08:20, 12:20, 16:20, 20:20) en lugar de `00/4:20`, o que
   `estado_legal.py` respete las horas de silencio.

3. **`Persistent=true`, sin comprobar** —
   Si el server estaba caído (o el timer se reinicia) a la hora del boletín, systemd
   dispara la corrida perdida de inmediato, a cualquier hora. La prueba salió en el
   propio `list-timers` del deploy: `legal-estado.timer` está programado
   `Mon,Fri 09:15` y su última corrida fue **`Mon 2026-09-07 20:20:00 CST`** — 11 horas
   fuera de horario, y ese servicio manda "reporte de avance de descargas legales" por
   Telegram. Quitado de `dof-daily`, `dof-daily-tarde`, `legal-digest`, `legal-estado`
   y `sjf-weekly`. Se mantiene en los jobs silenciosos (harvest/index), que no molestan.
   El server además tiene `*** System restart required ***` pendiente, así que los
   reinicios —y sus catch-ups— pasan de verdad.
4. **Sin horas de silencio en el scheduler**: un `fire_at` de madrugada (o mal calculado
   por el modelo) disparaba a esa hora. Ahora ventana **22:00 → 07:00 CDMX**; los avisos
   de esa franja **no se pierden**, se reprograman a las 07:00. `"urgente": true` en la
   entry se salta el silencio. Configurable: `DONNA_QUIET_START` / `DONNA_QUIET_END`.
   → Si el briefing matutino está en cola antes de las 7:00, baja `DONNA_QUIET_END`.
5. **La cola se vaciaba de golpe**: `fire_at <= now` mandaba todo el atraso junto. Ahora
   máximo `DONNA_MAX_POR_TICK` (3) por minuto, y un aviso con más de 2h de atraso llega
   marcado `⏰ (atrasado Nh)` para que Polo no lo lea como de ahora.

Para revisarlo sin entrar al servidor: `hetzner_estado(que="avisos_programados")` lista
la cola (hora, mensaje, recurrencia, diferidos) y la ventana de silencio vigente.

## D. El proceso de aprendizaje

La destilación nocturna (23:00, Haiku → AGENDA/PEOPLE/CLIENTES/IMPORTANT) existía, pero
tenía dos agujeros:

1. **Prohibía aprender de las correcciones.** La regla decía *"NO guardes hechos sobre
   Donna misma, el sistema, el bot"*, así que los tres "no, eso no era" de Polo se
   tiraban a la basura y el mismo error volvía. Ahora hay categoría `CORRECCIONES` →
   `LEARNINGS.md`, que **sí** se inyecta al system prompt, con instrucción de escribirlas
   como regla en imperativo ("si Polo dice 'recordatorio', usar `agendar_recordatorio`,
   no crear evento").
2. **Fallaba en silencio.** El scheduler solo logueaba el caso `OK`; sin API key o con
   JSON inválido, Donna dejaba de aprender semanas sin que nadie lo notara. Ahora loguea
   warning, y `estado_aprendizaje()` aparece en `/status`: última corrida, qué aprendió, y
   ⚠ si lleva más de 2 días sin correr.

Lo que **no** es el cuello de botella: la capacidad del modelo. Ninguna de estas fallas
mejora con más GPU — eran una descripción de tool que ordenaba preguntar, un prompt que
contradecía al conversor y un timer sin zona horaria.

---

# Mantener Haiku (sin subir a Sonnet): palancas de costo

Decisión de Polo: no encarecer los turnos con tools. Se queda **Haiku** y se hace
confiable con configuración. Ventana de silencio: **7:00am** (como quedó).

El fallo de Haiku no es de comprensión, es de **selección**: van 110 tools en cada
request (~15.4k tokens de definiciones; ~20 son `m365_*` casi idénticas). Pedirle que
acierte entre 110 opciones parecidas es lo que lo empuja a narrar en vez de ejecutar.

## Lo que se aplicó (costo cero)

1. **`tool_choice` forzado en el primer turno** cuando el mensaje ORDENA escribir
   (`tiene_intencion_de_escritura`). No puede contestar de memoria: tiene que ejecutar.
   Es la misma llamada al mismo modelo — no cuesta un peso más.
   El forzado con Haiku ya se había intentado en el flujo de documentos y se descartó
   porque el primer turno salía sin texto; aquí no aplica esa objeción, porque el texto
   final lo escribe el turno siguiente ya con los `tool_results` en mano.
2. **Se le quita la elección cuando la orden es de una sola familia**
   (`tool_forzada_por_intencion`): calendario → `m365_crear_evento`, recordatorio →
   `agendar_recordatorio`, nota → `append_to_memory`. De 110 opciones a 1. Precedente en
   el propio código: las queries de Slack ya forzaban `slack_resumen`.
   Dos guardas: sin hora en el mensaje NO se fuerza `m365_crear_evento` (si no, inventa
   la hora), y si el mensaje mezcla familias ("guárdalo como nota **y** recuérdame…") se
   deja `any`, porque forzar una sola sería peor.
3. **Las redes anti-fabricación** cuestan una llamada extra de Haiku *solo cuando se
   porta mal* — no en el caso normal. Es el gasto correcto: se paga por el error, no por
   la póliza.

## Lo que NO se hizo, a propósito

**Filtrar el bloque de tools por intención** (mandar 15 en vez de 110) suena a la
optimización obvia y sería contraproducente: `tools` + `system` son idénticos entre
llamadas y hoy viajan **cacheados** (`cache_read` ≈ 10% del precio). Filtrar por mensaje
crearía una variante de caché por combinación y se pagaría input completo mucho más
seguido — más caro, no más barato. Si algún día se quiere, tiene que ser con un número
FIJO y pequeño de canastas (3–4), cada una con su propia entrada de caché.

## Si aun así vuelve a narrar en vez de ejecutar

En ese orden, de lo más barato a lo más caro:
1. Revisar `LEARNINGS.md`: la corrección de Polo debería estar ahí como regla. Si no
   está, el problema es la destilación nocturna (ver `/status` → Aprendizaje).
2. Agregar el verbo que falló a `_WRITE_INTENT_RE` / `_CAL_VERB_RE` — es una línea y
   cuesta cero.
3. Solo entonces: subir a Sonnet los turnos multi-paso.

---

# Deploy: cómo se pone vivo (y por qué el `git pull` falla)

Dos errores en las instrucciones que se dieron primero, corregidos aquí:

1. **`cd /opt/louis && git pull` → "not a git repository".** `/opt/louis` NO es un clon
   de git: es el **destino de un rsync** desde la Mac (ver README, "Sincronización
   Mac → Hetzner"). El código vivo tampoco está ahí, sino en `/opt/openclaw/scripts/`,
   donde `deploy.sh` instala los `.py` de `cloud/hetzner/services/`.

2. **`systemctl restart` sin `sudo` pide contraseña.** Al correrlo pelón, systemd pide
   autenticación por polkit. Y **no hay contraseña que recordar**: `bootstrap/02-user.sh`
   crea al usuario con `adduser --disabled-password`, así que la cuenta no tiene
   contraseña — pero sí tiene `sudo` sin contraseña
   (`/etc/sudoers.d/polo` → `polo ALL=(ALL) NOPASSWD: ALL`). La solución es prefijar
   `sudo`, no adivinar contraseñas.

Además `deploy.sh` usa `systemctl enable --now`, que **no reinicia** un servicio que ya
está corriendo: sin un `restart` explícito, el `.py` nuevo no carga y parece que el
deploy "no hizo nada".

## `scripts/actualizar.sh`

Hace la secuencia completa y es idempotente:

1. clona o actualiza el repo en `/opt/louis-src` (SSH primero, porque el repo es
   **privado** y por HTTPS pediría token);
2. `rsync` de `cloud/hetzner/` → `/opt/louis`, **sin `--delete`** y excluyendo `.env`
   (los secretos no están en el repo: `.gitignore` los excluye, y con `--delete` se
   borrarían y `deploy.sh` dejaría de arrancar);
3. `sudo ./deploy.sh --skip-bootstrap`;
4. `daemon-reload` + `restart` real de los 4 servicios;
5. corre `test-comprension.py` y aborta si falla.

```bash
# como polo (NO como root)
/opt/louis/scripts/actualizar.sh claude/telegram-bot-comprehension-e6c6d2
```

Primera vez, cuando el script todavía no está en `/opt/louis`:

```bash
sudo git clone -b claude/telegram-bot-comprehension-e6c6d2 \
  git@github.com:Lbassoco95/nexo-louis.git /opt/louis-src
/opt/louis-src/cloud/hetzner/scripts/actualizar.sh claude/telegram-bot-comprehension-e6c6d2
```

Si el server no tiene credencial de GitHub, el script imprime las dos salidas: registrar
una deploy key, o empujar desde la Mac con rsync (el flujo del README).

## Alta de la deploy key (una sola vez por server)

El repo es privado y `louis-prod` no traía credencial de GitHub
(`git@github.com: Permission denied (publickey)`). Se registra una **deploy key de
solo lectura** — suficiente para `clone`/`fetch`, y no puede escribir al repo:

```bash
# 1) En el server, como polo. Genera la llave solo si no existe y pre-registra
#    github.com en known_hosts (si no, `git clone` se cuelga pidiendo yes/no).
sudo sh -c '
  install -d -m 700 /root/.ssh
  [ -f /root/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "louis-prod deploy"
  ssh-keygen -F github.com -f /root/.ssh/known_hosts >/dev/null 2>&1 \
    || ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts
  cat /root/.ssh/id_ed25519.pub
'
```

2. Copia la línea `ssh-ed25519 AAAA…` y pégala en
   github.com/Lbassoco95/nexo-louis → **Settings → Deploy keys → Add deploy key**.
   Título: `louis-prod`. **NO** marques "Allow write access".

3. Comprueba y clona:

```bash
sudo ssh -T git@github.com   # debe decir: "Hi Lbassoco95/nexo-louis! You've successfully authenticated"
sudo git clone -b <rama> git@github.com:Lbassoco95/nexo-louis.git /opt/louis-src
/opt/louis-src/cloud/hetzner/scripts/actualizar.sh <rama>
```

De ahí en adelante todo deploy es un solo comando: `/opt/louis/scripts/actualizar.sh <rama>`.

## `--solo-servicios`: actualizar la lógica sin correr todo el deploy

En `louis-prod`, `deploy.sh --skip-bootstrap` aborta con
`.env: variable LOUIS_DOMAIN está vacía`. `require_env()` exige
`LOUIS_DOMAIN AGENTS_DOMAIN ACME_EMAIL SYSTEM_USER ANTHROPIC_API_KEY`, y las tres
primeras solo sirven para **Caddy y el TLS** — no para el bot. Aparte de eso, un
`deploy.sh` completo toca Caddy, docker-compose, cron y los seeds: mucho riesgo en un
server en producción para actualizar tres archivos de Python.

```bash
/opt/louis/scripts/actualizar.sh <rama> --solo-servicios
```

Hace nada más lo que cambia al actualizar la lógica:

- copia los `.py` de `services/` a `/opt/openclaw/scripts/` (la misma lista que
  deploy.sh: si falta uno, las tools que lo importan fallan en silencio);
- instala los `.service` y `.timer` sustituyendo los `@@PLACEHOLDER@@`, tomando los
  valores de **la unit ya instalada y funcionando** (`User=`, `EnvironmentFile=`) en vez
  del `.env` — así no depende de variables que solo le importan a Caddy;
- respalda el `scripts/` anterior y las units en `/opt/openclaw/.respaldo-<fecha>/`
  antes de sobrescribir;
- `daemon-reload`, reinicia los 4 servicios y **reinicia los timers** (un
  `daemon-reload` no recalcula el próximo disparo, y los `.timer` cambiaron de zona
  horaria), y muestra los próximos disparos para confirmar que ya salen en hora CDMX;
- corre las pruebas y aborta si fallan.

Nota aparte: que `LOUIS_DOMAIN` esté vacía en `.env` vale la pena revisarla en frío
—Caddy y el TLS de `louis.kawiil.mx` dependen de ella— pero no bloquea al bot y no es
algo que convenga tocar en el mismo movimiento que un hotfix.

---

# `slack-bridge`: 268,148 reinicios en ~34 días (hallado durante el deploy)

Al verificar el deploy, `slack-bridge` aparecía en `activating`. El journal:

```
slack-bridge.service: Scheduled restart job, restart counter is at 268148.
slack-bridge.service: Main process exited, code=exited, status=1/FAILURE
```

A un reinicio cada 11s, 268,148 reinicios ≈ **34 días** en bucle. Es anterior al
deploy de hoy, no lo causaron los cambios.

**Causa:** `load_credentials()` hace `sys.exit(1)` cuando faltan `SLACK_BOT_TOKEN` /
`SLACK_APP_TOKEN`. Es una condición **permanente** —reintentar no la arregla— pero la
unit tenía `Restart=always` + `RestartSec=10`, así que reintentaba indefinidamente. El
traceback no salía en el journal porque la unit manda stdout/stderr a
`/opt/openclaw/logs/slack-bridge.log`.

**Por qué nadie lo vio en un mes:** `_verificar_conexiones` (el `/status` de Donna)
imprimía `? slack-bridge: activating` — indistinguible de un servicio que apenas
arranca. Un servicio muerto y uno iniciando se veían igual.

## Arreglos

1. **Código de salida dedicado.** Los errores de configuración salen con `78`
   (`EX_CONFIG`, la convención de `sysexits.h`) en lugar de `1`, y las units llevan
   `RestartPreventExitStatus=78`: falta un token → el servicio queda en `failed`,
   visible, y deja de consumir recursos. Un crash real (red, API caída) sigue
   reintentando. El log dice qué hacer para revivirlo
   (`systemctl reset-failed … && systemctl start …`).
2. **Backoff exponencial** en las 5 units con `Restart=always`: `RestartSteps=5` +
   `RestartMaxDelaySec=300` → 10s, 20s, … hasta 5 min, en vez de martillar cada 10s.
   Sigue auto-sanando, sin quemar el server. (Requiere systemd ≥ 254; Ubuntu 24.04
   trae 255.)
3. **`/status` ahora distingue arranque de bucle** (`_formato_estado_servicio` +
   `_n_reinicios`, que lee `NRestarts`):

   ```
   ✓ telegram-bridge: active
   ✗ slack-bridge: EN BUCLE DE CAÍDA — 268,148 reinicios. NO está arrancando,
     se cae y vuelve a intentar. Revisa: journalctl -u slack-bridge -n 30
   ✓ openclaw-gateway: active (⚠ 47 reinicios acumulados — se ha estado cayendo)
   ✗ cerebro-kawiil: FAILED — no va a reintentar solo.
   · ollama: inactive (apagado a propósito)
   ```

## Qué hacer con Slack

- **Si no usas Slack:** `sudo systemctl disable --now slack-bridge`.
- **Si sí lo usas:** pon `SLACK_BOT_TOKEN=xoxb-…` y `SLACK_APP_TOKEN=xapp-…` en
  `/opt/openclaw/credentials/slack.env` y arráncalo.

Revisa también el tamaño del log, que llevaba un mes creciendo con cada reinicio:
`du -sh /opt/openclaw/logs/`.

## Cargar las credenciales de Slack

`scripts/configurar-slack.sh` — pide los tokens con `read -rs` (no se hacen eco ni
quedan en `~/.bash_history`), escribe `/opt/openclaw/credentials/slack.env` en `0600`
con dueño el usuario del servicio, y levanta el bridge.

```bash
/opt/louis/scripts/configurar-slack.sh
```

Lo que el bridge lee de ese archivo (`slack-bridge.py` → `load_credentials`):

| Variable | Obligatoria | De dónde sale |
|---|---|---|
| `SLACK_BOT_TOKEN` | sí | api.slack.com/apps → tu app → **OAuth & Permissions** → Bot User OAuth Token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | sí | **Basic Information → App-Level Tokens** → Generate, scope `connections:write` (`xapp-…`) |
| `SLACK_DEFAULT_DM_USER` | no | Tu user ID (`U…`); lo usa `scheduler.py` para mandarte recordatorios por DM |
| `SLACK_ALLOWED_USERS` | no | Whitelist `U123,U456` |
| `SLACK_SIGNING_SECRET` | no | No se usa en Socket Mode; está por completitud |

En la app de Slack hace falta, además de los tokens:

- **Socket Mode encendido** (Settings → Socket Mode). Sin eso el `xapp-` no sirve.
- **Event Subscriptions → Subscribe to bot events:** `app_mention` y `message.im`
  (son los dos eventos que registra `slack-bridge.py`).
- **Scopes de bot**, por lo que llama el código:
  `chat:write` (`chat.postMessage`), `app_mentions:read`,
  `channels:read` + `groups:read` + `im:read` + `mpim:read` (`conversations_list`),
  `channels:history` + `groups:history` + `im:history` + `mpim:history`
  (`conversations_history`), `users:read` (`users_info`, `users_list`),
  `im:write` (`conversations_open`).
  Si cambias scopes hay que **reinstalar la app** en el workspace y el `xoxb-` cambia.

Dos trampas:

1. **`slack_bolt` puede no estar instalado.** El bridge muere en el `import` *antes* de
   leer los tokens, y el síntoma se confunde con "faltan credenciales". El script lo
   verifica primero (`pip install --break-system-packages slack-bolt slack-sdk`).
2. **`reset-failed` es obligatorio.** Con `RestartPreventExitStatus=78` el servicio
   quedó en `failed` a propósito y systemd no lo reintenta hasta limpiar ese estado.
3. **Un solo Socket Mode por App Token.** Si el OpenClaw de la Mac sigue corriendo con
   el mismo `xapp-`, Slack desconecta al más viejo y los dos se pelean
   (`docs/secrets.md`). Apaga el de la Mac:
   `launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist`.


## Diagnóstico en vez de suposición: historial de avisos

Las dos primeras hipótesis sobre los mensajes a deshoras fueron una acertada
(`Persistent`) y una falsa (la zona horaria). Para no volver a adivinar,
`hetzner_estado(que="avisos_programados")` ahora también lee `reminders/sent.jsonl` y
muestra **a qué horas te ha escrito de verdad**, marcando lo que cae fuera de
07:00–22:00:

```
Últimos 40 avisos enviados, por hora: 03h×1, 09h×1, 13h×1, 20h×1
⚠ 1 cayeron fuera de 07:00–22:00 — ésos son los que molestan.
  • 2026-09-07 09:15 Reporte de avance de descargas legales
  • 2026-09-07 20:20 Reporte de avance de descargas legales (catch-up)
  • 2026-09-07 03:40 Recordatorio: enviar oficio CNBV  ← fuera de horario
```

Un disparo muy lejos del horario de su timer delata un catch-up de systemd; un
`fire_at` de madrugada en la cola delata una hora mal calculada al crear el
recordatorio. Son causas distintas con arreglos distintos, y ahora se distinguen
leyendo, no suponiendo.

---

# Medir el aprendizaje: acervo legal y análisis

Pregunta de Polo: *"cuánto se ha aprendido de tesis jurisprudencias, del DOF y cómo
estamos almacenando estas investigaciones o análisis"*.

Hay **tres capas distintas** y sirve no confundirlas:

| Capa | Qué es | Dónde vive | Cómo se mide |
|---|---|---|---|
| **Acervo** | Lo descargado en bruto | `legal/sjf/biblioteca.db` (tesis + FTS5), `legal/dof/biblioteca_dof.db` (notas, leyes, reformas, ediciones + FTS5) | `hetzner_estado(que="legal_conteo")` |
| **Digerido** | Lo que cada agente `kawiil-*` ya indexó para poder usarlo | `knowledge/<agente>/index.json` + `docs/` | `hetzner_estado(que="aprendizaje_legal")` |
| **Producido** | Los análisis y entregables reales | Cerebro Kawiil (`entregables/`) | `entregables_listar()` |

Al 7-sep-2026: SJF 1,538 MB, DOF 2,569 MB, y **203 entregables** (84 listo, 64
archivado, 44 borrador, 10 en_vobo, 1 aprobado).

## El hueco que había

`legal_conteo` **nunca contaba las tesis del SJF**: su rama de SJF solo imprimía
`SJF: tablas=[...]` — los nombres de las tablas, no cuántas tesis hay, que es justo el
dato de la pregunta. Y la rama del DOF traía `'2026-05%'` clavado a mano, cuatro meses
viejo, desde cuando se hacía el backfill.

Reescrito para reportar, por tabla: registros totales, **cuántos traen el texto
completo** (descargado ≠ aprovechable: sin texto no se puede analizar ni buscar), rango
de fechas, cuántos entraron en los últimos 30 días (¿sigue creciendo o se paró?) y la
última corrida del scraper. El esquema se descubre en vivo con `PRAGMA table_info` en
lugar de asumir nombres de columna, porque las BD las escriben los scrapers y sus
columnas han cambiado; una tabla ausente se reporta y no revienta.

`aprendizaje_legal` es nuevo y mide la capa que no se veía en ninguna parte: de los GB
de acervo, **cuánto ya digirió cada uno de los 10 agentes** mapeados en
`KAWIIL_KNOWLEDGE_MAP`, con la fecha de su última indexación y un ⚠ si lleva más de 72h
sin avanzar. El indexador corre solo cada ~10 min (scheduler, 10 docs por tick, rotando
entre agentes), así que un agente parado varios días es señal de que algo falla.

# Código editado en vivo que no está en git

Al consultar el Cerebro salió que la versión VIVA reporta un archivo de memoria
`SEGUIMIENTOS`, y la palabra `SEGUIMIENTOS` **no existe en el repo**. El server estaba
corriendo un `cerebro_kawiil_mcp.py` que git no tiene.

Explicación: `self_update.py` le permite a Donna editar su propio código en caliente
(`editar_mi_codigo` sobre `/opt/openclaw/scripts/`). Esas ediciones **nunca vuelven al
repo**, así que el siguiente deploy las pisa en silencio.

`actualizar.sh` ahora lleva un manifiesto (`/opt/openclaw/.deploy-manifest.json`) con el
hash de cada archivo que instaló. Si en el siguiente deploy el archivo en disco no
coincide con lo que dejamos, fue editado en vivo: se avisa antes de pisarlo y se imprime
el `diff` contra el respaldo. Y `cerebro-kawiil` **no** se reinicia automáticamente —
sigue con su código en memoria— para no aplicar la versión del repo sin que sea una
decisión consciente.

**Lo perdido es recuperable** mientras exista el respaldo del primer deploy:

```bash
sudo diff /opt/openclaw/.respaldo-20260907202308/scripts/cerebro_kawiil_mcp.py \
          /opt/louis/services/cerebro_kawiil_mcp.py
```

Si el cambio vale, hay que portarlo al repo — no volver a editar solo el server, o el
siguiente deploy lo pisa otra vez.

---

# El repo tenía el Cerebro MESES atrasado (y el deploy lo degradó)

El diff entre el respaldo y el repo salió **al revés** de lo supuesto: el archivo VIVO
era la versión buena y nueva; `cloud/hetzner/services/cerebro_kawiil_mcp.py` estaba
meses atrás. El `--solo-servicios` sobrescribió el archivo en disco con la versión
vieja. El servicio siguió corriendo el código bueno **en memoria**, así que un
`systemctl restart cerebro-kawiil` habría destruido:

| Perdido | Qué hace |
|---|---|
| `recordar()` | Recordatorios pedidos en Cowork → la cola que Donna dispara por Telegram. Sin esto se perdían en silencio. |
| `recordatorios_pendientes()` | Listar lo agendado |
| `aprender()` | Sembrar memoria durable desde Cowork (IMPORTANT/PROJECTS/PEOPLE/CLIENTES/**LEARNINGS**) con dedup |
| `bitacora_cowork()` | Escribe `cowork-history.jsonl`, que el destilador nocturno ingiere |
| `entregable_actualizar()` | Actualizar el cuerpo de un entregable |
| `entregable_registrar(contenido=…)` | **El cuerpo COMPLETO del documento** — así se guardan los 203 entregables |
| Rutas de BD | `legal/sjf/biblioteca.db`, `legal/dof/biblioteca_dof.db` (el repo apuntaba a `legal/sjf.db` y `legal/dof.db`, que no existen) |
| Esquema SQL | `tesis.fecha_publicacion`, tabla `notas` con `texto_plano` e `incluido=1` (el repo consultaba una tabla `publicaciones` con `contenido`, esquema que ya no existe → `legal_buscar` habría reventado) |
| `SEGUIMIENTOS.md` | El archivo de memoria, renombrado desde `AGENDA.md` |
| Instrucciones del server | Las reglas de RECORDATORIOS y DOCUMENTOS que ve Cowork |

## Restauración

1. **En el server** (antes de cualquier reinicio):
   ```bash
   sudo cp /opt/openclaw/.respaldo-20260907202308/scripts/cerebro_kawiil_mcp.py \
           /opt/openclaw/scripts/cerebro_kawiil_mcp.py
   ```
2. **En el repo**: la versión buena se reconstruyó aplicando el diff en reverso
   (798 → 1037 líneas, 18 herramientas expuestas, que son las mismas del MCP vivo).

**Verificación:** el diff entre el repo original y la reconstrucción debe ser el inverso
exacto del diff observado en producción. Comprobado hunk por hunk: **28 de 28**, ninguno
de más ni de menos. Para confirmarlo contra el archivo real:

```bash
sudo diff /opt/openclaw/.respaldo-20260907202308/scripts/cerebro_kawiil_mcp.py \
          /opt/louis/services/cerebro_kawiil_mcp.py     # debe salir vacío
```

## La lección

`self_update.py` deja que Donna edite su propio código en `/opt/openclaw/scripts/`, y
esas ediciones **nunca vuelven al repo**. El repo dejó de ser la fuente de verdad sin
que nadie lo notara, y el primer deploy en meses degradó producción. Dos mitigaciones:

- `actualizar.sh` guarda un manifiesto de hashes y **avisa antes de pisar** un archivo
  editado en vivo (imprime el `diff` contra el respaldo).
- `cerebro-kawiil` no se reinicia automáticamente: sigue con su código en memoria hasta
  que reiniciarlo sea una decisión consciente.

Pendiente menor: el docstring del módulo (líneas 18-19) documenta las rutas viejas
`legal/sjf.db` / `legal/dof.db`. Se dejó igual a propósito, para que la verificación de
arriba salga limpia; corregirlo va en un commit aparte.

## `verificar-drift.sh`: distinguir edición viva de cambio del repo

Después de restaurar el Cerebro quedó la pregunta obvia: **¿los otros 7 archivos también
traían código que el repo no tiene?** Un `diff -q respaldo vs /opt/louis/services/` NO
la contesta — ahí ya está la versión nueva del repo, así que los 8 "difieren" y no se
distingue una edición viva de un cambio legítimo. (Mi primera instrucción de verificación
tenía ese error, y de paso comparaba contra una copia del repo que en el server todavía
estaba sin actualizar.)

La comparación correcta es contra la versión del repo **anterior al deploy**, o sea la
rama base:

```bash
/opt/louis/scripts/verificar-drift.sh            # toma el respaldo más reciente
/opt/louis/scripts/verificar-drift.sh /opt/openclaw/.respaldo-20260907202308
```

Para cada archivo compara `origin/main:cloud/hetzner/services/<f>` contra el respaldo:

- **idéntico** → el repo era la fuente de verdad, el deploy no perdió nada;
- **difiere** → había código en vivo que git no tenía; imprime cuántas líneas y los dos
  comandos para sacar el diff completo y portarlo al repo.

Como referencia, lo que cambió en el repo en esta sesión (`origin/main` → rama):

| Archivo | Líneas |
|---|---|
| `louis_core.py` | +731 −114 |
| `cerebro_kawiil_mcp.py` | +273 −34 |
| `scheduler.py` | +60 −6 |
| `telegram-bridge.py` | +48 −14 |
| `slack-bridge.py` | +13 −2 |
| `openclaw_gateway.py`, `self_update.py` | +9 −9 (renombre a Donna) |
| `browser_runner.py` | +1 −1 |

Si `verificar-drift.sh` reporta un archivo sucio, el diff que salga **no** son estos
cambios: son ediciones vivas que hay que rescatar del respaldo. **No borres los
respaldos hasta cerrar esa revisión** — son la única copia.


---

# Cierre del caso "mensajes a todas horas": no existía

Tres hipótesis, tres equivocadas, y todas por el mismo vicio: razonar sobre el código de
`main` sin comprobar que fuera el que corre, y presentar la inferencia como
comprobación.

| # | Hipótesis | Veredicto |
|---|---|---|
| 1 | `sjf-weekly.timer` sin zona horaria disparaba a las 2am | **Falsa** — el server ya está en CST; `list-timers` mostraba `LAST 08:00 CST` |
| 2 | Catch-up de `Persistent=true` (el disparo de las 20:20) | **Falsa** — el timer real es `00/4:20`, cada 4h; 20:20 era una corrida normal |
| 3 | El horario de `legal-estado` quedó clavado en la hora de arranque | **Falsa** — el mecanismo existe, pero el estado real dice `last_sent 08:20`, fase semanal |

## Lo que dicen los datos

`reminders/sent.jsonl`, 219 avisos:

```
05h×2  07h×104  08h×10  09h×51  10h×15  11h×4  12h×4
13h×2  14h×4  15h×2  17h×2  18h×14  19h×3  22h×2
```

Fuera de 07:00–22:00: **4**, y los cuatro pedidos explícitamente — dos a las 22:00
(recordatorios para el día siguiente, `source: user`/`cowork`) y dos el 3-jun a las
05:26/05:31 (un briefing adelantado, una sola vez).

**No hay mensajes a deshoras.** Lo que hay es **volumen concentrado**: 155 de 219 (71%)
entre 07:00 y 09:00, más los boletines de los timers en la misma franja (DOF 09:00, DOF
18:30, digest lunes 09:30, SJF lunes 08:00, legal-estado semanal 08:20). Unos 7 mensajes
automáticos al día. Eso es una decisión de producto —¿cuántos boletines quiere Polo?— no
un bug de horario.

## Por eso se revierten las horas de silencio

La ventana 22:00→07:00 habría **diferido a las 7:00 los dos recordatorios de las 22:00
que Polo puso a propósito**, y movido el briefing de las 05:26. La implementación exime
`urgente: true`, pero en los datos reales *todo* lo que cae fuera de horario es
`source: user` o `cowork` — o sea, precisamente lo que no hay que tocar.

`scheduler.py` vuelve a su estado original. Se van con él el tope de ráfaga y el prefijo
`(atrasado Nh)`: también resolvían mecanismos que los datos no muestran ocurriendo.

Lo único que sobrevive de este hilo es la **capacidad de medir**:
`hetzner_estado(que="avisos_programados")` con el historial por hora. Si algún día
vuelven los mensajes a deshoras, ese comando lo contesta en un minuto en vez de en tres
hipótesis falsas.


---

# Lo verificado, reaplicado sobre la base correcta

Base: `claude/affectionate-dijkstra-KSoqy` (6-ago), el código realmente desplegado.
Se descartó todo lo que no tenía causa comprobada. Cambios sobre `donna_core.py` y dos
bridges:

| # | Cambio | Evidencia que lo justifica |
|---|---|---|
| 1 | Comprensión: sentidos no documentales, preguntas de seguimiento, cercanía verbo↔tipo, `_es_analisis_legal` más estricto, verbos acentuados | El PPTX de "Análisis legal" ante el Oficio CNBV 411-2/1364/2026 |
| 2 | `m365_crear_evento`: ejecutar sin pedir confirmación; redes anti-fabricación para eventos y notas; gerundios y "¿confirmo?" como stall; reglas "ACCIONES: EJECUTA, NO ANUNCIES" | 4 eventos anunciados como agendados que nunca existieron; **Polo confirmó que ya los crea** |
| 3 | `FORMATO TELEGRAM`: Markdown estándar en vez de legacy | El prompt pedía `*una sola*` = negrita; el conversor lo vuelve *cursiva* |
| 4 | `EX_CONFIG=78` + `RestartPreventExitStatus` + backoff; `/status` distingue arranque de bucle (`NRestarts`) | 268,148 reinicios de `slack-bridge` invisibles ~34 días |
| 5 | `legal_conteo` reescrito + `aprendizaje_legal` nuevo | La rama de SJF nunca contaba las tesis; el DOF traía "mayo-2026" a mano |
| 6 | `CORRECCIONES` → `LEARNINGS.md` en la destilación nocturna | La regla prohibía aprender de las correcciones de Polo |
| 7 | `tool_choice` forzado en órdenes de escritura (tool exacta si la familia es única) | Decisión de Polo: mantener Haiku, no subir a Sonnet |

Descartado: el renombre a Donna (esta rama ya lo hizo mejor), las horas de silencio y
el tope de ráfaga del scheduler (los datos dicen que no hay problema de horarios), y
cualquier cambio a los timers de agosto.

Verificación: 87 casos en `scripts/test-comprension.py`, más el caso del CNBV y el del
calendario comprobados a mano contra `donna_core`.

# Qué le falta al acervo legal (lo que salió al probarlo)

`legal_buscar("fe pública")` **funciona** — devuelve tesis del SJF y notas del DOF con
texto. Pero la misma salida delata tres cosas:

1. **La búsqueda usa `LIKE`, no los índices FTS5 que las BD ya tienen.** Las BD traen
   `tesis_fts`, `notas_fts` y `leyes_fts` (búsqueda de texto completo con ranking bm25),
   y las consultas hacen `texto LIKE '%término%'` con un `LIMIT` y sin `ORDER BY`.
   Resultado: devuelve *las primeras filas que encuentra*, no las más relevantes ni las
   más recientes — por eso al buscar "fe pública" salieron tres convenios de registro
   civil de 2016. Es el arreglo de mayor impacto y menor riesgo del acervo.
2. **Las tesis no tienen fecha:** todos los resultados salen como `[SJF/None]`. La
   columna `fecha_publicacion` existe pero está vacía. Sin fecha no se puede ordenar por
   vigencia ni distinguir una tesis de 1995 de una de 2026 — para análisis legal eso es
   riesgo profesional. (El `legal_conteo` nuevo ya lo marca con
   `⚠ SIN FECHA en ningún registro`.)
3. **El sistema de embeddings está construido pero no conectado.** Los siete `nexo_*`
   (`nexo_embeddings.py`, `nexo_retrieve.py`, `nexo_kb_audit.py`, tres `nexo_backfill*`)
   son "Capa 1 · Bloque 2a — standalone, sin OpenClaw todavía": `donna_core.py` los
   menciona 8 veces, pero `cerebro_kawiil_mcp.py` —lo que Donna y Cowork usan de verdad
   para buscar— no los usa, y no tienen unit de systemd. La búsqueda semántica existe en
   el repo y no está en el circuito.

Orden sugerido: (1) FTS5 + bm25 + orden por fecha, (2) poblar `fecha_publicacion`,
(3) conectar `nexo_retrieve`. Los tres son trabajo aparte, con su propia verificación.

---

# Búsqueda legal: FTS5 y las tesis sin fecha

Los dos defectos que salieron al probar `legal_buscar("fe pública")`. Esta vez se
empezó leyendo el código que ESCRIBE las BD (`legal-scrapers/sjf_biblioteca.py`,
`dof_biblioteca.py`), no el que las lee.

## Lo que ya existía y nadie usaba

Los scrapers crean índices FTS5 **de contenido externo, con triggers de sincronía**:

```sql
CREATE VIRTUAL TABLE tesis_fts USING fts5(
    rubro, texto, precedentes,
    content='tesis', content_rowid='registro_digital',
    tokenize="unicode61 remove_diacritics 2");
-- + triggers tesis_ai / tesis_ad / tesis_au
```

Igual `notas_fts(titulo, texto_plano)` sobre `notas` y `leyes_fts` sobre `leyes`.
Están bien hechos: `remove_diacritics 2` significa que "fe publica" encuentra
"fe pública", y los triggers mantienen el índice al día.

Y la búsqueda hacía `texto LIKE '%término%' LIMIT 5` **sin `ORDER BY`**: devolvía las
primeras filas que encontraba. Por eso al buscar "fe pública" salían tres convenios de
registro civil de 2016 en lugar de las tesis notariales.

## El arreglo

`legal_buscar` ahora consulta el FTS con ranking bm25:

```sql
SELECT t.rubro, t.texto, t.fecha_publicacion, t.epoca, t.instancia
FROM tesis t
JOIN (SELECT rowid, rank FROM tesis_fts WHERE tesis_fts MATCH ?
      ORDER BY rank LIMIT ?) m ON t.registro_digital = m.rowid
ORDER BY m.rank LIMIT ?
```

Cuatro decisiones que vale explicar:

- **`_fts_query()` entrecomilla palabra por palabra y las une con AND.** Así ningún
  carácter del usuario se interpreta como operador FTS5 (`-`, `*`, `:`, `NEAR`, `OR`)
  ni rompe la sintaxis: `"LFPIORPI -algo"` → `"LFPIORPI" AND "algo"`. Si el usuario
  entrecomilla todo, se respeta como frase exacta.
- **El filtro (`incluido = 1`) se aplica DESPUÉS del ranking**, y por eso la subconsulta
  pide `limite × 5` filas: con el filtro dentro del `LIMIT`, un filtro estricto vaciaría
  el resultado.
- **Respaldo a `LIKE`.** Si el FTS falla —índice sin construir, sintaxis, BD vieja— la
  búsqueda responde igual. Y **la vía usada se reporta siempre**: si cayó a `LIKE` o el
  índice está desfasado, sale `⚠ Búsqueda degradada` en la respuesta, no se descubre
  semanas después. Cuando el FTS da 0 resultados pero `LIKE` sí encuentra, el índice
  está desincronizado y la respuesta trae el comando para reconstruirlo
  (`INSERT INTO tesis_fts(tesis_fts) VALUES('rebuild')`).
- **`orden="reciente"`** como opción, para cuando importe la vigencia. Con la salvedad
  de que en el SJF no sirve todavía — por lo de abajo.

Como las tesis no traen fecha, la salida ahora muestra **época e instancia**, que es lo
único que ubica una tesis en el tiempo mientras la fecha no exista.

## Las tesis sin fecha

`fecha_publicacion` existe, tiene índice (`idx_tesis_fecha`) y el scraper la mapea:

```python
"fecha_publicacion": raw.get("fechaPublicacion"),
```

…y llega vacía. Lo que salva el caso: `tesis.raw_json` guarda la respuesta **completa**
del API, así que si el dato viene con otro nombre, se recupera **sin volver a
descargar 1.5 GB**.

`scripts/diagnostico-fechas-sjf.py` lo resuelve en dos pasos:

```bash
# 1) Diagnóstico — solo lectura
python3 /opt/louis/scripts/diagnostico-fechas-sjf.py

# 2) Reparación (ensayo primero, luego --aplicar)
python3 /opt/louis/scripts/diagnostico-fechas-sjf.py --reparar --clave <la_que_diga>
python3 /opt/louis/scripts/diagnostico-fechas-sjf.py --reparar --clave <la_que_diga> --aplicar
```

El diagnóstico inspecciona los campos reales del `raw_json` de las tesis sin fecha y
distingue tres escenarios, probados los tres contra BD sintéticas con el esquema real:

| Escenario | Qué reporta | Qué hacer |
|---|---|---|
| El API la trae con otro nombre | La clave candidata y ejemplos de valores | `--reparar --clave X --aplicar` |
| El campo existe pero viene vacío | `⚠ vienen VACÍOS en el API` | No es bug del scraper; usar `epoca` como proxy |
| Ya todas tienen fecha | `✓ nada que reparar` | Nada |

Dos cuidados en la reparación: es **ensayo por default** (hay que pasar `--aplicar`), y
va **por lotes de 5,000** porque el trigger `tesis_au` reindexa el FTS en cada `UPDATE`
— un `UPDATE` masivo de una sola vez reescribiría el índice completo en una transacción
gigante. Por lotes se ve el avance y se puede interrumpir.

## Las tesis sin fecha: qué dijeron los datos

El diagnóstico en el server:

- **178,554** tesis · **26,967 (15%)** con `fecha_publicacion` · **151,587 sin**
- Las que SÍ tienen fecha son **Décima (17,991), Undécima (7,683) y Duodécima (1,293)
  Época** — suma exacta de 26,967. Las que no, son **Novena Época y anteriores**.
- En la muestra, `fechaPublicacion` viene en la respuesta del API pero **vacía**: no es
  un bug del scraper. El API solo la da desde la Décima Época (2011+).

Pero la **cita** sí lleva la fecha:

```
localizacion = [J]; 9a. Época; Pleno; S.J.F. y su Gaceta; Tomo V, Mayo de 1997; Pág. 5
volumen      = Tomo V, Mayo de 1997
```

`fecha_desde_cita()` la extrae con precisión de mes. Cobertura del 100% en la primera
muestra — **pero esa muestra no era representativa**: un `LIMIT 200` sin `ORDER BY`
devuelve las primeras filas en orden de rowid, que vienen en bloques del mismo tomo
(198693-198698, todas Novena Época de mayo-1997). Se cambió a **muestreo estratificado
por época con `ORDER BY RANDOM()`**, y la cobertura se reporta **por época**, no solo
global: si una época entera no es derivable (los "Volumen 175-180, Cuarta Parte" de la
Séptima Época no traen mes), se ve en vez de diluirse en el promedio.

### Dos decisiones de diseño, y por qué

**1. Va en una tabla aparte (`tesis_fecha_aprox`), no en una columna de `tesis`.**
Salió de una falla en pruebas: al hacer `UPDATE tesis`, el trigger `tesis_au` ejecuta un
`'delete'` contra el índice FTS externo, y si el índice no tiene esa fila —porque la
tabla se pobló antes de que existieran los triggers, o el índice se reconstruyó— SQLite
responde **`database disk image is malformed`**. Un `UPDATE` masivo podía tronar a media
corrida sobre 178 mil filas. Con la tabla aparte no se dispara ningún trigger, no se
reescribe el índice de 1.5 GB, y se revierte con un `DROP TABLE`. El script comprueba de
todos modos la integridad del FTS (`integrity-check`) y avisa.

**2. La fecha derivada NO se mezcla con `fecha_publicacion`,** y en cada resultado se
marca con `~`:

```
[SJF/~1997-05 · Pleno]      NOTARIOS. FE PÚBLICA          ← derivada de la cita
[SJF/2024-03-15 · 1a Sala]  AVISO DE FE PÚBLICA LFPIORPI  ← la que publica la Corte
```

Una fecha sacada de "Tomo V, Mayo de 1997" es una inferencia con precisión de mes. Si
algún día se cita una tesis apoyándose en su fecha, hay que poder saber de dónde salió el
dato — mezclarlas crearía justo el tipo de dato falsamente confiable que esta sesión
estuvo corrigiendo toda la tarde. `orden="reciente"` usa `COALESCE(fecha_publicacion,
fecha_aprox)`, así que ordena por vigencia usando la mejor fecha disponible.

Si la tabla no existe, la búsqueda degrada limpio y muestra la época. Probados los dos
caminos.

```bash
python3 /opt/louis/scripts/diagnostico-fechas-sjf.py --proxy      # cobertura por época
python3 /opt/louis/scripts/diagnostico-fechas-sjf.py --derivar    # ensayo
python3 /opt/louis/scripts/diagnostico-fechas-sjf.py --derivar --aplicar
```
