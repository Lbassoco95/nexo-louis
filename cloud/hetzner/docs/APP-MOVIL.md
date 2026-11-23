# App móvil de Louis (PWA)

App instalable en el celular, servida por el gateway en `https://louis.kawiil.mx/app`.
Reemplaza a Telegram como canal principal: chat con Louis (markdown, tablas, voz),
pestaña **Hoy** (urgentes, vencimientos, marcar hecho), **Entregables** (filtros, búsqueda,
detalle) y **Ajustes**.

## Instalar
1. Despliega: `./deploy.sh` (copia `services/app/` a `/opt/openclaw/scripts/app/`) y reinicia el gateway.
2. En el celular abre `https://louis.kawiil.mx/app`.
3. Pega el token (el valor de `OPENCLAW_GATEWAY_TOKEN` del `.env` del servidor). Se guarda solo en el teléfono.
4. iPhone: Safari → Compartir → *Agregar a pantalla de inicio*. Android: Chrome → ⋮ → *Instalar app*.

## Seguridad
- El cascarón (`/app*`) es público pero no contiene datos ni secretos.
- Todo dato va por `/v1/app/*` y `/v1/chat/completions` con `Authorization: Bearer`; sin token → 401 (falla cerrado).
- Cerrar sesión en Ajustes borra el token del teléfono. Para revocar un teléfono perdido: rotar `OPENCLAW_GATEWAY_TOKEN`.

## Endpoints nuevos (Bearer)
`GET /v1/app/ping` · `GET /v1/app/hoy` · `GET /v1/app/entregables` · `GET /v1/app/entregable?f=` · `POST /v1/app/agenda/hecho {texto}`

## Streaming y notificaciones push (v2)

**Streaming.** El chat usa `POST /v1/app/chat/stream` (SSE): progreso en vivo («Pensando… 6 s»), la
respuesta aparece escribiéndose y el botón ➤ se vuelve ■ para detenerla. Ojo: `call_llm` entrega la
respuesta completa (enrutado + tools + varios modelos), así que el texto se fragmenta al terminar de
generarse; no es token-por-token del modelo. Si el teléfono se bloquea a media respuesta, el servidor la
guarda 1 h (`GET /v1/app/chat/result?rid=`) y la app la recupera al volver; si además hay push activado,
llega una notificación «Louis respondió».

**Push.** Web Push estándar (VAPID + cifrado RFC 8291, módulo `services/louis_push.py`, solo requiere
`cryptography`). Las llaves VAPID se generan solas la primera vez en `/opt/openclaw/state/push_vapid.json`
(0600) y las suscripciones viven en `push_subs.json`. Todo lo que el scheduler manda por Telegram
(briefings, recordatorios, seguimientos) y las alertas directas de Louis ahora también llegan como push.
Opcional: `VAPID_SUBJECT` en el `.env` (por defecto `https://louis.kawiil.mx`).

Activar en el teléfono: Ajustes → Notificaciones → *Activar*. **iPhone (iOS 16.4+): la app debe estar
instalada en la pantalla de inicio y abrirse desde su ícono**; en Safari normal no hay push.
Tests: `python3 cloud/hetzner/tests/test_louis_push.py` (incluye el vector oficial del RFC 8291).

Endpoints nuevos (Bearer): `POST /v1/app/chat/stream` · `GET /v1/app/chat/result` · `GET /v1/app/push/key` ·
`GET /v1/app/push/status` · `POST /v1/app/push/{subscribe,unsubscribe,test}`

## Apagar Telegram (cuando el push esté confirmado)
Interruptor por archivo-bandera, sin reiniciar servicios y reversible:
```bash
sudo touch /opt/openclaw/state/telegram_off        # apaga Telegram saliente (avisos solo por push)
sudo systemctl disable --now telegram-bridge       # deja de atender chats entrantes de Telegram
# Revertir:
sudo rm /opt/openclaw/state/telegram_off && sudo systemctl enable --now telegram-bridge
```
Red de seguridad: la bandera solo surte efecto si hay al menos un teléfono suscrito al push; si no
hay ninguno, Telegram sigue enviando. Slack no se ve afectado.

## Nombre del asistente (Donna)
El asistente se llama **Donna** (antes Louis). Un solo punto de configuración: `AGENT_NAME` en el `.env`
(por defecto `Donna`; se conserva entre deploys). Lo usan la app (título, manifest, textos), las notificaciones
push, el briefing/recordatorios y un bloque de identidad al inicio del system prompt que prevalece sobre las
menciones viejas de «Louis» en `AGENTS.md`/memoria. No se renombran el dominio `louis.kawiil.mx`, los nombres de
archivo internos ni el bot de Telegram (ese se cambia con @BotFather).

## Cambiar el link a `donna.kawiil.mx`
1. DNS: crear un registro `A` de `donna` → IP del servidor (donde se administra `kawiil.mx`).
2. En `/opt/louis/.env`: `LOUIS_DOMAIN="donna.kawiil.mx"` (el nombre de la variable no cambia).
3. `cd /opt/louis && sudo ./deploy.sh --skip-bootstrap` (Caddy saca el certificado solo, 1–2 min).
4. En el celular: abrir `https://donna.kawiil.mx/app`, pegar el token y reactivar notificaciones. Es otro
   origen: el token, el historial local y el permiso de push no se transfieren; en iPhone hay que reinstalar
   el ícono desde el link nuevo.
Los documentos HTML nuevos toman la dirección de `LOUIS_DOMAIN` (o de `CHAT_ENDPOINT` si se define). Los
generados ANTES del cambio llevan escrito `louis.kawiil.mx` en su botón de chat y dejarán de funcionar ahí.
Cada dominio es un sitio distinto en Caddy; mantener los dos activos exige un bloque adicional en el Caddyfile.

## Bandeja de notificaciones, confirmaciones y widget (v3)

**Bandeja (🔔 en el encabezado).** Cada aviso (push, recordatorio, briefing, seguimiento) se guarda en
`/opt/openclaw/state/notifs.json` (últimos 200) aunque la notificación se descarte. La campana muestra los no
leídos y el mismo número aparece en el ícono de la app (Badge API: iOS 16.4+ instalada, Android). Tocar una
notificación abre la bandeja en ese aviso. Endpoints (Bearer): `GET /v1/app/notifs`, `POST /v1/app/notifs/read`,
`POST /v1/app/notifs/action`; `GET /v1/app/hoy` ahora incluye `sin_leer` y `confirmaciones`.

**Botones de confirmación.** Los avisos que invitan a «cierra todos los sugeridos» (seguimiento de la agenda) o a
«agrega todas las altas» (seguimiento de Slack) traen un botón que hace lo mismo que escribir esa frase. Reglas:
lista cerrada de acciones (`_ACTIONS` en `openclaw_gateway.py`); la acción debe estar ofrecida por ese aviso; solo
se ejecuta una vez (el segundo toque devuelve el resultado guardado); y solo aplica la propuesta del MISMO DÍA que el
aviso (si ya hay una más reciente responde 409 y no toca la AGENDA). La app pide confirmación antes de ejecutar.
Para agregar otra confirmación: una regla en `ACTION_RULES` (louis_push.py) y una entrada en `_ACTIONS`.

**Widget (iPhone).** Una app web no puede crear widgets nativos. Alternativa: el script `app/widget-scriptable.js`
para la app gratuita **Scriptable** (Ajustes → «Copiar script del widget»; se sirve en `/app/widget-scriptable.js`,
sin token: lo pide una vez y lo guarda en el llavero del iPhone). Muestra urgentes, fechas, abiertos y
confirmaciones pendientes; se refresca cada ~15 min y al tocarlo abre Hoy. Un widget nativo real exigiría una app
nativa (Xcode + WidgetKit + cuenta de Apple Developer).
Pruebas: `python3 cloud/hetzner/tests/test_notifs.py`, `node cloud/hetzner/tests/test_widget_scriptable.js` (con un
simulador de la API de Scriptable; no sustituye probarlo en el iPhone).

## Entrar con usuario y contraseña (v4)
La app ya no pide pegar el token. Se entra con **usuario y contraseña** y el servidor entrega una **sesión propia**
(`dns_…`): aleatoria, de 30 días (se renueva con el uso), revocable. El token maestro del gateway queda como
credencial de administrador (CLI `donna-chat.py`, `verificar-donna.sh`, opción «Entrar con token (avanzado)»).

**Crear o cambiar la contraseña** (en el servidor; la pide sin mostrarla, mínimo 12 caracteres, mejor una frase):
`python3 /opt/louis/tools/donna-passwd.py polo`. Otros comandos: `--listar` (usuarios y sesiones), `--cerrar-sesiones
[usuario]`, `--borrar usuario`. Al cambiar la contraseña se cierran las sesiones anteriores. No requiere reiniciar.

**Seguridad (`services/app_auth.py`, solo stdlib):** contraseña con scrypt y sal propia, nunca en texto; en disco
(`/opt/openclaw/state/app_users.json`, `app_sessions.json`, 0600) la sesión se guarda solo como SHA-256; límite de
intentos (5 por IP, 10 por usuario y 30 globales en 15 min — bloquea aun con la contraseña correcta); usuario
inexistente y contraseña mala responden igual y tardan igual. **Alcance:** una sesión solo vale en `/v1/app/*`
(chat, Hoy, entregables, bandeja, push); `/v1/tools`, `/v1/agents` y `/v1/chat/completions` siguen exigiendo el token
maestro. Cerrar sesión la invalida en el servidor; «Cerrar sesión en todos los dispositivos» las invalida todas
(también `donna-passwd.py --cerrar-sesiones` si pierdes un teléfono). Endpoints: `POST /v1/app/login`,
`/v1/app/logout`, `/v1/app/sessions/revoke-all`, `GET /v1/app/sessions`.
Pruebas: `python3 cloud/hetzner/tests/test_app_auth.py` (22 casos, incluye el servidor HTTP real).

## Tableros / artefactos (v5)

Donna puede crear **tableros visuales** (seguimiento de un proyecto sin dashboard, KPIs, tablas) y verlos en la app:
botón 🧩 del encabezado = lista; en el chat aparece una tarjeta «🧩 Abrir tablero».

- Herramientas de Donna: `crear_artefacto`, `actualizar_artefacto` (guarda 5 versiones), `listar_artefactos`.
- **Seguridad:** el HTML pasa por un sanitizador de lista permitida (sin scripts, formularios, enlaces, imágenes remotas ni
  `url()` externos) y se muestra en `<iframe sandbox="">` con CSP `default-src 'none'`: no corre código, no sale a la red,
  no ve tu sesión. Importa porque el contenido puede venir de mensajes de Slack o correos.
- **Veracidad:** al pie de cada tablero el **servidor** (no el modelo) escribe de qué herramientas salieron los datos y cuándo;
  si Donna no consultó datos, dice «cifras NO verificadas» (⚠️ en la lista).
- Archivos: `/opt/openclaw/state/artefactos/*.json` (0600). Tope 300 KB c/u, 200 en total.
- Endpoints: `GET /v1/app/artefactos`, `GET /v1/app/artefacto?id=…`.
- Pruebas: `python3 -m unittest tests.test_artefactos`.

## Patio Yoltik (v6)

Donna lee y modifica Patio Yoltik (Supabase `udqkldwxforwgncinkrr`) con DOS roles distintos de Postgres:
`patio_lectura` (lee todo, solo SELECT) y `patio_escritura` (solo UPDATE de tareas e INSERT de comentarios; no borra).
SQL: `scripts/patio-yoltik-rol-lectura.sql` y `scripts/patio-yoltik-rol-escritura.sql`. Credenciales en el servidor:
`sudo bash /opt/louis/scripts/setup-patio-yoltik-creds.sh` → `/opt/openclaw/credentials/patio-yoltik.env` (0600).
Herramientas: `patio_estado/tablas/describir/query/tareas/proyectos/tarea_detalle` (lectura),
`patio_actualizar_tarea` (cerrar = `{"estatus":"completada"}`) y `patio_avance` (escritura, auditada en `logs/patio-yoltik-ops.log`).
Pruebas: `python3 -m unittest tests.test_patio_yoltik`.

## Slack de Yoltik (v7)

Segundo workspace: la app «Donna Yoltik». Para que Donna vea TODO lo que ve Polo (privados, DMs, grupos) se usa el token de usuario
`SLACK_USER_TOKEN_YOLTIK` (xoxp-; User Token Scopes: channels/groups/im/mpim `:read` y `:history`, users:read). Sin él, el de bot (permisos de bot: `channels:read/history`, `groups:read/history`, `users:read`, `chat:write`).
Token en `/opt/openclaw/credentials/slack.env` como `SLACK_BOT_TOKEN_YOLTIK` (`sudo bash scripts/setup-slack-yoltik.sh`, que prueba el token).
Donna solo ve los canales donde la invites (`/invite @Donna`). Las herramientas `slack_resumen/canales/leer/dm_leer` aceptan
`workspace: "yoltik"` (default kawiil). El resumen de las 18:17 y el seguimiento de las 07:41 incluyen Yoltik con etiqueta `[Yoltik]`.
Pruebas: `python3 -m unittest tests.test_slack_workspaces`.

## Adjuntos y grabaciones (v8)

📎 en el chat: tomar foto, subir archivos (PDF, Word, Excel, PowerPoint, audio, video…) o **grabar** reuniones/notas de voz.
- **Sin tope de tamaño propio:** la subida es por partes de 8 MB escritas directo a disco y reanudable; el único límite es el espacio libre
  del servidor (`MEDIA_MIN_FREE_GB`, 3 GB de reserva por defecto). Las fotos se reducen a 2048 px antes de subir.
- **Grabación larga:** sube mientras graba; mantiene la pantalla encendida (Wake Lock). En iPhone la grabación se corta si bloqueas el teléfono o
  sales de la app: es una limitación de iOS para apps web.
- **Procesamiento** (en el servidor, 2 a la vez): foto → lectura con visión de Claude; audio/video → transcripción por tramos de 10 min con marcas de
  tiempo (OpenAI si hay `OPENAI_API_KEY` en `credentials/openai.env`; si no, whisper local) + resumen/acuerdos/pendientes; documentos → texto.
  Requiere `ffmpeg` (y `poppler-utils` para PDF). Aviso a la bandeja cuando termina una transcripción.
- **Donna los consulta:** el contenido va en el mensaje; para el resto y para búsquedas usa `adjuntos_buscar`, `adjuntos_leer`, `adjuntos_listar`.
- Archivos en `/opt/openclaw/state/media/<id>/` (0700/0600). Endpoints: `POST /v1/app/media/init`, `PUT …/<id>/chunk?i=N`, `POST …/<id>/finish`,
  `GET /v1/app/media`, `GET …/<id>`, `…/<id>/text`, `…/<id>/file`.
- Pruebas: `python3 -m unittest tests.test_media`.

## Cowork → Cerebro → Donna (v9)

El conector Cerebro (MCP) ahora tiene `memoria_registrar(tema, resumen, tipo, proyecto, hecho, pendiente, aprendizaje)`: al terminar un trabajo
relevante en Cowork, Claude lo guarda en `spaces/general/COWORK.md` (credenciales retiradas, sin duplicados del mismo día, rotación a
`COWORK-ARCHIVO.md` al pasar de 60 KB). Si hay `aprendizaje`, también se anota en `LEARNINGS.md`. Donna carga `COWORK.md` en su contexto (lo más
reciente) y lo destila cada noche. Las instrucciones del servidor MCP piden a Cowork usarlo al cerrar cada trabajo.
Para que Cowork vea la herramienta nueva: reconectar el conector Cerebro (o abrir una sesión nueva). Pruebas: `python3 -m pytest tests/test_cerebro_memoria.py`.

## Traductor (v10)

🌐 en el encabezado: español ⇄ inglés ⇄ chino (mandarín simplificado, con pinyin). Por turnos: «Hablo yo» / «Habla el otro» (o escribe) →
se transcribe (OpenAI gpt-4o-transcribe) → se traduce (Claude Haiku, instruido como intérprete; el texto se trata como contenido, nunca como
órdenes) → se escucha (OpenAI TTS; el modelo se elige entre los que la cuenta tiene). Cada turno se anota al instante en
`state/traducciones/<sid>.jsonl`; «Terminar y guardar» lo archiva como adjunto «conversación traducida» con resumen, acuerdos y pendientes
(Donna lo busca con `adjuntos_buscar` y propone tareas). Endpoints: `POST /v1/app/translate/new|translate|speak|finish`,
`PUT /v1/app/translate/audio?de=&a=&sid=`. Pruebas: `python3 -m unittest tests.test_translate`.
Siguiente: traducción simultánea en videollamadas con `gpt-realtime-translate` (requiere leer su guía: developers.openai.com debe estar permitido en la red de la sesión).

## Voz con Donna, avisos con enlace y celular → Donna (v11)

- **🎧 Conversación por voz:** manos libres (escucha → entiende (OpenAI) → piensa (Donna, con instrucción de responder breve y hablado) → te contesta en voz →
  vuelve a escuchar). Detecta el silencio solo; «adiós» / «eso es todo» termina; tocar el círculo mientras habla la interrumpe. Todo queda en el chat y en la memoria.
- **Avisos con enlace:** una transcripción lista llega como notificación; al tocarla abre el documento (resumen, texto completo y «Preguntarle a Donna»).
  En la bandeja, cada aviso con documento trae «📄 Abrir y consultar». «Mis adjuntos» tiene buscador sobre todo el texto.
- **Diagnóstico del teléfono:** la app manda sus errores (micrófono, JS) a `clientlog` (máx. 30/min) → `journalctl -u openclaw-gateway | grep clientlog`.
- **ACADEMICO.md:** memoria de diplomados y doctorado (plantilla en `spaces/general/ACADEMICO.md`; copiar a `/opt/openclaw/spaces/general/` si no existe).
- **Del celular a Donna (Atajos de iPhone, sin apps nativas):** un usuario cuyo nombre empieza con `atajo-` SOLO puede subir archivos
  (`PUT|POST /v1/app/media/quick?nombre=…`, cuerpo = el archivo, cualquier tamaño). Crear: `python3 tools/donna-passwd.py atajo-polo`.
  Atajo «Enviar a Donna» (aparece en el menú Compartir): 1) *Obtener contenido de URL* `POST https://donna.kawiil.mx/v1/app/login`, JSON `{"user":"atajo-polo","password":"…"}`
  → 2) *Obtener valor del diccionario* `token` → 3) *Repetir con cada elemento* de la entrada: *Obtener contenido de URL* `PUT https://donna.kawiil.mx/v1/app/media/quick?nombre=[Nombre]`,
  encabezado `Authorization: Bearer [token]`, cuerpo *Archivo*. Sirve para fotos, PDFs, notas, capturas, enlaces y texto. Lo que NO se puede: que una web app lea sola tu
  galería, contactos, mensajes o calendario; solo recibe lo que tú eliges o lo que un Atajo/automatización le envía.
- Pruebas: `python3 -m unittest tests.test_voice tests.test_pwa_assets`.

## Respuesta al instante (v12)

Voz con Donna y traductor ahora usan **streaming**: `PUT /v1/app/voice/turn` y `PUT /v1/app/translate/stream` devuelven eventos
(`stt` lo que dijiste → `respuesta`/`tr` → `audio` por trozos de 1-2 frases, sintetizados en paralelo → `done` con tiempos). El primer audio suena sin esperar el resto.
Transcripción rápida (`gpt-4o-mini-transcribe`). El contexto de la conversación por voz vive en el servidor por `vsid` (solo esa conversación, 30 min), no en el historial del chat.
La app muestra «⏱ te entendió · respuesta · primera voz» para ver dónde se va el tiempo. Para voz simultánea real (sin turnos): `tools/probar-realtime.py` averigua qué modelos/rutas Realtime tiene la cuenta.

## Voz primero, turnos de 5 minutos y compartir (v13)

- **Primero el audio, luego el texto:** en el traductor y en la conversación con Donna el servidor manda el 1.er trozo de voz, DESPUÉS el texto y luego el resto del audio
  (si la voz falla, el texto llega igual). Los turnos largos se leen completos: hasta 6000 caracteres en ≤10 trozos que se sintetizan en paralelo.
- **Turnos de hasta 5 minutos** (cuenta regresiva en los últimos 30 s; al llegar se envía solo, no se pierde), pantalla encendida (Wake Lock) y sin voces encimadas:
  al grabar de nuevo se calla cualquier audio que siga sonando; «Hablo yo» salta la reproducción.
- **Compartir:** 🔗 en el detalle de un adjunto y al guardar una conversación → enlace secreto (192 bits) de SOLO LECTURA, 1/7/30 días, revocable («Dejar de compartir»).
  Página pública `GET /s/<token>` sin scripts, `noindex`, CSP cerrada; 20 intentos fallidos/min por IP. Donna lo hace con `adjuntos_compartir` (solo si Polo lo pide).
- **Traducción simultánea (en vivo):** `tools/probar-traduccion-en-vivo.py` averigua la configuración y los eventos de `gpt-realtime-translate` con la cuenta real.
- Pruebas: `python3 -m unittest tests.test_voice tests.test_ws_codec`.

## Traducción en vivo (v14)

⚡ dentro de 🌐: simultánea con `gpt-realtime-translate`. El teléfono habla con ESTE servidor (WebSocket `/v1/app/live`, 1.er mensaje `auth`) y el servidor con OpenAI
(`wss://api.openai.com/v1/realtime/translations`), así la llave nunca sale de Hetzner. Protocolo verificado con `tools/probar-traduccion-en-vivo-2.py`:
`session.update` (`audio.output.language`, `audio.input.transcription`), audio con `session.input_audio_buffer.append` (PCM16 24 kHz), y de regreso
`session.output_audio.delta`, `session.input_transcript.delta`, `session.output_transcript.delta`. «Hablo yo / Habla el otro» cambia el idioma de salida sin cortar la sesión.
Límites: 2 sesiones a la vez, 30 min por sesión, 90 s sin audio la cierra (`LIVE_MAX_SECONDS`, `LIVE_IDLE_SECONDS`). Con bocina el micrófono se pausa mientras suena la traducción; con audífonos no.
Al terminar se guarda como conversación (resumen, compartir). `tools/probar-donna-realtime.py` averigua cómo montar a Donna sobre `gpt-realtime-2.1` (siguiente paso).
Pruebas: `python3 -m unittest tests.test_live`.

## Donna en tiempo real (v15)

🎧 ahora abre a Donna sobre la voz de OpenAI (`gpt-realtime-2.1`, de voz a voz): sin turnos, con detección de cuándo hablas, y se calla si le hablas encima.
Servidor: WebSocket `/v1/app/donna` (mismo esquema que el traductor en vivo; la llave nunca sale de Hetzner). El modelo de voz tiene la personalidad y un resumen de tu memoria
(`core.build_realtime_brief`: perfil, ACADEMICO, preferencias aprendidas, COWORK) y UNA herramienta, `consultar_donna(pedido)`, que pasa cualquier dato o acción al cerebro completo de Donna
(`core.call_llm` con todas sus herramientas y protecciones anti-invención) y devuelve el texto para que lo diga; `terminar_conversacion` cierra. Antes de cambiar algo (tareas, correos, eventos)
confirma contigo en voz. Las transcripciones (lo que dijiste y lo que dijo) van al historial → memoria de las 23:00. Protocolo verificado con `tools/probar-donna-realtime.py`.
«Modo clásico (por turnos)» queda como respaldo. Con bocina el micrófono se pausa mientras habla; con audífonos se puede interrumpir hablando; tocar el círculo siempre interrumpe.
Variables: `LIVE_DONNA_MODEL` (gpt-realtime-2.1), `LIVE_DONNA_VOICE` (marin), `LIVE_MAX_SECONDS`. Pruebas: `python3 -m unittest tests.test_donna_live`.

## Donna que HACE lo que dice, transcripciones en su base y acceso directo (v16)

- **Acciones reales por voz:** herramientas directas `guardar_nota`, `recordar_regla`, `crear_recordatorio` (módulo `donna_voz.py`): cada una VERIFICA el resultado (relee el archivo) y devuelve ✅ solo si quedó hecho,
  ❌ con el motivo si no. Instrucción de «hacer, no prometer» + **vigilante de promesas**: si Donna dice «ya lo guardé / voy a agendarlo» sin haber llamado una herramienta en esa respuesta, el servidor le inyecta una
  corrección del sistema y le pide actuar (se avisa a la app con `warn`).
- **Trabajo largo → segundo plano:** `consultar_donna` espera 40 s (`DONNA_CONSULTA_ESPERA`); si no termina, sigue solo y manda notificación con el resultado. `tarea_en_segundo_plano` lo deja corriendo de inmediato (sin tope de la conversación).
- **Transcripciones en su base:** cada audio/video transcrito y cada conversación traducida o por voz se carga SOLA a `TRANSCRIPCIONES.md` (resumen, fecha, id; sin credenciales; rota a `TRANSCRIPCIONES-ARCHIVO.md`);
  Donna la lee en su memoria y en su resumen de voz. Las que ya existían: `python3 tools/cargar-transcripciones.py` o «carga las transcripciones a tu base» (`adjuntos_ingerir`).
- **Conversaciones por voz guardadas:** al terminar 🎧 la conversación queda como adjunto «Conversación con Donna …» con resumen (buscable, compartible) y pasa a la base.
- **Acceso directo:** 🎙 flotante en todas las pantallas (un toque = abre y escucha); enlace `/app/?hablar=1` (pantalla de voz lista, un toque); atajo al mantener el icono (Android); el widget de Scriptable trae «🎙 Hablar con Donna».
  iPhone: Atajos → «Abrir URL» `https://donna.kawiil.mx/app/?hablar=1`, ponle de nombre «Hablar con Donna» y asígnalo a: Botón de Acción (Ajustes → Botón de Acción → Atajo), Toque trasero (Accesibilidad → Tocar → Toque trasero),
  Pantalla de bloqueo / Centro de control (control de Atajos), widget de Atajos, o por voz: «Oye Siri, hablar con Donna».
- Pruebas: `python3 -m unittest tests.test_donna_live tests.test_media`.

## Trabajos: lo grande se lleva con un plan y avance visible (v17)

Cuando un pedido es demasiado grande para contestarlo en la conversación (voz o chat), Donna lo registra como **TRABAJO** (`app_trabajos.py`) y lo ves en la pestaña **🛠️ Trabajos**:
1. **Planea:** un modelo lo divide en 3-8 pasos concretos, cada uno `leer` (consultar/analizar), `memoria` (guardar, informe, tablero) o `cambio` (modifica sistemas externos).
2. **Ejecuta solo**, paso por paso, con el cerebro completo de Donna (sus herramientas y protecciones anti-invención); cada paso recibe lo que dieron los anteriores y su resultado queda guardado.
3. **No cambia nada sin ti:** los pasos `cambio` quedan en «🕓 espera tu confirmación» y solo corren cuando los confirmas en el módulo.
4. **Resume** (hecho · hallazgos · pendiente), **notifica** (toca el aviso → abre el trabajo) y se puede **Continuar** para reintentar lo que falló; **Pausar** y **Cancelar** disponibles. Si el servidor se reinicia, retoma solo.
- En la voz: `tarea_en_segundo_plano` crea el trabajo y Donna dice «armé un plan de N pasos y lo ves en el módulo Trabajos»; en la pantalla de voz aparece el botón «🛠️ Ver el trabajo».
  En el chat: herramienta `trabajo_crear` (+ marcador `[[trabajo:ID]]` → botón «Ver trabajo») y `trabajo_estado`. Dentro de un paso no se pueden crear trabajos nuevos.
- También puedes crear uno directo desde la pestaña Trabajos. Límites: 3 corriendo a la vez, 8 pasos, 100 guardados. Estado en `/opt/openclaw/state/trabajos/*.json`.
- API: `GET /v1/app/trabajos`, `GET /v1/app/trabajo?id=`, `POST /v1/app/trabajos/crear`, `POST /v1/app/trabajo/accion` (`pausar|reanudar|cancelar|confirmar`+`paso`).
- Pruebas: `python3 -m unittest tests.test_trabajos`.

## Diseño adaptable y conversaciones guardadas (v18)

- **Se ajusta al dispositivo:** teléfono (<760 px) = barra superior limpia (☰ conversaciones · ✏️ nueva · 🔔 · ⋯ más) + barra inferior; tableta/computadora (≥760 px) = barra lateral fija con conversaciones, secciones (Hoy, Trabajos, Entregables, Ajustes) y herramientas (voz, traductor, tableros), contenido centrado y sin barra inferior.
- **Conversaciones guardadas en el servidor** (`app_chats.py`, `/opt/openclaw/state/chats/<usuario>/`): se guardan solas ~1.5 s después de cada mensaje, se ven iguales en iPhone, tableta y compu. ☰ → lista agrupada (Hoy/Ayer/Esta semana…), búsqueda en títulos y contenido, abrir, ⋯ → cambiar nombre / borrar, «＋ Nueva conversación». El título sale del primer mensaje.
- API: `GET /v1/app/chats[?q=]`, `GET /v1/app/chat?id=`, `POST /v1/app/chats/guardar|renombrar|borrar|nuevo`.
- Arreglos: el botón 🎙 ya no tapa las sugerencias; burbujas ajustadas a su texto; hoja inferior sobre el cajón.
- Pruebas: `python3 -m unittest tests.test_chats tests.test_pwa_assets`.

## Tableros acoplados e interactivos, paneles y aspecto «Cristal» (v19)

- **Tablero al lado del chat:** en compu/tableta un tablero se abre en un panel fijo a la derecha (⤢ pantalla completa, ✕ cerrar) y, si Donna lo crea o lo actualiza en el chat, se abre/refresca solo. En teléfono ocupa toda la pantalla (‹ Donna para volver).
- **Se trabaja dentro del tablero:** el panel trae su propio hilo + caja (con atajos: resumir, qué falta, agregar columna, actualizar). Donna recibe el contenido del tablero como contexto (solo para el modelo) y puede `actualizar_artefacto`. Todo lo hablado queda **guardado en la conversación** (mensajes con `art`, etiqueta «🧩 Conversación del tablero») y en el historial que alimenta la memoria nocturna (solo «[Tablero «título»] pregunta», sin el volcado del contenido).
- **Paneles tipo menú** en pantallas grandes: notificaciones, trabajos, traductor… salen como panel flotante a la derecha (sin oscurecer todo); ⋯ de conversaciones es menú contextual.
- **Aspecto «Cristal»** (desde v21 es el único aspecto; ya no hay selector): vidrio esmerilado, fondo con degradados suaves, barras flotantes, claro/oscuro automático. Solo CSS (`backdrop-filter`), sin dependencias; probado como técnica inspirada en proyectos públicos de «liquid glass» (ninguno copiado). La refracción real con filtros SVG solo funciona en Chromium, no en Safari/iPhone, por eso no se usa.
- Pruebas: `python3 -m unittest tests.test_chats tests.test_pwa_assets`.

## Escuchar y subtitular (v20)

Traducción **pasiva**: tocas «Escuchar» y Donna va escribiendo **en español** lo que oye en inglés o chino, sin voz, mientras tú sigues con lo tuyo.
- **Acceso en cualquier momento:** 👂 en la cabecera (compu), ⋯ → «Escuchar y subtitular» (teléfono), barra lateral, menú del traductor, atajo de la pantalla de inicio («Escuchar», `/app/?escuchar=1`).
- **Sigue escuchando si sales de la pantalla:** ⌄ minimiza y queda una pastilla con el último subtítulo (tócala para volver); se detiene solo con «■ Detener». En iPhone la app debe seguir abierta y la pantalla encendida (iOS pausa el micrófono de las apps web al bloquear).
- **Quién habla:** etiqueta «Voz 1 · grave/media/aguda» por tono de voz (detector de tono + brillo en el teléfono, sin enviar nada extra). Tocas la etiqueta para ponerle nombre. Es **aproximado**: separa bien voces de tono distinto (hombre/mujer, niño…), no distingue dos voces parecidas ni si hablan encimadas; el tono no es un dato fiable del género, por eso solo se describe como grave/media/aguda.
- **Silencio:** si en 1 min nadie habla, avisa en la app y manda una notificación «¿Sigo escuchando?» (toca → te lleva a Seguir / Terminar); si en 3 min más nadie responde, pausa sola (cada minuto cuesta). Variables: `LIVE_ESCUCHAR_SILENCIO` (60), `LIVE_ESCUCHAR_CIERRE` (180), `LIVE_ESCUCHAR_MAX_SECONDS` (7200 = 2 h; reconecta solo si OpenAI corta antes).
- **Ahorro:** en los silencios no se manda audio; cada frase se cierra con ~1.8 s de cola.
- **«¿De qué hablan?»** resume lo escuchado hasta ahora (tema, puntos clave, pendientes). **«Terminar y guardar»** archiva la conversación (con quién habló cada frase) como transcripción buscable y la lleva a la memoria de Donna.
- **Compu:** Opciones (Aa) → «Escuchar el audio de una pestaña» para subtitular una videollamada o video (Chrome/Edge, marcando «Compartir audio de la pestaña»); en iPhone solo por micrófono (iOS no deja capturar el audio de otras apps).
- WebSocket `/v1/app/live` con `{modo:'escuchar'}`; mensajes extra: `ping`, `seguir` (app→servidor) y `silencio`, `voz` (servidor→app). Pruebas: `python3 -m unittest tests.test_live` y `node tests/test_escuchar_voz.js`.

## Nueva interfaz «Cristal» + Escuchar y transcribir + frente a frente (v21)

- **Aspecto único «Cristal»** (vidrio esmerilado) según el diseño aprobado; claro/oscuro lo decide el sistema. Íconos de línea (los emoji solo viven dentro del texto de Donna y de quien escribe: un convertidor
  los sustituye en la interfaz). Escritorio: barra lateral de 290 px (conversaciones, buscador, secciones) + panel central + panel de contexto «Esperan tu decisión / Tablero reciente» (≥1280 px, solo en la conversación);
  tableta: barra lateral + panel; teléfono: una columna con encabezado y campo de escritura flotantes, 4 secciones abajo y el resto (Escuchar, Tableros, Traductor, Ajustes) en el menú ☰.
- **Botones siempre disponibles** (abajo a la derecha): «Hablar con Donna» y «Escuchar» (se vuelve «Escuchando · 04:12»); en teléfono, círculos sobre el campo de escritura.
- **Secciones nuevas:** Escuchar, Tableros (lista) y Traductor son secciones de la barra lateral; la voz y el traductor frente a frente son pantallas completas.
- **Traductor frente a frente:** dos mitades (español / 中文); la del chino gira 180° para quien está al otro lado (en tableta, opción «Lado a lado»). Cada mitad tiene su botón de hablar; mientras uno habla el otro se deshabilita;
  «Dijiste/Te dicen» (你说/对方说); el lado en español muestra además el chino con pinyin y se escucha en chino. «Terminar» regresa al traductor (los turnos quedan para «Terminar y guardar»).
- **Escuchar y transcribir:** transcribe al español lo que dicen otros (chino, inglés…), con voces numeradas y de color (siempre con número), hora, chino original opcional, frase «Hablando ahora», subrayado punteado en lo dudoso,
  panel de voces con «Nombrar» (el nombre se recuerda), pausar/continuar/detener y guardar, y tres interruptores. Indicador rojo «Escuchando · tiempo» en el encabezado de todas las pantallas (tira en teléfono). Aviso de
  silencio al minuto (banner + notificación con «Seguir escuchando» / «Detener y guardar»); si nadie responde **no se detiene sola**, solo vuelve a preguntar. Al guardar queda como **entregable** (con voces y horas) y como documento
  que Donna puede consultar. **Privacidad:** confirmación de consentimiento una vez por sesión; solo se guarda texto, nunca audio. Contrato con el servidor: `docs/ESCUCHAR-CONTRATO.md`.
- Accesibilidad: contraste ≥4.5:1 (probado en claro y oscuro), controles ≥44 px, teclado y nombres accesibles, `prefers-reduced-motion` detiene las animaciones, el desenfoque se apaga solo en equipos modestos (`html.lowfx`).
- Pruebas: `python3 -m unittest tests.test_live tests.test_escucha tests.test_chats tests.test_pwa_assets`, `node tests/test_escuchar_voz.js`, `node tests/test_pwa_offline.js http://127.0.0.1:PUERTO`.

## Compras: Donna prepara, tú pagas (Paso 1)

Pides «cómprame boletos del cine» y Donna, con el navegador del servidor (Chromium headless), busca cartelera, horarios y precios reales, recorre la compra hasta ANTES del pago (`browser_pasos`: varios pasos en la misma
página) y te entrega un botón **«Abrir y pagar con Apple Pay»** (`[[pagar:URL|descripción]]`) que abre el sitio del comercio. El pago lo haces tú con Face ID: Apple Pay no se puede automatizar y no hay tarjetas en el servidor.
- **Candados por código** (`browser_guard.py`, no solo instrucciones): el navegador NO teclea en campos de pago (tarjeta, CVV, vencimiento, CLABE, NIP, códigos SMS) y NO pulsa «Pagar / Confirmar compra / Apple Pay…»; «Comprar boletos» (que abre asientos) sí.
- El botón de pago solo aparece para comercios permitidos (Cinépolis, Cinemex, Cinemark, Ticketmaster MX, Boletia, Superboletos…) y con https, verificado en servidor y en la app; cualquier otro enlace se muestra como «no verificado».
- Lo que diga una página web se trata como información, nunca como instrucciones.
- Limitaciones reales: **Cinemex** se deja leer; **Cinépolis** bloquea a los navegadores automáticos (Cloudflare) — Donna lo dice y te da el enlace directo. Si el sitio pide login o código SMS, lo haces tú.
- Arreglo de fondo: antes cada comando del navegador abría una página nueva en blanco (leer/clic después de navegar no veían nada); ahora recuerda la última página y `navegar` devuelve el texto.
- Instalación del navegador en el servidor: `sudo bash ~/donna-deploy/cloud/hetzner/scripts/install-playwright.sh` (el verificador, sección 3l, lo comprueba).
- Pruebas: `tests/test_browser_guard.py` (candados + navegador real contra una página local; requiere Playwright en Python) y `tests/test_agenda_directa.py` (marcadores de pago).

## Navegador: inicias sesión tú, Donna la retoma (v24)
Vista **Navegador** (menú lateral): eliges Cinemex, Cinépolis, Cinemark, OpenTable, Ticketmaster, Boletia u otro sitio https y se abre un Chromium del servidor que ves y manejas (toques, texto, teclas, scroll). Escribes tu usuario/contraseña/código **tú**; con «Es contraseña» el campo se oculta. «Guardar sesión y cerrar» deja las cookies en `browser-context.json` (0600), el mismo archivo que usa Donna al buscar y preparar compras, así que retoma la sesión sin ver la contraseña. «Olvidar» borra la sesión de un sitio. Solo https hacia sitios públicos; se cierra solo a los 10 min. Código: `services/browser_live.py`, endpoint `POST /v1/app/navegador`. Pruebas: `tests/test_browser_live.py`.

## Avisos interactivos, resúmenes en base de datos y Seguimiento (v25)
- **Base de datos** `donna.db` (SQLite en `/opt/openclaw/state`, 0600): `notificaciones`, `notif_eventos`, `resumenes`, `seguimientos`, `seg_eventos`. Los historiales solo se agregan. El esquema es portable a Postgres. La bandeja vieja (`notifs.json`) se migra sola la primera vez (queda como `.migrado`). Módulos: `donna_db.py`, `donna_seg.py`, `donna_resumen.py`.
- **Avisos interactivos**: cada aviso trae ✓ Hecho · ⏰ 1 h · 🌅 Mañana (8:00) · 💬 Preguntar (abre un chat con el aviso) · ✕ Descartar. Los pospuestos vuelven solos a su hora. Si el aviso es de un seguimiento, «Hecho» y «Posponer» también lo aplican al seguimiento.
- **Resúmenes** (mediodía, cierre del día, semanal): ya no llegan como mensaje largo. Se guardan estructurados (Para hoy · Mañana · Vencidas · Entregables · Briefs) y el aviso es una línea que abre el resumen, con ✓ por pendiente. Lo de fechas pasadas va en «Vencidas», no en «Para hoy». También aparecen en la vista Hoy («Resúmenes guardados»).
- **Seguimiento** (vista propia, independiente de los avisos): lo que Donna sigue por ti (tareas, tu día, compras, reservaciones). Se crea en el chat («dale seguimiento a…», herramienta `seguimiento_crear`) o en la vista. Cuando toca revisión avisa una vez con botones y repite según su cadencia. Pruebas: `tests/test_donna_db.py`.

## Donna proactiva (v27)
Ver `docs/DONNA-PROACTIVA-PLAN.md`. Seguimiento → «🛰 Lo que Donna vigiló» (bitácora), «Propuestas de cierre» (con evidencia; aprobar, rechazar o «cierra solas las de este tipo») y «Cuánta autonomía le doy». Scheduler: radar de fechas 08:00; cierres 12:00 y 17:00. Endpoints: `GET /v1/app/radar`, `POST /v1/app/radar/cierre|autonomia|ejecutar`. Pruebas: `tests/test_radar.py`.

## Agente de vigilancia (v29)
Sin horarios fijos: el agente decide qué revisar y cuándo volver (`donna_agente.py`; ver `docs/DONNA-PROACTIVA-PLAN.md`). Seguimiento → «🧠 Revisar ahora», «Qué decidió y por qué» y «Lo que ha aprendido de cómo trabajas» (confirmar / corregir / olvidar). Endpoints: `GET /v1/app/agente`, `POST /v1/app/agente/conocimiento`, `POST /v1/app/radar/ejecutar {que:'agente'}`. Pruebas: `tests/test_agente.py`.
