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
