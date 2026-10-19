# Aprendizaje de Louis (Donna)

Louis no se reentrena: usa modelos por API. "Aprender" = **memoria de reglas que se carga en cada
respuesta** (`LEARNINGS.md`) + un bucle que la alimenta rápido con ayuda de modelos de la API.

```
Polo corrige ──► capturar ──► destilar (Haiku, 1×/día) ──► proponer ──► Polo aprueba ──► LEARNINGS.md
 (regex, gratis)   episodes.jsonl   reglas candidatas JSON    «Aprendí esto…»   «aprueba reglas 1, 3»
```

## Lo que ya existía y se reutiliza
- `LEARNINGS.md` (tope 8,000 chars en el prompt) como reglas vinculantes; tool `save_learning` (explícito).
- Destilación nocturna de memoria de hechos (`run_memory_distillation`, 23h) → PEOPLE/CLIENTES/AGENDA.
- Patrón del steward de AGENDA: el modelo propone, **Polo aprueba con un mensaje literal**.

## Fase 1 (implementada) — `services/learning.py`
| Pieza | Qué hace | Costo |
|---|---|---|
| `capture()` | En `call_llm`: si el mensaje de Polo corrige ("no así", "no me gusta", "de dónde sacas…"), da una regla ("la próxima vez…") o refuerza ("así sí"), guarda un episodio (su mensaje + respuesta previa). | $0 |
| `distill()` | 1×/día (21h CDMX, scheduler): Haiku convierte episodios en reglas generales `{topic, rule, evidencia, confidence}`. Ve LEARNINGS actual y lo ya rechazado para no repetir. | centavos/día |
| Filtros en código | confianza ≥ 0.55, ≤ 5 por corrida, sin duplicados (similitud ≥ 0.8), descarta credenciales/órdenes de sistema/"ignora tus instrucciones". | — |
| Revisión | Louis manda «Aprendí N cosas… ¿las dejo?». Polo: «aprueba reglas 1, 3» / «descarta regla 2» / «aprueba todas las reglas» / «reglas pendientes». | — |
| Aplicación | Aprobadas → `LEARNINGS.md` (respaldo previo en `learning/backups/`, bitácora en `history.jsonl`). | — |

### Seguridad (por qué se puede confiar en el bucle)
- Solo aprende de mensajes de Polo; nunca de páginas web ni de resultados de tools.
- **El modelo no tiene tool para aprobar**: una regla entra solo con el mensaje literal de Polo.
- El texto de los episodios se pasa al modelo como dato; hay filtro de reglas peligrosas en código.
- Estado en `/opt/openclaw/learning/` (modo 0700).

### Métricas
`stats()`: aprobadas vs descartadas (precisión de lo que aprende), pendientes, episodios por procesar.
Si se descarta más de la mitad, hay que afinar `_DISTILL_SYSTEM` o `MIN_CONFIDENCE`.

## Fase 2 (siguiente)
1. **Compactar LEARNINGS.md** con Sonnet cuando pase de ~7,000 chars (fusionar, resolver contradicciones,
   proponer el archivo nuevo para aprobación). Hoy solo avisa.
2. **Señal implícita**: marcar respuestas que Polo repregunta de inmediato o reformula (sin que lo diga).
3. **Aprendizaje por agente**: reglas con `scope` (agentes/herramientas) que se inyectan solo al agente
   correspondiente (hoy todo va a un único archivo).
4. Batch API de Anthropic para destilaciones grandes (50% más barato).

## Fase 3
- **Set de evaluación** (casos reales de Polo: D237, tareas, correos) + juez LLM que revisa si las
  respuestas cumplen las reglas vigentes; se corre antes de aprobar reglas nuevas para detectar regresiones.
- Revisión semanal de reglas obsoletas (no usadas / contradichas).
- Biblioteca de "skills" por tipo de tarea (análisis de decretos, contratos) generada a partir de casos aprobados.

## Despliegue
Copiar `learning.py`, `louis_core.py` y `scheduler.py` a `/opt/openclaw/scripts/` y reiniciar
`telegram-bridge slack-bridge scheduler openclaw-gateway`.
