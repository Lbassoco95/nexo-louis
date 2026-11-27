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

### Ajuste v33 — la caja de texto del asistente nunca queda cortada
Caso real: en una ventana baja la caja «Pregúntale…» quedaba cortada. Ahora: el texto de ayuda es el estado vacío del chat (desaparece con el primer mensaje); los atajos van en **una fila deslizable**; el chat es lo que se encoge (la caja de texto siempre queda visible y dentro del panel); «Voces detectadas» se puede plegar también en escritorio y arranca **plegada si la ventana mide menos de 860 px de alto**; el botón dice **«— Minimizar»** (la grabación sigue) y minimizado queda una barra «▢ Abrir». Probado en 2000×1060, 1440×800, 1280×720, 1280×640, 1100×560 y 1280×530. Elección de modelo (corrida real de Polo, 5 casos): **Haiku 4.5** 5/5 · 1.4 s · US$0.0033/consulta; Haiku 5.5 5/5 · 2.8 s · ≈2× los tokens; se queda Haiku 4.5 (escalada a Sonnet 4.6).

## Audio y verificación de voces (decisión de Polo, 23-nov-2026)
- **Qué se guarda ahora:** el texto (como antes) **y el audio** de la junta, en el servidor de Polo (`/opt/openclaw/state/escucha_audio`, archivos 0600). Mientras graba, la app sube el audio por partes de ~10 s; si la pestaña se cae solo se pierden unos segundos.
- **Plazo:** el audio se borra solo a los N días de guardar la junta (Ajustes → «Juntas (Escuchar)», 30 por defecto; el audio que nunca se ligó a una junta, a los 30 días sin tocarse). Polo puede borrarlo antes desde «Revisión de voces». Se puede apagar por completo («Guardar el audio de las juntas»).
- **OpenAI:** al guardar la junta, el audio completo se manda a OpenAI (`gpt-4o-transcribe-diarize`) para etiquetar quién habló. Es una **propuesta**: Polo revisa y aplica; nada cambia solo.
- **Consentimiento:** el aviso previo a escuchar lo dice (texto + audio + OpenAI + rescate del texto).
- **Pendiente de validar:** el formato de la API de OpenAI no se pudo probar contra la API real desde el repositorio; usar `tools/probar-diarizacion.py` con un audio de verdad. Alternativa abierta a comparar con el MISMO audio: pyannote «community-1» autoalojado.

## Cómo actualizar sin cortar una grabación
- Usar `bash ~/donna-deploy/cloud/hetzner/tools/actualizar-donna.sh` (no el comando largo de install + restart). Si detecta una grabación en curso (audio, rescate o autoguardado de los últimos 2 min) **no instala ni reinicia nada**; `--esperar` actualiza solo al terminar; `--forzar` solo por urgencia.
- Si aun así el servidor se reinicia durante una grabación: la app se reconecta sola, el audio sigue guardándose y la copia de rescate/borrador conserva el texto; solo se pierde la transcripción del hueco (se puede reconstruir del audio).

## Detener una escucha desde cualquier dispositivo
- En Escuchar, si hay una escucha activa en otra pantalla (conexión en vivo o autoguardado/rescate de los últimos 2 min) aparece «Hay una escucha activa» con **⏹ Detener y guardar en todos los dispositivos**.
- La orden llega a esa pantalla por la conexión en vivo (`detener_remoto`, al instante) y en la respuesta de su autoguardado (cada ~20 s, aunque la conexión esté caída). La pantalla se detiene y guarda la junta sola (sin pedir nombres). La orden caduca a los 10 min.
- Una junta no se guarda dos veces: si dos pantallas intentan guardar la misma, la segunda recibe «ya se guardó desde otra pantalla». Un borrador cuya escucha sigue activa no ofrece «Guardarla así» (se pisaría con la otra pantalla).

## Abrir una junta guardada (ver, oír, descargar, eliminar)
- En Escuchar → Juntas guardadas, cada junta tiene **👁️ Abrir**: resumen, voces, notas, materiales, tareas propuestas y la transcripción completa (por tramos de 150 líneas), con **⬇️ Descargar minuta (.md)**, **🎧 Escuchar la grabación** (reproductor) y **⬇️ Descargar audio** (un solo MP3 con todos los tramos).
- **🗑️ Eliminar esta junta** borra la junta y lo que colgaba de ella (si tiene audio, pide confirmar que también se borra el audio). No borra el documento de Entregables.
- Una junta guardada marca su borrador como «guardada»: un autoguardado tardío ya no lo resucita y guardar otra vez no la duplica.
- En juntas largas la pantalla solo pinta las últimas 120 intervenciones (botón «Mostrar anteriores») y se repinta a lo más cada 1.5 s: evita que el navegador agote la memoria («Se actualizó esta página web porque estaba utilizando bastante memoria»).

## Revisión de voces: límites medidos con audio real (24-nov-2026)
- `gpt-4o-transcribe-diarize` acepta como máximo **1400 s (≈23 min) de audio por llamada** (error 400 «longer than 1400 seconds»). El audio se parte SIEMPRE en piezas de 20 min (`DONNA_DIARIZA_PIEZA_S`, 1200 por defecto), aunque el archivo pese poco.
- Tope por junta: **4 horas** de audio (`DONNA_DIARIZA_MAX_H`); lo que pase de eso no se revisa y la pantalla lo avisa.
- Medido: 120 s de audio tardaron 44 s (≈0.37× el tiempo real). Una junta de 100 min tarda ~35–40 min en revisarse; la pantalla muestra «pieza X de N».
- Cada tramo (y cada pieza, si la API no acepta las muestras de voz que se usan para mantener las etiquetas) tiene sus propias etiquetas; la alineación empareja una voz de la transcripción una vez por tramo/pieza, no una vez en toda la junta.

## Audio: comprobar que quedó bien, cuánto cubre y recuperar audio suelto
- **Solo hay audio de las juntas grabadas desde la versión con audio (v49).** Las anteriores nunca lo tuvieron y no se puede recuperar. Cada junta con audio muestra «cubre X de Y min» y avisa si faltan partes (p. ej. una junta que empezó antes de activar el audio).
- **Verificación automática al guardar:** a los 8 s se comprueba que cada tramo se pueda leer y que dure lo que debe (partes × 10 s); si algo falla llega una notificación «⚠️ Revisa el audio de la junta». En la junta abierta se ve el detalle por tramo.
- **Audio suelto** (se grabó pero la junta nunca se guardó): aparece en Escuchar → «🎧 Audio sin junta guardada» con Ligar a una junta / Descargar / Borrar. Se borra solo a los 30 días sin tocarse.
- **Revisión de voces:** las que fallaron por el límite de 1400 s de OpenAI se relanzan solas una vez, 90 s después de arrancar el servidor. `tools/verificar-junta.py` muestra la cobertura del audio y el estado de la revisión (lista, procesando «pieza X de N», o el error).

## Micrófono: volumen y cortes
- **Audio guardado:** al descargarlo y al mandarlo a OpenAI se le quita el rumble, se **amplifica hasta +25 dB** y se nivela (`AF_VOZ` en `donna_audio.py`), sin recortar. El audio original guardado no se modifica.
- **En vivo:** ganancia automática (ya existente) para que la transcripción oiga voces bajas; el micrófono se pide sin cancelación de eco ni de ruido para no apagar a quien habla lejos.
- **Si el sistema silencia o corta el micrófono** (otra app lo usa, AirPods, suspensión): la app avisa, lo **reabre sola** sin detener la escucha y abre un tramo de audio nuevo. Si solo hay ceros con la pista «viva» (AirPods en un cuarto callado también los dan) espera 90 s y reintenta cada 10 min como máximo.
- Pendiente de diseño aprobado: `docs/BIBLIOTECA-DE-VOCES.md`.

## Reconstruir el texto desde el audio
- Cuando la transcripción en vivo falló y solo quedó el audio, en **Escuchar → Audio sin junta guardada** aparece **🧾 Reconstruir texto**.
- **Solo se hace cuando Polo lo pide con ese botón y confirma** que el audio sale hacia OpenAI (`gpt-4o-transcribe-diarize`: texto y quién habló, con tiempos). Cuesta saldo de OpenAI y tarda (≈ 1 min por cada 3 de audio; hasta 4 h por audio).
- El resultado es una **junta nueva** (intervenciones con hora y voz, voces sin nombre) ligada a ese mismo audio, y un aviso cuando termina. La junta original no se toca; nada se reemplaza.
- **Una grabación en curso no se toca:** si llegó audio en los últimos 3 minutos, no se puede reconstruir, ligar ni borrar desde otra pantalla (se muestra «se está grabando ahora mismo»).
- El actualizador no reinicia el servidor mientras una reconstrucción esté corriendo.

## Completar el texto que falta de una junta (misma junta)
- En la ficha de una junta con audio aparece **🧾 Falta el texto de X de audio** cuando el audio guardado es más largo que su transcripción (la transcripción en vivo se cayó, o se ligaron a mano audios que nunca tuvieron texto).
- **Solo corre con el botón «Completar el texto desde el audio» y tras confirmar** que ese audio sale hacia OpenAI. Se mide qué partes NO tienen texto, **se omite el silencio** y solo lo demás se manda a OpenAI.
- El texto nuevo se **agrega al final de la misma junta**, con una marca «Texto reconstruido desde el audio…»; **no toca ni reemplaza** lo que ya había. Las voces nuevas quedan sin nombre (Voz 3, 4…).
- Si varios audios se encimaban en el reloj (todos empezaban en 0 al ligarlos a mano), el **más reciente conserva su lugar** (se asume que es el que llevó el texto en vivo) y los demás se acomodan después, uno tras otro, en orden de grabación.
- Lo ya revisado (con texto o silencio) se recuerda: no se vuelve a ofrecer ni a cobrar. Si se interrumpe, lo ya hecho se conserva.
- Una junta con un audio grabándose ahora mismo no se toca. El actualizador no reinicia mientras esto corre.
- **Chino (y japonés):** el texto reconstruido de lo que se habló en chino se **traduce al español** (traducción automática por lotes con Haiku) y queda como texto principal; el **original se conserva** en la intervención (idioma «zh») y sale en la minuta. Si un lote no se puede traducir, esas intervenciones conservan el original y se cuentan como «sin traducir». La traducción es automática: para decisiones importantes conviene verificarla.

## OpenAI sin saldo
- Si OpenAI responde que la cuenta no tiene créditos (error 429 «no credits remaining»), Escuchar muestra un **aviso rojo fijo** («NO se está transcribiendo; el audio sigue guardándose»), **no reconecta en bucle** y manda **una notificación** (como máximo una por hora). La grabación del audio no se interrumpe: cuando recargues el saldo, el botón «Ya recargué: reintentar» reconecta, y el texto de lo que faltó se recupera con «Completar el texto desde el audio».
- Reconstruir, completar y revisar voces dicen lo mismo con las palabras claras («OpenAI no tiene saldo…») en lugar del error crudo.
