# Paso 1: Registrar la app de Nexo en Entra ID

Repite este proceso DOS veces: una en tenant Kawiil, otra en tenant Yoltik. Cada tenant produce su propio Client ID, Tenant ID y secret.

## Antes de empezar

Necesitas estar logueado en `https://entra.microsoft.com` con tu cuenta admin del tenant correspondiente (`admin@kawiil.mx` para Kawiil, equivalente para Yoltik).

Si tienes ambas cuentas, hazlo en ventanas privadas separadas o cierra sesión entre una y otra para no confundir tenants.

---

## A. Registrar la app

1. Abre **`https://entra.microsoft.com`** y entra con la cuenta admin del tenant.
2. Verifica arriba a la derecha que el tenant correcto esté seleccionado. Si no, click en tu perfil → "Switch directory".
3. Menú izquierdo → **Identity** → **Applications** → **App registrations**.
4. Click en **+ New registration** (arriba).
5. Llena el formulario:
   - **Name:** `Nexo - Asistente Ejecutivo` (mismo nombre en ambos tenants para consistencia)
   - **Supported account types:** `Accounts in this organizational directory only (Single tenant)` ← **importante**, single-tenant
   - **Redirect URI:**
     - Platform: `Public client/native (mobile & desktop)`
     - URI: `http://localhost:8765/callback`
     (Es un loopback redirect para Authorization Code Flow con PKCE; OpenClaw escuchará temporalmente en ese puerto durante el OAuth handshake)
6. Click **Register**.

Te lleva a la página "Overview" de la app. Anota:
- **Application (client) ID** — lo vas a usar como `CLIENT_ID`
- **Directory (tenant) ID** — lo vas a usar como `TENANT_ID`

---

## B. Configurar permisos (API permissions)

1. Menú izquierdo de la app → **API permissions**.
2. Click **+ Add a permission** → **Microsoft Graph** → **Delegated permissions**.
3. Selecciona estas 5 permissions (usa el buscador):
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `Calendars.ReadWrite`
   - `User.Read` (probablemente ya viene por default)
   - `offline_access`
4. Click **Add permissions**.
5. Después de agregarlas, click **Grant admin consent for [tu tenant]** y confirma con **Yes**. Esto evita que tengas que aprobar cada permission al loguear.

Deberías ver las 5 permissions con un check verde "Granted for [tu tenant]".

---

## C. Configurar autenticación (Authentication)

1. Menú izquierdo de la app → **Authentication**.
2. Verifica que en "Mobile and desktop applications" aparezca `http://localhost:8765/callback`. Si no, agrégalo.
3. Baja a **Advanced settings** → **Allow public client flows** → ponlo en **Yes**.
   (Esto habilita Authorization Code Flow + PKCE sin necesidad de secret. Es el flow correcto para apps locales sin servidor backend.)
4. Click **Save** arriba.

---

## D. (Opcional) Restricción de IP

Si quieres, en **Authentication** → **Conditional Access** puedes limitar que esta app solo se autentique desde tu IP de oficina o Tailscale. Por ahora no lo hacemos — primero validamos que funciona, después restringimos.

---

## E. ¿Necesito secret?

**No.** Con `Allow public client flows = Yes` y PKCE no necesitas client secret. Es más seguro porque no hay secret que se pueda filtrar.

Si por alguna razón el MCP que usemos requiere secret (lo decidiremos en el paso 3), entonces sí — pero esperamos a ver.

---

## F. Anotar los valores

Crea un archivo temporal `~/.openclaw/m365-kawiil.env` (luego haremos uno para Yoltik):

```bash
mkdir -p ~/.openclaw/credentials
cat > ~/.openclaw/credentials/m365-kawiil.env <<'EOF'
M365_KAWIIL_CLIENT_ID="..."
M365_KAWIIL_TENANT_ID="..."
M365_KAWIIL_REDIRECT_URI="http://localhost:8765/callback"
M365_KAWIIL_AUTHORITY="https://login.microsoftonline.com/<TENANT_ID>"
EOF
chmod 600 ~/.openclaw/credentials/m365-kawiil.env
```

Rellena con los valores de Overview. **Importante:** chmod 600 para que solo tú puedas leer el archivo.

Después repite todo (A-F) para Yoltik y guarda en `~/.openclaw/credentials/m365-yoltik.env`.

---

## Cuando hayas terminado

Dime y pasamos al paso 2 (instalar y configurar el MCP de Outlook en OpenClaw + hacer el primer OAuth handshake para obtener el token de acceso).

## Resultados esperados al final del paso 1

- ✅ Una app registrada en cada tenant (Kawiil y Yoltik)
- ✅ Cada app con 5 permissions Graph + admin consent
- ✅ Public client flows habilitado
- ✅ Dos archivos `.env` con Client ID + Tenant ID en `~/.openclaw/credentials/`

Tiempo estimado: 10-15 min por tenant (20-30 min total).
