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
