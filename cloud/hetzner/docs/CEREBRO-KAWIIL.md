# Cerebro Kawiil — Cerebro compartido Louis ↔ Cowork

> Estado: **propuesta de proyecto** · Autor: Louis (Kawiil) · Fecha: 2026-06-04
> Rama de trabajo: `claude/affectionate-dijkstra-KSoqy`

---

## 1. El problema (qué pasó)

Probamos la proactividad de Louis preguntando *"¿cómo van los perfiles de puesto de
Joshui?"*. Louis respondió **"pendiente de redactar"** — pero los perfiles **ya
estaban hechos en Claude Cowork**; solo faltaba el visto bueno de dirección.

La causa raíz no es un bug de Louis: **el trabajo vive en Cowork y Louis no puede
verlo.** Hoy son dos cerebros separados:

- **Louis** (Hetzner): memoria/AGENDA, recordatorios, acervo legal (SJF + DOF),
  Telegram, M365, Dropbox. Sabe *qué hay que hacer* y *qué se publicó en el gobierno*.
- **Cowork** (app de Claude): donde tú y yo producimos los entregables reales
  (perfiles de puesto, oficios, análisis, documentos). Sabe *qué ya se hizo*.

Mientras no compartan entendimiento, Louis seguirá reportando como "pendiente"
cosas que ya están listas, y no podrá ser un verdadero dispatcher.

## 2. Objetivo

Un **cerebro compartido** para que Cowork y Louis tengan el mismo entendimiento:

1. **Louis lee lo que Cowork produjo** → deja de reportar como pendiente lo que ya
   está hecho; reporta estado real ("listo, falta VoBo de dirección").
2. **Louis puede preparar/despachar trabajo a Cowork** (briefs de dispatch) cuando
   haga falta producir un entregable.
3. Una sola fuente de verdad: memoria de Louis + acervo legal + entregables de
   Cowork, todo consultable desde ambos lados.

Principio que se mantiene de todo el proyecto: **datos duros, sin interpretación.**
El cerebro expone *hechos* (qué archivo existe, qué estado tiene, qué se publicó);
el análisis es una capa aparte y opcional.

## 3. Arquitectura propuesta

Un **servidor MCP "Cerebro Kawiil"** corriendo en Hetzner, conectado como
*connector* en la app de Claude. Así los agentes de Cowork leen/escriben el mismo
cerebro que Louis.

```
┌─────────────────┐         MCP (connector)        ┌──────────────────────┐
│   Claude App    │  ◄──────────────────────────►  │  Cerebro Kawiil (MCP) │
│  Chat / Cowork  │                                │   en Hetzner          │
└─────────────────┘                                │                       │
                                                    │  expone, read/write:  │
┌─────────────────┐    lee/escribe (ya existe)     │  • Memoria + AGENDA   │
│      Louis      │  ◄──────────────────────────►  │  • Acervo SJF + DOF   │
│  (telegram-bot) │                                │  • Entregables Cowork │
└─────────────────┘                                │  • Kawiil Central     │
                                                    └──────────────────────┘
```

### Recursos que expone el MCP (read/write, con scope)
- **Memoria / AGENDA** de Louis (`/opt/openclaw/spaces/general/AGENDA.md` y memoria):
  leer pendientes, marcar hecho, editar en su lugar (ya tenemos
  `reemplazar_pendiente`).
- **Acervo legal** SJF + DOF (las dos BDs SQLite ya pobladas): consulta de tesis,
  jurisprudencias, notas del DOF — dato duro.
- **Entregables de Cowork**: índice de lo producido (qué documento, para qué cliente,
  estado: borrador / listo / en VoBo / aprobado).
- **Kawiil Central**: estado de seguimiento del equipo (cuando se resuelva el error
  transaccional de Supabase).

### Herramientas (tools MCP)
- `agenda_leer`, `agenda_marcar_hecho`, `agenda_editar`
- `legal_buscar` (SJF/DOF), `legal_estado`
- `entregables_listar`, `entregable_estado` (lo que Cowork ya hizo)
- `dispatch_preparar_brief` (Louis arma el encargo para Cowork)

## 4. La restricción honesta

**No existe una API pública para inyectar tareas de Dispatch en Cowork
directamente.** No puedo, desde Louis, "crear una tarea de Cowork" por API como si
apretara el botón. Por eso el puente real es:

- **Cowork → Louis (lo importante hoy):** lo que produce Cowork se publica en un
  almacén compartido (Dropbox / carpeta del repo) **+** el MCP lo indexa, y Louis lo
  lee. Esto resuelve el 80% del dolor: Louis deja de mentir sobre el estado.
- **Louis → Cowork (dispatch):** Louis **prepara el brief** (qué hay que producir,
  para quién, con qué insumos del acervo legal/memoria) y lo deja listo para que tú
  o yo lo tomemos en Cowork. Es "dispatch asistido", no automático — hasta que haya
  API, esa es la frontera real.

## 5. Fases / hitos

| Fase | Entregable | Resultado |
|------|-----------|-----------|
| **0. Base (ya hecho)** | Pipeline legal con dato duro (SJF+DOF, daily/weekly/backfill), boletines HTML, reporte de estado, fixes de memoria (`reemplazar_pendiente`, no-falsas-confirmaciones, prompt de proactividad), botones en Telegram | ✅ Funcionando y desplegado |
| **1. Almacén compartido** | Carpeta Dropbox/repo "Entregables Kawiil" con convención de estado (borrador/listo/VoBo/aprobado) + Louis lee ese índice | Louis ve lo que Cowork produjo |
| **2. MCP Cerebro Kawiil v1 (read-only)** | Servidor MCP en Hetzner exponiendo memoria + acervo legal + índice de entregables; conectado como connector en la app | Cowork y Louis consultan el mismo cerebro |
| **3. MCP write + dispatch briefs** | Tools de escritura (marcar hecho, editar agenda) + `dispatch_preparar_brief` | Louis como dispatcher asistido |
| **4. Kawiil Central** | Resolver error transaccional (Supabase) e integrar al MCP | El equipo recibe notificación/seguimiento |

## 6. Seguridad

- El MCP corre en Hetzner detrás de **auth** (token/clave por connector); nada
  abierto a internet sin credencial.
- **Scope acotado**: solo expone los recursos Kawiil listados, read/write
  controlado por tool (no acceso de shell arbitrario).
- Secretos en el server (no en el repo); los PATs/credenciales nunca se pegan en
  chat. (Recordatorio: los PATs expuestos en sesiones previas deben **revocarse**.)
- Datos duros: el MCP devuelve hechos verificables; cualquier capa de análisis
  (OpenClaw/Ollama) va separada y marcada como tal.

## 7. Sobre qué se construye

Este proyecto **no parte de cero**: se monta sobre lo que ya quedó funcionando esta
sesión — el pipeline legal de dato duro y los fixes de memoria/proactividad. El
MCP es la pieza que conecta los dos cerebros que hoy trabajan a ciegas uno del otro.

---

### Próximo paso inmediato
Decidir si arrancamos la **Fase 1 (almacén compartido)** ahora — es la de mayor
impacto y menor esfuerzo — o si lo retomamos en una sesión dedicada al **MCP
(Fase 2)**.
