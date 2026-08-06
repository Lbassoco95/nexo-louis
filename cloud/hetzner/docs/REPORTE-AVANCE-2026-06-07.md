# Reporte de avance — Donna / Cerebro Kawiil
**Fecha:** 7 de junio de 2026 · **Sesión de trabajo intensiva**

---

## 1. Recuperación crítica: Donna revivido

Donna estaba **caído** (errores `is_status_command`, `call_llm history_file`, `_is_billing_error`). Causa: otra sesión intentó meter el Cerebro a producción con un script que **parchaba el archivo vivo** (`patch_donna_core.py`), y lo corrompió.

- ✅ **Revivido**: se restauró el `donna_core.py` bueno (8120 líneas) y se le **injertó el Cerebro Kawiil de forma limpia** (sin parches).
- ✅ **Regla establecida**: nunca parchear el archivo vivo; siempre desplegar archivo completo.

## 2. Cerebro Kawiil — MCP en producción

El cerebro compartido Donna ↔ Cowork, en línea:

- ✅ Servicio `cerebro-kawiil` en Hetzner, expuesto con **HTTPS** (`https://cerebro.kawiil-central.mx`) vía Caddy.
- ✅ **OAuth** implementado (los connectors de Claude lo exigen): discovery + registro dinámico + PKCE.
- ✅ **DNS-rebinding protection** desactivada (estaba bloqueando el SSE detrás de Caddy).
- ✅ Donna **lee el cerebro** desde Telegram (`cerebro_estado`, `cerebro_listar`, `cerebro_proyecto_estado`, `cerebro_crear_brief`, `cerebro_sync_agenda`).
- 🔜 Falta: conectar el connector en la app (Cowork) — quedó a un paso.

## 3. Memoria confiable

- ✅ **`reemplazar_pendiente`**: ya no duplica el checkbox (`- [ ] - [x]`).
- ✅ AGENDA depurada (perfiles Joshui correctos, sin duplicados).
- ✅ Donna reporta estado real y honesto (deja de inventar/confirmar en falso).

## 4. Briefing matutino — reconstruido

Antes: "razonaba" la agenda sobre la memoria y **revivía juntas viejas** (inventó una junta Dazon que no existía).

- ✅ Ahora es **determinístico**: lee el **calendario M365 EN VIVO** de **ambos tenants (Kawiil + Yoltik)** — cero invención.
- ✅ Sale como **documento HTML visual** (tabla), no la lista fea de Telegram.
- ✅ **Brandeado** con el logo Kawiil + colores de marca.
- ✅ Dedup de eventos repetidos entre calendarios.
- 🔜 Falta: timer L–V 07:00 + apagar el briefing viejo de texto.

## 5. Pipeline legal (SJF + DOF) — datos duros

**Calendario de reportes:**
- **SJF semanal** → lunes (publicaciones de la semana pasada, ventana de 7 días, por materia).
- **DOF diario** → L–V (reporta el día hábil anterior; lunes da el viernes; descansa fines de semana).
- **Avance de descargas** → lunes y viernes.

**Estado corroborado (datos reales):**

| | SJF | DOF |
|---|---|---|
| Acervo | 30,606 / 262,016 (12%) | ~210,468 notas válidas |
| Al día | ✅ (última 5-jun) | ✅ (L–V) |
| Backfill histórico | ✅ activo (cursor ~1.92M) | ✅ activo (agresivo) |
| Texto completo | **98%** ✅ | **5%** ⚠️ |
| PDF | **99%** ✅ | **1%** ⚠️ |
| Listo para deep learning | **SÍ** | **No — falta extraer histórico** |

- ✅ Reporte de avance mejorado: faltante hacia atrás, temporalidad, al-día vs backfill, % indexación, bloque de deep learning.
- ✅ Timers canónicos versionados (`dof-daily`, `legal-estado`, `sjf-weekly`, `sjf-update`, `sjf-backfill`), TZ CDMX.

## 6. Branding

- ✅ Kit de marca Kawiil en el repo + servidor (`/opt/openclaw/assets/brand`).
- ✅ **Manifiesto** con metadatos para que la IA elija el asset por contexto.
- ✅ Logo + colores aplicados al briefing (extensible a boletines).

## 7. Acceso remoto

- ✅ **Termius (iPhone) conectado al servidor por llave SSH** — funciona con datos móviles. Trabajo desde fuera de casa habilitado.

---

## Pendientes (priorizados)

**Autonomía / infraestructura**
- 🔜 Clonar el repo en el servidor (deploy key) → deploys desde Termius sin la Mac.

**Briefing**
- 🔜 Timer L–V 07:00 + apagar el briefing viejo de texto.
- 🔜 HTML interactivo (colapsables, checkboxes, filtros).

**Legal / deep learning**
- 🔜 Extraer texto/PDF del **DOF histórico** (cuello de botella para DL: solo 5%).
- 🔜 Depurar fechas basura del DOF (año 0000, 27 futuras).
- 🔜 Cazar el **duplicado del DOF** (viene de un job en la Mac — `launchctl`).

**Integraciones**
- 🔜 Cerebro: conectar el connector en Cowork (app).
- 🔜 Gamma: guardar la key como secreto + tool `generar_visual_gamma`.
- 🔜 Canva en Cowork (Settings → Connectors).
- 🔜 Yoltik: cargar su kit de marca.

**Seguridad (importante)**
- ⚠️ **Rotar el token del Cerebro** (se expuso en chat varias veces).
- ⚠️ **Rotar la API key de Gamma** cuando esté funcional (se expuso en chat).

---

*Generado por Donna (Kawiil) — sesión del 7-jun-2026.*
