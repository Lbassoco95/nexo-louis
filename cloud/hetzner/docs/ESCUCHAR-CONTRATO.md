# Escuchar y transcribir — contrato app ⇄ servidor

Qué existía y qué se agregó, para alinear con el equipo del motor de transcripción.

## Lo que ya existía (servicios de voz y traducción)

- `WS /v1/app/live` (`openclaw_gateway._live_ws`): puente hacia `gpt-realtime-translate` (OpenAI). La app manda audio PCM16 24 kHz; el servidor devuelve
  `in` (transcripción del idioma original, en deltas), `out` (texto traducido, en deltas), `ev` (eventos `*.done`) y `error`. La llave nunca sale del servidor.
- `POST /v1/app/translate/log|finish|resumen` — archivar una conversación y resumirla.

## Lo que se agregó (modo `escuchar`)

Se abre el mismo WebSocket con `{t:'auth', token, modo:'escuchar'}`. El destino es SIEMPRE español (no traduce de vuelta) y no se reenvía voz sintetizada.

| Sentido | Mensaje | Significado |
|---|---|---|
| app → servidor | binario PCM16 | audio; en los silencios la app no manda nada (ahorra datos) |
| app → servidor | `{t:'ping'}` | cada 20 s, mantiene viva la sesión en silencio |
| app → servidor | `{t:'pausa'}` / `{t:'continuar'}` | pausar sin cerrar la sesión; en pausa el servidor no pregunta por silencio |
| app → servidor | `{t:'seguir'}` | «Seguir escuchando»: reinicia la cuenta de silencio sin cortar nada |
| app → servidor | `{t:'cfg', aviso:true\|false}` | activar/desactivar el aviso de silencio |
| servidor → app | `{t:'listo', to:'es', modo:'escuchar', max}` | sesión lista (máx. 2 h, la app reconecta sola si OpenAI corta antes) |
| servidor → app | `{t:'in'\|'out', d, ms}` | texto original / texto en español, en vivo |
| servidor → app | `{t:'silencio', seg, repite}` | un minuto sin voces (`LIVE_ESCUCHAR_SILENCIO`, 60 s). **La escucha no se detiene sola**: se vuelve a preguntar cada `LIVE_ESCUCHAR_REPREGUNTA` (300 s) |
| servidor → app | `{t:'voz'}` | alguien volvió a hablar: la pregunta se retira |

El aviso de silencio también sale como **notificación push** «¿Seguimos escuchando?» con dos acciones (`Seguir escuchando`, `Detener y guardar`); el service worker las traduce a
`?link=escuchar:seguir|detener`.

`POST /v1/app/escucha/guardar` — `{duracion, voces:[{n,nombre,tono}], lineas:[{t,voz,nombre,tono,es,orig,idioma,bajo}]}` → crea un **entregable** (`tipo: transcripcion`,
`estado: aprobado`, con voces y horas) y un documento buscable por Donna (entra a su memoria). **Solo texto: el audio nunca se guarda.**

## Lo que el diseño pide y el motor actual NO entrega (decisiones)

| Dato del diseño | Estado | Decisión |
|---|---|---|
| Texto en español, chino original, silencios | ✔ entregados por el motor | se usan tal cual |
| **Quién habla (diarización)** | ✘ `gpt-realtime-translate` no la da | se resuelve en el dispositivo: tono (F0 por autocorrelación) + brillo espectral → «Voz N» con color y número. Es aproximado: separa voces de tono distinto, no dos voces parecidas ni voces encimadas. Recomendación: si se necesita diarización fuerte, usar un motor que la ofrezca (p. ej. diarización de un STT dedicado) y reemplazar `esVoz()`; el contrato de arriba no cambia |
| **Confianza del texto** | ✘ el motor no entrega confianza por frase | heurística: se marca con subrayado punteado la frase cuyo audio fue muy débil (nivel medio del micrófono < 0.018). Si el equipo del motor entrega una confianza real, basta con mandar `bajo:true` en `lineas[]` |
| **Género de la voz** («mujer · voz aguda») | ✘ no es confiable inferirlo del tono | se describe solo el tono: «voz grave / media / aguda». El nombre lo pone el usuario |
| Recordar voces entre sesiones | ✔ en el dispositivo (`localStorage`) | por tono y timbre aproximados; ver pregunta abierta |

## Personas: quién dijo qué (v26)
- **En vivo**: la app detecta nombres en lo que se dice (misma lógica que el servidor): «me llamo / soy Fulano» → sugiere ese nombre para la voz que habló; «Fulano, ¿tú qué opinas?» → lo anota como «le hablaron». Todo aparece como sugerencias en «Voces detectadas → Nombres que escuché»; **nada se asigna sin tocarlo**. Las voces con nombre se reconocen en la siguiente sesión (tono y brillo aproximados, ahora también desde el perfil guardado en el servidor).
- **Al guardar**: pantalla «¿Quién es quién?» (se puede omitir). `POST /v1/app/escucha/guardar` ahora acepta `voces[].f0/hf` y responde `{sesion_id, personas:[{id,nombre,voz}], sugeridos:[{nombre,tipo,veces,voz}]}`.
- **Base** (`donna.db`): `personas`, `sesiones_escucha`, `intervenciones` (con búsqueda de texto FTS5), `menciones`. Las voces genéricas («Voz 2») NO crean persona. Módulo `donna_personas.py`.
- **Después**: vista **Personas** (ficha editable, aportaciones, dónde hablan de la persona, fusionar duplicados, eliminar, asignar voces que quedaron sin nombre, buscar «qué se dijo de…»). Endpoints: `GET /v1/app/personas|persona|aportaciones|escucha/sesiones`, `POST /v1/app/persona` (crear|editar|fusionar|eliminar), `POST /v1/app/escucha/asignar`.
- **Cruce con Kawiil Central** (solo lectura, bajo demanda con «Cruzar con Kawiil Central»): busca un usuario con ese nombre en `profiles`, sus tareas abiertas en `tasks` y el cliente si la ficha tiene empresa. Donna lo usa con `persona_buscar` y `aportaciones_buscar`.
- **Privacidad**: solo texto y, de la voz, dos números aproximados — solo de quien se nombró. Eliminar una persona borra su ficha y perfil de voz; sus frases quedan en la transcripción sin nombre. Sigue vigente el aviso de que se graba a otras personas.

## Donna en la junta (v31) — `donna_junta.py`
Panel «💬 Donna en la junta» dentro de Escuchar: mientras se graba, Polo le pregunta, le pide buscar documentos, que anote o que cree una tarea. **La grabación sigue.**
- **No inventa.** Responde solo con (a) lo dicho en la junta (cada frase con su hora `mm:ss`) y (b) lo que devuelven sus herramientas. Herramientas **solo de lectura**: transcripción en vivo, documentos subidos (buscar y leer), agenda, tareas de Kawiil Central y de Patio, juntas anteriores, fichas de personas y correo de Outlook; más `anotar` y `proponer_tarea` (la tarea queda como propuesta; nada se crea sin aprobación).
- **Verificador en código** (no confiar en el modelo): cada fuente citada debe existir (la hora debe ser de una frase real; el documento/correo/tarea debe haber salido de una herramienta en esa consulta); las cifras y fechas de la respuesta deben aparecer en esas fuentes (se entienden «26 de noviembre» ≈ «26/11»). Una fuente falsa se quita y se avisa; si afirma datos sin ninguna fuente verificable, se sustituye por «No encontré eso en la junta ni en tus documentos, así que no te lo afirmo.»
- Las fuentes salen como botones: 🕒 salta a esa frase de la transcripción; 📄 documento; ✉️ correo.
- **Notas y consultas se guardan con la junta**: `POST /v1/app/escucha/guardar` acepta `live_id`; el entregable incluye «Notas tomadas durante la junta» y «Consultas a Donna durante la junta», y las tareas propuestas durante la junta quedan ligadas a la sesión (y no se duplican con las que Donna extrae después).
- Endpoints: `POST /v1/app/escucha/asistente` (`{live_id, pregunta, lineas}`; 20/min), `POST /v1/app/escucha/nota`, `GET /v1/app/escucha/notas?live_id=`. Pruebas: `tests/test_junta_asistente.py`.
- Límites: hoy se pregunta **escribiendo** (el micrófono está ocupado grabando); la transcripción de la junta viaja al modelo para contestar (igual que el resto del chat).

### Ajuste v32 — diseño sin encimarse, y la transcripción ya no se pierde
- **Diseño.** El asistente ya no se encima con la transcripción. En escritorio (≥1100 px) vive en una **columna lateral** con pestañas **💬 Preguntar / 📝 Notas** y botón **—** para **minimizarlo** (queda una barra con el número de notas; se recuerda tu elección). En pantallas angostas hay un selector **📜 Transcripción | 💬 Donna**: ves una cosa a la vez a pantalla completa y los controles de grabación siempre quedan visibles. Probado en 1280, 900 y 390 px, claro y oscuro, sin solapes.
- **Transcripción.** Si hablan **español**, el modelo de traducción puede entregar solo un fragmento. Ahora el texto del turno es **lo que se oyó** cuando es español y la traducción es más corta (`esEs()`); eso es lo que se muestra, se manda al asistente y se guarda. (El modo español-solo-transcripción con un motor de transcripción dedicado sigue siendo la mejora de fondo: más fiel y más barata.)
- **Modelos y costo** (`donna_modelos.py`): el asistente usa el modelo **barato** (`DONNA_MODELO_JUNTA`, por defecto Haiku 4.5) y **solo escala a uno más fuerte** (`DONNA_MODELO_JUNTA_ESCALADA`, por defecto Sonnet 4.6) cuando el verificador no pudo respaldar la respuesta; `DONNA_JUNTA_ESCALAR=0` lo apaga. Los tokens de cada consulta se guardan y la app muestra el costo estimado de la semana (solo donde se conoce el precio). `tools/probar-modelos.py MODELO ...` mide calidad, velocidad y costo con tus llaves antes de cambiar nada. Las reglas y herramientas fijas van en caché de prompt.
