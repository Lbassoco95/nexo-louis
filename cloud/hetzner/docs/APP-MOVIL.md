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
