# Diseño: Multi-bot de agentes en Telegram (Louis + agente-bots)

> Estado: **PROPUESTA — pendiente de aprobación**. No hay código aún.
> Decisiones tomadas por Polo:
> 1. Arrancar con **diseño detallado primero** (este documento).
> 2. Regla de oro: **los agente-bots SOLO responden cuando se les @-menciona** (sin charla autónoma → sin loops por diseño).

---

## 1. Objetivo

Reproducir el modelo "Autonomous Agents — bots talking to bots" de Telegram, pero
controlado: que en un **grupo** de Telegram convivan **Louis** y uno o varios
**agente-bots** especializados (Legal, Finanzas, etc.), de forma que Polo **vea**
la conversación entre ellos en lugar de que la orquestación ocurra invisible dentro
de Louis.

## 2. Base técnica confirmada (Telegram Bot-to-Bot Mode, 7-may-2026)

- Se activa **por bot en @BotFather** ("Bot to Bot Communication Mode", en su MiniApp).
- Con el modo activo, el `getUpdates` del bot empieza a recibir `Update` cuyo
  `message.from.is_bot = true` → es decir, **el bot ve los mensajes de otros bots**
  en el grupo.
- En un grupo, un bot le habla a otro **@-mencionándolo** o **respondiéndole**; si
  al menos uno tiene el modo activo, el receptor recibe el mensaje y puede responder.
- DMs directos bot↔bot requieren **opt-in mutuo** (no lo usaremos en el piloto; todo
  ocurre en el grupo).

Fuentes: telegram.org/blog/ai-bot-revolution-11-new-features, core.telegram.org/bots/api-changelog.

## 3. Estado actual del código (para no romperlo)

- `telegram-bridge.py` usa **un solo token** (`telegram.env`: `TELEGRAM_BOT_TOKEN`) y
  **filtra a un solo `chat_id`** (tu DM). Cualquier chat distinto se ignora
  (`process_update`, línea ~6: `if str(chat.id) != str(chat_id): return`).
- `allowed_updates = ["message"]`.
- Historial de conversación en un único `HISTORY_FILE` (single-chat).
- Los agentes hoy son **prompts** en `spaces/general/agents/{nombre}.md`, invocados
  internamente por `invocar_agente` / `_invocar_agente` (corren dentro del proceso de
  Louis y devuelven texto). Agentes kawiil existentes: calli, citlali, ehecatl,
  metzli, nelli, patli, tepantli, my, central, …

**Implicación:** el multi-bot NO modifica el flujo actual de tu DM con Louis. Se
añade (a) soporte para que Louis también opere en un **grupo** nuevo, y (b) un
**servicio nuevo** que corre los agente-bots. El DM 1-a-1 sigue igual.

## 4. Topología propuesta

```
┌────────────────────── Grupo Telegram "Kawiil — Agentes" ───────────────────────┐
│  👤 Polo            🤖 Louis (@LouisKawiilBot)                                   │
│                     ⚖️  @KawiilLegalBot     💰 @KawiilFinanzasBot   … (futuros)  │
└─────────────────────────────────────────────────────────────────────────────────┘
        │                        │                          │
        │ getUpdates             │ getUpdates               │ getUpdates
        ▼                        ▼                          ▼
   telegram-bridge          agentbots-service (NUEVO, 1 proceso, N tokens)
   (Louis, ya existe;       · poll por token
    + se le habilita        · cada bot responde SOLO si lo @-mencionan
    leer el grupo y el      · reutiliza el prompt del agente kawiil-* correspondiente
    modo bot-to-bot)        · 1 mención = 1 respuesta (sin auto-continuar)
```

- **Un solo proceso nuevo** (`agentbots-service`) multiplexa todos los tokens de
  agente-bots (un hilo / loop de polling por token). Más simple de operar que N
  servicios systemd.
- Cada agente-bot **reutiliza** el prompt de su agente kawiil-* (no se duplica
  conocimiento; el bot es solo "la cara en Telegram" del agente que ya existe).

## 5. Modelo de interacción (mención-driven, sin loops)

Regla única y central: **un agente-bot responde EXCLUSIVAMENTE cuando su @username
aparece en un mensaje del grupo.** Nunca responde "porque sí". Esto hace imposible el
loop infinito sin necesidad de contadores complejos.

Flujos posibles:

**A) Polo pregunta directo a un agente** (lo más simple, piloto):
```
Polo:           @KawiilLegalBot ¿qué riesgo legal tiene la cláusula X?
KawiilLegalBot: ⚖️ [análisis legal]        ← responde 1 vez, se detiene
```

**B) Polo pide a Louis que coordine** (orquestación visible, fase 2):
```
Polo:   Louis, arma la opinión de la cláusula X con apoyo legal y fiscal
Louis:  Va. @KawiilLegalBot ¿riesgo legal de la cláusula X?
                                @KawiilFinanzasBot ¿impacto fiscal?
KawiilLegalBot:  ⚖️ [respuesta]            ← cada uno responde 1 vez
KawiilFinanzasBot: 💰 [respuesta]
Louis:  (lee ambas vía bot-to-bot mode) → 📋 Síntesis final para Polo
```
En el flujo B, **Louis** sí necesita leer las respuestas de los bots (requiere
bot-to-bot mode activo en Louis) y esperar a que lleguen para sintetizar. Eso añade
coordinación asíncrona (ver §7, fase 2). En el **piloto** empezamos con el flujo A,
que no requiere que Louis consuma nada programáticamente.

## 6. Componentes a construir

1. **`agentbots-service`** (nuevo, Python, estilo `telegram-bridge.py`):
   - Config: `credentials/agentbots.env` con un token por agente-bot y el `chat_id`
     del grupo permitido. Ej:
     ```
     GROUP_CHAT_ID=-100xxxxxxxxxx
     AGENTBOT_LEGAL_TOKEN=123:abc      AGENTBOT_LEGAL_AGENTE=kawiil-tepantli
     AGENTBOT_FINANZAS_TOKEN=456:def   AGENTBOT_FINANZAS_AGENTE=kawiil-patli
     ```
   - Por cada token: long-poll `getUpdates` con `allowed_updates=["message"]`.
   - Al recibir un mensaje en `GROUP_CHAT_ID` que contenga `@<su_username>`:
     extrae la tarea (texto sin la mención), llama a la lógica de agente ya existente
     (reutiliza `core._invocar_agente(agente, tarea)`), y publica la respuesta en el
     grupo con un prefijo/emoji del agente.
   - Si el mensaje **no** lo menciona → lo ignora (incluido si viene de otro bot).
   - Offsets independientes por token (archivo por bot).
2. **Cambios mínimos en Louis (`telegram-bridge.py`)**:
   - Permitir operar en **dos** chats: tu DM (como hoy) **y** el grupo nuevo
     (`GROUP_CHAT_ID`). Hoy `process_update` corta si el chat no es el único
     configurado; se cambia por una **lista blanca** de chats permitidos.
   - Historial **por chat** (key = chat_id) para no mezclar el DM con el grupo.
   - (Fase 2) Habilitar lectura de mensajes de bots y la capacidad de @-mencionar
     agente-bots + esperar/correlacionar sus respuestas para sintetizar.
3. **Reutilización**: cero duplicación de conocimiento; los agente-bots invocan los
   prompts kawiil-* existentes. Si mañana creas un agente nuevo con `crear_agente`,
   exponerlo como bot es solo añadir una línea de token en `agentbots.env`.

## 7. Fases

- **Fase 1 — Piloto (flujo A):** 1 agente-bot (ej. Legal = `kawiil-tepantli`) + Louis
  en el grupo. Polo @-menciona al bot y recibe respuesta. Louis solo está presente
  (lee el grupo). Objetivo: validar tokens, permisos del grupo, bot-to-bot mode,
  formato de respuestas y costo por mensaje. **Sin coordinación asíncrona.**
- **Fase 2 — Orquestación visible (flujo B):** Louis @-menciona agente-bots, lee sus
  respuestas (bot-to-bot mode) y sintetiza. Requiere: correlación de respuestas
  (¿qué bot contestó a qué?), timeout de espera, y manejo de "un bot no respondió".
- **Fase 3 — Escalar:** añadir el resto de agente-bots (Finanzas, RRHH, …), un token
  por agente en `agentbots.env`.

## 8. Control de loops y costo

- **Loops:** resueltos por diseño (mención-only). Un agente-bot jamás responde a otro
  bot salvo que ese mensaje lo @-mencione explícitamente, y los agente-bots **no
  @-mencionan a nadie** en sus respuestas (regla de redacción) → cadena imposible.
- **Costo:** cada mención = exactamente **una** llamada a Claude (1 respuesta, sin
  auto-continuar). Añadimos:
  - **Dedup**: ignorar menciones repetidas del mismo `update_id` / mensaje.
  - **Rate-limit por bot**: máx. N respuestas por minuto (anti-abuso).
  - **Tope de longitud** de tarea y de salida.
  - Métrica en log: nº de invocaciones por bot/día.

## 9. Seguridad

- Los agente-bots **solo** atienden el `GROUP_CHAT_ID` autorizado; cualquier otro
  chat se ignora (igual que Louis hoy).
- Tokens en `credentials/agentbots.env` (perm 600, fuera de git).
- El grupo es privado (solo Polo + bots). Si Telegram entrega mensajes de otros
  miembros, se puede restringir además por `from.id` (lista blanca de usuarios).
- Bot-to-bot mode se habilita **solo** en los bots que lo necesitan.

## 10. Lo que debe hacer Polo en @BotFather (yo no tengo acceso)

Por **cada** agente-bot (en el piloto, solo 1):
1. `/newbot` → nombre y @username (ej. `KawiilLegalBot`). Guardar el **token**.
2. En @BotFather → ese bot → **Bot Settings** → activar **"Bot to Bot Communication
   Mode"** (MiniApp).
3. `/setprivacy` → **Disable** (para que el bot lea mensajes del grupo sin tener que
   ser respondido por reply; lo necesitamos para detectar la @-mención de forma
   fiable). *(A confirmar en pruebas: con bot-to-bot mode quizá no haga falta.)*
4. Añadir el bot al **grupo** y hacerlo (idealmente) admin para que vea todo.
5. Activar **Bot to Bot Communication Mode** también en **Louis** (para fase 2).
6. Pasarme: el/los **token(s)** y el **chat_id del grupo** (lo obtengo o te digo cómo).

## 11. Riesgos / preguntas abiertas

- **Privacy mode vs bot-to-bot mode:** confirmar en pruebas si con bot-to-bot mode el
  bot recibe la @-mención sin desactivar privacy. (Se valida en el piloto.)
- **Correlación en fase 2:** cómo sabe Louis que una respuesta del grupo corresponde
  a su pregunta (propuesta: usar `reply_to_message` o un marcador/ID en la mención).
- **Costo agregado:** si en el futuro hubiera muchas menciones, vigilar el gasto
  (métrica en log + alerta).
- **Multi-usuario:** hoy todo es Polo. Si entran más personas al grupo, definir quién
  puede invocar agentes.

## 12. Entregable del piloto (cuando se apruebe)

- `agentbots-service.py` + unit systemd + `agentbots.env.example`.
- Parche mínimo a `telegram-bridge.py`: lista blanca de chats + historial por chat.
- Guía de pruebas paso a paso en el grupo.
- Sin tocar el flujo actual del DM con Louis.
