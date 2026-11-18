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

Segundo workspace: la app «Donna Yoltik» (permisos de bot: `channels:read/history`, `groups:read/history`, `users:read`, `chat:write`).
Token en `/opt/openclaw/credentials/slack.env` como `SLACK_BOT_TOKEN_YOLTIK` (`sudo bash scripts/setup-slack-yoltik.sh`, que prueba el token).
Donna solo ve los canales donde la invites (`/invite @Donna`). Las herramientas `slack_resumen/canales/leer/dm_leer` aceptan
`workspace: "yoltik"` (default kawiil). El resumen de las 18:17 y el seguimiento de las 07:41 incluyen Yoltik con etiqueta `[Yoltik]`.
Pruebas: `python3 -m unittest tests.test_slack_workspaces`.
