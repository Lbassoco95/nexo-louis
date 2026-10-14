# Biblioteca Legal — acceso seguro del equipo con Microsoft 365

La Biblioteca Legal (búsqueda de SJF y DOF, fichas con su PDF y panel de lo que
aprenden los agentes) vive en `https://biblioteca.kawiil.mx`. Cada persona entra con
**su cuenta @kawiil.mx de Microsoft 365**. No hay contraseña compartida: quien se da de
baja en Microsoft pierde el acceso solo.

## Cómo funciona

```
Navegador ──HTTPS──► Caddy (biblioteca.kawiil.mx)
                       │  ¿sesión válida?  (forward_auth)
                       ├────────────────► oauth2-proxy (127.0.0.1:4180) ◄──► Microsoft Entra ID
                       │  sí: pasa el correo verificado en X-Auth-Request-Email
                       └────────────────► biblioteca.py (127.0.0.1:4050, solo lectura)
```

- `oauth2-proxy` solo contesta "sesión válida / no válida"; **nunca sirve contenido**.
- La app solo escucha en `127.0.0.1`: no hay forma de llegar a ella sin pasar por Caddy.
- **Falla cerrado:** si `oauth2-proxy` se cae, Caddy responde 502 y nadie entra.
- Caddy **sobrescribe** `X-Auth-Request-Email` y `X-Auth-Request-User` con lo que dice
  `oauth2-proxy`; un cliente no puede suplantar a otra persona mandando esa cabecera.
- **La app debe validar la identidad que recibe:** si el proxy aprueba una sesión sin correo,
  Caddy entrega el texto literal `{http.reverse_proxy.header.X-Auth-Request-Email}` (la
  cabecera falsa del cliente igual se descarta). `biblioteca.py` solo acepta un valor con
  forma de correo y rechaza cualquier otro.

Verificado con Caddy 2.8.4 real y un proxy de sesión simulado: (A) proxy caído → 502 sin
contenido; (B) sin sesión → 302 a `/oauth2/start`; (C) con sesión → la app recibe el
correo; (D) cabecera falsa del cliente → la app recibe el correo verificado, no el falso;
(E) sesión sin correo + cabecera falsa → la falsa se descarta (la app recibe un texto no válido).
**No se ha probado todavía con un tenant real de Microsoft.**

## Mientras Microsoft 365 no esté configurado: túnel SSH

La app (`biblioteca.py`, unidad `biblioteca.service`) escucha **solo en `127.0.0.1:4050`**: no
es accesible desde Internet. Para usarla ya, abre un túnel desde tu Mac:

```bash
ssh -L 4050:127.0.0.1:4050 polo@louis.kawiil.mx
```

y mientras esa terminal esté abierta entra a `http://localhost:4050`. Solo entra quien tenga
acceso SSH al servidor (llave); la app los registra como `local`. **No actives el sitio
`biblioteca.kawiil.mx` de Caddy sin el inicio de sesión de Microsoft** (pasos abajo): cualquier
petición que llegue por Caddy sin un correo verificado recibe 401 (la app falla cerrado), pero
el inicio de sesión es lo que identifica a cada persona.

Estado de agentes e indexación sin abrir el navegador (solo lectura):
`python3 /opt/openclaw/scripts/biblioteca.py --estado`

## Lo que necesitas antes

1. **DNS:** un registro `A` de `biblioteca.kawiil.mx` hacia la IP del VPS. Sin esto Caddy no
   puede obtener el certificado TLS de ese sitio (no afecta a los demás).
2. **Una persona con rol de administrador en Microsoft Entra** (Application Administrator
   o superior) para el paso siguiente.

## 1. Registrar la aplicación en Microsoft Entra (la hace un administrador)

Centro de administración de Entra → **Identidad → Aplicaciones → Registros de aplicaciones
→ Nuevo registro**:

| Campo | Valor |
|---|---|
| Nombre | `Biblioteca Legal Kawiil` |
| Tipos de cuenta compatibles | **Solo cuentas de este directorio organizativo (inquilino único)** |
| URI de redirección | Plataforma **Web**: `https://biblioteca.kawiil.mx/oauth2/callback` |

Después, en esa aplicación:

1. **Información general:** copia **Id. de aplicación (cliente)** e **Id. de directorio
   (inquilino)**. Este último es el mismo `MS_TENANT_ID` que ya usa la integración de
   Outlook; el Id. de cliente es **distinto**: no reutilices la app de Outlook (otra URI de
   redirección y otros permisos).
2. **Certificados y secretos → Nuevo secreto de cliente.** Copia el **Valor** en el
   momento: Microsoft solo lo muestra una vez. Elige una caducidad (p. ej. 12 meses) y
   **agéndala**: el día que caduque, el inicio de sesión dejará de funcionar.
3. **Permisos de API:** con los permisos delegados por defecto de Microsoft Graph
   (`User.Read`) y `openid`, `profile`, `email`, `offline_access` basta (este último permite
   renovar y revalidar la sesión cada hora). No pidas nada más.
4. **Configuración de token → Agregar notificación opcional → ID → `email`** (si alguna
   persona entra y `oauth2-proxy` dice que no encuentra su correo).

### Limitar quién puede entrar (recomendado)

El dominio `@kawiil.mx` ya se exige en `oauth2-proxy`, pero conviene además una lista
explícita. Entra → **Aplicaciones empresariales → Biblioteca Legal Kawiil**:

- **Propiedades → "¿Se requiere asignación?" = Sí.**
- **Usuarios y grupos → Agregar usuario/grupo:** solo las personas (o un grupo) que deban
  consultar la biblioteca. Quien no esté asignado recibe el error `AADSTS50105` aunque
  tenga cuenta de la empresa.

## 2. Poner las credenciales en el servidor (nunca en el repo ni en el chat)

En el `.env` del VPS (`/opt/louis/.env`):

```bash
BIBLIOTECA_DOMAIN=biblioteca.kawiil.mx
BIBLIOTECA_PORT=4050
BIBLIOTECA_EMAIL_DOMAIN=kawiil.mx
BIBLIOTECA_TENANT_ID=<Id. de directorio>
BIBLIOTECA_CLIENT_ID=<Id. de aplicación (cliente)>
BIBLIOTECA_CLIENT_SECRET=<Valor del secreto>
BIBLIOTECA_COOKIE_SECRET=<ver abajo>
COMPOSE_PROFILES=biblioteca
```

Genera el secreto de las cookies con:
`python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`

## 3. Activar

```bash
cd /opt/louis
docker compose --env-file .env up -d oauth2-proxy
docker compose --env-file .env up -d caddy        # recarga el Caddyfile con el sitio nuevo
docker logs --tail 30 louis-oauth2-proxy          # debe decir "OAuthProxy configured for ..."
```

Si el contenedor se reinicia en bucle, casi siempre falta una variable `BIBLIOTECA_*` o el
secreto es incorrecto: el motivo aparece en `docker logs louis-oauth2-proxy`.

## 4. Probar (hazlo antes de avisar al equipo)

1. En una ventana privada abre `https://biblioteca.kawiil.mx` → debe llevarte al inicio de
   sesión de Microsoft y, al volver, mostrar la biblioteca.
2. Con una cuenta **no asignada** en Entra → debe rechazar (`AADSTS50105`).
3. Con una cuenta personal (Outlook/Gmail) → debe rechazar.
4. `curl -I https://biblioteca.kawiil.mx/` sin sesión → `302` hacia `/oauth2/start`.

## Operación

- **Dar de baja a alguien:** quitarlo de "Usuarios y grupos" (o deshabilitar su cuenta). Las
  sesiones duran 8 h y se renuevan con Microsoft cada hora: en cuanto Entra rechaza la
  renovación la sesión se cierra. Esperado: **hasta ~1 h; tope 8 h**. Esto no se ha medido con
  un tenant real; si necesitas corte inmediato, cierra también sus sesiones desde Entra
  ("Revocar sesiones") y reinicia `oauth2-proxy`.
- **Rotar el secreto:** crear uno nuevo en Entra, actualizar `BIBLIOTECA_CLIENT_SECRET` y
  `docker compose --env-file .env up -d oauth2-proxy`.
- **Quién consultó qué:** el log de Caddy (`/opt/louis-data/caddy/data/logs/biblioteca.log`)
  y el de la app registran el correo verificado de cada petición.

## Errores frecuentes

| Mensaje | Causa |
|---|---|
| `AADSTS50011` (la URL de respuesta no coincide) | El URI de redirección de Entra no es exactamente `https://biblioteca.kawiil.mx/oauth2/callback`. |
| `AADSTS50105` | La persona no está asignada a la aplicación empresarial. |
| `AADSTS7000215` | El secreto de cliente es incorrecto o caducó. |
| 502 en la página | `oauth2-proxy` no está corriendo (a propósito: falla cerrado). |
| "email not found" tras iniciar sesión | Falta la notificación opcional `email` (paso 1.4). |
