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
