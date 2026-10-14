# Indexador de agentes (DOF y SJF → conocimiento de los kawiil-*)

Cada pasada de fondo toma un agente `kawiil-*` con palabras clave definidas en
`KAWIIL_KNOWLEDGE_MAP` (`louis_core.py`), elige documentos del DOF y del SJF que coinciden con
ellas, los resume con DeepSeek y guarda el resumen en
`spaces/general/agents/knowledge/<agente>/docs/` y un aprendizaje corto en
`OLLAMA_LEGAL_MEMORY.md`. La lógica vive en `services/legal_indexer.py`; `louis_core.py` solo la
invoca y mantiene el candado, la rotación y el presupuesto.

## Qué corrige (medido con datos reales del servidor, 14-oct-2026)

| Problema | Evidencia | Corrección |
|---|---|---|
| Cada agente se estancaba | 391 de ~40,700 documentos relevantes (1 %); la consulta pedía siempre los 10 más recientes y, ya indexados, concluía «al día» | Cursor (fecha, id) por flujo que solo avanza hacia atrás + ventana de 90 días que se revisa siempre para publicaciones nuevas o tardías |
| 12 de 22 agentes sin palabras clave | Los 12 tenían los mismos 13 documentos del DOF (2016-2019) y consumían 12 de cada 22 turnos | La rotación solo incluye agentes presentes en `KAWIIL_KNOWLEDGE_MAP` (un agente sin mapa aún puede pedirse a mano o por la cola de prioridad) |
| Sin prioridad | Daba igual una tesis aislada que una jurisprudencia, o una mención perdida que una coincidencia en el título | 6 flujos ponderados: jurisprudencia/título primero, luego texto, luego tesis aisladas (cada flujo conserva turnos, nada se muere de hambre) |
| Memoria de «ya indexados» al azar | `list(already_indexed)[-500:]` sobre un conjunto sin orden: pasado el tope repetía y volvía a cobrar documentos | La verdad es la carpeta `docs/` del agente (el id numérico al final del nombre del archivo) |
| Sin presupuesto, bloqueaba al scheduler | Cada documento es una llamada a DeepSeek y se hacía dentro del hilo del scheduler | Tope diario de documentos, pasada en un hilo aparte con candado (nunca dos a la vez) y resúmenes en paralelo |
| Mensajes de error guardados como conocimiento | `call_deepseek` no lanza ante un fallo: devuelve un texto como `(error DeepSeek HTTP 429: ...)`, y `_summarize_for_knowledge` lo trataba como el resumen: el documento quedaba «indexado» con un mensaje de error y el error entraba a la memoria de Ollama | El resumidor lanza; el documento queda pendiente, el cursor retrocede y se reintenta. Tras 3 fallos se guarda el texto original marcado «Resumen automático no disponible» (sin tocar la memoria de Ollama). Si fallan la mitad o más de una pasada, el indexador de fondo se pausa 30 min |
| Notas nuevas del DOF invisibles | `update` no clasifica y ningún timer del servidor ejecutaba `classify`: las notas posteriores a ~5-jun quedan con `incluido=NULL`, no se les baja texto, ni las indexan los agentes ni salen en la Biblioteca | `dof-harvest.service` ejecuta `classify` tras `update` |

## Parámetros (en `/opt/openclaw/openclaw.env`)

| Variable | Default | Qué hace |
|---|---|---|
| `LEGAL_BG_LIMIT` | 20 | Documentos nuevos por pasada (una pasada cada 10 min, un agente por pasada) |
| `LEGAL_BG_DAILY_MAX` | 1500 | Tope diario de documentos entre todos los agentes. Cada documento es una llamada a DeepSeek: ajusta según lo que quieras gastar |
| `LEGAL_BG_WORKERS` | 3 | Resúmenes en paralelo |

Con 10 agentes en rotación, cada agente recibe turno cada ~100 min (≈14 pasadas/día): hasta
~290 documentos/día por agente, limitado por el tope diario total.

## Cómo ver el avance

* Biblioteca → **Agentes**: revisados contra universo por agente, presupuesto de hoy, y por agente
  la tabla «Avance por flujo» (hechos, universo, fecha por la que va, errores).
* `python3 /opt/openclaw/scripts/biblioteca.py --estado` (solo lectura).
* Estado crudo: `knowledge/<agente>/cursor.json` y `/opt/openclaw/state/legal-bg-budget.json`.
* Log: líneas `legal bg [bg]: kawiil-x:+N` (log del scheduler).

## Desplegar en el servidor (no es un clon de git; hay respaldo y reversa)

`louis_core.py` lo importan el scheduler, el gateway y los bridges, así que **antes de reemplazarlo
comprueba que el del servidor es el mismo que el del repo previo a este cambio**:

```bash
sha256sum /opt/openclaw/scripts/louis_core.py
# debe ser d4bc5da14b8a5a6cd8d1bd6bb9f2add972067f0b24a54e50b192e2daf152e62a
```

Si NO coincide, no lo reemplaces (el servidor tiene cambios que el repo no): avisa y se integra a mano.

```bash
cd /opt/openclaw/scripts
sudo cp louis_core.py louis_core.py.bak-$(date +%Y%m%d%H%M)                       # respaldo
R=https://raw.githubusercontent.com/Lbassoco95/nexo-louis/main/cloud/hetzner/services
sudo curl -fsSL $R/legal_indexer.py -o legal_indexer.py
sudo curl -fsSL $R/louis_core.py    -o louis_core.py
sudo chown polo:polo legal_indexer.py louis_core.py
python3 -m py_compile legal_indexer.py louis_core.py && echo compila
sudo systemctl restart scheduler                                                   # el que indexa
journalctl -u scheduler -n 20 --no-pager
```

El gateway y los bridges siguen con el código anterior en memoria hasta que se reinicien (solo
afecta a la herramienta manual `legal_indexar`); reinícialos cuando convenga:
`sudo systemctl restart openclaw-gateway telegram-bridge slack-bridge`.

Clasificación del DOF (sin reemplazar la unidad, con un drop-in) y puesta al corriente:

```bash
sudo mkdir -p /etc/systemd/system/dof-harvest.service.d
sudo tee /etc/systemd/system/dof-harvest.service.d/classify.conf >/dev/null <<'EOT'
[Service]
ExecStartPost=/usr/bin/python3 /opt/openclaw/legal/dof/dof_biblioteca.py classify
EOT
sudo systemctl daemon-reload
python3 /opt/openclaw/legal/dof/dof_biblioteca.py classify     # una vez, para lo acumulado
```

Tras clasificar, `dof-contents` (cada 8 min) descarga el texto de lo recién incluido.

**Reversa:** `sudo cp louis_core.py.bak-<fecha> louis_core.py && sudo systemctl restart scheduler`.

## Limpiar lo que el defecto anterior pudo dejar

Documentos cuyo «resumen» es un mensaje de error de DeepSeek (y líneas equivalentes en la memoria
de Ollama). Primero **solo cuenta**:

```bash
K=/opt/openclaw/spaces/general/agents/knowledge
grep -rlE "^\((error (llamando a )?DeepSeek|sin respuesta de DeepSeek|respuesta vacía de DeepSeek)" $K/*/docs | wc -l
grep -cE "\((error (llamando a )?DeepSeek|sin respuesta de DeepSeek|respuesta vacía de DeepSeek)" /opt/openclaw/spaces/general/OLLAMA_LEGAL_MEMORY.md
```

Si hay, bórralos y reinicia el cursor de los agentes afectados para que se vuelvan a resumir (lo ya
resumido bien no se repite: la verdad es la carpeta `docs/`):

```bash
grep -rlE "^\((error (llamando a )?DeepSeek|sin respuesta de DeepSeek|respuesta vacía de DeepSeek)" $K/*/docs | xargs -r rm -v
rm -f $K/*/cursor.json
sed -i -E '/\((error (llamando a )?DeepSeek|sin respuesta de DeepSeek|respuesta vacía de DeepSeek)/d' /opt/openclaw/spaces/general/OLLAMA_LEGAL_MEMORY.md
```

## Pruebas

`python3 cloud/hetzner/tests/test_legal_indexer.py` (bases sintéticas con los esquemas reales; sin
red ni DeepSeek): progreso hasta cubrir el 100 % del universo, notas sin clasificar excluidas,
publicaciones nuevas y tardías, idempotencia, recuperación sin `cursor.json`, resumidor caído
(reintento y degradación tras 3 fallos), BD ausente, palabras clave hostiles y presupuesto.
