# Donna proactiva — plan de trabajo

Objetivo (Polo): que Donna **no sea reactiva**. Que dé seguimiento sola, mantenga la información al día (Kawiil Central y Patio Yoltik), aprenda de lo que se cierra y avanza, y que no se le pase ninguna fecha.

## Qué ya funciona (v27) — `donna_radar.py`
| Pieza | Cuándo | Qué hace |
|---|---|---|
| **Radar de fechas** | cuando el agente decide | Junta lo que vence en Agenda, Kawiil Central y Patio Yoltik y avisa por etapa (7 d · 3 d · mañana · hoy · vencida), **una vez por etapa**. Lo vencido se recuerda una vez por semana. Guarda el radar como resumen y el aviso es una línea. Silencioso si nada cambió. |
| **Cierres con evidencia** | cuando el agente decide | Cruza lo abierto contra lo que ya pasó (Slack, juntas escuchadas, tareas hechas en Kawiil Central / Patio, agenda cerrada). Un juez (Haiku) decide si la evidencia dice **explícitamente** que se resolvió; si sí, **propone** cerrar citando la fuente. |
| **Autonomía que Polo concede** | siempre | Por defecto solo propone. Con «✓ Y cierra solas las de este tipo» las de **bajo riesgo** de esa clase (agenda / Kawiil Central / Patio) se cierran solas, avisando con la evidencia y con botón de deshacer. **Regulatorio, legal, dinero y clientes nunca se cierran solos.** Polo puede volver a «Solo propone» cuando quiera. |
| **Aprendizaje** | cada decisión | Cada sí/no/deshacer se guarda etiquetado. Más rechazos de una clase → Donna exige más confianza en esa clase (0.70 base, hasta 0.95). Lo rechazado no se vuelve a proponer con la misma evidencia. |
| **Bitácora visible** | siempre | Seguimiento → «🛰 Lo que Donna vigiló»: cada corrida deja una línea (qué revisó, contra qué, qué encontró) y se puede lanzar «Buscar cierres ahora». Donna también lo cuenta con la herramienta `radar_estado`. |

Seguridad de escritura: en Kawiil Central solo cierra si reconoce el estado «hecho» de esa base (si no, no toca nada y lo dice); en Patio usa `completada`. Se guarda el estado previo para deshacer. Toda escritura queda en el registro de auditoría de esos módulos.

## Fuentes
Conectadas: Agenda · Slack (resumen diario) · Juntas escuchadas (Escuchar) · Kawiil Central · Patio Yoltik · **Correo y calendario de Outlook** (Kawiil y Yoltik, solo lectura, `donna_m365.py`).
- Correo: lo que Polo **envió** y las respuestas **recibidas** (asunto, destinatarios y ~300 caracteres) sirven de evidencia para cerrar pendientes («ya se mandó la carta»).
- Calendario: reuniones **ya realizadas** (con quién) son evidencia; las reuniones de **hoy y mañana** y las invitaciones **sin responder** entran al radar de fechas. Una reunión no se «cierra».
- Privacidad: solo los fragmentos que Donna cita al proponer un cierre se guardan; el texto de correos y eventos es dato, nunca instrucciones.
Siguientes, en este orden: **WhatsApp** (requiere decidir el canal: WhatsApp Business API / número dedicado — el acceso a un WhatsApp personal no es estable ni permitido), y reglas de **actividades** de Kawiil Central (sede, invitados sin responder, +1 por aprobar, proveedor sin confirmar; las reglas y 14 pruebas están en el PR #586 de `kawiil-central` y hay que traerlas aquí).

## Fases
0. ✅ Radar de fechas + cierres con evidencia + autonomía + bitácora (esta entrega).
1. ✅ Correo y calendario como fuente de evidencia y de fechas (entregado). Sigue: reunión → tareas propuestas (de lo escuchado: «quién · qué · cuándo») que Donna **crea** en Kawiil Central/Patio con tu aprobación.
2. Mantener Kawiil/Patio al día en sentido contrario: lo que se resolvió y no se marcó (ya), y lo nuevo que aparece en mensajes y juntas y no está registrado.
3. Revisión semanal de avance: qué se cerró, qué se estancó, qué cambió de fecha; tablero de estadísticas (cuántas propuestas acertó).
4. NVIDIA (abajo).

## NVIDIA — créditos y acceso para deep learning (beneficio de la empresa)
Hoy el juez usa un modelo alojado (Haiku) y todo funciona sin GPU. Los créditos NVIDIA se aprovechan donde sí aportan, en pilotos medibles:
1. **Juez de evidencia propio**: afinar un clasificador con el conjunto de datos que Donna ya va etiquetando (`donna_radar.exportar_dataset()` → JSONL: pendiente, evidencia, fuente, etiqueta sí/no). Meta: igualar o superar a Haiku con menor costo y sin mandar datos a terceros.
2. **Embeddings y búsqueda semántica** de transcripciones, mensajes y tareas (en vez de coincidencia de palabras): menos falsos negativos al detectar «ya se resolvió».
3. **Predicción de fechas en riesgo** (qué tareas probablemente se atrasarán según historial) para avisar antes.
4. **Voz**: separación de hablantes más robusta que la actual (tono) para Escuchar/Personas.
Por confirmar con Polo: qué incluye exactamente el beneficio (créditos de API del catálogo de NVIDIA —endpoints compatibles con OpenAI—, horas de GPU en la nube, o servidor propio), y quién administra la cuenta. La llave se pide con la ventana segura del entorno, nunca por el chat.
Criterio de éxito del piloto 1: con ≥200 decisiones etiquetadas, precisión ≥ la de Haiku en el mismo conjunto de prueba, y cero cierres de alto riesgo sin aprobación.

## Decisiones tomadas por Polo
- Empezar por el radar de actividades/fechas (Fase 0).
- Donna puede **proponer o cerrar**, siempre con la evidencia de por qué; el nivel de autonomía lo concede Polo conforme avanzan.
- Fuentes: Slack, correo, juntas, calendario, Kawiil Central, Patio Yoltik y, a futuro, WhatsApp.
- NVIDIA: es el motivo principal porque hay créditos y acceso para deep learning por beneficio de la empresa.

## De la junta a tareas propuestas (v28) — `donna_tareas.py`
Al guardar una escucha, Donna lee la transcripción en segundo plano y **propone** tareas (quién · qué · cuándo) con la **frase exacta** que las originó. Solo cuentan compromisos explícitos; cada propuesta debe traer una cita que exista de verdad en la transcripción (si no, se descarta), y se omiten las que ya están en la agenda. Nada se crea solo: en **Seguimiento → Tareas propuestas** (o desde el aviso) Polo edita título, responsable y fecha y elige **Agenda**, **Kawiil Central** (con proyecto; la cita viaja en la descripción) o **Seguimiento**. Lo aprobado/rechazado se guarda, ajusta la exigencia y puede exportarse como conjunto de datos (`donna_tareas.exportar_dataset`). Herramienta de Donna: `reunion_tareas`. Pruebas: `tests/test_tareas_junta.py`.

## El agente de vigilancia (v29) — `donna_agente.py` · reemplaza los horarios fijos
Polo pidió que los radares **no sean programados ni un prompt fijo**, sino una actividad de un agente que revisa, aprende cómo trabaja y se adapta. Ahora:
- **Ya no hay horas fijas** (8:00 / 12:00 / 17:00). El reloj del scheduler solo comprueba cada minuto, sin red ni modelo, si **al agente le toca despertar según lo que él mismo decidió** (o si hay una junta nueva).
- Cada ciclo: **observa** (cuándo escribe Polo, qué tan rápido decide, cuánta carga hay en agenda / Kawiil Central / Patio / calendario, qué cambió, lo que ya sabe de él) → **decide** con el modelo qué revisar (radar de fechas, buscar cierres, leer una junta) y **cuándo volver** (10 min a 12 h; p. ej. antes de que empiece su día, o pronto si hay una junta en una hora) → **actúa** solo con revisiones que proponen (los candados no cambian) → **aprende**.
- **Aprendizaje de cómo trabajas**: horario, ritmo, qué decide rápido o rechaza, qué fuentes sirven, qué proyectos/personas dominan la semana. Cada hecho guarda su evidencia y confianza; repetirse lo refuerza, los datos que lo contradicen lo bajan. Solo temas de **trabajo** (nada de salud, finanzas personales ni familia).
- **Tú mandas**: en Seguimiento → «Lo que ha aprendido de cómo trabajas» puedes ✓ confirmar, ✎ corregir o 🗑 olvidar. Lo que corriges ya no lo pisa el agente; lo olvidado no vuelve. Lo aprendido entra también al contexto de Donna en el chat.
- **Visible**: «Última revisión», «ella decidió volver ~…», «Qué decidió y por qué» y las revisiones del día (tope 30/día).
- **Red de seguridad**: si el modelo falla, hace una revisión básica y reintenta en 3 h; nunca está callada más de 12 h; las herramientas caras tienen un mínimo entre usos (3 h).
- Límite honesto: «aprender» aquí es **memoria de observaciones + decisiones que se ajustan**, no entrenar un modelo. El conjunto de datos etiquetado (`exportar_dataset` de cierres y tareas de juntas) es lo que alimentará el piloto de deep learning con los créditos NVIDIA.
