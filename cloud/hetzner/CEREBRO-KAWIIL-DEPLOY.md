# Cerebro Kawiil — Puesta en producción paso a paso

> Tiempo estimado: 20 minutos si el VPS ya está corriendo.

---

## Paso 0 — Prerequisitos

```bash
# En el VPS (como root o polo con sudo)
python3 --version   # necesitas 3.10+
systemctl status telegram-bridge   # confirma que Louis ya corre
```

---

## Paso 1 — Variables de entorno en Hetzner

Edita `/opt/openclaw/openclaw.env` y agrega al final:

```bash
# Cerebro Kawiil MCP
CEREBRO_DOMAIN=cerebro.kawiil.mx
CEREBRO_PORT=4040
CEREBRO_KAWIIL_TOKEN=$(openssl rand -hex 32)   # genera uno aquí y pégalo fijo
```

> **Guarda el token.** Lo necesitarás en Claude.ai → Connectors.
> Nunca lo pegues en el chat ni en el repo.

---

## Paso 2 — Instalar dependencias Python

```bash
sudo bash /opt/louis/bootstrap/07-cerebro-kawiil.sh
```

Verifica que no haya errores:
```bash
python3 -c "from mcp.server.fastmcp import FastMCP; import uvicorn; print('OK')"
```

---

## Paso 3 — Copiar el script al runtime

```bash
sudo install -m 0755 -o polo -g polo \
  /opt/louis/services/cerebro_kawiil_mcp.py \
  /opt/openclaw/scripts/cerebro_kawiil_mcp.py
```

---

## Paso 4 — Instalar el servicio systemd

```bash
# Rellenar los placeholders del .service y copiarlo
sudo sed \
  -e "s|@@SYSTEM_USER@@|polo|g" \
  -e "s|@@OPENCLAW_HOME@@|/opt/openclaw|g" \
  -e "s|@@ENV_FILE@@|/opt/openclaw/openclaw.env|g" \
  /opt/louis/services/cerebro-kawiil.service \
  > /etc/systemd/system/cerebro-kawiil.service

sudo systemctl daemon-reload
sudo systemctl enable --now cerebro-kawiil
```

Verificar que arrancó:
```bash
sudo systemctl status cerebro-kawiil
curl http://127.0.0.1:4040/health
# Debe responder: {"status":"ok","service":"cerebro-kawiil","version":"1.1"}
```

---

## Paso 5 — DNS

En tu proveedor de DNS, agrega:

```
cerebro.kawiil.mx  →  A  →  <IP del VPS>
```

Espera ~60 segundos para propagación.

---

## Paso 6 — Actualizar Caddy

El Caddyfile ya incluye el bloque de Cerebro Kawiil.
Solo necesitas copiar el Caddyfile actualizado y recargar Caddy:

```bash
# Desde /opt/louis (donde vive el repo)
sudo docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

O reiniciar el contenedor:
```bash
sudo docker compose restart caddy
```

Verificar TLS:
```bash
curl https://cerebro.kawiil.mx/health
```

---

## Paso 7 — Conectar en Claude.ai

1. Ir a **claude.ai → Settings → Connectors** (o desde la app de escritorio)
2. Agregar nuevo connector:
   - **URL**: `https://cerebro.kawiil.mx/sse`
   - **Tipo**: SSE
   - **Auth**: Bearer Token → pegar el valor de `CEREBRO_KAWIIL_TOKEN`
3. Guardar y verificar que aparezcan las herramientas del Cerebro

---

## Paso 8 — Verificar en Cowork

En una sesión de Cowork (con el connector activo), prueba:

```
cerebro_estado()
```

Debe responder algo como:
```
Cerebro Kawiil — 2026-06-04 15:30
Memoria: AGENDA(4KB) | IMPORTANT(2KB) | JOURNAL(8KB) | PROJECTS(3KB) | PEOPLE(2KB)
Legal: SJF⚠️ | DOF⚠️
Entregables: ✅listo:1 | briefs_pendientes:0
```

---

## Paso 9 (opcional) — Conectar Louis al MCP

Para que Louis pueda escribir entregables y briefs desde Telegram/Slack,
agrega en `louis_core.py` las tool definitions del MCP. Esto va en una
segunda iteración cuando el almacén esté maduro.

Por ahora Louis **lee** directamente de disco (ya lo hace con `build_operational_snapshot()`),
y Cowork es quien **escribe** en el almacén compartido.

---

## Troubleshooting

```bash
# Ver logs del servicio
sudo journalctl -u cerebro-kawiil -f

# Probar sin Caddy (directo al puerto interno)
curl -H "Authorization: Bearer TU_TOKEN" http://127.0.0.1:4040/sse

# Reiniciar después de actualizar el script
sudo systemctl restart cerebro-kawiil

# Ver si el puerto está en uso
ss -tlnp | grep 4040
```

---

## Costo de tokens por herramienta (referencia)

| Herramienta | Tokens típicos | Cuándo usar |
|---|---|---|
| `cerebro_estado()` | ~80 | Inicio de sesión, siempre |
| `agenda_pendientes()` | ~150 | Revisar tareas pendientes |
| `entregables_listar()` | ~100 + 30/ítem | Ver estado de entregables |
| `legal_estado()` | ~50 | Verificar disponibilidad |
| `agenda_snapshot()` | ~500 | Contexto operativo completo |
| `entregable_estado(completo=False)` | ~80 | Metadatos de un entregable |
| `entregable_estado(completo=True)` | ~400-1500 | Leer el documento completo |
| `memoria_leer(max_chars=2000)` | ~500 | Acceso a memoria específica |
| `legal_buscar(limite=5)` | ~400 | Búsqueda legal básica |
| Writes (`registrar`, `marcar_hecho`, etc.) | ~50 | Sin restricción |
