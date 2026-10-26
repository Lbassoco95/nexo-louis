# Cerebro Kawiil v1.2 — confiabilidad (auditoría 26-oct-2026)

Criterio general: ante dato viejo vs nuevo prevalece el nuevo con su fecha; lo que no se pueda
confirmar se marca **«por validar»** y no se presenta como cierto. Código: `services/cerebro_calidad.py`
(reglas puras, probadas en `tests/test_cerebro_calidad.py`) + herramientas en `cerebro_kawiil_mcp.py`.

## Reglas

| # | Regla | Dónde |
|---|-------|-------|
| 1 | `agenda_pendientes()` entrega TODOS los `- [ ]` (sin tope de 20); `offset`/`limite` para paginar. Cada ítem: fecha de actualización, responsable, fuente. | `agenda_pendientes` |
| 2 | Sello > 7 días → «SIN REVISAR». «HOY dd-mmm» con fecha pasada → «VENCIDO / (era HOY …)». El sello `[visto …]` es *primera revisión*, no actualización real. | `parse_agenda`, `agenda_normalizar` |
| 3 | En IMPORTANT/CRÍTICO, un bullet cuyas fechas ya pasaron es **histórico** y se muestra en «HISTÓRICO (no críticos)». JOURNAL muestra su última fecha y avisa si está desactualizado. | `clasificar_critico`, `memoria_leer`, `cerebro_estado` |
| 4 | `entregables_auditar()`: estado real sugerido (superado, plazo vencido sin evidencia, enviado sin acuse, inactivo). **«archivado» por auto-archivado NO equivale a enviado.** OC-005 (acuse) y OC-006 (respuesta) son documentos distintos: uno no supera al otro. | `auditar_entregables` |
| 5 | Credenciales: toda salida del Cerebro pasa por el filtro; `memoria_depurar(aplicar=True)` las retira del disco con respaldo. | `redactar_credenciales` |
| 6 | Identidad pendiente ↔ tarea de Kawiil Central (abajo). | `emparejar`, `agenda_vs_tablero` |
| 7 | Un reporte regulatorio es «presentado» solo con acuse/folio con identificador; si no, **«sin evidencia»** (tampoco se afirma «pendiente»). | `obligacion_estado` |

## Criterio de identidad pendiente ↔ tarea (Kawiil Central)

Son el mismo asunto si se cumple, **en este orden**:

1. **Vínculo explícito**: el pendiente lleva `[KC:<id de la tarea>]` (o la tarea lleva `[CER:<n>]` en su descripción). Es la única forma autoritativa; al crear una tarea desde un pendiente se escribe el vínculo en ambos lados.
2. **Coincidencia fuerte**: comparten un identificador duro (expediente `37/2016`, oficio, folio, `R13`/`B-13211`, `OC-006/2026`, monto) **y** el mismo cliente o título parecido.
3. **Candidata por validar**: mismo cliente y título ≥ 60 % similar, sin identificador común. Nunca se liga sola.
4. Si nada aplica → **«sin tarea en el tablero»**. Varias candidatas → **«ambigua»**.

`agenda_vs_tablero(tareas_json)` aplica el criterio. El Cerebro no tiene credenciales de la BD de Kawiil Central: hay que pasarle el export de tareas.

## Puesta en producción

```bash
cd /opt/louis && git pull                 # rama claude/exciting-brown-s8lyfu hasta que se fusione
sudo ./deploy.sh                          # copia cerebro_calidad.py junto a cerebro_kawiil_mcp.py y reinicia
# o a mano: install -m 0755 services/cerebro_calidad.py services/cerebro_kawiil_mcp.py /opt/openclaw/scripts/
sudo systemctl restart cerebro-kawiil && curl -s http://127.0.0.1:4040/health   # version 1.2
```

Después, con visto bueno de Polo: `memoria_depurar(aplicar=False)` (revisar), luego `aplicar=True`;
igual con `agenda_normalizar`. Ambas dejan respaldo `.bak-<fecha>`.

Nota: `bootstrap/07` ahora fija `mcp<2` (mcp 2.x renombró `FastMCP` y el servidor no arranca).
