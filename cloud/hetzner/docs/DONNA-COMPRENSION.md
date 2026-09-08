# Donna: nombre de marca + arreglo de comprensión

Fecha: 2026-09-07

Dos cambios que salieron del mismo caso real en Telegram.

## El caso

Polo pegó el recordatorio del vencimiento y cerró con una pregunta:

> de este vencimiento ⏰ 🔴 HOY VENCE — Oficio CNBV 411-2/1364/2026 (Sylon Asesores).
> Enviar el correo a dllamas@cnbv.gob.mx … Guardar el correo de acuse con el folio:
> es la única constancia de **presentación** en tiempo.
> **lo agregaste a kawiil central para que llevemos el seguimiento?**

El bot contestó con un PPTX titulado *"Análisis legal — de este vencimiento ⏰ 🔴 HOY
VENCE — Oficio CNBV 41"*. Nunca contestó la pregunta.

### Por qué

`needs_doc_sonnet()` pedía solo que en CUALQUIER parte del mensaje hubiera un tipo de
documento **y** un verbo de pedido:

| Señal | Qué hizo match | Dónde venía realmente |
|---|---|---|
| tipo de documento | `presentación` | "constancia de **presentación** en tiempo" (trámite legal) |
| verbo de pedido | `Enviar` | "**Enviar** el correo a dllamas@cnbv.gob.mx" (instrucción del trámite) |

Las dos palabras estaban a ~400 caracteres de distancia y ninguna era una orden para
Donna: eran parte del contexto pegado. Ya dentro del flujo de documento,
`_es_analisis_legal()` matcheaba con solo ver `CNBV`, así que salió como "análisis
legal", y `_doc_tipo_de_mensaje()` eligió PPTX por la misma palabra `presentación`.

## Arreglo (comprensión)

En `services/louis_core.py`:

1. **Sentidos no documentales** (`_limpiar_sentidos_no_documentales`): se borran del
   mensaje frases como "constancia / acuse / fecha / plazo de presentación" y
   "presentación en tiempo" antes de buscar el tipo de documento. Se aplica también en
   `_doc_tipo_de_mensaje()` para que no elija PPTX.
2. **Preguntas de seguimiento** (`es_pregunta_de_seguimiento`): "¿lo agregaste a kawiil
   central?", "¿ya lo guardaste?", "¿lo registraste?" ya no disparan generación de
   archivo — hay que contestarlas. Requiere signo de interrogación, para no confundir la
   orden ("agrégalo a kawiil central") con la pregunta. Válvula de escape: si Polo nombra
   un formato explícito ("¿me lo pasas **en PDF**?"), sí es pedido de documento.
3. **Cercanía verbo↔tipo** (`_doc_verbo_pegado_al_tipo`, ventana de 45 caracteres): el
   verbo tiene que estar pegado al tipo. "hazme un informe" sí; "Enviar el correo…" en la
   línea 2 y "presentación" en la línea 8, no.
4. **`_es_analisis_legal`**: mencionar CNBV / IMPI / amparo ya no basta. La señal fuerte
   ahora exige además que el mensaje pida un entregable.
5. **Verbos acentuados**: `prepárame`, `ármame`, `conviérteme` — antes no matcheaban
   (`prepara\w*` no cubre `prepára`), así que pedidos legítimos se caían.
6. **System prompt** — sección `# COMPRENSIÓN DEL MENSAJE`: separa el contexto pegado de
   la petición real, contesta la pregunta con el hecho verificado, acusa el vencimiento en
   una línea, y si no distingue qué quiere Polo, pregunta en vez de generar el archivo.
7. **`generar_documento`** (descripción de la tool) y la red de seguridad #2 del bridge
   (`_promete_documento`) también quedan bloqueadas en preguntas de seguimiento.

Si el detector rápido falla y devuelve `False` en un pedido real, no se pierde nada: el
modelo sigue teniendo la tool `generar_documento` en el chat normal. El error caro es el
contrario (mandar un PPTX basura), y ése es el que se cerró.

### Pruebas

```bash
python3 cloud/hetzner/scripts/test-comprension.py
```

32 casos, sin red ni credenciales: el mensaje del CNBV, preguntas de seguimiento,
contexto pegado, y los pedidos de documento que deben seguir funcionando.

## Arreglo (nombre)

El asistente se presenta y firma como **Donna** (antes "Louis"), en línea con el Cerebro
Kawiil que ya usa ese nombre del lado de Cowork:

- identidad del system prompt (femenino: "asistente ejecutiva"), briefing diario,
  módulo de memoria, generador de documentos;
- autoría de los entregables: pie de PDF/DOCX/PPTX/HTML, chat embebido
  ("Pregúntale a Donna"), pie del briefing, `preparado_por` de los briefs del Cerebro;
- dashboard de agentes del gateway y descripciones de tools;
- adjunto HTML de Telegram: `donna_<fecha>.html`.

`ASSISTANT_NAME` en `louis_core.py` es el único lugar que define el nombre para lo que se
imprime en entregables. Si Polo le dice "Louis", el prompt indica que responda normal pero
que se presente como Donna.

**A propósito NO se renombraron** (renombrarlos rompe units, logs, rutas o el sync):
`louis_core.py`, `louis_html.py`, los servicios systemd, `/opt/louis`, las variables
`LOUIS_*`, la carpeta `_Louis-Generados` y el handle real de Slack `Louis-Nexo` / `@Louis`.

**Pendiente manual:** el nombre que se ve arriba del chat de Telegram lo define BotFather,
no el código → `@BotFather` → `/setname` → elegir el bot → "Donna". Igual `/setdescription`
y `/setabouttext` si dicen Louis. En Slack, el nombre de la app se cambia en
api.slack.com → Basic Information → App Name (y ahí sí habría que actualizar el handle en
las descripciones de las tools de Slack).

## Deploy

```bash
# en Hetzner, como root
cd /opt/louis && git pull && ./deploy.sh --skip-bootstrap
systemctl restart telegram-bridge slack-bridge openclaw-gateway
```

---

# Segunda ronda (7-sep, tarde): acciones fabricadas, formatos y horarios

De la conversación de las 6:54–6:57pm salieron tres fallas más. Ninguna era falta de
inteligencia del modelo: eran instrucciones contradictorias y plomería.

## A. Decía "agendado" y el calendario quedaba vacío

Donna escribió *"✅ Correcto. Agendando los 4 eventos de MAÑANA en Microsoft Calendar…"*
y *"Evento agendado en Microsoft Calendar (Kawiil)"*. No se creó nada.

Causas, en orden de culpa:

1. **La descripción de la tool le ordenaba preguntar.** `m365_crear_evento` decía
   literal: *"Crea evento en calendario. Confirma fecha/hora con Polo antes."* Polo ya
   había dado las 4 horas exactas y ella igual preguntó *"¿Confirmo que los meto al
   calendario de Kawiil?"* — obedeciendo la tool. De ahí el bucle infinito de
   confirmación.
2. **Nada verificaba la afirmación.** Ya existía una red anti-fabricación para
   recordatorios (`_REMINDER_CLAIM_RE` → obliga a llamar `agendar_recordatorio`), pero
   no para eventos ni para notas. Afirmar era gratis.
3. **`_es_stall` no veía el gerundio.** Detectaba "voy a crear" pero no "Agendando…",
   y tampoco el "¿Confirmo que…?".

Arreglos:

- `m365_crear_evento` ahora dice lo contrario: si Polo dio día y hora, **ejecuta sin
  preguntar** (el dato ES la confirmación), una llamada por evento, `tenant` = kawiil por
  default, 30 min si no hay fin, y prohibido decir "agendado" sin OK de la tool. Incluye
  la distinción recordatorio ≠ evento.
- `_CLAIM_NETS`: tabla de (afirmación, tools que la vuelven verdad, corrección). Cubre
  eventos (`m365_crear_evento`) y notas/memoria (`append_to_memory`, …). Si el turno
  afirma sin haber llamado ninguna, `_accion_fabricada()` lo empuja a ejecutarla de
  verdad — una vez por familia, para no ciclar el loop.
- `_es_stall` ahora cubre gerundios de acción (agendando, creando, registrando,
  guardando, bloqueando…) y el bucle de confirmación (`¿confirmo`, `¿procedo`,
  `¿los meto`, `¿quieres que lo…`).
- System prompt, sección `# ACCIONES: EJECUTA, NO ANUNCIES`: nunca afirmar sin OK de la
  tool; no pedir permiso dos veces; un ítem = una llamada; reportar lo que devolvió la
  tool (incluidos los fallos parciales); recordatorio ≠ evento; "anótalo" = tool de
  memoria de verdad.

## B. Unos mensajes con un formato y otros con otro

El prompt y el conversor se contradecían:

| El prompt le pedía | Lo que `format_for_telegram` hace |
|---|---|
| "Negrita: `*una sola*`, NO `**dos**`" | `*x*` → **cursiva**; `**x**` → negrita |
| "NO uses headers `#`" | `# x` → negrita |

Cuando obedecía el prompt, los títulos salían en cursiva; cuando escribía su Markdown
natural, salían bien. Mismo bot, dos estilos. Arreglado: el prompt de Telegram (y el de
Slack, que tenía el mismo desfase) ahora pide **Markdown estándar**, que es lo que los
conversores entienden, y advierte de no mezclar `*x*` con `**x**`.

Segunda causa: el troceado a 4000 caracteres cortaba a ciegas y partía los tags
(`<b>` en un chunk, `</b>` en el otro) → Telegram devolvía 400 y **ese** chunk caía a
texto plano, así que un mensaje largo salía mitad con formato y mitad en crudo.
`_chunk_html()` ahora corta en salto de línea.

## C. Mensajes a todas horas

Cuatro causas distintas:

1. **`sjf-weekly.timer` sin zona horaria**: `OnCalendar=Mon 08:00` se interpreta en hora
   del server (UTC) = **2:00am CDMX**, y ese timer manda resumen por Telegram. Ahora
   `America/Mexico_City` explícito. Igual `sjf-update.timer` (13:30 UTC → 13:30 CDMX).
2. **`Persistent=true` en los timers que notifican**: si el server estaba caído a la hora
   del boletín, systemd lo disparaba al arrancar, a cualquier hora. Quitado de
   `dof-daily`, `dof-daily-tarde`, `legal-digest`, `legal-estado`, `sjf-weekly`. Se
   mantiene en los jobs silenciosos (harvest/index).
3. **Sin horas de silencio en el scheduler**: un `fire_at` de madrugada (o mal calculado
   por el modelo) disparaba a esa hora. Ahora ventana **22:00 → 07:00 CDMX**; los avisos
   de esa franja **no se pierden**, se reprograman a las 07:00. `"urgente": true` en la
   entry se salta el silencio. Configurable: `DONNA_QUIET_START` / `DONNA_QUIET_END`.
   → Si el briefing matutino está en cola antes de las 7:00, baja `DONNA_QUIET_END`.
4. **La cola se vaciaba de golpe**: `fire_at <= now` mandaba todo el atraso junto. Ahora
   máximo `DONNA_MAX_POR_TICK` (3) por minuto, y un aviso con más de 2h de atraso llega
   marcado `⏰ (atrasado Nh)` para que Polo no lo lea como de ahora.

Para revisarlo sin entrar al servidor: `hetzner_estado(que="avisos_programados")` lista
la cola (hora, mensaje, recurrencia, diferidos) y la ventana de silencio vigente.

## D. El proceso de aprendizaje

La destilación nocturna (23:00, Haiku → AGENDA/PEOPLE/CLIENTES/IMPORTANT) existía, pero
tenía dos agujeros:

1. **Prohibía aprender de las correcciones.** La regla decía *"NO guardes hechos sobre
   Donna misma, el sistema, el bot"*, así que los tres "no, eso no era" de Polo se
   tiraban a la basura y el mismo error volvía. Ahora hay categoría `CORRECCIONES` →
   `LEARNINGS.md`, que **sí** se inyecta al system prompt, con instrucción de escribirlas
   como regla en imperativo ("si Polo dice 'recordatorio', usar `agendar_recordatorio`,
   no crear evento").
2. **Fallaba en silencio.** El scheduler solo logueaba el caso `OK`; sin API key o con
   JSON inválido, Donna dejaba de aprender semanas sin que nadie lo notara. Ahora loguea
   warning, y `estado_aprendizaje()` aparece en `/status`: última corrida, qué aprendió, y
   ⚠ si lleva más de 2 días sin correr.

Lo que **no** es el cuello de botella: la capacidad del modelo. Ninguna de estas fallas
mejora con más GPU — eran una descripción de tool que ordenaba preguntar, un prompt que
contradecía al conversor y un timer sin zona horaria.

---

# Mantener Haiku (sin subir a Sonnet): palancas de costo

Decisión de Polo: no encarecer los turnos con tools. Se queda **Haiku** y se hace
confiable con configuración. Ventana de silencio: **7:00am** (como quedó).

El fallo de Haiku no es de comprensión, es de **selección**: van 110 tools en cada
request (~15.4k tokens de definiciones; ~20 son `m365_*` casi idénticas). Pedirle que
acierte entre 110 opciones parecidas es lo que lo empuja a narrar en vez de ejecutar.

## Lo que se aplicó (costo cero)

1. **`tool_choice` forzado en el primer turno** cuando el mensaje ORDENA escribir
   (`tiene_intencion_de_escritura`). No puede contestar de memoria: tiene que ejecutar.
   Es la misma llamada al mismo modelo — no cuesta un peso más.
   El forzado con Haiku ya se había intentado en el flujo de documentos y se descartó
   porque el primer turno salía sin texto; aquí no aplica esa objeción, porque el texto
   final lo escribe el turno siguiente ya con los `tool_results` en mano.
2. **Se le quita la elección cuando la orden es de una sola familia**
   (`tool_forzada_por_intencion`): calendario → `m365_crear_evento`, recordatorio →
   `agendar_recordatorio`, nota → `append_to_memory`. De 110 opciones a 1. Precedente en
   el propio código: las queries de Slack ya forzaban `slack_resumen`.
   Dos guardas: sin hora en el mensaje NO se fuerza `m365_crear_evento` (si no, inventa
   la hora), y si el mensaje mezcla familias ("guárdalo como nota **y** recuérdame…") se
   deja `any`, porque forzar una sola sería peor.
3. **Las redes anti-fabricación** cuestan una llamada extra de Haiku *solo cuando se
   porta mal* — no en el caso normal. Es el gasto correcto: se paga por el error, no por
   la póliza.

## Lo que NO se hizo, a propósito

**Filtrar el bloque de tools por intención** (mandar 15 en vez de 110) suena a la
optimización obvia y sería contraproducente: `tools` + `system` son idénticos entre
llamadas y hoy viajan **cacheados** (`cache_read` ≈ 10% del precio). Filtrar por mensaje
crearía una variante de caché por combinación y se pagaría input completo mucho más
seguido — más caro, no más barato. Si algún día se quiere, tiene que ser con un número
FIJO y pequeño de canastas (3–4), cada una con su propia entrada de caché.

## Si aun así vuelve a narrar en vez de ejecutar

En ese orden, de lo más barato a lo más caro:
1. Revisar `LEARNINGS.md`: la corrección de Polo debería estar ahí como regla. Si no
   está, el problema es la destilación nocturna (ver `/status` → Aprendizaje).
2. Agregar el verbo que falló a `_WRITE_INTENT_RE` / `_CAL_VERB_RE` — es una línea y
   cuesta cero.
3. Solo entonces: subir a Sonnet los turnos multi-paso.
