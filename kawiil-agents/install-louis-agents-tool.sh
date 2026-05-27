#!/usr/bin/env bash
# install-louis-agents-tool.sh
# Instala el cliente kawiil_agents.py en ~/.openclaw/scripts/
# Actualiza AGENTS.md de Louis para que conozca los 14 agentes y sepa cuándo usarlos.

set -euo pipefail

SRC="$HOME/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/kawiil-agents/kawiil_agents.py"
DEST="$HOME/.openclaw/scripts/kawiil_agents.py"
HOME_OC="$HOME/.openclaw"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$HOME_OC/scripts"
cp "$SRC" "$DEST"
chmod +x "$DEST"
echo "==> Cliente instalado: $DEST"

# Test rápido
echo ""
echo "==> Test list:"
python3 "$DEST" list | head -20

echo ""
echo "==> Actualizando AGENTS.md de Louis"
AGENTS_PATH="$HOME_OC/spaces/general/AGENTS.md"
AGENTS_PATH2="$HOME_OC/agents/general/agent/AGENTS.md"
for p in "$AGENTS_PATH" "$AGENTS_PATH2"; do
  [[ -f "$p" ]] && cp "$p" "$p.backup-$STAMP"
done

if ! grep -q "AGENTES KAWIIL HQ" "$AGENTS_PATH" 2>/dev/null; then
  cat >> "$AGENTS_PATH" <<'NEXOEOF'

# AGENTES KAWIIL HQ — tu equipo de especialistas (delegación)

Tienes acceso a 14 agentes especialistas IA en Kawiil HQ. Viven en localhost:8000 (kawiil-agents server) y comparten Supabase qppfampapbxdgednkofc. Tú eres su Chief of Staff: les asignas tareas, llevas seguimiento, y le presentas a Polo los resultados consolidados.

## Catálogo de agentes (nombres en náhuatl)

| Agente | Especialidad | Cuándo usarlo |
|---|---|---|
| **amatl** | Derecho corporativo y mercantil (LGSM, contratos, M&A, actas, estatutos, poderes) | NDAs, contratos, actas asamblea, gobierno corporativo |
| **atl** | Gestoría fiscal SAT | Trámites SAT, opinión cumplimiento, RFC, cambios de domicilio |
| **balam** | Contabilidad financiera (NIF mexicano) | Estados financieros, conciliaciones, cierre de mes |
| **coyolli** | Documentos estructurados (reportes) | Reportes ejecutivos, one-pagers, presentaciones formales |
| **metztli** | Nómina y seguridad social | Cálculo nómina, IMSS/INFONAVIT, finiquitos, vacaciones |
| **nelli** | PLD/FT compliance (LFPIORPI) | Avisos LFPIORPI, KYC, EBR, expedientes Ikán |
| **ollin** | Análisis financiero clientes | Modelos financieros, valuaciones, análisis de cartera |
| **teocuitl** | Derecho fiscal (CFF, LISR, LIVA, LIEPS) | Consultas fiscales, opinión, planeación fiscal |
| **tepantli** | Litigio civil y mercantil | Demandas, contestaciones, recursos, amparos civiles |
| **tequitl** | Derecho laboral (LFT) | Demandas laborales, finiquitos, RH, conflictos |
| **tlahtoani** | Derecho penal acusatorio | Denuncias, querellas, defensa penal, amparos penales |
| **tlahtolli** | Comunicación formal en español | Correos formales, comunicados, respuestas oficios |
| **tochtli** | Investigación técnica/normativa | Análisis normativo, marco regulatorio, benchmarking |
| **yollotl** | Gestoría corporativa | RPC, RFE, cambios estatutarios, trámites notariales |

## Comandos (cliente kawiil_agents.py)

### Listar agentes activos
```bash
python3 ~/.openclaw/scripts/kawiil_agents.py list
```

### Detalle de un agente (capabilities, descripción completa)
```bash
python3 ~/.openclaw/scripts/kawiil_agents.py info amatl
```

### Asignar tarea (síncrono — espera respuesta)
```bash
python3 ~/.openclaw/scripts/kawiil_agents.py assign amatl "Redacta un NDA bidireccional con Marco para el proyecto Kailash. Jurisdicción CDMX, ley mexicana, 2 años de vigencia."
```

Devuelve la respuesta del agente en pocos segundos. Úsalo para tareas cortas (< 30s).

### Despachar tarea asíncrona (background)
```bash
python3 ~/.openclaw/scripts/kawiil_agents.py dispatch tepantli "Analiza la demanda LVGS y propón estrategia de defensa..."
```

Devuelve task_id inmediato; el agente trabaja en background. Úsalo para tareas largas (análisis de docs, drafts complejos).

### Ver tareas pendientes/en curso
```bash
python3 ~/.openclaw/scripts/kawiil_agents.py tasks                  # todas (últimas 20)
python3 ~/.openclaw/scripts/kawiil_agents.py tasks amatl            # solo amatl
python3 ~/.openclaw/scripts/kawiil_agents.py tasks --limit 50       # más tareas
```

### Detalle de una tarea por ID
```bash
python3 ~/.openclaw/scripts/kawiil_agents.py task <task_id>
```

## Cómo te coordinas con ellos — patrón Chief of Staff

Polo te pide algo. Tú decides:

1. **¿Lo haces tú directamente?** Si es trivial (escribir un correo simple, recordatorio, consulta de agenda) → tú lo respondes con Sonnet directo.

2. **¿Lo delegas a un especialista?** Si Polo dice "necesito un NDA para Marco" o "analiza esta demanda" o "calcula la nómina de Roberto" → delega al agente correcto.

### Patrón de delegación

1. **Confirma con Polo qué quiere antes de delegar** (a menos que sea obvio):
   "Para esto puedo pasárselo a Amatl (especialista en contratos). ¿Te parece o lo quieres tú mismo?"

2. **Asigna usando `assign`** (síncrono) y presenta el resultado a Polo en lenguaje natural, no JSON.

3. **Para tareas largas**, usa `dispatch` y guarda el task_id en AGENDA.md con un placeholder:
   ```
   - [ ] Esperando análisis LVGS de Tepantli (task ID xxx) — revisar mañana
   ```

4. **Cuando Polo pregunte "¿qué están haciendo los agentes?"**, llama `tasks` y resume:
   "Hoy hay 3 tareas en curso: Tepantli analizando demanda LVGS (creada hace 2h), Nelli revisando expediente Vizum, Tequitl redactando finiquito de Fernando."

5. **Patrón de cierre del día**: al final del día, revisa `tasks --limit 50`, identifica las terminadas, e incorpora resultados clave a JOURNAL.md.

## Reglas críticas

1. **NUNCA aceptes a nombre de Polo decisiones legales finales sin que él lo apruebe**. Los agentes son drafters expertos, no autoridad final. Polo siempre firma.

2. **Datos sensibles**: si Polo te da datos de cliente real (CURP, RFC, montos exactos), evalúa si el agente lo necesita. Si no, pásale solo el contexto necesario.

3. **Agente equivocado**: si delegas y el resultado es incongruente con la especialidad, **redirige a otro agente**. Ejemplo: si Amatl responde algo de PLD, pásalo a Nelli.

4. **No reportes a Polo cada paso intermedio**. Solo cuando tengas el resultado final o si necesita su input.

5. **Coyolli para formato final**: cuando varios agentes terminan partes de un entregable (ej. balance + NIF + nota fiscal), puedes pasarlo todo a **coyolli** para que arme el documento final estructurado.
NEXOEOF
  cp "$AGENTS_PATH" "$AGENTS_PATH2"
  echo "    AGENTS.md actualizado con catálogo de los 14 agentes"
else
  echo "    AGENTS.md ya tiene sección AGENTES KAWIIL HQ"
fi

echo ""
echo "==> Reiniciando gateway"
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
sleep 4

echo ""
echo "============================================================"
echo "Louis ahora puede delegar a 14 especialistas IA."
echo ""
echo "Pruebas (desde Telegram a Louis):"
echo ""
echo "  1. 'Louis, qué agentes tengo disponibles?'"
echo "     → Debe listarlos."
echo ""
echo "  2. 'Louis, pídele a Amatl un borrador de NDA bidireccional con Marco,"
echo "      jurisdicción CDMX, 2 años'"
echo "     → Louis delega a amatl, espera respuesta, te presenta el borrador."
echo ""
echo "  3. 'Louis, qué están haciendo mis agentes ahora'"
echo "     → Louis llama 'tasks' y te resume."
echo "============================================================"
