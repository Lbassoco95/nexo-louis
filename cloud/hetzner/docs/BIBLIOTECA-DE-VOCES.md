# Biblioteca de voces — diseño para aprobar

Estado: **solo diseño, nada de esto está construido.** Toca datos sensibles (la voz identifica a una persona y son terceros), así que se construye únicamente con la aprobación de Polo en los puntos de la sección 8.

## 1. Qué resuelve
Hoy las voces se distinguen en vivo solo por tono y brillo y se equivocan (interrupciones, entonación, una persona partida en varias voces). La revisión con el audio (OpenAI) corrige quién habló en cada turno **dentro de una junta**, pero cada junta empieza de cero: la «Voz 2» de hoy no se sabe que es el Gonzalo de ayer. La biblioteca recuerda **cómo suena cada persona** para reconocerla desde el primer minuto, en juntas distintas y en reuniones largas.

## 2. Principios
- **Opt-in por persona.** Nadie entra a la biblioteca sin que Polo lo apruebe una por una («Guardar la voz de Gonzalo para reconocerlo»). Por defecto, **solo equipo interno o personas con consentimiento explícito**; reguladores y externos (p. ej. CNBV) no entran por defecto.
- **Se puede borrar todo:** por persona, por muestra o toda la biblioteca, desde Personas y Ajustes.
- **Se queda en tu servidor.** Las muestras solo salen hacia OpenAI cuando se usan en una revisión (como referencia de voz), igual que el audio de la junta.
- **Mínimo necesario:** muestras cortas (4–8 s) y un vector numérico; no se guarda el audio completo de nadie en la biblioteca.
- **Nada cambia solo:** la biblioteca propone nombres; Polo aprueba, como hoy con la revisión de voces.

## 3. Qué se guarda por persona
| Dato | Para qué | Tamaño |
|---|---|---|
| 1–3 **muestras de audio** (4–8 s, un solo hablante, de juntas distintas) | referencias para OpenAI (`known_speaker_references`, hasta 4 personas por llamada) | ~100 KB c/u |
| **Huella acústica** (vector de timbre) + tono medio (f0) | reconocer a la persona localmente y decidir si una voz es nueva | ~2 KB |
| Metadatos: junta de origen, fecha, calidad, quién la aprobó | auditoría y borrado | — |

## 4. Cómo se alimenta
1. Tras aplicar la revisión de voces (o al nombrar una voz en una junta con audio), Donna propone: «¿Guardo la voz de Gonzalo para reconocerlo en futuras juntas?» por persona, con casilla.
2. Elige sola el **mejor clip**: un solo hablante (sin interrupciones según la diarización), sin ruido, volumen alto, 5–8 s. Hasta 3 por persona, de juntas y micrófonos distintos (da variedad).
3. Polo puede escucharlo, reemplazarlo o borrarlo.

## 5. Cómo se usa (tres niveles)
- **Nivel 1 — referencias a OpenAI (rápido de construir).** Antes de revisar una junta se eligen hasta **4 personas probables** (asistentes del evento de Outlook de esa hora, co-participantes recientes, quienes más han hablado) y sus muestras viajan como referencias: las etiquetas regresan **ya con nombre**. También mantiene iguales las etiquetas entre las piezas de 20 min de una junta larga.
- **Nivel 2 — huella local.** Para quien no cupo en las 4 y para voces nuevas: se calcula la huella de cada voz detectada y se compara con la biblioteca (similitud coseno) con tres zonas: **alta** = se asigna el nombre; **media** = se pregunta; **baja** = «voz nueva». Si dos voces de una junta tienen timbres distintos, son personas distintas aunque el tono se parezca.
- **Nivel 3 — cierre del día.** Una vez al día se juntan las voces sin nombre de todas las juntas y se agrupan por huella: «hoy escuché 3 voces nuevas; 2 parecen la misma persona». Sugiere nombres con lo que ya detecta Donna («Gonzalo, ¿qué opinas?» → se dirigen a esa voz). Sale en el resumen de «Tu día».

## 6. Timbre y tono (lo pedido)
- **Tono (f0)** y brillo, lo que se usa hoy, son rasgos pobres: cambian con la entonación y el volumen.
- **Timbre** se captura con una **huella de hablante** (embedding). Opciones, de menos a más pesada: **WeSpeaker / CAM++ en ONNX** (`onnxruntime`, ~30–100 MB, corre bien en CPU), **ECAPA-TDNN** (SpeechBrain, requiere PyTorch ~2 GB), **TitaNet** (NeMo). El servidor es solo CPU. **Recomendación:** probar primero la opción ONNX y compararla con ECAPA sobre las juntas ya etiquetadas; el tono se conserva solo como rasgo complementario para desempates.
- *Nota de honestidad:* no he podido verificar desde aquí la instalación ni el rendimiento de estos modelos en tu servidor; es un **experimento de medio día** antes de comprometerse.

## 7. Cómo sabremos que funciona (antes de activar nada)
Con las juntas ya etiquetadas por Polo (p. ej. la de 605 intervenciones con Gonzalo y Carlos):
- ≥ **90 %** de turnos con el nombre correcto al reconocer personas guardadas.
- ≤ **5 %** de falsos positivos (asignar un nombre conocido a una voz nueva).
- Medido por junta y por duración; los resultados se muestran antes de activar la asignación automática. La asignación automática solo se considera para la zona «alta» y siempre con deshacer.

## 8. Decisiones que necesito de Polo
1. **¿Quién puede entrar a la biblioteca?** Propuesta: solo equipo interno y personas que hayan dado su consentimiento; nadie de la CNBV ni externos por defecto.
2. **Retención:** propuesta, hasta que Polo la borre (por persona o completa).
3. **Instalar el motor de huellas** (`onnxruntime` + un modelo de unos 30–100 MB) en el servidor. ¿Se aprueba el experimento?
4. **Las muestras viajan a OpenAI** cuando se usan como referencia. ¿Se acepta, igual que el audio de la junta?
5. **Asignación automática:** ¿solo propuesta (recomendado al inicio) o también aplicar sola la zona «alta»?

## 9. Fases
- **F1 (una sesión):** tablas, captura de muestras desde el audio guardado con consentimiento por persona, UI en Personas/Ajustes, referencias a OpenAI (hasta 4) en la revisión. Sin modelos locales.
- **F2 (experimento + integración):** huella local; medir con las juntas etiquetadas; si cumple, integrar el nivel 2.
- **F3:** consolidación del cierre del día y su resumen en «Tu día».
