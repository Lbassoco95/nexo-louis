# Memoria — Louis como Orquestador del ecosistema Kawiil
> Punto de consulta. Última actualización: 7-jun-2026 (sesión intensiva).
> Rama: `claude/affectionate-dijkstra-KSoqy`

---

## 1. La visión (el enfoque correcto)

**Louis NO hace todo él mismo. Louis ORQUESTA.** Es el cerebro que decide, recuerda y
**despacha**; las piezas especializadas ejecutan. El usuario (Polo) habla con Louis por
Telegram (o con Cowork en la app), y Louis coordina:

```
                        ┌──────────────────────────────┐
                        │   CEREBRO KAWIIL (Hetzner)    │  memoria compartida
                        │   MCP · HTTPS · OAuth          │  (la única fuente de verdad)
                        │   • Memoria / AGENDA           │
                        │   • Acervo legal SJF + DOF     │
                        │   • Entregables / briefs       │
                        └───────────────┬────────────────┘
              lee/escribe directo       │      connector MCP (✅ conectado)
        ┌─────────────────────────────┐ │ ┌──────────────────────────────┐
        │  LOUIS (Telegram, Hetzner)  │◄┼►│  COWORK (app de Claude)       │
        │  • orquestador / dispatcher │ │ │  • ejecuta: visión, navegador │
        │  • memoria + legal + agenda │ │ │  • DISEÑO: Gamma, Canva       │
        │  • genera docs (PDF/HTML)   │ │ │  • QA visual contra ejemplos  │
        │  • art-dirige prompts       │ │ └──────────────────────────────┘
        └─────────────────────────────┘
```

**Regla de oro de todo el proyecto:** datos duros, sin inventar. Louis cita fuentes
reales (DOF/SJF/normas); nunca fabrica salidas de comandos, juntas, ni confirmaciones.

---

## 2. Cómo Louis usa cada herramienta

**Cerebro Kawiil (lo usa directo).** Es su memoria + acervo legal + entregables.
Tools: `cerebro_estado`, `cerebro_listar`, `cerebro_proyecto_estado`,
`cerebro_crear_brief`, `cerebro_sync_agenda`, `legal_buscar`, `legal_estado`.
Cowork lo lee por el connector (`https://cerebro.kawiil-central.mx/sse`).

**Gamma (visuales).** Dos modos:
- *Borrador rápido (automático):* `generar_visual_gamma` → API v1.0 (`gamma_gen.py`),
  formato social/presentación, entrega el PNG real como archivo. Más plano.
- *Diseño bueno (el que rompe molde):* el modo **"Gráfico" de Gamma es solo UI** (no API)
  y es el único que acepta **referencia del Kawiilito**. Aquí Louis es **arquitecto del
  prompt**: arma un prompt art-directed A LA MEDIDA de cada post (escena, mensaje, mood
  cambian; marca fija: Kawiilito, paleta navy/azul/verde/naranja, logo) y Cowork lo pega
  en el modo Gráfico con la referencia.

**Canva (diseño con Brand Kit).** Vía connector de Canva en Cowork. Cowork ve imágenes
y usa el Brand Kit. Louis despacha el encargo; Cowork diseña.

**Cowork (capa de ejecución creativa/visual).** Lo que necesita visión, navegador o
diseño. Flujo: Louis art-dirige + despacha (brief en el Cerebro) → Cowork genera (Gamma/
Canva) y hace **QA visual** comparando contra los posts de oro de Kawiil → valida o ajusta.

**Restricción honesta:** no hay API para inyectar tareas a Cowork automáticamente; el
puente es el Cerebro (Louis deja el brief, Cowork lo lee). El modo Gráfico de Gamma y la
referencia del Kawiilito son UI, no API.

---

## 3. Estado actual — qué FUNCIONA (al 7-jun-2026)

- ✅ **Louis vivo** (recuperado del rompimiento por patch script), memoria confiable
  (`reemplazar_pendiente` sin duplicar checkbox; sin falsas confirmaciones).
- ✅ **Cerebro Kawiil MCP** en producción: HTTPS + OAuth + DNS-rebinding resuelto.
  **Connector conectado en Cowork** — Cowork lee el cerebro real del servidor. 🎉
- ✅ **Briefing** determinístico: calendario M365 en vivo (Kawiil + Yoltik), HTML visual
  brandeado, sin inventar juntas.
- ✅ **Pipeline legal** (dato duro): SJF semanal (lun), DOF diario L–V (reporta día hábil
  anterior, descansa findes), avance de descargas (lun+vie) con faltante/temporalidad/
  indexación/deep-learning. SJF 30,606 (98% texto/99% PDF → listo DL); DOF ~210k (5%
  texto/1% PDF → falta extraer histórico).
- ✅ **Gamma funcional** desde el servidor (API v1.0; UA de navegador evita Cloudflare;
  entrega el PNG real). Tool `generar_visual_gamma`. Louis = arquitecto de prompts.
- ✅ **Documentos**: PDF cuando pides PDF; **HTML interactivo** (colapsables + buscador +
  expandir/colapsar) cuando pides HTML. Sin duplicados, un solo doc consolidado, sin
  filtrar prompts de agentes. Saneador de `tool_use/tool_result` (mató el error 400).
- ✅ **Acceso remoto**: Termius (móvil, datos) + repo clonado en el servidor (deploys con
  `git pull` sin la Mac). Mac configurada para no dormir (`sleep 0` batería y AC).
- ✅ Key de Anthropic válida (HTTP 200).
- ✅ Kit de marca Kawiil en repo+servidor + manifiesto para selección por contexto.

---

## 4. Pendientes (para retomar)

- 🔜 **Calidad del análisis legal**: que el documento se arme DESDE la investigación
  multi-agente (agente internacional → agente mexicano `kawiil-tochtli` mexicaniza) y no
  de un solo tiro. Ver en vivo el trabajo del agente mexicano (panel de agentes).
- 🔜 **"Elaborado por [agente]"** en los documentos (acreditar al agente real).
- 🔜 **Revisión a profundidad tipo chat** sobre una investigación: usar Cowork (ya con el
  Cerebro) para profundizar turno a turno con el acervo real (`legal_buscar`).
- 🔜 **Theme "Kawiil" en Gamma** + 3–5 posts de oro como referencia para el QA visual.
- 🔜 **DOF al servidor** (autonomía total, sin depender de la Mac) + extraer texto/PDF
  histórico del DOF (cuello de botella para deep learning).
- 🔜 **Conector Canva en Cowork** + tool de Gamma madura.
- 🔜 Briefing diario interactivo (mismo estilo HTML colapsable).

## 5. Seguridad
- ⚠️ Rotar el **token del Cerebro** (`CEREBRO_KAWIIL_TOKEN`) y la **key de Gamma** —
  ambos pasaron por el chat. Tras rotar el token, **reconectar el connector** una vez.
- Secretos en el servidor (`/opt/openclaw/credentials`, `openclaw.env`), nunca en repo/chat.

## 6. Operación / deploy (desde Termius o Mac)
```
cd ~/nexo-louis && git pull origin claude/affectionate-dijkstra-KSoqy
sudo install -m 0755 -o polo -g polo ~/nexo-louis/cloud/hetzner/services/<archivo> /opt/openclaw/scripts/<archivo>
sudo systemctl restart telegram-bridge   # o cerebro-kawiil
```
Servidor: Hetzner 204.168.131.21 (polo@louis-prod). Repo en `~/nexo-louis`.
