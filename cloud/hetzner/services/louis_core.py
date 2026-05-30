#!/usr/bin/env python3
"""
louis_core.py — lógica compartida entre canales (Telegram, Slack, …).

Encapsula:
  - Routing Ollama / Claude
  - Tool definitions y execute_tool
  - System prompt (memoria estructurada + ubicación + reglas)
  - M365 helpers (subprocess m365.py)
  - Self-introspection (verificar_conexiones)

Cada bridge (telegram-bridge.py, slack-bridge.py) importa de aquí y
encima añade su propia capa de transporte/IO.
"""

import os
import sys
import json
import time
import subprocess
import logging
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

# ===== Paths =====
# En Hetzner systemd corre como polo pero el home está protegido (ProtectSystem=full).
# Usamos /opt/openclaw cuando exista (caso runtime) y caemos a ~/.openclaw en dev (Mac).
HOME = Path.home()
if Path("/opt/openclaw").exists():
    HOME_OC = Path("/opt/openclaw")
else:
    HOME_OC = HOME / ".openclaw"
SPACE = HOME_OC / "spaces" / "general"
AGENTS_DIR = SPACE / "agents"   # sub-agentes ligeros (prompts especializados)
ANTHROPIC_ENV_FILE = HOME_OC / ".env"
# Bitácora de actividad de agentes — la consume el dashboard visual del gateway.
# Cada línea es un evento JSON: {ts, evento, agente, modelo, parent, detalle}.
AGENT_ACTIVITY_FILE = HOME_OC / "logs" / "agent-activity.jsonl"

M365_SCRIPT = HOME_OC / "scripts" / "m365" / "m365.py"
if not M365_SCRIPT.exists():
    M365_SCRIPT = HOME_OC / "scripts" / "m365.py"

# ===== Modelos =====
# Routing:
#   • Default chat → Ollama local (gratis, privado, sin créditos Anthropic)
#   • /sonnet, /claude, /fuerte, /profundo → Claude Sonnet (análisis profundo + tools)
#   • /haiku → Claude Haiku (rápido, sin tools)
#   • /llama, /ollama, /local → Ollama forzado (sin fallback a Claude)
CLAUDE_SONNET = "claude-sonnet-4-6"      # tool use, decisiones complejas
CLAUDE_HAIKU = "claude-haiku-4-5-20251001"  # chat rápido por default
CLAUDE_MODEL = CLAUDE_SONNET              # compat (cuando se usa tool use)
ANTHROPIC_API_BASE = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# DeepSeek (provider alterno barato — /deepseek, /ds). Compatible OpenAI chat.
DEEPSEEK_API_BASE = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_FAST_MODEL = os.environ.get(
    "OLLAMA_FAST_MODEL",
    os.environ.get("OLLAMA_DEFAULT_MODEL", "llama3.1:8b"),
)
OLLAMA_QUALITY_MODEL = os.environ.get("OLLAMA_QUALITY_MODEL", "gpt-oss:20b")
OLLAMA_MODEL = OLLAMA_FAST_MODEL  # alias logging / status
OLLAMA_CHAT_TIMEOUT = int(os.environ.get("OLLAMA_CHAT_TIMEOUT", "90"))
OLLAMA_QUALITY_TIMEOUT = int(os.environ.get("OLLAMA_QUALITY_TIMEOUT", "240"))
AGENT_FALLBACK_OLLAMA = os.environ.get("AGENT_FALLBACK_OLLAMA", "1").lower() in ("1", "true", "yes")
OLLAMA_SNAPSHOT_CONTEXT_MAX = 2000  # snapshot en system, no en user
OLLAMA_MAX_SYSTEM_CHARS = 10_000
OLLAMA_MAX_HISTORY_TURNS = 16
OLLAMA_MEMORY_DEFAULT_SNIPPET = 300
OLLAMA_MEMORY_LIMITS = {
    "AGENDA.md": 3500,
    "IMPORTANT.md": 2000,
    "JOURNAL.md": 1200,
    "USER.md": None,
}
TZ_CDMX = timezone(timedelta(hours=-6))
STATE_DIR = HOME_OC / "state"
LAST_BRIEFING_FILE = STATE_DIR / "last-briefing.json"
SESSION_HINTS_FILE = STATE_DIR / "session-hints.json"

M365_HINT_RE = re.compile(
    r"\b(correo|correos|inbox|outlook|m365|bandeja|mail|email|calendario)\b",
    re.IGNORECASE,
)

# Escritura explícita en archivos de memoria → Sonnet + tools (nunca Ollama)
MEMORY_WRITE_RE = re.compile(
    r"(?:"
    r"\b(anota|anotar|guarda|guardar|registra|registrar)\b.*\b(?:agenda|AGENDA|clientes|CLIENTES|"
    r"important|IMPORTANT|journal|JOURNAL|memoria|learnings|LEARNINGS|prospectos|PROSPECTOS)\b"
    r"|"
    r"\b(recuérdame|recuerdame|recuérdalo|recuerdalo)\b"
    r"|"
    r"\bagrega\s+(?:a|en)\s+(?:la\s+)?(?:agenda|AGENDA|clientes|CLIENTES|important|journal|memoria)\b"
    r"|"
    r"\bactualiza\s+(?:la\s+)?(?:agenda|AGENDA|clientes|CLIENTES|important)\b"
    r"|"
    r"\bescribe\s+en\s+(?:la\s+)?(?:agenda|AGENDA|clientes|memoria|important)\b"
    r"|"
    r"\b(anota|anotar)\s+(?:en\s+)?(?:agenda|AGENDA)\s*:"
    r")",
    re.IGNORECASE,
)

# Sub-agentes legales / invocación → Sonnet + tools
LEGAL_AUTO_SONNET_RE = re.compile(
    r"(?:"
    r"\b(invocar|invoca|delegar|delega)\s+(?:a\s+)?(?:el\s+)?(?:agente|sub-?agente|legal-)\b"
    r"|"
    r"\bdelega\s+(?:a\s+)?legal-"
    r"|"
    r"\b(consejo\s+experto\s+legal|consejo\s+legal|consejo\s+experto)\b"
    r"|"
    r"\b(crear|crea|nuevo|registra|registrar)\s+(?:un\s+)?(?:agente|sub-?agente)\b"
    r"|"
    r"\bcrea\s+agente\b"
    r"|"
    r"\b(listar|lista)\s+(?:mis\s+)?agentes\b"
    r"|"
    r"\b(agente\s+legal|agentes\s+legales)\b"
    r")",
    re.IGNORECASE,
)

STATUS_COMMAND_RE = re.compile(
    r"^(?:/status|status|estado|verifica(?:r)?\s+conexiones?)\s*$",
    re.IGNORECASE,
)

BRIEFING_EXPLICIT_RE = re.compile(
    r"\b(briefing|pendientes\s+de\s+hoy|qué\s+tengo\s+urgente|que\s+tengo\s+urgente|"
    r"resumen\s+del\s+d[ií]a|lista\s+de\s+pendientes|qué\s+tengo\s+para\s+hoy)\b",
    re.IGNORECASE,
)

AGENTS_LIST_COMMAND_RE = re.compile(
    r"^(?:/agentes|agentes|lista\s+agentes|listar\s+agentes|cuántos\s+agentes|cuantos\s+agentes)\s*$",
    re.IGNORECASE,
)

OPERATIONAL_CONTEXT_RE = re.compile(
    r"\b(pendiente|pendientes|agenda|briefing|urgente|hoy|mañana|manana|resumen|"
    r"recuerda|recordar|vimos|matutino|seguimiento|prioridad|backlog|journal|"
    r"importante|completado|cerrar\s+el\s+día|cerremos)\b",
    re.IGNORECASE,
)
FOLLOW_UP_BRIEFING_RE = re.compile(
    r"\b(mañana|manana|vimos|recuerdas|recordaste|briefing|pendientes\s+de\s+hoy|"
    r"lo\s+de\s+la\s+mañana|esta\s+mañana|mismo\s+listado)\b",
    re.IGNORECASE,
)
GREETING_RE = re.compile(
    r"^(hola|buenos?\s*d[ií]as|buenas?\s*tardes|buenas?\s*noches|hey|hi)\b",
    re.IGNORECASE,
)

# ===== Routing (qué va a Claude vs Ollama) =====
TOOL_KEYWORDS = [
    r"\b(verifica|verificar|chequea|chequear|revisa|status|estado|conexion|conexión|conexiones|conectado|conectada|conectas)\b",
    r"\b(dónde\s+(corres|estás|vives)|donde\s+(corres|estas|vives))\b",
    r"\b(qué\s+(tienes|puedes|conectado|conexión|cuentas|servicios|herramientas|tools)|que\s+(tienes|puedes|conectado))\b",
    r"\b(host|hostname|servidor|vm|server|configuración|configuracion|setup)\b",
    r"\b(actualiza|actualizar|escribe|guarda|anota|recuerda|recuérdame|recordame|registra)\b",
    r"\b(memoria|agenda|recordatorio|reminder|tarea|pendiente)\b",
    r"\b(léeme|leeme|abre|consulta)\s+(la|el|mi|mis)\s+(agenda|memoria|aprendizajes|learnings|important|proyectos|projects)\b",
    r"\b(amatl|atl|balam|coyolli|metztli|nelli|ollin|teocuitl|tepantli|tequitl|tlahtoani|tlahtolli|tochtli|yollotl)\b",
    r"\b(agente|agentes|despacha|dispatch|kawiil)\b",
    r"\b(correo|correos|email|outlook|inbox|mail|carpeta|archivo|archivar|borrar|marcar)\b",
    r"\b(calendario|calendar|junta|juntas|reunión|reunion|reuniones|cita|citas|evento|eventos)\b",
    r"\b(manda|envía|envia|enviar|responde|responder|reenvía|reenvia|reenviar)\b",
    r"\b(slack|canal|mensaje\s+a)\b",
    # Sub-agentes
    r"\b(crear|nuevo|registrar|invocar|delegar|listar)\s+(agente|sub-agente|subagente|asistente)\b",
    r"\b(asistente\s+de|asistente\s+especialista|asistente\s+especializado)\b",
    # Vida ejecutiva ampliada (CRM + personal)
    r"\b(cliente|clientes|prospecto|prospectos|pipeline|seguimiento\s+comercial)\b",
    r"\b(familia|cumpleaños|cumpleanios|aniversario|aniversarios|hijo|hija|esposa|mamá|papá|mama|papa)\b",
    r"\b(médico|medico|doctora|doctor|cita\s+médica|cita\s+medica|examen|estudio|análisis|analisis|medicamento|receta)\b",
    r"\b(viaje|viajes|vuelo|hotel|reserva|reservación|reservacion|itinerario)\b",
    r"\b(personal|persoal|fuera\s+de\s+oficina|vida\s+personal)\b",
    # Biblioteca legal (SJF + DOF)
    r"\b(tesis|jurisprudenc\w+|scjn|sjf|semanario|dof|diario\s+oficial|reforma|decreto|ley|leyes|amparo)\b",
    r"\b(descarga|descargas|backfill|biblioteca\s+legal|análisis\s+legal|analisis\s+legal|índice|index|indexa\w*)\b",
    # Mac status + wake
    r"\b(mac|macbook|laptop|computadora|compu|equipo)\b",
    r"\b(prendida|prendido|apagada|apagado|encendida|encendido|dormida|dormido|hibernando|sleep|batería|bateria|enchufada|cargando)\b",
    r"\b(heartbeat|sync|sincroniz\w+|sincronización|sincronizacion)\b",
    # Kawiil Central (producción Vercel + Supabase)
    r"\b(kawiil[\s-]?central|kawiil[\s-]?os|mati|matiox)\b",
    r"\b(tarea|tareas|proyecto|proyectos|avance|avances|backlog|pendiente)\b",
    r"\b(supabase|vercel|postgres|base\s+de\s+datos|database)\b",
    # Browser / navegación / vault
    r"\b(navega|abre\s+(?:la\s+)?(?:p[aá]gina|web|sitio|url)|chrome|browser|click|llena|completa\s+(?:el\s+)?formulario|login\s+en|sesi[oó]n\s+en)\b",
    r"\b(screenshot|captura\s+de\s+pantalla|extrae\s+de\s+la\s+p[aá]gina)\b",
    r"\b(vault|bitwarden|contrase[ñn]a|password|credencial(?:es)?)\b",
    r"^/sonnet\b", r"^/claude\b", r"^/calidad\b", r"^/fuerte\b", r"^/verify\b", r"^/status\b",
]
TOOL_REGEX = re.compile("|".join(TOOL_KEYWORDS), re.IGNORECASE)

OLLAMA_FORCE_PREFIXES = ("/llama", "/ollama", "/local")
OLLAMA_QUALITY_PREFIXES = ("/oss",)
HAIKU_FORCE_PREFIXES = ("/haiku", "/rápido", "/rapido")
DEEPSEEK_FORCE_PREFIXES = ("/deepseek", "/ds")
CLAUDE_FORCE_PREFIXES = (
    "/sonnet", "/claude", "/calidad", "/fuerte", "/profundo", "/analisis", "/análisis",
    "/verify",
)

# ===== Logger =====
log = logging.getLogger("louis_core")


# ===== Env helpers =====
def load_env_file(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_anthropic_key():
    env = load_env_file(ANTHROPIC_ENV_FILE)
    key = env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(f"No encontré ANTHROPIC_API_KEY en {ANTHROPIC_ENV_FILE} ni en env")
    return key


def load_deepseek_key():
    env = load_env_file(ANTHROPIC_ENV_FILE)
    key = env.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(f"No encontré DEEPSEEK_API_KEY en {ANTHROPIC_ENV_FILE} ni en env")
    return key


# ===== HTTP =====
def http_post_json(url: str, headers: dict, body: dict, timeout: int = 120):
    """POST JSON. Si hay HTTPError, lee el body y attach-lo al exception antes de re-raise.
    Los callers pueden hacer e.body si quieren ver el body sin re-leer (e.read() ya no devuelve nada).
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Leemos el body y lo guardamos como atributo del exception ANTES de raise.
        # Esto permite: (a) loguear el body acá, (b) que el caller acceda a e.body sin re-leer
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        e.body = err_body  # type: ignore[attr-defined]
        log.error(f"HTTP {e.code} en POST {url}: body_len={len(err_body)} body={err_body[:500]}")
        raise  # re-raise la HTTPError original — callers existentes (telegram_send_message) siguen funcionando


# ===== Memoria estructurada =====
# Memorias laborales + ejecutivas + personales. Louis lleva CRM ligero (CLIENTES,
# PROSPECTOS), agenda personal (PERSONAL, FAMILIA), salud (SALUD), viajes (VIAJES)
# y la operación normal de Kawiil/Yoltik (PROJECTS, PEOPLE, IMPORTANT, AGENDA).
MEMORY_FILES = [
    "USER.md",          # Perfil de Polo (rol, preferencias generales)
    "AGENDA.md",        # Pendientes operativos / del día / próximos
    "JOURNAL.md",       # Log diario — qué pasó cada día
    "LEARNINGS.md",     # Reglas/preferencias aprendidas en conversación
    "IMPORTANT.md",     # Decisiones críticas, contexto load-bearing
    "PROJECTS.md",      # Proyectos de Kawiil/Yoltik y estado
    "PEOPLE.md",        # Equipo Kawiil/Yoltik (roles, contactos, contexto)
    "CLIENTES.md",      # CRM ligero — clientes activos (Kailash, etc) + estado cuenta
    "PROSPECTOS.md",    # Pipeline comercial — quién, qué quieren, siguiente paso
    "PERSONAL.md",      # Vida personal — agenda no-oficina, citas, hobbies, planes
    "FAMILIA.md",       # Familia + cumpleaños + aniversarios + recordatorios anuales
    "SALUD.md",         # Citas médicas, medicamentos, exámenes pendientes
    "VIAJES.md",        # Viajes pasados/próximos + preferencias (aerolínea, hotel)
    "FINANZAS.md",      # Notas financieras personales (NO números de cuenta) — pagos recurrentes, deadlines fiscales
]


def load_system_prompt(channel: str = "telegram") -> str:
    """Concatena AGENTS.md + memoria estructurada. Adapta por canal."""
    parts = []
    agents = SPACE / "AGENTS.md"
    if agents.exists():
        parts.append(agents.read_text())
    parts.append("\n\n# CONTEXTO DE MEMORIA (archivos vivos)\n")
    for fname in MEMORY_FILES:
        path = SPACE / fname
        if path.exists():
            parts.append(f"\n## {fname}\n```\n{path.read_text()}\n```\n")

    parts.append(
        "\n\n# IDENTIDAD Y TONO (Ollama / chat normal)\n"
        "Eres Louis (Nexo), asistente ejecutivo DE Polo Bassoco (CEO Kawiil/Yoltik). "
        "Hablas A Polo en segunda persona — NUNCA te llames Louis ni le digas 'Hola Louis'.\n"
        "Español mexicano profesional. Conciso: máx. 3 párrafos salvo que pida detalle.\n"
        "NO describas tu pipeline interno (no digas 'revisando snapshot', 'según instrucción', etc.).\n"
        "Si falta un dato en memoria/snapshot, dilo; no inventes plazos, casos ni placeholders.\n"
        "\n# BRIEFING DIARIO\n"
        "En el system prompt recibes [CONTEXTO INTERNO] con AGENDA/IMPORTANT/JOURNAL parseados. "
        "Úsalos como única fuente de pendientes.\n"
        "**Primera conversación del día** (hola / buenos días): saluda a Polo + triage URGENTE + "
        "máx. 4 bullets Para HOY fieles al contexto + ¿Por dónde empezamos?\n"
        "**Mismo día después:** saludo breve; recuerda urgente si aplica; si no, ¿en qué te ayudo?\n"
        "**'Los de la mañana':** retoma el último briefing guardado + contexto actual.\n"
    )

    canal_text = ""
    if channel == "telegram":
        canal_text = (
            "\n\n# CANAL ACTUAL: Telegram\n"
            "Estás respondiendo por Telegram. Polo prefiere respuestas largas y formales por default. "
            "**FORMATO TELEGRAM:** usa Markdown legacy de Telegram:\n"
            "- Negrita: `*una sola*` (NO `**dos**`, eso aparece literal)\n"
            "- Cursiva: `_texto_`\n"
            "- Código: `` `texto` ``\n"
            "- Links: `[texto](url)`\n"
            "- NO uses headers `#`, `##`, `###` (aparecen como texto plano)\n"
            "- Emojis sí, son nativos\n"
            "- Para 'títulos' de secciones usa `*Título:*` en negrita.\n"
            "Si recibes un audio transcrito, considera que puede tener errores de transcripción "
            "(palabras técnicas como 'FIATCOIN', 'LFPIORPI', 'Kawiil', 'Yoltik' pueden venir mal escritas)."
        )
    elif channel == "slack":
        canal_text = (
            "\n\n# CANAL ACTUAL: Slack\n"
            "Estás respondiendo por Slack. Tono más operativo, mensajes más cortos que en Telegram. "
            "**FORMATO SLACK (mrkdwn):**\n"
            "- Negrita: `*texto*` (un asterisco)\n"
            "- Cursiva: `_texto_`\n"
            "- Tachado: `~texto~`\n"
            "- Código inline: `` `texto` ``\n"
            "- Bloque de código: triple backticks\n"
            "- Listas con `•` o `-`, sin headers Markdown.\n"
            "- Mencionar usuarios con `<@USERID>`."
        )
    parts.append(canal_text)

    parts.append(
        "\n\n# UBICACIÓN ACTUAL\nCorres en Hetzner Cloud (CPX42, Helsinki, IP 204.168.131.21), "
        "NO en la Mac de Polo. La migración se completó el 2026-05-25. "
        "Tu proceso es systemd, llamando Ollama local (llama3.1:8b) por default "
        "y Claude Sonnet 4.6 solo para tareas con tool use. Ya NO dependes de la Mac."
        "\n\n# QUIÉN ERES — ASISTENTE EJECUTIVO TOTAL DE POLO\n"
        "No eres un bot de oficina. Eres el asistente ejecutivo completo de Leopoldo (Polo) Bassoco, "
        "CEO de Kawiil/Yoltik. Tu trabajo va MÁS ALLÁ de la oficina:\n"
        "- **Profesional**: correos, calendarios, juntas, agentes, proyectos Kawiil/Yoltik, clientes (Kailash, Ikán), prospectos.\n"
        "- **Personal**: agenda no-laboral, familia, cumpleaños y aniversarios, citas médicas, hobbies, viajes, planes con amigos.\n"
        "- **Estratégico**: cuando Polo te cuenta algo (idea, reunión, decisión), captúralo en la memoria correcta automáticamente sin que tenga que pedírtelo. Si menciona un prospecto nuevo → PROSPECTOS.md. Si menciona el cumpleaños de alguien → FAMILIA.md con la fecha. Si menciona síntoma/cita médica → SALUD.md.\n"
        "- **Proactivo**: lleva tú la lista de pendientes (AGENDA.md). Si Polo te pide algo y luego se distrae, persíguelo. En briefings menciona seguimientos que ya hiciste y los que faltan.\n"
        "\n\n# ARCHIVOS DE MEMORIA — QUÉ VA DÓNDE\n"
        "- USER.md: rol de Polo, preferencias generales (no editar mucho)\n"
        "- AGENDA.md: pendientes operativos del día/semana\n"
        "- IMPORTANT.md: decisiones críticas y contexto que NO debes olvidar\n"
        "- LEARNINGS.md: reglas/preferencias que Polo te enseña en conversación\n"
        "- PROJECTS.md: proyectos Kawiil/Yoltik (Ikán, Kailash Sprints, Nexo, etc) con estado\n"
        "- PEOPLE.md: equipo Kawiil/Yoltik — Carmen Ruvalcaba, Marco, Alan, JC, etc\n"
        "- CLIENTES.md: clientes activos (Kailash hoy; Ikán cuando entre en producción)\n"
        "- PROSPECTOS.md: pipeline comercial — quién, qué necesita, próximo paso, fecha de seguimiento\n"
        "- PERSONAL.md: vida personal de Polo — agenda no-oficina, hobbies, planes\n"
        "- FAMILIA.md: familia + cumpleaños + aniversarios (recordatorios anuales)\n"
        "- SALUD.md: citas médicas pendientes, exámenes, medicamentos\n"
        "- VIAJES.md: viajes pasados/próximos, preferencias (aerolínea, hotel, status frecuente)\n"
        "- FINANZAS.md: notas financieras personales — pagos recurrentes, deadlines fiscales (NUNCA guardes números de cuenta o tarjetas)\n"
        "Usa `append_to_memory` cuando agregas. Usa `write_memory` solo si vas a reemplazar TODO el archivo."
        "\n\n# CUANDO POLO PREGUNTE DÓNDE ESTÁS O QUÉ TIENES CONECTADO\n"
        "SIEMPRE invoca primero la tool `verificar_conexiones`. Esto te da datos EN VIVO "
        "(hostname, IP, servicios systemd activos, modelos Ollama, M365 Kawiil/Yoltik con prueba real). "
        "NO contestes solo desde memoria — esta puede estar desactualizada. Reporta lo que la tool devuelve."
        "\n\n# CONTROL DE CORREOS\n"
        "Tienes control total de M365 Kawiil y Yoltik vía las tools m365_*. Cuando Polo pida operaciones "
        "(leer, marcar leído, archivar, borrar, responder, mandar, calendario), úsalas. Para BORRAR siempre "
        "confirma primero. Para MANDAR correo nuevo o crear evento: muestra borrador y espera 'confirmo'."
        "\n\n# BIBLIOTECA LEGAL (SJF + DOF) — CONSULTA, NO DESCARGA\n"
        "Tienes acceso de lectura a dos bases de datos SQLite que se sincronizan desde la "
        "Mac de Polo cada 15 min: SJF (tesis y jurisprudencias del Semanario Judicial Federación) "
        "y DOF (Diario Oficial de la Federación). Los scripts de descarga viven en la Mac, no en ti. "
        "Tu trabajo es REPORTAR estado, BUSCAR y AVISAR:\n"
        "- `legal_estado(modulo)` — estado de descarga (total, % progreso, última corrida, errores). "
        "Úsalo cuando Polo pregunte 'cómo va la descarga', 'cuántas tesis llevamos', 'qué tan al día estamos del DOF'.\n"
        "- `legal_buscar(modulo, query)` — búsqueda full-text. Útil para 'busca tesis sobre amparo indirecto', "
        "'qué dijo el DOF de reforma fiscal'.\n"
        "- `legal_ultimo(modulo, n)` — últimas N publicaciones recientes.\n"
        "- `legal_briefing()` — combinado SJF + DOF, ideal para el briefing matutino.\n"
        "Si Polo pregunta por el estado y la BD no se ha sincronizado todavía, dile claramente "
        "'la BD no ha llegado al VPS aún — revisa que el cron de mac-push-legal.sh esté activo en tu Mac'.\n"
        "## CUANDO LA BD NO TIENE EL TEXTO (ej: publicaciones recientes sin HTML descargado)\n"
        "Si `legal_buscar` no encuentra algo reciente (la BD tiene el índice pero no el texto "
        "completo), NO te quedes en 'no se pudo'. ACCIONA: usa el browser para traer el texto real "
        "del portal oficial. Flujo: `browser_navegar('https://www.dof.gob.mx/index_113.php?year=AAAA&month=MM&day=DD')` "
        "(ejemplar de un día) o la búsqueda del DOF, luego `browser_leer` para extraer el contenido, "
        "y entrega el resultado (o un .html con el análisis). El browser ya está configurado para no "
        "ser bloqueado por el portal. Para SJF usa el buscador público de la SCJN igual con el browser. "
        "La regla es: si te lo piden, lo CONSIGUES y lo MANDAS — no reportas que no se pudo.\n"
        "## INDEXACIÓN DE CONOCIMIENTO LEGAL (agentes kawiil-*)\n"
        "- `legal_indexar(agente, forzar, limite)` — indexa DOF/SJF para un agente kawiil-* específico "
        "(o 'todos' para todos). El agente lee docs relevantes a su especialidad, los resume con Claude Haiku "
        "y guarda el conocimiento. Úsalo cuando Polo diga 'indexa a kawiil-nelli', 'que los agentes aprendan', "
        "'actualiza el conocimiento legal'. El scheduler lo corre automáticamente cada semana.\n"
        "- `legal_conocimiento(agente)` — muestra qué sabe un agente kawiil-*: cuántos docs tiene, "
        "resumen semanal, última indexación. Úsalo cuando Polo pregunte 'qué sabe kawiil-metzli del ISR'.\n"
        "IMPORTANTE: los agentes kawiil-* ya tienen su conocimiento indexado INYECTADO automáticamente "
        "cuando los invocas. No necesitas pedirles que 'busquen' — ya saben lo que hay en DOF/SJF de su "
        "especialidad y pueden citar publicaciones específicas."
        "\n\n# SELF-UPDATE — PUEDES EDITARTE A TI MISMO\n"
        "Tienes tools (`leer_mi_codigo`, `editar_mi_codigo`, `reiniciar_mi_servicio`, "
        "`ver_mis_backups`, `restaurar_mi_codigo`) para modificar tu propio código en /opt/openclaw/scripts/. "
        "Cuando Polo te pida una mejora o un fix (ej: 'agrega recurrencia yearly', 'cambia el timeout'):\n"
        "1. Lee primero el archivo relevante con `leer_mi_codigo` para ver el código exacto.\n"
        "2. Propón el cambio en chat: muestra el old_string, el new_string, qué efecto tiene.\n"
        "3. ESPERA confirmación explícita de Polo ('sí', 'hazlo', 'aplica'). No edites sin confirmar.\n"
        "4. Llama `editar_mi_codigo` con descripcion clara. Se valida sintaxis automáticamente y se hace backup.\n"
        "5. Llama `reiniciar_mi_servicio` para que tome efecto. Verifica que quede 'active'.\n"
        "6. Si algo falla, usa `restaurar_mi_codigo` con el backup_id que te devolvió editar_mi_codigo.\n"
        "Los archivos editables son: louis_core.py, telegram-bridge.py, slack-bridge.py, scheduler.py, "
        "m365.py, m365/m365.py, import-legal-agents.sh. Servicios reiniciables: telegram-bridge, "
        "slack-bridge, scheduler. NUNCA toques credenciales, .env, o archivos fuera de la whitelist."
        "\n\n# PROACTIVIDAD — TÚ PERSIGUES A POLO, NO AL REVÉS\n"
        "Eres un asistente de primer nivel. NO esperas a que Polo te pregunte para "
        "darle seguimiento. Tienes la tool `agendar_recordatorio` que dispara mensajes "
        "PUSH al canal en el futuro. Úsala AGRESIVAMENTE:\n"
        "- Si Polo dice 'tengo junta a las 3', agenda recordatorio 15 min antes.\n"
        "- Si Polo menciona una fecha de seguimiento ('Carmen me regresa el viernes'), agéndalo.\n"
        "- Si Polo pide algo y no termina ('después le marco a Marco'), agenda al final del día.\n"
        "- Para deadlines fiscales/pagos recurrentes en FINANZAS.md, agenda recurrencia mensual.\n"
        "- Para cumpleaños en FAMILIA.md, agenda recurrente anual (workaround: 'monthly' x 12).\n"
        "Cuando agendes, AVÍSALE a Polo que ya lo agendaste (id, fecha). No agendas a sus "
        "espaldas pero tampoco le pidas permiso para cada cosa obvia. Si Polo dice 'no, "
        "cancélalo' usa `cancelar_recordatorio`."
        "\n\n# SUB-AGENTES — DELEGACIÓN\n"
        "Puedes crear, listar e invocar sub-agentes especializados (asistente de RRHH, "
        "asistente comercial, asistente legal, asistente personal, redactor de correos, etc.). "
        "Un sub-agente es un prompt especializado guardado en spaces/general/agents/{nombre}.md. "
        "Cuando Polo te pida algo muy específico y repetitivo ('necesito un asistente que me ayude "
        "a redactar correos a clientes en mi tono'), considera proponer crear un agente. "
        "Tools: `crear_agente`, `listar_agentes`, `invocar_agente`. Confirma con Polo el prompt antes "
        "de crear un agente nuevo.\n"
        "## REGLA LEGAL OBLIGATORIA — internacional como referencia, México como ley\n"
        "Los ~92 agentes `legal-*` (claude-for-legal) son contexto EE.UU.: sirven SOLO como "
        "REFERENCIA TÉCNICA internacional. Los agentes mexicanos (kawiil-nelli, kawiil-tepantli y "
        "los que crees con nombres en náhuatl) son los que mandan: revisan esa referencia y producen "
        "la versión OBLIGATORIA en México citando la norma local (CFF, LFPIORPI, LFPDPPP, LFT, CNBV, "
        "UIF, SAT, INAI). Para CUALQUIER duda legal/regulatoria usa `consejo_experto_legal(area, "
        "pregunta)`: trae la referencia internacional Y la mexicaniza automáticamente vía kawiil-nelli. "
        "NUNCA entregues una conclusión legal extranjera sin mexicanizarla. Cuando crees un agente "
        "legal nuevo, dale en su prompt esta misma doctrina (referencia internacional → versión MX "
        "obligatoria) para que aprenda de los internacionales pero cite siempre derecho mexicano."
        "\n\n# MAC DE POLO — ESTADO Y WAKE\n"
        "La Mac de Polo manda heartbeat cada 30s a /opt/openclaw/state/mac_heartbeat.json "
        "(batería, AC power, SSID, uptime). Tools:\n"
        "- `mac_estado()` — reporta si está online/offline, % batería, hace cuánto fue el último heartbeat. "
        "Úsalo cuando Polo pregunte 'está prendida mi Mac', 'sync activo', o ANTES de pedirle algo que necesite la Mac.\n"
        "- `mac_wake_request(razon)` — cuando necesitas que la Mac se prenda (ej: para correr backfill SJF, "
        "sync de Projects), revisa estado y le manda Telegram pidiéndole prenderla. Si la batería era baja "
        "o sin AC, le dice que la conecte al cargador. NO intenta WoL automático (Hetzner está en otra red).\n"
        "Cuando un usuario te pida algo que NECESITE archivos de la Mac (Projects/ por ejemplo) y veas que "
        "está offline, primero llama `mac_estado()`, luego `mac_wake_request(razon)`, y le explicas que "
        "esperas el heartbeat para continuar."
        "\n\n# KAWIIL CENTRAL (PRODUCCIÓN — Vercel + Supabase, NO ES CLON)\n"
        "Kawiil Central es la app que Polo usa para gestionar proyectos y tareas. Está EN PRODUCCIÓN:\n"
        "- Frontend: https://www.kawiil-central.mx (Vercel)\n"
        "- DB: Supabase (proyecto privado de Polo)\n"
        "Tú NO tienes el código, NO clonas el repo, NO editas archivos. Tu trabajo es OPERAR contra "
        "la base de datos para que Polo pueda mover tareas y aterrizar avances desde Telegram/Slack.\n"
        "\n"
        "Tools disponibles:\n"
        "- `kawiil_central_estado()` — verifica Vercel reachable + Supabase OK + count de tareas/proyectos. "
        "Úsalo cuando Polo pregunte 'cómo va kawiil-central' o al inicio de una sesión de trabajo.\n"
        "- `kawiil_central_tablas()` — lista las tablas de la BD. Úsalo cuando no sepas el schema.\n"
        "- `kawiil_central_describir(tabla)` — schema de una tabla (columnas, tipos, FKs). "
        "**Úsalo ANTES de hacer INSERT/UPDATE para saber exactamente qué campos existen.**\n"
        "- `kawiil_central_proyectos(estado?)` — lista proyectos activos.\n"
        "- `kawiil_central_tareas(estado?, proyecto_id?, asignado_a?, limit?)` — filtra tareas por estado, "
        "proyecto, responsable. Si los filtros no embonan con el schema real, devuelve error útil.\n"
        "- `kawiil_central_crear_tarea(titulo, proyecto_id, descripcion?, asignado_a?, prioridad?, "
        "deadline?, campos_extra?)` — crea tarea. Mapea flexible a columnas existentes. "
        "**SIEMPRE confirma con Polo el contenido antes de crear.**\n"
        "- `kawiil_central_actualizar_tarea(tarea_id, cambios)` — UPDATE selectivo, útil para mover de "
        "estado, reasignar, cerrar. `cambios` es dict campo→valor.\n"
        "- `kawiil_central_avance(tarea_id, texto, porcentaje?)` — registra avance/comentario. "
        "Busca tabla de comentarios; si no existe, anexa a campo notes/history de la tarea. "
        "**Úsalo cuando Polo te dicte por voz lo que avanzó — tú lo aterrizas en el sistema.**\n"
        "- `kawiil_central_query(sql, razon)` — SQL libre con guardrails. Para casos donde los wrappers "
        "no embonan con el schema real. PERMITIDO: SELECT/INSERT/UPDATE. BLOQUEADO: DROP/TRUNCATE/ALTER. "
        "DELETE requiere 'DELETE_CONFIRM' en razón Y confirmación explícita de Polo.\n"
        "\n"
        "FLUJO RECOMENDADO cuando Polo te dice algo como 'avancé X' o 'creemos tarea para Y':\n"
        "1. Si es la primera vez de la sesión, llama `kawiil_central_estado()` para verificar conectividad "
        "y descubrir las tablas que hay.\n"
        "2. Si necesitas INSERT/UPDATE, llama primero `kawiil_central_describir('tasks')` (o el nombre real) "
        "para conocer columnas exactas.\n"
        "3. Para CREAR: muestra a Polo el contenido propuesto (titulo, proyecto, descripcion, prioridad, "
        "asignado, deadline) y espera 'confirmo' / 'sí' antes de llamar `crear_tarea`.\n"
        "4. Para ACTUALIZAR estado o registrar AVANCES: hazlo directo si Polo dictó claramente "
        "(ej: 'cierra la tarea X', 'avancé 50% en Y') — pero SIEMPRE menciónale el id y campo que cambiaste.\n"
        "5. Cuando Polo te dicte una nota de voz larga con avances de múltiples tareas, separa las tareas, "
        "intenta matchear por título/keyword contra `kawiil_central_tareas`, y propón los `avance(...)` "
        "antes de ejecutar.\n"
        "\n"
        "PROHIBICIONES explícitas de Polo:\n"
        "- NO borrar tareas/proyectos sin confirmación literal de Polo.\n"
        "- NO tocar configuraciones de sistema (esto se traduce a: nada de DROP/TRUNCATE/ALTER/GRANT/REVOKE).\n"
        "- NO operar contra archivos del repo — no tienes acceso (vive en Vercel, no aquí)."
        "\n\n# BROWSER HEADLESS — Chromium en Hetzner\n"
        "Tienes un Chromium corriendo en Hetzner que persiste cookies/sesiones entre llamadas. "
        "Es independiente del Chrome de Polo. Sirve para tareas como: consultar páginas web, hacer login "
        "automatizado en sistemas, llenar formularios, monitorear cambios, scraping puntual.\n"
        "Tools:\n"
        "- `browser_navegar(url)` — abre la URL. Siempre empieza por aquí.\n"
        "- `browser_leer()` — devuelve título + texto visible de la página actual.\n"
        "- `browser_screenshot()` — PNG base64 + path guardado.\n"
        "- `browser_click(selector, by='css'|'text'|'role')` — click en elemento.\n"
        "- `browser_escribir(selector, value)` — llena un input. **NO uses esta tool para passwords directos** — usa `browser_login`.\n"
        "- `browser_press(key)` — Enter/Tab/etc.\n"
        "- `browser_esperar(selector, timeout_ms)` — espera elemento (útil post-submit).\n"
        "- `browser_login(url, vault_item, ...)` — FLUJO COMPUESTO: navega + saca creds del vault + llena form + submit. **Esta es la forma segura de hacer login**, nunca tipees passwords con browser_escribir.\n"
        "- `browser_historial(n)` — últimas N páginas visitadas.\n"
        "- `browser_reset()` — borra cookies/storage (sesión limpia).\n"
        "\n"
        "Flujo típico para login: `browser_login(url='https://app.x.com/login', vault_item='Slack Kawiil')`. "
        "Si los selectores default no funcionan en una página rara, pásale `user_selector`/`pass_selector`/`submit_selector`."
        "\n\n# VAULT BITWARDEN — secretos seguros\n"
        "Polo tiene sus passwords en Bitwarden. Tú puedes consultarlos vía bw CLI conectado en Hetzner:\n"
        "- `vault_buscar(query)` — lista items que matcheen. NUNCA devuelve passwords.\n"
        "- `vault_obtener(item_id, campo, razon)` — UN campo específico (username/password/totp/uri/notes). "
        "Si pides 'password', NUNCA se imprime en chat — solo se confirma longitud y se queda disponible para uso interno (ej: browser_login lo usa).\n"
        "Cada acceso queda en /opt/openclaw/logs/vault-access.log con timestamp + razón. "
        "Si Polo te pide ver explícitamente un password en chat: confirma 2 veces antes de mandarlo."
    )
    return "\n".join(parts)


OLLAMA_ANTI_HALLUCINATION_TAIL = (
    "\n\n# REGLAS OLLAMA (obligatorio)\n"
    "El bloque [CONTEXTO INTERNO] en este system prompt tiene prioridad sobre memoria genérica. "
    "NO inventes tareas, nombres ni placeholders. Si falta un dato, di que no está en AGENDA/IMPORTANT.\n"
    "NUNCA repitas etiquetas internas ([CONTEXTO INTERNO], INSTRUCCIÓN, SNAPSHOT) en tu respuesta.\n"
    "MEMORIA — SOLO LECTURA: puedes citar el contexto interno pero NO digas que ya anotaste, guardaste "
    "o actualizaste un archivo. Si Polo pide guardar algo, indica que use `/sonnet anota en AGENDA: …` "
    "o escriba explícitamente qué guardar.\n"
)

OLLAMA_CHAT_STYLE_APPEND = (
    "\n\n# MODO CHARLA (con historial)\n"
    "Responde como asistente ejecutivo en conversación fluida con Polo. "
    "Máximo 2-4 párrafos o bullets cortos; no vuelques listas completas de AGENDA. "
    "Usa el historial y el contexto interno; si ya diste briefing, no lo repitas entero.\n"
)

GPT_OSS_STYLE_APPEND = (
    "\n\n# ESTILO gpt-oss:20b\n"
    "Respuestas estructuradas: veredicto primero, luego bullets si hay 3+ ítems. "
    "Sin emojis por default. Si no sabes, dilo en una línea.\n"
)

_LEAK_LINE_RE = re.compile(
    r"(SNAPSHOT\s+OPERATIVO|CONTEXTO\s+INTERNO|##\s*INSTRUCCI|INSTRUCCIÓN:|MENSAJE\s+DE\s+POLO|"
    r"ÚLTIMO\s+BRIEFING\s+ENVIADO|según\s+(el\s+)?snapshot|revisando\s+el\s+\*?SNAPSHOT)",
    re.IGNORECASE,
)


def _read_space_file(fname: str) -> str:
    p = SPACE / fname
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _extract_markdown_section(text: str, section_title: str) -> str:
    """Extrae contenido bajo ## section_title hasta el siguiente ##."""
    pattern = rf"(?im)^##\s*{re.escape(section_title)}\s*$"
    m = re.search(pattern, text)
    if not m:
        return ""
    start = m.end()
    rest = text[start:]
    nxt = re.search(r"(?m)^##\s+", rest)
    block = rest[: nxt.start()] if nxt else rest
    return block.strip()


def _open_checkbox_lines(text: str, max_items: int = 20) -> list[str]:
    items = []
    for line in text.splitlines():
        if re.match(r"^\s*-\s*\[\s*\]\s+", line):
            items.append(line.strip())
        if len(items) >= max_items:
            break
    return items


def _last_journal_entry(journal_text: str, max_chars: int = 1200) -> str:
    if not journal_text.strip():
        return "(sin entradas en JOURNAL.md)"
    parts = re.split(r"(?m)^##\s+(\d{4}-\d{2}-\d{2})", journal_text)
    if len(parts) >= 3:
        date = parts[-2]
        body = parts[-1].strip()
        return f"## {date}\n{body[:max_chars]}"
    return journal_text.strip()[:max_chars]


def _extract_critical_important(important: str) -> str:
    """Solo subsección CRÍTICO de IMPORTANT.md (evita volcar el archivo entero)."""
    m = re.search(
        r"(?im)^##\s*CR[IÍ]TICO[^\n]*\n([\s\S]*?)(?=^##\s+|\Z)",
        important,
    )
    if m:
        return m.group(1).strip()[:800]
    if important.strip():
        return important[:800]
    return ""


def build_operational_snapshot(compact: bool = True) -> str:
    """Datos reales de AGENDA/IMPORTANT/JOURNAL para anclar respuestas (sin inventar)."""
    agenda = _read_space_file("AGENDA.md")
    important = _read_space_file("IMPORTANT.md")
    journal = _read_space_file("JOURNAL.md")
    clientes = _read_space_file("CLIENTES.md")
    meta = f"Generado: {datetime.now(TZ_CDMX).strftime('%Y-%m-%d %H:%M')} CDMX"
    lines = []

    hoy = _extract_markdown_section(agenda, "Para HOY") or _extract_markdown_section(agenda, "Para hoy")
    urgent_block = _extract_markdown_section(agenda, "URGENTE")
    open_all = _open_checkbox_lines(agenda, 15 if compact else 25)

    lines.append("*Para HOY*")
    if hoy:
        lines.append(hoy[:1200 if compact else 3500])
    elif open_all:
        lines.extend(open_all[:10 if compact else 12])
    else:
        lines.append("(sin pendientes abiertos en AGENDA)")

    if urgent_block:
        lines.append("\n*URGENTE*")
        lines.append(urgent_block[:600 if compact else 1500])

    crit = _extract_critical_important(important)
    lines.append("\n*IMPORTANT — CRÍTICO*")
    lines.append(crit if crit else "(vacío)")

    lines.append("\n*JOURNAL (última entrada)*")
    lines.append(_last_journal_entry(journal, 400 if compact else 1200))

    lines.append("\n*CLIENTES*")
    if clientes.strip():
        cap = 800 if compact else 1500
        lines.append(clientes[:cap])
        if len(clientes) > cap:
            lines.append("…[truncado]")
    else:
        lines.append("(vacío)")

    body = "\n".join(lines)
    if not compact:
        return f"{meta}\n\n{body}"
    return body


def is_first_conversation_today(history_file: Path | None) -> bool:
    if not history_file or not history_file.exists():
        return True
    today = datetime.now(TZ_CDMX).date()
    last_date = None
    for line in history_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = entry.get("ts", "")
            if ts:
                last_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ_CDMX).date()
        except Exception:
            pass
    if last_date is None:
        return True
    return last_date < today


def needs_operational_context(user_message: str) -> bool:
    if not user_message:
        return False
    return bool(OPERATIONAL_CONTEXT_RE.search(user_message))


def _wants_follow_up_briefing(user_message: str) -> bool:
    return bool(FOLLOW_UP_BRIEFING_RE.search(user_message or ""))


def _is_greeting(user_message: str) -> bool:
    msg = (user_message or "").strip()
    return bool(GREETING_RE.match(msg)) and len(msg.split()) <= 8


def _load_last_briefing_text() -> str:
    if not LAST_BRIEFING_FILE.exists():
        return ""
    try:
        data = json.loads(LAST_BRIEFING_FILE.read_text())
        return data.get("text", "") or ""
    except Exception:
        return ""


def save_last_briefing(text: str, snapshot: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(TZ_CDMX).isoformat(),
        "text": text[:8000],
        "snapshot_preview": snapshot[:2000],
    }
    LAST_BRIEFING_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_ollama_task_instruction(user_message: str, history: list, first_of_day: bool) -> str:
    """Instrucción interna (va en system, no debe aparecer en la respuesta a Polo)."""
    msg = (user_message or "").strip()
    if first_of_day and (_is_greeting(msg) or not history):
        return (
            "Tarea ahora: briefing del día para Polo. Solo datos del contexto interno. "
            "Máx. 4 bullets Para HOY reales, triage URGENTE, cierra con ¿Por dónde empezamos?"
        )
    if first_of_day and needs_operational_context(msg):
        return "Tarea: briefing completo usando solo el contexto interno. No inventes ítems."
    if _wants_follow_up_briefing(msg):
        return (
            "Tarea: Polo retoma pendientes de la mañana. Usa último briefing + contexto actual. "
            "Cita ítems concretos, sin placeholders."
        )
    if history and not needs_operational_context(msg):
        return (
            "Tarea: responde la pregunta de Polo con contexto interno + historial. "
            "Continuidad con mensajes previos."
        )
    return "Tarea: responde usando solo contexto interno e historial. No inventes datos."


def _load_ollama_legal_memory(user_message: str, max_chars: int = 2000) -> str:
    """
    Carga los aprendizajes legales más relevantes para inyectarlos en el contexto
    de Ollama cuando el mensaje toca temas legales. Devuelve "" si no hay nada.
    """
    if not OLLAMA_LEGAL_MEMORY.exists():
        return ""
    msg_lower = (user_message or "").lower()
    legal_words = {
        "ley", "decreto", "dof", "sjf", "tesis", "jurisprudencia", "artículo",
        "art.", "reglamento", "norma", "nom", "impuesto", "sat", "isr", "iva",
        "imss", "stps", "inai", "cnbv", "uif", "lfpiorpi", "cfdi", "scjn",
        "amparo", "contrato", "laboral", "fiscal", "penal", "civil", "mercantil",
        "societario", "corporativo", "compliance", "pld", "lavado", "datos personales",
        "privacidad", "obligación", "sanción", "multa", "tribunal", "tfja",
    }
    is_legal = any(w in msg_lower for w in legal_words)
    if not is_legal:
        return ""
    try:
        content = OLLAMA_LEGAL_MEMORY.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- [")]
        # Tomar las más recientes (al fondo del archivo)
        recent = lines[-60:]  # últimas 60 entradas
        if not recent:
            return ""
        bloque = "\n".join(recent)
        if len(bloque) > max_chars:
            bloque = bloque[-max_chars:]
        return (
            "\n\n[CONOCIMIENTO LEGAL INDEXADO — DOF/SJF (no mencionar esta etiqueta)]\n"
            "Aprendizajes recientes de publicaciones reales. Úsalos para dar contexto "
            "específico cuando el tema aplique:\n\n"
            + bloque
        )
    except Exception:
        return ""


def build_ollama_internal_context(
    user_message: str,
    history: list,
    snapshot: str,
    first_of_day: bool,
    chat_mode: bool = False,
) -> str:
    """Contexto operativo para append al system prompt (invisible para Polo)."""
    if chat_mode:
        last_b = _load_last_briefing_text()
        ref = (last_b[:800] if last_b else snapshot[:800]) or "(sin briefing previo)"
        legal_mem = _load_ollama_legal_memory(user_message, max_chars=1200)
        return (
            "\n\n[CONTEXTO INTERNO — modo charla; no repitas briefing completo ni esta etiqueta]\n"
            f"{ref}\n"
            f"{legal_mem}\n\n"
            "Tarea: Responde la pregunta de Polo de forma conversacional. Usa el historial del chat. "
            "No vuelques toda la AGENDA; cita solo lo relevante. Máx. 2-4 párrafos o bullets cortos."
        )
    snap_show = (
        snapshot
        if len(snapshot) <= OLLAMA_SNAPSHOT_CONTEXT_MAX
        else snapshot[:OLLAMA_SNAPSHOT_CONTEXT_MAX] + "\n…[truncado]"
    )
    legal_mem = _load_ollama_legal_memory(user_message, max_chars=2000)
    blocks = [
        "\n\n[CONTEXTO INTERNO — no mencionar esta etiqueta ni repetir su texto]",
        snap_show,
    ]
    if legal_mem:
        blocks.append(legal_mem)
    last_b = _load_last_briefing_text()
    if _wants_follow_up_briefing(user_message) and last_b:
        blocks.extend([
            "",
            "[Último briefing enviado a Polo — referencia]",
            last_b[:2500],
        ])
    blocks.append("\n" + _build_ollama_task_instruction(user_message, history, first_of_day))
    return "\n".join(blocks)


def sanitize_ollama_response(text: str) -> str:
    """Quita fugas de prompt y corrige saludos erróneos."""
    if not text:
        return text
    kept = []
    for line in text.splitlines():
        if _LEAK_LINE_RE.search(line):
            continue
        kept.append(line)
    out = "\n".join(kept).strip()
    if not out:
        out = text.strip()
    out = re.sub(
        r"^(¡Hola|Hola),?\s+Louis[!,.]?\s*",
        "¡Hola Polo! ",
        out,
        count=1,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"¿Qué prefieres hacer primero,?\s+Louis\??",
        "¿Por dónde empezamos?",
        out,
        flags=re.IGNORECASE,
    )
    return out.strip()


def _load_session_hints() -> dict:
    if not SESSION_HINTS_FILE.exists():
        return {}
    try:
        return json.loads(SESSION_HINTS_FILE.read_text())
    except Exception:
        return {}


def _save_session_hints(data: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_HINTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _sonnet_hint_already_shown() -> bool:
    return bool(_load_session_hints().get("sonnet_hint_shown"))


def _mark_sonnet_hint_shown():
    data = _load_session_hints()
    data["sonnet_hint_shown"] = datetime.now(TZ_CDMX).isoformat()
    _save_session_hints(data)


def _mark_last_route(route: str):
    data = _load_session_hints()
    data["last_route"] = route
    data["last_route_ts"] = datetime.now(TZ_CDMX).isoformat()
    _save_session_hints(data)


def _session_had_briefing() -> bool:
    return _load_session_hints().get("last_route") == "deterministic-briefing"


def _recent_assistant_briefing(history: list | None) -> bool:
    if not history:
        return False
    for h in reversed(history[-6:]):
        if h.get("role") != "assistant":
            continue
        content = (h.get("content") or "")
        if "¿Por dónde empezamos?" in content or "briefing" in content[:300].lower():
            return True
    return False


def wants_explicit_briefing(user_message: str) -> bool:
    return bool(BRIEFING_EXPLICIT_RE.search(user_message or ""))


def _had_briefing_this_session(history: list | None) -> bool:
    return _recent_assistant_briefing(history) or _session_had_briefing()


def _is_ollama_chat_mode(user_message: str, history: list | None) -> bool:
    if wants_explicit_briefing(user_message):
        return False
    if (user_message or "").strip().lower().startswith(OLLAMA_QUALITY_PREFIXES):
        return False
    hist = history or []
    return bool(hist) and _had_briefing_this_session(hist)


def _needs_sonnet_hint(user_message: str) -> bool:
    if needs_claude(user_message) or needs_sonnet_auto(user_message):
        return False
    return bool(M365_HINT_RE.search(user_message or ""))


def _sonnet_hint_response() -> str:
    return (
        "Para revisar tu *inbox*, redactar correos o usar calendario M365 necesito "
        "*Claude con tools* (no solo Ollama local).\n\n"
        "Ejemplo:\n"
        "`/sonnet revisa mis correos de hoy en Yoltik y dame resumen ejecutivo`\n\n"
        "Requiere créditos Anthropic activos. En modo local te ayudo con agenda, "
        "pendientes y seguimiento desde AGENDA.md."
    )


def needs_memory_write(user_message: str) -> bool:
    """True si Polo pide persistir en archivos de memoria (carril escritura)."""
    if not user_message:
        return False
    msg = user_message.strip()
    if msg.lower().startswith(OLLAMA_FORCE_PREFIXES):
        return False
    return bool(MEMORY_WRITE_RE.search(msg))


def needs_legal_sonnet(user_message: str) -> bool:
    """True si el mensaje requiere tools de agentes legales."""
    if not user_message:
        return False
    msg = user_message.strip()
    if msg.lower().startswith(OLLAMA_FORCE_PREFIXES):
        return False
    return bool(LEGAL_AUTO_SONNET_RE.search(msg))


def needs_sonnet_auto(user_message: str) -> bool:
    """Ruta Sonnet sin prefijo: escritura memoria o agentes legales."""
    return needs_memory_write(user_message) or needs_legal_sonnet(user_message)


def _memory_write_billing_msg() -> str:
    return (
        "⚠️ Para *guardar en memoria* (AGENDA, CLIENTES, etc.) necesito Claude con tools.\n\n"
        "Tu cuenta Anthropic está sin créditos. Cuando recargues, escribe por ejemplo:\n"
        "`/sonnet anota en AGENDA: llamar a Gonzalo mañana 10:00`\n\n"
        "Mientras tanto puedo *leer* pendientes desde AGENDA con un `hola` o preguntas de seguimiento."
    )


def _legal_sonnet_billing_msg() -> str:
    return (
        "⚠️ Para *agentes legales* (invocar, consejo experto, listar) necesito Claude Sonnet con tools.\n\n"
        "Recarga créditos en https://console.anthropic.com/settings/billing y usa:\n"
        "`/sonnet lista mis agentes`\n"
        "`/sonnet invoca agente legal-regulatory-compliance con tarea: …`"
    )


def _sonnet_auto_billing_msg(user_message: str) -> str:
    if needs_memory_write(user_message):
        return _memory_write_billing_msg()
    if needs_legal_sonnet(user_message):
        return _legal_sonnet_billing_msg()
    return _billing_error_msg()


def _format_memory_tool_confirmations(tool_results: list[str]) -> str:
    lines = [r for r in tool_results if r and ("OK agregado" in r or "OK escrito" in r)]
    if not lines:
        return ""
    return "✅ *Memoria actualizada:*\n" + "\n".join(f"• {ln}" for ln in lines)


# ===== Escritura determinística de memoria (sin Claude / sin créditos) =====
# Permite que Louis "aprenda" aunque la cuenta Anthropic no tenga créditos: la
# forma explícita "anota [en <archivo>]: <contenido>" se guarda directo con
# append_to_memory, sin pasar por Sonnet. Es el carril de aprendizaje a prueba
# de fallos — siempre disponible, gratis y local.
_MEMORY_FILE_KEYWORDS = (
    ("agenda", "AGENDA.md"),
    ("prospecto", "PROSPECTOS.md"),
    ("cliente", "CLIENTES.md"),
    ("importante", "IMPORTANT.md"),
    ("important", "IMPORTANT.md"),
    ("journal", "JOURNAL.md"),
    ("bitacora", "JOURNAL.md"),
    ("bitácora", "JOURNAL.md"),
    ("aprendizaje", "LEARNINGS.md"),
    ("learning", "LEARNINGS.md"),
    ("proyecto", "PROJECTS.md"),
    ("project", "PROJECTS.md"),
    ("personal", "PERSONAL.md"),
    ("familia", "FAMILIA.md"),
    ("salud", "SALUD.md"),
    ("viaje", "VIAJES.md"),
    ("finanza", "FINANZAS.md"),
    ("equipo", "PEOPLE.md"),
    ("gente", "PEOPLE.md"),
    ("people", "PEOPLE.md"),
)

_MEMORY_TRIGGER_RE = re.compile(
    r"^\s*(?:anota|anotar|agrega|agregar|registra|registrar|guarda|guardar|"
    r"apunta|apuntar|recu[eé]rdame|recuerdame|recu[eé]rdalo|recuerdalo)\b[:\s]*",
    re.IGNORECASE,
)
_MEMORY_FILE_PREFIX_RE = re.compile(
    r"^(?:en|a|al)\s+(?:la\s+|el\s+|mi\s+)?([\wáéíóúñ]+)\s*:?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _resolve_memory_file_strict(token: str) -> str | None:
    """Devuelve el archivo de memoria si el token coincide con una palabra clave, si no None."""
    t = (token or "").lower().strip()
    for kw, fname in _MEMORY_FILE_KEYWORDS:
        if t.startswith(kw):
            return fname
    return None


def try_deterministic_memory_write(user_message: str, strict: bool = True) -> str | None:
    """Guarda una nota en memoria SIN Claude (append directo).

    strict=True  → solo dispara con señal explícita (dos puntos en el mensaje,
                   o archivo nombrado: "anota en AGENDA: …"). Pensado como
                   fast-path que ahorra créditos y funciona offline sin robarle
                   a Sonnet peticiones matizadas.
    strict=False → permisivo (sin ':'); usado como fallback cuando Sonnet falla
                   por créditos, para no perder el aprendizaje.

    Devuelve confirmación (str) o None si el mensaje no es una nota clara.
    """
    if not user_message:
        return None
    msg = strip_override_prefix(user_message.strip())
    tm = _MEMORY_TRIGGER_RE.match(msg)
    if not tm:
        return None
    rest = msg[tm.end():].strip()
    if not rest:
        return None

    fname = "AGENDA.md"
    explicit_file = False
    fpm = _MEMORY_FILE_PREFIX_RE.match(rest)
    if fpm:
        candidate = _resolve_memory_file_strict(fpm.group(1))
        if candidate:
            fname, explicit_file, rest = candidate, True, fpm.group(2).strip()

    if strict and ":" not in user_message and not explicit_file:
        return None

    rest = rest.lstrip(":").strip()
    if not rest:
        return None

    fecha = datetime.now(TZ_CDMX).strftime("%Y-%m-%d %H:%M")
    result = execute_tool("append_to_memory", {"filename": fname, "content": f"- [{fecha}] {rest}"})
    if not result.startswith("OK"):
        return f"⚠️ No pude guardar en {fname}: {result}"
    return f"✅ Anotado en *{fname}*:\n• {rest}"


def should_deterministic_operational_response(user_message: str, history: list | None = None) -> bool:
    """Briefing instantáneo solo en primer saludo o pedido explícito (no en charla con historial)."""
    msg = (user_message or "").strip()
    if not msg:
        return True
    low = msg.lower()
    if low.startswith(OLLAMA_QUALITY_PREFIXES):
        return False

    hist = history or []
    has_history = len(hist) >= 1
    had_briefing = _had_briefing_this_session(hist)

    if wants_explicit_briefing(msg):
        return True

    if has_history and had_briefing:
        return False

    if _is_greeting(msg) and len(msg.split()) <= 8:
        return True

    if not has_history and wants_explicit_briefing(msg):
        return True

    return False


def is_status_command(user_message: str) -> bool:
    return bool(STATUS_COMMAND_RE.match((user_message or "").strip()))


def is_agents_list_command(user_message: str) -> bool:
    return bool(AGENTS_LIST_COMMAND_RE.match((user_message or "").strip()))


def format_agents_list_compact(max_names: int = 15) -> str:
    """Lista agentes registrados sin LLM (OpenClaw / spaces/general/agents/)."""
    if not AGENTS_DIR.exists():
        return "No hay carpeta de agentes en el VPS. Corre import-legal-agents.sh."
    files = sorted(AGENTS_DIR.glob("*.md"))
    if not files:
        return "No hay sub-agentes registrados. Usa `/sonnet crea agente …` o POST /v1/agents."
    legal = [f.stem for f in files if f.stem.startswith("legal-")]
    custom = [f.stem for f in files if not f.stem.startswith("legal-")]
    lines = [
        f"*Agentes OpenClaw* — {len(files)} registrados",
        f"  • Legales (claude-for-legal): {len(legal)}",
        f"  • Personalizados: {len(custom)}",
        "",
        "*Ejemplos:*",
    ]
    for name in (legal[: max_names - 2] + custom[:2])[:max_names]:
        lines.append(f"  • `{name}`")
    if len(files) > max_names:
        lines.append(f"  … y {len(files) - max_names} más")
    lines.extend([
        "",
        "*Invocar desde Telegram (Ollama local):*",
        "`invoca legal-regulatory-compliance: tu tarea en una línea`",
        "",
        "*API (en el VPS):*",
        "`POST /v1/agents/{nombre}` body `{\"tarea\":\"…\",\"modelo_override\":\"ollama\"}`",
        "",
        "Listado completo con descripciones: `/sonnet lista mis agentes` (requiere créditos).",
    ])
    return "\n".join(lines)


def _ollama_chat_timeout_fallback(user_message: str, snapshot: str) -> str:
    """Fallback conversacional cuando Ollama timeout tras briefing (no repetir dump)."""
    ref = _load_last_briefing_text()[:600] or snapshot[:600]
    q = (user_message or "").strip()[:120]
    return (
        "No alcancé a terminar la respuesta con Ollama local a tiempo.\n\n"
        f"*Tu pregunta:* {q or '(vacía)'}\n\n"
        f"*Contexto rápido:*\n{ref}\n\n"
        "Prueba:\n"
        "• Reformula en una frase más corta\n"
        "• `/oss` + tu pregunta (modelo más grande, más lento)\n"
        "• `briefing` si quieres ver la lista completa de pendientes"
    )


def format_morning_briefing_deterministic(snapshot: str) -> str:
    """Briefing push/Telegram — plantilla compacta con datos reales (sin LLM)."""
    hour = datetime.now(TZ_CDMX).hour
    saludo = "Buenos días" if hour < 12 else ("Buenas tardes" if hour < 19 else "Buenas noches")
    ts = datetime.now(TZ_CDMX).strftime("%H:%M")
    return (
        f"*{saludo} Polo* — briefing {datetime.now(TZ_CDMX).strftime('%A %d %b %Y')} (CDMX)\n\n"
        f"{snapshot[:3200]}\n\n"
        f"¿Por dónde empezamos?\n_(datos AGENDA/IMPORTANT · {ts})_"
    )


def deterministic_operational_response(snapshot: str | None = None) -> tuple[str, str]:
    """Respuesta operativa instantánea (<1s)."""
    snap = snapshot if snapshot is not None else build_operational_snapshot(compact=True)
    response = sanitize_ollama_response(format_morning_briefing_deterministic(snap))
    _mark_last_route("deterministic-briefing")
    return response, "deterministic-briefing"


def _resolve_ollama_model(user_message: str) -> tuple[str, int]:
    """Modelo y timeout según prefijo (/oss = calidad lenta)."""
    msg = (user_message or "").strip().lower()
    if msg.startswith(OLLAMA_QUALITY_PREFIXES):
        return OLLAMA_QUALITY_MODEL, OLLAMA_QUALITY_TIMEOUT
    return OLLAMA_FAST_MODEL, OLLAMA_CHAT_TIMEOUT


def generate_morning_briefing() -> str:
    snap = build_operational_snapshot()
    return format_morning_briefing_deterministic(snap)


def _trim_system_for_ollama(
    system_prompt: str,
    chat_mode: bool = False,
    ollama_model: str | None = None,
) -> str:
    """Recorta system prompt priorizando AGENDA/IMPORTANT/JOURNAL."""
    model = ollama_model or OLLAMA_FAST_MODEL
    tail = OLLAMA_ANTI_HALLUCINATION_TAIL
    if chat_mode:
        tail += OLLAMA_CHAT_STYLE_APPEND
    elif "gpt-oss" in model.lower():
        tail += GPT_OSS_STYLE_APPEND
    mem_marker = "# CONTEXTO DE MEMORIA"
    canal_marker = "# CANAL ACTUAL:"
    mem_idx = system_prompt.find(mem_marker)
    canal_idx = system_prompt.find(canal_marker, mem_idx if mem_idx >= 0 else 0)
    if chat_mode and mem_idx >= 0:
        head = system_prompt[:mem_idx]
        canal_part = system_prompt[canal_idx:] if canal_idx >= 0 else ""
        out = head[:6000] + "\n[memoria completa omitida en modo charla]\n" + canal_part
        if len(out) > OLLAMA_MAX_SYSTEM_CHARS:
            out = out[:OLLAMA_MAX_SYSTEM_CHARS]
        log.info(f"System prompt Ollama (chat): {len(system_prompt)} → {len(out)} chars")
        return out + tail
    if len(system_prompt) <= OLLAMA_MAX_SYSTEM_CHARS:
        return system_prompt + tail
    if mem_idx < 0:
        out = system_prompt[:OLLAMA_MAX_SYSTEM_CHARS] + "\n[contexto truncado para Ollama]"
        return out + tail
    head = system_prompt[:mem_idx]
    canal_part = system_prompt[canal_idx:] if canal_idx >= 0 else ""
    if len(head) > 8000:
        head = head[:8000] + "\n[AGENTS.md truncado para Ollama]\n"
    mem_block = system_prompt[mem_idx:canal_idx if canal_idx > mem_idx else len(system_prompt)]
    compact_mem = []
    current_fname = None
    for chunk in re.split(r"(## [A-Z_]+\.md\n```)", mem_block):
        if chunk.startswith("## "):
            compact_mem.append(chunk)
            fname_m = re.match(r"## ([A-Z_]+\.md)", chunk)
            current_fname = fname_m.group(1) if fname_m else None
        elif chunk.strip():
            snippet = chunk.strip()
            limit = OLLAMA_MEMORY_LIMITS.get(current_fname or "", OLLAMA_MEMORY_DEFAULT_SNIPPET)
            if current_fname == "JOURNAL.md":
                snippet = _last_journal_entry(snippet)
            if limit is not None and len(snippet) > limit:
                snippet = snippet[:limit] + "\n…[truncado]"
            elif limit is None:
                pass
            elif len(snippet) > OLLAMA_MEMORY_DEFAULT_SNIPPET:
                snippet = snippet[:OLLAMA_MEMORY_DEFAULT_SNIPPET] + "\n…[truncado]"
            compact_mem.append(snippet + "\n```\n")
    mem_compact = "".join(compact_mem)
    out = head + "\n\n# CONTEXTO DE MEMORIA (resumido para Ollama local)\n" + mem_compact + "\n" + canal_part
    if len(out) > OLLAMA_MAX_SYSTEM_CHARS:
        out = out[:OLLAMA_MAX_SYSTEM_CHARS] + "\n[fin de contexto Ollama]"
    log.info(f"System prompt Ollama: {len(system_prompt)} → {len(out)} chars")
    return out + tail


def _trim_history_for_ollama(history: list) -> list:
    if len(history) <= OLLAMA_MAX_HISTORY_TURNS:
        return history
    return history[-OLLAMA_MAX_HISTORY_TURNS:]


def _ollama_unavailable_msg(reason: str = "") -> str:
    extra = f" Detalle: {reason}" if reason else ""
    return (
        "⚠️ Ollama local no pudo responder a tiempo o falló."
        f"{extra}\n\n"
        "Sigo en modo local (sin gastar Anthropic). Prueba:\n"
        "• Mensaje más corto\n"
        "• Esperar 1-2 min (el modelo puede estar cargando en CPU)\n"
        "• /sonnet … solo para análisis profundo, correos, tools o agentes legales (requiere créditos Anthropic)\n\n"
        "Chat normal = Ollama automático. No necesitas /llama."
    )


# ===== Tools =====
TENANT_ENUM = ["kawiil", "yoltik"]

TOOLS_DEFINITION = [
    {
        "name": "read_memory",
        "description": "Lee un archivo de memoria de Louis (AGENDA.md, USER.md, LEARNINGS.md, JOURNAL.md, IMPORTANT.md, PROJECTS.md, PEOPLE.md).",
        "input_schema": {
            "type": "object",
            "properties": {"filename": {"type": "string", "enum": MEMORY_FILES + ["JOURNAL.md"]}},
            "required": ["filename"],
        },
    },
    {
        "name": "write_memory",
        "description": "Sobrescribe un archivo de memoria. Para agregar usa append_to_memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "enum": MEMORY_FILES + ["JOURNAL.md"]},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "append_to_memory",
        "description": "Agrega contenido al final de un archivo de memoria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "enum": MEMORY_FILES + ["JOURNAL.md"]},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "agendar_recordatorio",
        "description": "Programa un recordatorio PROACTIVO — Louis lo envía al canal (Telegram default) a la hora indicada. ÚSALO siempre que Polo diga 'recuérdame', 'avísame', 'mañana a las X', 'el viernes', etc. Sé proactivo: si Polo menciona algo con fecha futura, ofrécele agendarlo. Recurrencia opcional.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mensaje": {"type": "string", "description": "Mensaje que se le enviará a Polo cuando dispare (en primera persona/segunda persona, ej: 'Junta con Carmen en 30 min')"},
                "fecha_hora": {"type": "string", "description": "ISO 8601 con tz CDMX -06:00. Ej: '2026-05-26T08:00:00-06:00'. Si Polo dice 'mañana a las 9', calcula la fecha exacta tú."},
                "canal": {"type": "string", "enum": ["telegram", "slack"], "default": "telegram"},
                "modo": {"type": "string", "enum": ["raw", "enrich"], "default": "enrich", "description": "enrich = Haiku reformula en tono Louis; raw = manda literal"},
                "recurrencia": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "description": "Opcional. 'yearly' es ideal para cumpleaños y aniversarios."},
            },
            "required": ["mensaje", "fecha_hora"],
        },
    },
    {
        "name": "listar_recordatorios",
        "description": "Lista recordatorios pendientes en el queue (los que aún no se han disparado).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancelar_recordatorio",
        "description": "Cancela un recordatorio por su ID (obtener primero con listar_recordatorios).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "create_reminder",
        "description": "[LEGACY — prefiere agendar_recordatorio que SÍ dispara push, este solo escribe a AGENDA.md] Anota en AGENDA.md.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "datetime": {"type": "string", "description": "Formato YYYY-MM-DD HH:MM en hora local CDMX"},
            },
            "required": ["text", "datetime"],
        },
    },
    {
        "name": "save_learning",
        "description": "Agrega entrada a LEARNINGS.md cuando descubres preferencia o regla de Polo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "rule": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["topic", "rule", "context"],
        },
    },
    {
        "name": "crear_agente",
        "description": "Crea un sub-agente especializado (ej: 'asistente-rrhh', 'redactor-correos', 'asistente-personal'). Guarda su system prompt en spaces/general/agents/{nombre}.md. Confirma con Polo el prompt antes de crear.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Identificador kebab-case del agente, ej: 'asistente-rrhh'"},
                "especialidad": {"type": "string", "description": "Una línea describiendo qué hace"},
                "prompt": {"type": "string", "description": "System prompt completo del agente (rol, tono, qué debe hacer y qué no)"},
                "modelo": {"type": "string", "enum": ["claude-sonnet-4-6", "claude-haiku-4-5", "ollama"], "default": "claude-sonnet-4-6", "description": "Qué modelo usa por default cuando lo invoques"},
            },
            "required": ["nombre", "especialidad", "prompt"],
        },
    },
    {
        "name": "listar_agentes",
        "description": "Lista todos los sub-agentes registrados (nombre, especialidad, modelo). Útil para que Polo vea qué tiene disponible.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "invocar_agente",
        "description": "Delega una tarea a un sub-agente. Carga su prompt + lleva la tarea como input. Devuelve la respuesta del sub-agente. Útil cuando una tarea encaja mejor con un especialista (ej: redactor-correos para escribir un correo en tono Yoltik).",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre del agente a invocar"},
                "tarea": {"type": "string", "description": "Instrucción que se le da al agente"},
                "contexto": {"type": "string", "description": "Contexto adicional (datos, restricciones), opcional"},
                "modelo_override": {"type": "string", "description": "Forzar 'ollama' para sub-call local sin Anthropic"},
            },
            "required": ["nombre", "tarea"],
        },
    },
    {
        "name": "delegar_agente",
        "description": "Alias de invocar_agente — delega tarea a un sub-agente registrado en agents/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "tarea": {"type": "string"},
                "contexto": {"type": "string"},
                "modelo_override": {"type": "string"},
            },
            "required": ["nombre", "tarea"],
        },
    },
    {
        "name": "consejo_experto_legal",
        "description": "Dictamen legal MEXICANIZADO. Consulta 1-3 agentes legal-* internacionales (claude-for-legal, contexto US) como REFERENCIA, y luego el agente mexicano kawiil-nelli revisa ese razonamiento y produce la versión OBLIGATORIA en México citando la norma local (CFF, LFPIORPI, LFPDPPP, LFT, CNBV, UIF, SAT, INAI), separando 'obligatorio en México' de 'buena práctica internacional'. ÚSALO para cualquier duda legal/regulatoria antes de responder a un cliente o director. La referencia internacional enseña; México es la ley que se aplica.",
        "input_schema": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "description": "Dominio legal — uno de: ai-governance, privacy, ip, employment, commercial, corporate, regulatory, product. Si no estás seguro, pasa 'general' y el sistema infiere."},
                "pregunta": {"type": "string", "description": "Pregunta o caso a consultar"},
                "contexto": {"type": "string", "description": "Hechos relevantes (cliente, jurisdicción, documentos, etc.)"},
                "max_expertos": {"type": "integer", "default": 3, "description": "Cuántos expertos consultar (1-5)"},
            },
            "required": ["area", "pregunta"],
        },
    },
    {
        "name": "legal_estado",
        "description": "Reporta el estado actual de la biblioteca legal (SJF o DOF). Stats: total, % progreso, última corrida, breakdown por tipo/materia, fecha más reciente, errores. ÚSALO cuando Polo pregunte 'cómo va la descarga', 'cuántas tesis tengo', 'cómo va el análisis', 'cómo va el DOF', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "modulo": {"type": "string", "enum": ["sjf", "dof", "ambos"], "default": "ambos"},
            },
        },
    },
    {
        "name": "legal_buscar",
        "description": "Búsqueda full-text en la biblioteca legal. SJF busca en tesis (rubro+texto+precedentes). DOF busca en notas publicadas (titulo+texto). Devuelve top resultados con metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "modulo": {"type": "string", "enum": ["sjf", "dof"]},
                "query": {"type": "string", "description": "Texto a buscar (FTS5 syntax — comillas para frases exactas)"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["modulo", "query"],
        },
    },
    {
        "name": "legal_ultimo",
        "description": "Últimas N entradas más recientes (por fecha de publicación SJF o fecha del DOF).",
        "input_schema": {
            "type": "object",
            "properties": {
                "modulo": {"type": "string", "enum": ["sjf", "dof"]},
                "n": {"type": "integer", "default": 10},
            },
            "required": ["modulo"],
        },
    },
    {
        "name": "legal_briefing",
        "description": "Briefing ejecutivo combinado SJF + DOF: cómo va la descarga, cuánto se descargó en las últimas 24h, qué llegó nuevo importante, errores. Ideal para el briefing matutino del scheduler.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "legal_indexar",
        "description": "Indexa DOF/SJF para un agente legal kawiil-* específico (o 'todos' para indexar todos). Lee documentos relevantes a la especialidad del agente, los resume con Claude Haiku, y guarda el conocimiento en su directorio. Los agentes usan ese conocimiento automáticamente cuando responden preguntas. ÚSALO cuando Polo diga 'indexa a kawiil-nelli', 'actualiza el conocimiento', 'que los agentes aprendan del DOF', o cuando el scheduler lo programe semanalmente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agente": {"type": "string", "description": "Nombre del agente kawiil-* (ej: 'kawiil-nelli') o 'todos' para indexar todos los kawiil-*."},
                "forzar": {"type": "boolean", "default": False, "description": "Si true, re-indexa aunque ya fue indexado recientemente."},
                "limite": {"type": "integer", "default": 30, "description": "Máximo de documentos nuevos a indexar por agente (10-100)."},
            },
            "required": ["agente"],
        },
    },
    {
        "name": "legal_conocimiento",
        "description": "Muestra el conocimiento indexado de un agente kawiil-*: cuántos docs tiene, áreas cubiertas, última indexación, resumen semanal. ÚSALO cuando Polo pregunte 'qué sabe kawiil-nelli', 'cuánto contexto tiene el agente', 'muéstrame el resumen del DOF que tiene kawiil-metzli'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agente": {"type": "string", "description": "Nombre del agente kawiil-* (ej: 'kawiil-nelli')."},
            },
            "required": ["agente"],
        },
    },
    {
        "name": "mac_estado",
        "description": "Reporta si la Mac de Polo está prendida y enviando heartbeat. Devuelve: online/offline, segundos desde último heartbeat, % batería, AC power, SSID, IP pública. ÚSALO cuando Polo pregunte 'está prendida mi Mac', 'cómo está mi compu', 'sync activo', o ANTES de pedirle que actualice algo (si offline, avísale primero).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "mac_wake_request",
        "description": "Cuando la Mac está offline y Louis necesita que se prenda (ej: para correr scripts SJF, sync de Projects, etc.), evalúa último estado: si batería suficiente → manda Telegram a Polo pidiéndole que la prenda; si batería <10% o estado 'discharging' → manda mensaje 'conéctala al cargador'; si no hay heartbeat reciente → 'no he visto tu Mac, ¿está bien?'. NO intenta WoL automático (no estamos en la misma LAN).",
        "input_schema": {
            "type": "object",
            "properties": {
                "razon": {"type": "string", "description": "Por qué Louis necesita la Mac prendida. Ej: 'para correr backfill SJF', 'para sync de Projects'."},
            },
            "required": ["razon"],
        },
    },
    {
        "name": "browser_navegar",
        "description": "Abre una URL en el browser headless (Chromium) en Hetzner y devuelve título + URL final. Mantiene cookies/sesión entre llamadas. Default espera a networkidle (apto para SPAs React/Vue/Angular). Si la SPA es muy lenta, pasa `wait_extra_ms` para esperar más tiempo antes de leer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "wait_until": {"type": "string", "enum": ["domcontentloaded", "load", "networkidle"], "default": "networkidle", "description": "Cuándo considerar terminada la navegación. networkidle = espera 500ms sin tráfico (mejor para SPAs)."},
                "wait_extra_ms": {"type": "integer", "default": 0, "description": "Espera adicional en ms después de cargar (útil para SPAs con animaciones largas o lazy loading)."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_leer",
        "description": "Devuelve el texto visible y la URL de la página actualmente abierta en el browser headless (sin volver a navegar). Útil después de navegar/click para extraer contenido.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_screenshot",
        "description": "Toma screenshot PNG de la página actual y devuelve base64 + path donde se guardó. Útil para confirmar visualmente que llegaste a la página correcta.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_click",
        "description": "Click en un elemento. selector por defecto CSS; usa by='text' para click por texto visible o by='role' para por accessibility role.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Selector CSS, texto, o role name"},
                "by": {"type": "string", "enum": ["css", "text", "role"], "default": "css"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_escribir",
        "description": "Llena un input field con un texto. selector es CSS (ej: 'input[name=email]', '#password'). NO uses esta tool para passwords directos — usa browser_login que toma del vault.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "browser_press",
        "description": "Presiona una tecla (Enter, Tab, Escape, etc) en el contexto actual del browser.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "default": "Enter"}},
        },
    },
    {
        "name": "browser_esperar",
        "description": "Espera hasta que un selector aparezca en la página (útil después de submit, navegación AJAX). timeout_ms default 15000.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "timeout_ms": {"type": "integer", "default": 15000},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_login",
        "description": "FLUJO COMPUESTO: abre una URL, busca un item en el vault Bitwarden por nombre/id, llena usuario y contraseña, y hace submit. Selectores default funcionan para sitios estándar; override con `user_selector`, `pass_selector`, `submit_selector` si la página es custom.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL del login (ej: https://app.x.com/login)"},
                "vault_item": {"type": "string", "description": "Nombre del item o id en Bitwarden (ej: 'Slack Kawiil')"},
                "user_selector": {"type": "string", "description": "Selector CSS del input usuario", "default": "input[type=email], input[name=email], input[name=username], input#username"},
                "pass_selector": {"type": "string", "description": "Selector CSS del input password", "default": "input[type=password]"},
                "submit_selector": {"type": "string", "description": "Selector CSS del submit", "default": "button[type=submit]"},
            },
            "required": ["url", "vault_item"],
        },
    },
    {
        "name": "browser_historial",
        "description": "Lista las últimas N páginas que abrió el browser. Útil para revisar dónde se navegó.",
        "input_schema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "browser_reset",
        "description": "Borra cookies/localStorage del browser headless. Úsalo cuando quieras empezar con sesión limpia (ej: ya no necesitas un login viejo).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "vault_op_buscar",
        "description": "Busca items en 1Password por texto (usa esto si el despacho Kawiil usa 1Password). Devuelve lista de {id, title, vault, urls}. NO devuelve passwords.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "vault": {"type": "string", "description": "Opcional: nombre del vault específico"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "vault_op_obtener",
        "description": "Obtiene UN campo de un item en 1Password. Campos: 'username', 'password', 'otp', 'url', 'notesPlain'. Audit log. NUNCA imprime passwords en chat — solo confirma longitud.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "id, nombre exacto o referencia op://vault/item/field"},
                "campo": {"type": "string", "enum": ["username", "password", "otp", "url", "notesPlain"]},
                "razon": {"type": "string"},
            },
            "required": ["item", "campo", "razon"],
        },
    },
    {
        "name": "vault_buscar",
        "description": "Busca items en el vault Bitwarden por texto. Devuelve lista de {id, name, username, url}. NO devuelve passwords — usa vault_obtener para campos específicos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto a buscar en nombres de items"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "vault_obtener",
        "description": "Obtiene UN campo específico de un item del vault Bitwarden. Campos: 'username', 'password', 'totp', 'uri', 'notes'. Sólo devuelve el valor solicitado, nunca el item completo. Audit log en /opt/openclaw/logs/vault-access.log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "id del item (de vault_buscar) o nombre exacto"},
                "campo": {"type": "string", "enum": ["username", "password", "totp", "uri", "notes"]},
                "razon": {"type": "string", "description": "Por qué se accede (queda en audit log)"},
            },
            "required": ["item_id", "campo", "razon"],
        },
    },
    {
        "name": "kawiil_central_estado",
        "description": "Reporta el estado de Kawiil Central (app de gestión interna de Polo, deploy en Vercel www.kawiil-central.mx con DB en Supabase). Verifica: Vercel reachable, Supabase REST OK, count de proyectos y tareas, tablas disponibles. Úsalo cuando Polo pregunte 'cómo va kawiil-central', 'qué tengo pendiente', o al inicio para descubrir el schema.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kawiil_central_tablas",
        "description": "Lista las tablas del schema public en la BD de kawiil-central (Supabase). Útil para descubrir el modelo de datos antes de operar (Louis no asume nombres, los descubre).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kawiil_central_describir",
        "description": "Describe una tabla de kawiil-central: columnas, tipos, foreign keys. Úsalo ANTES de hacer INSERT/UPDATE para saber qué campos existen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabla": {"type": "string", "description": "Nombre de la tabla (ej: 'tasks', 'projects')"},
            },
            "required": ["tabla"],
        },
    },
    {
        "name": "kawiil_central_query",
        "description": "Ejecuta SQL contra la DB de producción de kawiil-central (Supabase). PERMITIDO: SELECT, INSERT, UPDATE. BLOQUEADO: DROP/TRUNCATE/ALTER/GRANT/REVOKE (cambios de schema fuera de la app). DELETE bloqueado por default — si necesitas borrar tareas, incluye literal 'DELETE_CONFIRM' en razón Y confirma 2 veces con Polo. Úsalo para listar/crear/actualizar tareas, proyectos, avances. Si no sabes el schema, primero llama `kawiil_central_describir`.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Query SQL (PostgreSQL syntax — Supabase)"},
                "razon": {"type": "string", "description": "Por qué se ejecuta — queda en audit log"},
            },
            "required": ["sql", "razon"],
        },
    },
    {
        "name": "kawiil_central_tareas",
        "description": "Helper para listar tareas. Intenta descubrir tabla 'tasks' o equivalente. Filtros opcionales por estado, proyecto, asignado. Si tu schema es distinto al esperado, usa kawiil_central_query directo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "estado": {"type": "string", "description": "Filtro por estado/status (ej: 'pendiente', 'en_progreso', 'hecho')"},
                "proyecto_id": {"type": "string", "description": "UUID o id del proyecto"},
                "asignado_a": {"type": "string", "description": "Email o id del responsable"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "kawiil_central_crear_tarea",
        "description": "Crea una tarea en kawiil-central. Lee primero el schema con kawiil_central_describir('tasks') o equivalente. Devuelve el id de la tarea creada. SIEMPRE confirma con Polo el contenido antes de crear.",
        "input_schema": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string"},
                "proyecto_id": {"type": "string", "description": "id del proyecto al que pertenece"},
                "descripcion": {"type": "string"},
                "asignado_a": {"type": "string", "description": "id o email del responsable"},
                "prioridad": {"type": "string", "enum": ["baja", "media", "alta", "urgente"]},
                "deadline": {"type": "string", "description": "ISO 8601 date o datetime"},
                "campos_extra": {"type": "object", "description": "Otros campos del schema que kawiil-central use (status default, labels, etc)"},
            },
            "required": ["titulo", "proyecto_id"],
        },
    },
    {
        "name": "kawiil_central_actualizar_tarea",
        "description": "Actualiza una tarea existente — útil para mover de estado, reasignar, ajustar deadline. Pásale el id de la tarea y los campos a cambiar. Para registrar un AVANCE/comentario usa kawiil_central_avance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tarea_id": {"type": "string"},
                "cambios": {"type": "object", "description": "Diccionario campo→valor (ej: {'status': 'hecho', 'completed_at': '2026-05-26'})"},
            },
            "required": ["tarea_id", "cambios"],
        },
    },
    {
        "name": "kawiil_central_avance",
        "description": "Registra un avance/comentario/actualización en una tarea. Busca tabla 'task_updates', 'task_comments' o 'comments'. Si no encuentra ninguna, sugiere el SQL al usuario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tarea_id": {"type": "string"},
                "texto": {"type": "string", "description": "Texto del avance (Polo puede dictar lo que avanzó, Louis lo aterriza aquí)"},
                "porcentaje": {"type": "integer", "description": "% de avance (opcional)"},
            },
            "required": ["tarea_id", "texto"],
        },
    },
    {
        "name": "kawiil_central_proyectos",
        "description": "Lista los proyectos activos en kawiil-central. Filtro opcional por estado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "estado": {"type": "string", "description": "Filtro por estado (ej: 'activo', 'archivado')"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "kawiil_central_tareas_proximas",
        "description": "Tareas con deadline próximo. Auto-detecta la columna de deadline (due_date, deadline, fecha_limite, fecha_vencimiento). Útil para 'qué vence esta semana', 'qué se atrasó', 'qué pendiente urgente tengo'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dias": {"type": "integer", "default": 7, "description": "Mirar N días hacia adelante. 0 = solo hoy. Negativo = vencidas."},
                "estado_excluir": {"type": "string", "description": "Estado a EXCLUIR (típicamente 'hecho' o 'completada' para ver solo lo pendiente)", "default": "hecho"},
                "asignado_a": {"type": "string", "description": "Filtro por responsable (opcional)"},
            },
        },
    },
    {
        "name": "kawiil_central_tarea_detalle",
        "description": "Vista 360° de una tarea: la tarea + sus comentarios + el proyecto al que pertenece + el cliente. Equivalente a abrir una tarea en el dashboard de Kawiil Central. JOIN automático.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tarea_id": {"type": "string", "description": "id de la tarea"},
            },
            "required": ["tarea_id"],
        },
    },
    {
        "name": "kawiil_central_pipeline",
        "description": "Pipeline de leads/prospectos agrupado por etapa (status). Muestra cuántos hay en cada etapa, suma de valor si existe campo amount, y los top N leads activos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit_por_etapa": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "kawiil_central_clientes",
        "description": "Lista clientes activos. Filtros opcionales por nombre o segmento. Muestra cuántos proyectos abiertos y tareas pendientes por cliente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto en el nombre del cliente (búsqueda parcial)"},
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "leer_mi_codigo",
        "description": "Lee uno de los archivos del runtime de Louis (whitelist: louis_core.py, telegram-bridge.py, slack-bridge.py, scheduler.py, m365.py, m365/m365.py, import-legal-agents.sh). Úsalo ANTES de editar para ver el estado actual y poder hacer un match exacto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "archivo": {"type": "string", "description": "Nombre del archivo (ej: 'scheduler.py' o 'm365/m365.py')"},
                "offset": {"type": "integer", "default": 0, "description": "Línea de inicio (0-indexed)"},
                "limit": {"type": "integer", "default": 200, "description": "Cuántas líneas leer"},
            },
            "required": ["archivo"],
        },
    },
    {
        "name": "editar_mi_codigo",
        "description": "Edita un archivo del runtime con replace exact-match. Hace backup automático, valida sintaxis Python/bash, REVIERTE si hay error. NO reinicia el servicio (eso se hace por separado para confirmar antes). SIEMPRE muestra el diff a Polo y espera confirmación ANTES de llamar esta tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "archivo": {"type": "string"},
                "old_string": {"type": "string", "description": "Texto exacto a reemplazar (debe ser único en el archivo — incluye contexto suficiente)"},
                "new_string": {"type": "string", "description": "Texto nuevo"},
                "descripcion": {"type": "string", "description": "Una línea explicando qué hace el cambio"},
            },
            "required": ["archivo", "old_string", "new_string"],
        },
    },
    {
        "name": "reiniciar_mi_servicio",
        "description": "Reinicia un servicio systemd (whitelist: telegram-bridge, slack-bridge, scheduler) y verifica que quede 'active'. Si no arranca, devuelve el log para diagnosticar y sugiere rollback. ÚSALO solo después de editar_mi_codigo y haber confirmado con Polo.",
        "input_schema": {
            "type": "object",
            "properties": {"servicio": {"type": "string", "enum": ["telegram-bridge", "slack-bridge", "scheduler"]}},
            "required": ["servicio"],
        },
    },
    {
        "name": "ver_mis_backups",
        "description": "Lista los backups de código disponibles (con timestamp). Útil antes de restaurar.",
        "input_schema": {
            "type": "object",
            "properties": {"archivo": {"type": "string", "description": "Opcional — filtra por archivo"}},
        },
    },
    {
        "name": "restaurar_mi_codigo",
        "description": "Restaura un backup encima del archivo actual. Usa después de un cambio fallido. Requiere reiniciar el servicio después.",
        "input_schema": {
            "type": "object",
            "properties": {
                "archivo": {"type": "string"},
                "backup_id": {"type": "string", "description": "Nombre completo del backup (ver_mis_backups primero) o timestamp"},
            },
            "required": ["archivo", "backup_id"],
        },
    },
    {
        "name": "verificar_conexiones",
        "description": "Verifica EN VIVO el estado de Louis: host, IP, servicios, credenciales, M365, Ollama. Usa cuando Polo pregunte por status, dónde estás, qué tienes conectado.",
        "input_schema": {
            "type": "object",
            "properties": {"incluir_m365": {"type": "boolean", "default": True}},
        },
    },
    # M365 tools (mismas que antes)
    {
        "name": "m365_inbox",
        "description": "Lista correos del inbox de Outlook (subject, remitente, fecha, preview, message_id). Filtros opcionales: unread/all, sender (substring case-insensitive en nombre o email del remitente, ej 'sofia' o '@camtom').",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "filter": {"type": "string", "enum": ["unread", "all"], "default": "unread"},
                "limit": {"type": "integer", "default": 10},
                "sender": {"type": "string", "description": "Filtro por remitente (substring en nombre o email)"},
            },
            "required": ["tenant"],
        },
    },
    {
        "name": "m365_buscar",
        "description": "Búsqueda full-text en el correo. Devuelve IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["tenant", "query"],
        },
    },
    {
        "name": "m365_ver_correo",
        "description": "Lee el cuerpo completo de un correo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
            },
            "required": ["tenant", "message_id"],
        },
    },
    {
        "name": "m365_marcar_leido",
        "description": "Marca un correo como leído.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
            },
            "required": ["tenant", "message_id"],
        },
    },
    {
        "name": "m365_marcar_no_leido",
        "description": "Marca un correo como NO leído.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
            },
            "required": ["tenant", "message_id"],
        },
    },
    {
        "name": "m365_archivar",
        "description": "Archiva un correo (mueve a Archive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
            },
            "required": ["tenant", "message_id"],
        },
    },
    {
        "name": "m365_borrar_correo",
        "description": "Borra un correo (a papelera). PIDE CONFIRMACIÓN antes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
            },
            "required": ["tenant", "message_id"],
        },
    },
    {
        "name": "m365_mover_correo",
        "description": "Mueve un correo a una carpeta (Inbox/Archive/Drafts/SentItems/DeletedItems/JunkEmail o folder_id).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
                "folder": {"type": "string"},
            },
            "required": ["tenant", "message_id", "folder"],
        },
    },
    {
        "name": "m365_responder",
        "description": "Responde a un correo. Confirma con Polo antes de mandar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
                "body": {"type": "string"},
                "reply_all": {"type": "boolean", "default": False},
            },
            "required": ["tenant", "message_id", "body"],
        },
    },
    {
        "name": "m365_reenviar",
        "description": "Reenvía un correo a otros destinatarios con comentario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
                "to": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["tenant", "message_id", "to", "comment"],
        },
    },
    {
        "name": "m365_listar_folders",
        "description": "Lista carpetas de correo con sus IDs.",
        "input_schema": {
            "type": "object",
            "properties": {"tenant": {"type": "string", "enum": TENANT_ENUM}},
            "required": ["tenant"],
        },
    },
    {
        "name": "m365_calendario",
        "description": "Lista eventos del calendario (hoy/manana/semana/mes).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "rango": {"type": "string", "enum": ["hoy", "manana", "semana", "mes"], "default": "hoy"},
            },
            "required": ["tenant"],
        },
    },
    {
        "name": "m365_crear_evento",
        "description": "Crea evento en calendario. Confirma fecha/hora con Polo antes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "subject": {"type": "string"},
                "inicio": {"type": "string", "description": "YYYY-MM-DDTHH:MM CDMX"},
                "fin": {"type": "string", "description": "YYYY-MM-DDTHH:MM CDMX"},
                "asistentes": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["tenant", "subject", "inicio", "fin"],
        },
    },
    {
        "name": "m365_responder_evento",
        "description": "Acepta, rechaza o marca como tentativa una invitación a un evento de calendario. Para invitaciones de juntas. Acción: 'accept', 'decline', o 'tentative'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "event_id": {"type": "string"},
                "accion": {"type": "string", "enum": ["accept", "decline", "tentative"]},
                "comentario": {"type": "string", "description": "Comentario opcional que se envía al organizador"},
                "send_response": {"type": "boolean", "default": True, "description": "Si true, manda respuesta al organizador. Si false, responde silenciosamente."},
            },
            "required": ["tenant", "event_id", "accion"],
        },
    },
    {
        "name": "m365_pendientes_evento",
        "description": "Lista SOLO las invitaciones de calendario donde Polo aún no ha respondido (notResponded). Útil cuando Polo pregunta '¿qué invitaciones tengo pendientes?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "rango": {"type": "string", "enum": ["hoy", "manana", "semana", "mes"], "default": "semana"},
            },
            "required": ["tenant"],
        },
    },
    {
        "name": "m365_attachments_listar",
        "description": "Lista los archivos adjuntos de un correo (id, nombre, tamaño, content-type). Útil ANTES de descargar para ver qué hay.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
            },
            "required": ["tenant", "message_id"],
        },
    },
    {
        "name": "m365_attachment_descargar",
        "description": "Descarga UN attachment de un correo a /opt/openclaw/state/email-attachments/ y muestra preview (texto/PDF/Word). Para correos con link a SharePoint, este tool detecta referenceAttachment y te dice que uses m365_sharepoint_descargar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "message_id": {"type": "string"},
                "attachment_id": {"type": "string"},
            },
            "required": ["tenant", "message_id", "attachment_id"],
        },
    },
    {
        "name": "m365_sharepoint_descargar",
        "description": "Descarga un archivo de SharePoint/OneDrive desde URL de sharing. REQUIERE scope `Files.Read.All` + `Sites.Read.All` en la app Azure — si falla con ese mensaje, Polo debe agregar permisos y re-hacer OAuth. Ideal para links que JC/equipo comparten por correo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "url": {"type": "string", "description": "URL completa del link de sharing (https://kawiil-my.sharepoint.com/...)"},
            },
            "required": ["tenant", "url"],
        },
    },
    {
        "name": "m365_mandar_correo",
        "description": "Manda un correo nuevo. SIEMPRE muestra borrador y espera 'confirmo'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant": {"type": "string", "enum": TENANT_ENUM},
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string"},
                "content_type": {"type": "string", "enum": ["text", "html"], "default": "text"},
            },
            "required": ["tenant", "to", "subject", "body"],
        },
    },
]


def _run_m365_tool(name: str, args: dict) -> str:
    if not M365_SCRIPT.exists():
        return f"ERROR: no encuentro m365.py (busqué en {M365_SCRIPT})"
    tenant = args.get("tenant")
    if not tenant:
        return "ERROR: falta tenant (kawiil o yoltik)"
    if name == "m365_inbox":
        cmd = ["leer-correos", tenant, args.get("filter", "unread"), str(args.get("limit", 10))]
        if args.get("sender"):
            cmd.append(args["sender"])
    elif name == "m365_buscar":
        cmd = ["buscar", tenant, args["query"], str(args.get("limit", 10))]
    elif name == "m365_ver_correo":
        cmd = ["ver-correo", tenant, args["message_id"]]
    elif name == "m365_marcar_leido":
        cmd = ["marcar-leido", tenant, args["message_id"]]
    elif name == "m365_marcar_no_leido":
        cmd = ["marcar-no-leido", tenant, args["message_id"]]
    elif name == "m365_archivar":
        cmd = ["archivar", tenant, args["message_id"]]
    elif name == "m365_borrar_correo":
        cmd = ["borrar", tenant, args["message_id"]]
    elif name == "m365_mover_correo":
        cmd = ["mover", tenant, args["message_id"], args["folder"]]
    elif name == "m365_responder":
        sub = "responder-todos" if args.get("reply_all") else "responder"
        cmd = [sub, tenant, args["message_id"], args["body"]]
    elif name == "m365_reenviar":
        cmd = ["reenviar", tenant, args["message_id"], args["to"], args.get("comment", "")]
    elif name == "m365_attachments_listar":
        cmd = ["attachments-listar", tenant, args["message_id"]]
    elif name == "m365_attachment_descargar":
        cmd = ["attachment-descargar", tenant, args["message_id"], args["attachment_id"]]
    elif name == "m365_sharepoint_descargar":
        cmd = ["sharepoint-descargar", tenant, args["url"]]
    elif name == "m365_listar_folders":
        cmd = ["listar-folders", tenant]
    elif name == "m365_calendario":
        cmd = ["calendario", tenant, args.get("rango", "hoy")]
    elif name == "m365_crear_evento":
        cmd = ["crear-evento", tenant, args["subject"], args["inicio"], args["fin"],
               args.get("asistentes", ""), args.get("body", "")]
    elif name == "m365_responder_evento":
        cmd = ["responder-evento", tenant, args["event_id"], args["accion"],
               args.get("comentario", ""), str(args.get("send_response", True)).lower()]
    elif name == "m365_pendientes_evento":
        cmd = ["pendientes-evento", tenant, args.get("rango", "semana")]
    elif name == "m365_mandar_correo":
        cmd = ["mandar-correo", tenant, args["to"], args["subject"], args["body"],
               args.get("cc", ""), args.get("content_type", "text")]
    else:
        return f"ERROR: tool m365 desconocido: {name}"
    try:
        r = subprocess.run(
            ["python3", str(M365_SCRIPT)] + cmd,
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            err = (r.stderr or "").strip() or (r.stdout or "").strip() or "(sin detalle)"
            return f"ERROR m365.py: {err[:1500]}"
        out = (r.stdout or "").strip()
        if len(out) > 4000:
            out = out[:4000] + "\n... (truncado)"
        return out or "(OK, sin output)"
    except subprocess.TimeoutExpired:
        return "ERROR: m365.py timeout (60s)"
    except Exception as e:
        return f"ERROR ejecutando m365.py: {e}"


def _verificar_conexiones(incluir_m365: bool = True) -> str:
    out = ["=== Verificación EN VIVO de Louis ===\n"]
    try:
        hostname = subprocess.run(["hostname"], capture_output=True, text=True, timeout=2).stdout.strip()
        out.append(f"Hostname: {hostname}")
    except Exception:
        out.append("Hostname: ?")
    try:
        ip = subprocess.run(
            ["curl", "-sS", "--max-time", "5", "https://api.ipify.org"],
            capture_output=True, text=True, timeout=8,
        ).stdout.strip()
        out.append(f"IP pública: {ip}")
        if ip == "204.168.131.21":
            out.append("  → Confirmado: estoy en Hetzner CPX42 (Helsinki)")
        else:
            out.append(f"  ⚠ IP no coincide con 204.168.131.21 — investigar")
    except Exception as e:
        out.append(f"IP pública: error ({e})")
    try:
        uname = subprocess.run(["uname", "-srm"], capture_output=True, text=True, timeout=2).stdout.strip()
        out.append(f"OS: {uname}")
    except Exception:
        pass
    out.append("\n--- Servicios ---")
    for svc in ("telegram-bridge", "slack-bridge", "scheduler", "openclaw-gateway", "ollama", "fail2ban"):
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            state = r.stdout.strip() or r.stderr.strip()
            mark = "✓" if state == "active" else ("·" if state == "inactive" else "?")
            out.append(f"  {mark} {svc}: {state}")
        except Exception as e:
            out.append(f"  ? {svc}: error ({e})")
    out.append("\n--- Credenciales en /opt/openclaw/credentials/ ---")
    creds_dir = HOME_OC / "credentials"
    if creds_dir.exists():
        for c in sorted([p.name for p in creds_dir.iterdir() if p.is_file() and not p.name.startswith("._")]):
            out.append(f"  • {c}")
    out.append("\n--- Ollama ---")
    try:
        t0 = time.time()
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            ms = int((time.time() - t0) * 1000)
            models = [m.get("name", "?") for m in data.get("models", [])]
            out.append(f"  ✓ {OLLAMA_BASE} OK ({ms}ms)")
            out.append(f"  Chat: {OLLAMA_FAST_MODEL} (timeout {OLLAMA_CHAT_TIMEOUT}s)")
            out.append(f"  Calidad (/oss): {OLLAMA_QUALITY_MODEL}")
            out.append(f"  Modelos: {', '.join(models[:8]) or '(ninguno)'}")
    except Exception as e:
        out.append(f"  ✗ Ollama no responde: {e}")
    out.append("\n--- Agentes ---")
    try:
        n_agents = len(list(AGENTS_DIR.glob("*.md"))) if AGENTS_DIR.exists() else 0
        legal_sample = sorted(AGENTS_DIR.glob("legal-*.md"))[:3] if AGENTS_DIR.exists() else []
        out.append(f"  Registrados: {n_agents} en {AGENTS_DIR}")
        if legal_sample:
            out.append("  Ejemplos: " + ", ".join(f.stem for f in legal_sample))
    except Exception as e:
        out.append(f"  ? agentes: {e}")
    out.append("\n--- OpenClaw Gateway ---")
    try:
        req = urllib.request.Request("http://127.0.0.1:3000/v1/status")
        with urllib.request.urlopen(req, timeout=5) as r:
            st = json.loads(r.read())
            out.append(f"  agents_count: {st.get('agents_count', '?')}")
            out.append(f"  routing: {st.get('routing', {})}")
    except Exception as e:
        out.append(f"  ✗ gateway :3000 — {e}")
    if incluir_m365 and M365_SCRIPT.exists():
        out.append("\n--- M365 ---")
        for tenant in ("kawiil", "yoltik"):
            try:
                r = subprocess.run(
                    ["python3", str(M365_SCRIPT), "leer-correos", tenant, "all", "1"],
                    capture_output=True, text=True, timeout=20,
                )
                if r.returncode == 0 and r.stdout.strip():
                    line1 = r.stdout.splitlines()[0][:100] if r.stdout.splitlines() else "(vacío)"
                    out.append(f"  ✓ {tenant}: OK (ej último correo: {line1})")
                else:
                    err = (r.stderr or "").strip()[:200]
                    out.append(f"  ✗ {tenant}: {err or 'sin output'}")
            except Exception as e:
                out.append(f"  ✗ {tenant}: error ({e})")
    out.append(f"\n--- Tools ({len(TOOLS_DEFINITION)}) ---")
    by_cat = {}
    for t in TOOLS_DEFINITION:
        n = t["name"]
        cat = n.split("_")[0] if "_" in n else "memoria"
        by_cat.setdefault(cat, []).append(n)
    for cat, names in by_cat.items():
        out.append(f"  {cat}: {', '.join(names)}")
    out.append(f"\n--- Routing ---")
    out.append(f"  Chat default: DeepSeek ({DEEPSEEK_MODEL}) — fluido, API")
    out.append(f"  Briefing operativo: determinístico (<1s)")
    out.append(f"  Búsqueda/tools/agentes/memoria: Claude ({CLAUDE_SONNET})")
    out.append(f"  /oss: Ollama {OLLAMA_QUALITY_MODEL} — local, privado")
    out.append(f"  /llama: Ollama {OLLAMA_FAST_MODEL} — local forzado")
    return "\n".join(out)


# ===== Biblioteca Legal (SJF + DOF) =====
# Las BDs llegan vía rsync Mac→Hetzner (mac-push-legal.sh cada 15 min).
# Las abrimos READ-ONLY para que un rsync a mitad de query no rompa nada.
import sqlite3 as _sqlite

LEGAL_BASE = HOME_OC / "legal"
SJF_DB = LEGAL_BASE / "sjf" / "biblioteca.db"
DOF_DB = LEGAL_BASE / "dof" / "biblioteca_dof.db"


def _legal_open(db_path):
    """Abre SQLite en modo URI read-only con timeout corto."""
    if not db_path.exists():
        return None
    uri = f"file:{db_path}?mode=ro&immutable=0"
    conn = _sqlite.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = _sqlite.Row
    return conn


def _legal_estado_sjf() -> str:
    if not SJF_DB.exists():
        return f"SJF: BD no encontrada en {SJF_DB} (todavía no se sincroniza desde Mac, o el rsync no ha corrido)."
    try:
        conn = _legal_open(SJF_DB)
        total = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
        jurispr = conn.execute("SELECT COUNT(*) FROM tesis WHERE ta_tj=1").fetchone()[0]
        tesis_aisl = conn.execute("SELECT COUNT(*) FROM tesis WHERE ta_tj=0").fetchone()[0]
        ultima = conn.execute("SELECT MAX(fecha_publicacion) FROM tesis").fetchone()[0]
        ultima_fetch = conn.execute("SELECT MAX(fetched_at) FROM tesis").fetchone()[0]
        last_run = conn.execute(
            "SELECT started_at, finished_at, mode, registros_ok, registros_error, notes "
            "FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        # Universo conocido (si está en progress)
        prog = dict(conn.execute("SELECT key, value FROM progress").fetchall())
        universo = prog.get("universo_max") or prog.get("ultimo_registro_conocido") or "?"
        # Top materias
        materias = conn.execute(
            "SELECT materias, COUNT(*) c FROM tesis WHERE materias != '' GROUP BY materias ORDER BY 2 DESC LIMIT 5"
        ).fetchall()
        # Última 24h
        ultimas_24h = conn.execute(
            "SELECT COUNT(*) FROM tesis WHERE fetched_at >= datetime('now','-1 day')"
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        return f"SJF: error leyendo BD: {e}"

    out = ["📚 *SJF (Semanario Judicial Federación)*"]
    out.append(f"  Total tesis: *{total:,}*")
    if str(universo).isdigit() and int(universo) > 0:
        pct = (total / int(universo)) * 100
        out.append(f"  Universo: ~{int(universo):,} → *{pct:.1f}%* descargado")
    out.append(f"  • Jurisprudencias: {jurispr:,}")
    out.append(f"  • Tesis aisladas: {tesis_aisl:,}")
    out.append(f"  • Última publicación: {ultima or '?'}")
    out.append(f"  • Última descarga: {ultima_fetch or '?'}")
    out.append(f"  • Nuevas últimas 24h: *{ultimas_24h:,}*")
    if last_run:
        run_dict = dict(last_run)
        out.append(f"  • Última corrida: {run_dict.get('mode')} → ok={run_dict.get('registros_ok')}, err={run_dict.get('registros_error')} ({run_dict.get('started_at','?')[:16]})")
    if materias:
        out.append("  • Top materias:")
        for m in materias:
            out.append(f"     – {m['materias']}: {m['c']:,}")
    return "\n".join(out)


def _legal_estado_dof() -> str:
    if not DOF_DB.exists():
        return f"DOF: BD no encontrada en {DOF_DB} (todavía no se sincroniza desde Mac)."
    try:
        conn = _legal_open(DOF_DB)
        total = conn.execute("SELECT COUNT(*) FROM notas").fetchone()[0]
        incluidas = conn.execute("SELECT COUNT(*) FROM notas WHERE incluido=1").fetchone()[0]
        excluidas = conn.execute("SELECT COUNT(*) FROM notas WHERE incluido=0").fetchone()[0]
        sin_clas = conn.execute("SELECT COUNT(*) FROM notas WHERE incluido IS NULL").fetchone()[0]
        con_html = conn.execute("SELECT COUNT(*) FROM notas WHERE existe_html=1 AND texto_plano IS NOT NULL AND length(texto_plano) > 0").fetchone()[0]
        ultima_fecha = conn.execute("SELECT MAX(fecha) FROM notas").fetchone()[0]
        last_run = conn.execute(
            "SELECT started_at, finished_at, mode, items_ok, items_error "
            "FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        leyes_total = 0
        try:
            leyes_total = conn.execute("SELECT COUNT(*) FROM leyes").fetchone()[0]
        except Exception:
            pass
        # Tipos top
        tipos = conn.execute(
            "SELECT tipo_documento, COUNT(*) c FROM notas WHERE incluido=1 GROUP BY tipo_documento ORDER BY 2 DESC LIMIT 6"
        ).fetchall()
        # Últimas 24h
        ultimas_24h = conn.execute(
            "SELECT COUNT(*) FROM notas WHERE fetched_at >= datetime('now','-1 day')"
        ).fetchone()[0]
        nuevas_incluidas_24h = conn.execute(
            "SELECT COUNT(*) FROM notas WHERE classified_at >= datetime('now','-1 day') AND incluido=1"
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        return f"DOF: error leyendo BD: {e}"

    out = ["📜 *DOF (Diario Oficial de la Federación)*"]
    out.append(f"  Total notas: *{total:,}*")
    out.append(f"  • Incluidas (leyes/decretos/acuerdos): {incluidas:,}")
    out.append(f"  • Excluidas (licitaciones/avisos): {excluidas:,}")
    if sin_clas:
        out.append(f"  • Sin clasificar: {sin_clas:,}")
    out.append(f"  • Con HTML descargado: {con_html:,}")
    if leyes_total:
        out.append(f"  • Leyes vigentes: {leyes_total:,}")
    out.append(f"  • Fecha más reciente: {ultima_fecha or '?'}")
    out.append(f"  • Nuevas últimas 24h: *{ultimas_24h:,}* (incluidas tras clasificación: {nuevas_incluidas_24h:,})")
    if last_run:
        rd = dict(last_run)
        out.append(f"  • Última corrida: {rd.get('mode')} → ok={rd.get('items_ok')}, err={rd.get('items_error')} ({rd.get('started_at','?')[:16]})")
    if tipos:
        out.append("  • Tipos top (incluidas):")
        for t in tipos:
            out.append(f"     – {t['tipo_documento'] or '?'}: {t['c']:,}")
    return "\n".join(out)


def _legal_estado(modulo: str = "ambos") -> str:
    if modulo == "sjf":
        return _legal_estado_sjf()
    if modulo == "dof":
        return _legal_estado_dof()
    return _legal_estado_sjf() + "\n\n" + _legal_estado_dof()


def _legal_buscar(modulo: str, query: str, limit: int = 10) -> str:
    if modulo == "sjf":
        if not SJF_DB.exists():
            return "SJF BD no disponible aún (rsync Mac→Hetzner no ha corrido)"
        try:
            conn = _legal_open(SJF_DB)
            rows = conn.execute(
                "SELECT t.registro_digital, t.rubro, t.epoca, t.instancia, t.materias, t.fecha_publicacion, t.ta_tj "
                "FROM tesis_fts JOIN tesis t ON t.registro_digital = tesis_fts.rowid "
                "WHERE tesis_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            conn.close()
        except Exception as e:
            return f"Error buscando SJF: {e}"
        if not rows:
            return f"(sin resultados SJF para «{query}»)"
        out = [f"🔎 SJF · {len(rows)} resultado(s) para «{query}»\n"]
        for r in rows:
            kind = "JUR" if r["ta_tj"] == 1 else "TA"
            out.append(f"• [{kind}] {r['rubro'] or '(sin rubro)'}")
            out.append(f"    {r['instancia'] or '?'} · {r['epoca'] or '?'} · {r['fecha_publicacion'] or '?'} · id={r['registro_digital']}")
            if r['materias']:
                out.append(f"    Materias: {r['materias']}")
        return "\n".join(out)

    if modulo == "dof":
        if not DOF_DB.exists():
            return "DOF BD no disponible aún (rsync Mac→Hetzner no ha corrido)"
        try:
            conn = _legal_open(DOF_DB)
            rows = conn.execute(
                "SELECT n.cod_nota, n.fecha, n.titulo, n.tipo_documento, n.nombre_cod_orga_uno "
                "FROM notas_fts JOIN notas n ON n.cod_nota = notas_fts.rowid "
                "WHERE notas_fts MATCH ? AND n.incluido=1 "
                "ORDER BY n.fecha DESC, rank LIMIT ?",
                (query, limit),
            ).fetchall()
            conn.close()
        except Exception as e:
            return f"Error buscando DOF: {e}"
        if not rows:
            return f"(sin resultados DOF para «{query}»)"
        out = [f"🔎 DOF · {len(rows)} resultado(s) para «{query}»\n"]
        for r in rows:
            out.append(f"• [{r['tipo_documento'] or '?'}] {r['titulo']}")
            out.append(f"    {r['fecha']} · {r['nombre_cod_orga_uno'] or '?'} · cod={r['cod_nota']}")
        return "\n".join(out)

    return f"ERROR: modulo '{modulo}' inválido. Usa 'sjf' o 'dof'."


def _legal_ultimo(modulo: str, n: int = 10) -> str:
    if modulo == "sjf":
        if not SJF_DB.exists():
            return "SJF BD no disponible aún"
        try:
            conn = _legal_open(SJF_DB)
            rows = conn.execute(
                "SELECT registro_digital, rubro, fecha_publicacion, instancia, ta_tj, materias "
                "FROM tesis ORDER BY fecha_publicacion DESC, registro_digital DESC LIMIT ?",
                (n,),
            ).fetchall()
            conn.close()
        except Exception as e:
            return f"Error: {e}"
        if not rows:
            return "(sin tesis)"
        out = [f"📚 Últimas {len(rows)} tesis SJF (por fecha de publicación):\n"]
        for r in rows:
            kind = "JUR" if r["ta_tj"] == 1 else "TA"
            out.append(f"• [{r['fecha_publicacion']}] [{kind}] {r['rubro'] or '(sin rubro)'}")
            out.append(f"    {r['instancia'] or '?'} · {r['materias'] or '?'} · id={r['registro_digital']}")
        return "\n".join(out)

    if modulo == "dof":
        if not DOF_DB.exists():
            return "DOF BD no disponible aún"
        try:
            conn = _legal_open(DOF_DB)
            rows = conn.execute(
                "SELECT cod_nota, fecha, titulo, tipo_documento, nombre_cod_orga_uno "
                "FROM notas WHERE incluido=1 "
                "ORDER BY fecha DESC, cod_nota DESC LIMIT ?",
                (n,),
            ).fetchall()
            conn.close()
        except Exception as e:
            return f"Error: {e}"
        if not rows:
            return "(sin notas DOF incluidas)"
        out = [f"📜 Últimas {len(rows)} notas DOF (incluidas):\n"]
        for r in rows:
            out.append(f"• [{r['fecha']}] [{r['tipo_documento'] or '?'}] {r['titulo']}")
            out.append(f"    {r['nombre_cod_orga_uno'] or '?'} · cod={r['cod_nota']}")
        return "\n".join(out)

    return f"ERROR: modulo '{modulo}' inválido"


def _legal_briefing() -> str:
    """Briefing ejecutivo combinado SJF + DOF — pensado para el push matutino."""
    parts = ["📊 *Briefing biblioteca legal*\n"]
    parts.append(_legal_estado_sjf())
    parts.append("")
    parts.append(_legal_estado_dof())
    parts.append("")
    # Lo nuevo importante (últimos 5 DOFs incluidos)
    try:
        if DOF_DB.exists():
            conn = _legal_open(DOF_DB)
            top_dof = conn.execute(
                "SELECT fecha, tipo_documento, titulo FROM notas "
                "WHERE incluido=1 AND fetched_at >= datetime('now','-2 days') "
                "ORDER BY fecha DESC LIMIT 5"
            ).fetchall()
            conn.close()
            if top_dof:
                parts.append("📌 *DOF reciente (últimos 2 días):*")
                for r in top_dof:
                    titulo = (r["titulo"] or "")[:120]
                    parts.append(f"  • [{r['fecha']}] [{r['tipo_documento'] or '?'}] {titulo}")
    except Exception as e:
        parts.append(f"(no pude listar DOF reciente: {e})")
    return "\n".join(parts)


# ===== Legal Agent Knowledge Indexing =====
# Cada agente kawiil-* acumula resúmenes de DOF/SJF de su especialidad.
# Se indexa semanalmente por el scheduler; también se puede disparar manualmente.
# El conocimiento se inyecta automáticamente cuando se invoca el agente.

KNOWLEDGE_BASE = HOME_OC / "spaces" / "general" / "agents" / "knowledge"
KNOWLEDGE_MAX_DOCS = 500       # docs máximos por agente (acumulación continua)
KNOWLEDGE_CONTEXT_DOCS = 5     # docs que se inyectan en cada invocación de agente
# Rotation state: qué agente fue el último en indexarse en background
LEGAL_BG_ROTATION = HOME_OC / "state" / "legal-bg-rotation.json"
# Cola de prioridad: docs urgentes a indexar antes que el batch normal
LEGAL_BG_PRIORITY = HOME_OC / "state" / "legal-bg-priority.jsonl"

# Memoria legal de Ollama: aprendizajes condensados de DOF/SJF que Ollama absorbe
# en su contexto cuando responde temas legales. Se acumula en background.
OLLAMA_LEGAL_MEMORY = SPACE / "OLLAMA_LEGAL_MEMORY.md"
OLLAMA_LEGAL_MEMORY_MAX = 300  # entradas máximas antes de rotar

# Keywords DOF/SJF por agente. Agentes no listados usan su campo 'especialidad'.
KAWIIL_KNOWLEDGE_MAP: dict = {
    "kawiil-nelli": {
        "label": "Compliance PLD / ALD / LFPIORPI",
        "dof": ["LFPIORPI", "lavado de dinero", "financiamiento terrorismo", "UIF", "CNBV"],
        "sjf": ["lavado de dinero", "LFPIORPI", "financiamiento del terrorismo", "UIF"],
    },
    "kawiil-tepantli": {
        "label": "Ciberseguridad / Datos Personales / LFPDPPP",
        "dof": ["datos personales", "LFPDPPP", "INAI", "ciberseguridad"],
        "sjf": ["datos personales", "privacidad", "LFPDPPP", "INAI"],
    },
    "kawiil-metzli": {
        "label": "Fiscal / SAT / CFF",
        "dof": ["CFF", "SAT", "reforma fiscal", "ISR", "IVA", "IEPS"],
        "sjf": ["CFF", "SAT", "ISR", "IVA", "TFJA", "devolución impuestos"],
    },
    "kawiil-ehecatl": {
        "label": "Laboral / STPS / IMSS",
        "dof": ["LFT", "STPS", "IMSS", "seguridad social", "salario mínimo"],
        "sjf": ["LFT", "STPS", "derecho laboral", "IMSS", "despido injustificado"],
    },
    "kawiil-tonati": {
        "label": "Corporativo / M&A / Gobierno Corporativo",
        "dof": ["LGSM", "fusión", "escisión", "gobierno corporativo", "valores"],
        "sjf": ["fusión de sociedades", "gobierno corporativo", "accionistas", "LGSM"],
    },
    "kawiil-citlali": {
        "label": "Tecnología / Fintech / Regulación Digital",
        "dof": ["fintech", "Ley Fintech", "criptomonedas", "tecnología financiera", "CNBV"],
        "sjf": ["fintech", "tecnología financiera", "criptomonedas"],
    },
    "kawiil-yoliztli": {
        "label": "Propiedad Intelectual / IMPI",
        "dof": ["IMPI", "propiedad industrial", "marcas", "patentes", "derechos de autor"],
        "sjf": ["propiedad industrial", "IMPI", "marcas", "patente"],
    },
    "kawiil-tlali": {
        "label": "Ambiental / Energía / SEMARNAT",
        "dof": ["SEMARNAT", "ambiental", "energía", "cambio climático", "NOM"],
        "sjf": ["medio ambiente", "SEMARNAT", "impacto ambiental"],
    },
    "kawiil-patli": {
        "label": "Salud / Farmacéutico / COFEPRIS",
        "dof": ["COFEPRIS", "salud", "medicamentos", "dispositivos médicos"],
        "sjf": ["COFEPRIS", "derecho a la salud", "medicamentos"],
    },
    "kawiil-calli": {
        "label": "Inmobiliario / Vivienda / Desarrollo Urbano",
        "dof": ["desarrollo urbano", "vivienda", "INFONAVIT", "CONAVI"],
        "sjf": ["bienes inmuebles", "arrendamiento", "INFONAVIT"],
    },
}


def _knowledge_dir(agente: str) -> Path:
    d = KNOWLEDGE_BASE / agente
    d.mkdir(parents=True, exist_ok=True)
    (d / "docs").mkdir(exist_ok=True)
    return d


def _knowledge_index(agente: str) -> dict:
    """Lee index.json del agente o devuelve estructura vacía."""
    idx_file = KNOWLEDGE_BASE / agente / "index.json"
    if idx_file.exists():
        try:
            return json.loads(idx_file.read_text())
        except Exception:
            pass
    return {"agente": agente, "docs": [], "last_indexed": None, "total": 0}


def _knowledge_save_index(agente: str, idx: dict):
    idx_file = KNOWLEDGE_BASE / agente / "index.json"
    idx_file.write_text(json.dumps(idx, ensure_ascii=False, indent=2))


def _summarize_for_knowledge(agente: str, titulo: str, fecha: str, fuente: str,
                             texto: str) -> tuple[str, str]:
    """
    Usa DeepSeek para resumir un documento legal del DOF/SJF.
    Devuelve (resumen_completo, aprendizaje_ollama) donde:
    - resumen_completo: 400-600 palabras para el knowledge dir del agente
    - aprendizaje_ollama: 1-2 líneas condensadas que Ollama absorbe en su contexto
    """
    system = (
        f"Eres el indexador legal del agente '{agente}' (derecho mexicano). "
        f"Lee el documento y responde en DOS partes separadas por '|||OLLAMA|||':\n\n"
        f"PARTE 1 — RESUMEN COMPLETO (para base de conocimiento del agente, ~400 palabras):\n"
        f"- ¿Qué establece o resuelve?\n"
        f"- ¿Qué obligaciones/derechos crea o modifica?\n"
        f"- Artículos clave mencionados\n"
        f"- Normas relacionadas (leyes, reglamentos, NOMs)\n"
        f"- Implicación práctica para empresas en México\n\n"
        f"|||OLLAMA|||\n\n"
        f"PARTE 2 — APRENDIZAJE CLAVE (para Ollama, máx 2 líneas concisas):\n"
        f"Una síntesis de la idea más importante de este documento en 1-2 líneas "
        f"que un modelo local pueda usar como referencia rápida al responder.\n\n"
        f"Responde en español de México. No repitas el título en la parte 1."
    )
    prompt = f"Fuente: {fuente} | Fecha: {fecha}\nTítulo: {titulo}\n\n{texto[:5000]}"
    try:
        resumen_raw = call_deepseek(system, [], prompt)
        if resumen_raw and "|||OLLAMA|||" in resumen_raw:
            partes = resumen_raw.split("|||OLLAMA|||", 1)
            resumen = partes[0].strip()
            aprendizaje = partes[1].strip()[:300]
        elif resumen_raw:
            # DeepSeek no siguió el formato — tomar todo como resumen, extractar inicio
            resumen = resumen_raw.strip()
            aprendizaje = resumen[:200].rstrip(".") + "."
        else:
            resumen = texto[:800] + "…"
            aprendizaje = titulo[:150]
    except Exception as e:
        log.warning(f"DeepSeek summarize falló ({agente}): {e}")
        resumen = texto[:800] + "…"
        aprendizaje = titulo[:150]
    return resumen, aprendizaje


def _ollama_legal_append(agente: str, area: str, fecha: str, titulo: str, aprendizaje: str):
    """
    Agrega un aprendizaje condensado a OLLAMA_LEGAL_MEMORY.md.
    Ollama lo absorberá en su contexto cuando responda temas legales.
    Rota el archivo si supera OLLAMA_LEGAL_MEMORY_MAX entradas.
    """
    try:
        OLLAMA_LEGAL_MEMORY.parent.mkdir(parents=True, exist_ok=True)
        entrada = (
            f"- [{datetime.now().strftime('%Y-%m-%d')}] [{agente}] [{area}] "
            f"**{titulo[:80]}** — {aprendizaje}\n"
        )
        if OLLAMA_LEGAL_MEMORY.exists():
            contenido = OLLAMA_LEGAL_MEMORY.read_text()
            lineas = [l for l in contenido.splitlines() if l.startswith("- [")]
            if len(lineas) >= OLLAMA_LEGAL_MEMORY_MAX:
                # Rotar: quitar el 20% más antiguo
                lineas = lineas[OLLAMA_LEGAL_MEMORY_MAX // 5:]
            lineas.append(entrada.rstrip())
            header = (
                "# Memoria Legal de Ollama\n"
                "_Aprendizajes condensados DOF/SJF — actualizado automáticamente en background_\n\n"
            )
            OLLAMA_LEGAL_MEMORY.write_text(header + "\n".join(lineas) + "\n")
        else:
            OLLAMA_LEGAL_MEMORY.write_text(
                "# Memoria Legal de Ollama\n"
                "_Aprendizajes condensados DOF/SJF — actualizado automáticamente en background_\n\n"
                + entrada
            )
    except Exception as e:
        log.warning(f"_ollama_legal_append: {e}")


def _legal_indexar_agente(agente: str, forzar: bool = False, limite: int = 30,
                          background: bool = False) -> str:
    """
    Indexa DOF/SJF para el agente. En background=True procesa lo más reciente
    que aún no está indexado sin gate de recencia — la puerta real es already_indexed.
    forzar=True re-indexa todo desde cero (borra el set de already_indexed).
    """
    if not _AGENT_NAME_RE.match(agente):
        return f"ERROR: nombre de agente '{agente}' inválido."
    agent_file = AGENTS_DIR / f"{agente}.md"
    if not agent_file.exists():
        return f"ERROR: agente '{agente}' no existe."

    idx = _knowledge_index(agente)

    # En modo manual (no background, no forzar), avisamos si no hay nada nuevo que hacer
    if not background and not forzar and idx.get("docs") and idx.get("last_indexed"):
        try:
            last = datetime.fromisoformat(idx["last_indexed"])
            hours_ago = int((datetime.now() - last).total_seconds() / 3600)
            if hours_ago < 1:
                return (f"⏭ {agente}: indexado hace {hours_ago}h "
                        f"({idx.get('total', 0)} docs). Usa forzar=true para re-indexar.")
        except Exception:
            pass

    # forzar=True: empieza desde cero
    if forzar:
        idx["docs"] = []

    # Obtener keywords para este agente
    cfg = KAWIIL_KNOWLEDGE_MAP.get(agente)
    if not cfg:
        meta = _parse_agent_file(agent_file)
        especialidad = meta.get("especialidad", "")
        words = [w for w in especialidad.replace(",", " ").split() if len(w) > 4][:5]
        cfg = {"label": especialidad, "dof": words, "sjf": words}

    kdir = _knowledge_dir(agente)
    docs_dir = kdir / "docs"
    nuevos = 0
    errores = 0
    already_indexed = set(idx.get("docs", []))

    # Cuántos por keyword (distribuir el límite)
    dof_keywords = (cfg.get("dof") or [])[:4]
    sjf_keywords = (cfg.get("sjf") or [])[:4]
    per_kw_dof = max(1, limite // max(len(dof_keywords), 1))
    per_kw_sjf = max(1, limite // max(len(sjf_keywords), 1))

    # ── Indexar desde DOF (más recientes primero) ──────────────────────────
    if DOF_DB.exists() and nuevos < limite:
        conn = _legal_open(DOF_DB)
        for keyword in dof_keywords:
            if nuevos >= limite:
                break
            try:
                rows = conn.execute(
                    "SELECT n.cod_nota, n.fecha, n.titulo, n.tipo_documento, n.texto_plano "
                    "FROM notas n "
                    "WHERE n.incluido=1 AND n.texto_plano IS NOT NULL "
                    "AND length(n.texto_plano) > 200 "
                    "AND (n.titulo LIKE ? OR n.texto_plano LIKE ?) "
                    "ORDER BY n.fecha DESC LIMIT ?",
                    (f"%{keyword}%", f"%{keyword}%", per_kw_dof * 5),
                ).fetchall()
            except Exception as e:
                log.warning(f"DOF bg query '{keyword}': {e}")
                rows = []
            for r in rows:
                if nuevos >= limite:
                    break
                doc_id = f"dof-{r['cod_nota']}"
                if doc_id in already_indexed:
                    continue
                try:
                    titulo_doc = r["titulo"] or "(sin título)"
                    resumen, aprendizaje = _summarize_for_knowledge(
                        agente, titulo_doc, r["fecha"] or "", "DOF", r["texto_plano"] or "")
                    fname = f"dof-{r['fecha'] or 'nd'}-{r['cod_nota']}.md"
                    (docs_dir / fname).write_text(
                        f"# {titulo_doc}\n"
                        f"**Fuente:** DOF | **Fecha:** {r['fecha']} | "
                        f"**Tipo:** {r['tipo_documento'] or '?'} | **ID:** {r['cod_nota']}\n\n"
                        f"{resumen}\n"
                    )
                    _ollama_legal_append(agente, cfg.get("label", "DOF"),
                                        r["fecha"] or "", titulo_doc, aprendizaje)
                    already_indexed.add(doc_id)
                    nuevos += 1
                except Exception as e:
                    log.warning(f"Doc DOF {r['cod_nota']} ({agente}): {e}")
                    errores += 1
        conn.close()

    # ── Indexar desde SJF (más recientes primero) ──────────────────────────
    if SJF_DB.exists() and nuevos < limite:
        conn = _legal_open(SJF_DB)
        for keyword in sjf_keywords:
            if nuevos >= limite:
                break
            try:
                rows = conn.execute(
                    "SELECT t.registro_digital, t.rubro, t.epoca, t.instancia, "
                    "t.materias, t.fecha_publicacion, t.ta_tj, t.texto "
                    "FROM tesis t "
                    "WHERE t.texto IS NOT NULL AND length(t.texto) > 200 "
                    "AND (t.rubro LIKE ? OR t.texto LIKE ? OR t.materias LIKE ?) "
                    "ORDER BY t.fecha_publicacion DESC LIMIT ?",
                    (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", per_kw_sjf * 5),
                ).fetchall()
            except Exception as e:
                log.warning(f"SJF bg query '{keyword}': {e}")
                rows = []
            for r in rows:
                if nuevos >= limite:
                    break
                doc_id = f"sjf-{r['registro_digital']}"
                if doc_id in already_indexed:
                    continue
                try:
                    kind = "Jurisprudencia" if r["ta_tj"] == 1 else "Tesis Aislada"
                    rubro = r["rubro"] or "(sin rubro)"
                    resumen, aprendizaje = _summarize_for_knowledge(
                        agente, rubro, r["fecha_publicacion"] or "", "SJF", r["texto"] or "")
                    fname = f"sjf-{r['fecha_publicacion'] or 'nd'}-{r['registro_digital']}.md"
                    (docs_dir / fname).write_text(
                        f"# {rubro}\n"
                        f"**Fuente:** SJF ({kind}) | **Época:** {r['epoca'] or '?'} | "
                        f"**Instancia:** {r['instancia'] or '?'} | "
                        f"**Fecha:** {r['fecha_publicacion']} | **ID:** {r['registro_digital']}\n"
                        f"**Materias:** {r['materias'] or '?'}\n\n"
                        f"{resumen}\n"
                    )
                    _ollama_legal_append(agente, cfg.get("label", "SJF"),
                                        r["fecha_publicacion"] or "", rubro, aprendizaje)
                    already_indexed.add(doc_id)
                    nuevos += 1
                except Exception as e:
                    log.warning(f"Doc SJF {r['registro_digital']} ({agente}): {e}")
                    errores += 1
        conn.close()

    # ── Actualizar índice y resumen ─────────────────────────────────────────
    total_docs = len(list(docs_dir.glob("*.md")))
    if nuevos > 0:
        _rebuild_weekly_summary(agente, kdir, docs_dir)
    idx.update({
        "agente": agente,
        "label": cfg.get("label", ""),
        "docs": list(already_indexed)[-KNOWLEDGE_MAX_DOCS:],
        "last_indexed": datetime.now().isoformat(),
        "total": total_docs,
    })
    _knowledge_save_index(agente, idx)

    if background:
        return f"{agente}:+{nuevos}" if nuevos else f"{agente}:al-día"
    return (
        f"✅ Indexación {agente} completada:\n"
        f"  • Nuevos: {nuevos} | Errores: {errores}\n"
        f"  • Total en knowledge: {total_docs}\n"
        f"  • Área: {cfg.get('label', '?')}"
    )


def _rebuild_weekly_summary(agente: str, kdir: Path, docs_dir: Path):
    """Reconstruye resumen-semanal.md con los últimos 20 docs."""
    try:
        all_docs = sorted(docs_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        if not all_docs:
            return
        lines = [f"# Resumen semanal de conocimiento — {agente}\n",
                 f"_Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"]
        for doc in all_docs:
            try:
                content = doc.read_text()
                # Primer bloque: título + primeras 3 líneas del resumen
                doc_lines = [l for l in content.splitlines() if l.strip()]
                lines.append("\n---\n\n")
                lines.extend([l + "\n" for l in doc_lines[:5]])
            except Exception:
                pass
        (kdir / "resumen-semanal.md").write_text("".join(lines))
    except Exception as e:
        log.warning(f"_rebuild_weekly_summary {agente}: {e}")


def _load_agent_knowledge(agente: str, max_chars: int = 8000) -> str:
    """
    Carga el conocimiento indexado del agente para inyectarlo como contexto.
    Devuelve string vacío si no hay conocimiento aún.
    """
    docs_dir = KNOWLEDGE_BASE / agente / "docs"
    if not docs_dir.exists():
        return ""
    # Los más recientes primero
    all_docs = sorted(docs_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not all_docs:
        return ""
    chunks = []
    total = 0
    for doc in all_docs[:KNOWLEDGE_CONTEXT_DOCS]:
        try:
            text = doc.read_text()[:2000]
            if total + len(text) > max_chars:
                break
            chunks.append(text)
            total += len(text)
        except Exception:
            pass
    if not chunks:
        return ""
    header = f"## Conocimiento indexado de {agente} (DOF/SJF)\n_Últimas publicaciones analizadas:_\n\n"
    return header + "\n\n---\n\n".join(chunks)


def _legal_indexar_todos(limite_por_agente: int = 20) -> str:
    """Indexa todos los agentes kawiil-*. Para el tool manual (con respuesta verbose)."""
    if not AGENTS_DIR.exists():
        return "No hay directorio de agentes."
    kawiil_files = [f for f in AGENTS_DIR.glob("kawiil-*.md")]
    if not kawiil_files:
        return "No hay agentes kawiil-* registrados."
    resultados = []
    for f in kawiil_files:
        agente = f.stem
        try:
            r = _legal_indexar_agente(agente, forzar=False, limite=limite_por_agente)
            resultados.append(f"{agente}: {r.splitlines()[0] if r else '?'}")
            log.info(f"legal_indexar_todos: {agente} → {r[:80]}")
        except Exception as e:
            resultados.append(f"{agente}: ERROR {e}")
            log.warning(f"legal_indexar_todos {agente}: {e}")
    return f"Indexación manual legal ({len(kawiil_files)} agentes):\n" + "\n".join(resultados)


# ── Prioridad: encola un tema/doc para indexación inmediata ───────────────────
def _legal_enqueue_priority(agente: str, tema: str, razon: str = ""):
    """Agrega a la cola de prioridad para que el bg-tick lo indexe primero."""
    try:
        LEGAL_BG_PRIORITY.parent.mkdir(parents=True, exist_ok=True)
        entry = {"agente": agente, "tema": tema, "razon": razon,
                 "ts": datetime.now().isoformat()}
        with LEGAL_BG_PRIORITY.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"_legal_enqueue_priority: {e}")


def _legal_drain_priority() -> list[dict]:
    """Lee y vacía la cola de prioridad. Devuelve lista de entries."""
    if not LEGAL_BG_PRIORITY.exists():
        return []
    try:
        lines = LEGAL_BG_PRIORITY.read_text().splitlines()
        LEGAL_BG_PRIORITY.write_text("")  # vaciar
        result = []
        for l in lines:
            l = l.strip()
            if l:
                try:
                    result.append(json.loads(l))
                except Exception:
                    pass
        return result
    except Exception:
        return []


def _legal_bg_next_agent() -> str | None:
    """
    Devuelve el siguiente agente kawiil-* en rotación round-robin.
    Guarda estado en LEGAL_BG_ROTATION para que persista entre ticks.
    """
    if not AGENTS_DIR.exists():
        return None
    kawiil = sorted(f.stem for f in AGENTS_DIR.glob("kawiil-*.md"))
    if not kawiil:
        return None
    rot = {}
    if LEGAL_BG_ROTATION.exists():
        try:
            rot = json.loads(LEGAL_BG_ROTATION.read_text())
        except Exception:
            pass
    last = rot.get("last_agent", "")
    try:
        idx = kawiil.index(last)
        nxt = kawiil[(idx + 1) % len(kawiil)]
    except ValueError:
        nxt = kawiil[0]
    LEGAL_BG_ROTATION.parent.mkdir(parents=True, exist_ok=True)
    LEGAL_BG_ROTATION.write_text(json.dumps(
        {"last_agent": nxt, "ts": datetime.now().isoformat()}))
    return nxt


def _legal_indexar_background_tick(limite: int = 10) -> str:
    """
    Un tick de indexación en background — silencioso, llamado cada N minutos
    por el scheduler. Procesa:
      1) Cola de prioridad primero (temas urgentes / leyes recién subidas)
      2) Siguiente agente en rotación round-robin, 10 docs más recientes sin indexar
    Devuelve string corto para el log.
    """
    resultados = []

    # 1) Prioridad: si hay entries en la cola, indexar esos agentes primero
    priority = _legal_drain_priority()
    for entry in priority[:3]:  # máx 3 prioridades por tick para no bloquear
        agente = entry.get("agente", "")
        if not agente or not _AGENT_NAME_RE.match(agente):
            continue
        if not (AGENTS_DIR / f"{agente}.md").exists():
            continue
        try:
            r = _legal_indexar_agente(agente, forzar=False, limite=limite, background=True)
            resultados.append(f"[prio] {r}")
            log.info(f"legal bg priority: {r}")
        except Exception as e:
            log.warning(f"legal bg priority {agente}: {e}")

    # 2) Rotación normal: siguiente agente en la lista
    agente = _legal_bg_next_agent()
    if agente:
        try:
            r = _legal_indexar_agente(agente, forzar=False, limite=limite, background=True)
            resultados.append(f"[bg] {r}")
            log.info(f"legal bg tick: {r}")
        except Exception as e:
            log.warning(f"legal bg tick {agente}: {e}")
            resultados.append(f"[bg] {agente}: error {e}")

    return " | ".join(resultados) if resultados else "bg: sin agentes"


# ===== Mac status / wake request =====
MAC_HEARTBEAT_FILE = HOME_OC / "state" / "mac_heartbeat.json"


def _mac_estado() -> str:
    """Lee el último heartbeat de la Mac y reporta estado."""
    if not MAC_HEARTBEAT_FILE.exists():
        return "❌ No hay heartbeat de la Mac. Probablemente nunca se configuró o nunca se ha conectado."
    try:
        data = json.loads(MAC_HEARTBEAT_FILE.read_text())
    except Exception as e:
        return f"❌ Heartbeat corrupto: {e}"

    ts_str = data.get("ts", "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_s = int((now - ts).total_seconds())
    except Exception:
        delta_s = -1

    online = (0 <= delta_s <= 90)  # heartbeat cada 30s, doy margen 3x
    status = "🟢 online" if online else "🔴 offline"

    parts = [f"💻 *Mac de Polo:* {status}"]
    if delta_s >= 0:
        if delta_s < 60:
            parts.append(f"  Último heartbeat: hace {delta_s}s")
        elif delta_s < 3600:
            parts.append(f"  Último heartbeat: hace {delta_s // 60} min")
        elif delta_s < 86400:
            parts.append(f"  Último heartbeat: hace {delta_s // 3600} h")
        else:
            parts.append(f"  Último heartbeat: hace {delta_s // 86400} días")
    parts.append(f"  Host: {data.get('hostname', '?')}")
    parts.append(f"  Uptime: {data.get('uptime', '?')}")
    batt = data.get("battery_pct")
    on_ac = data.get("on_ac_power", False)
    batt_state = data.get("battery_state", "?")
    if batt is not None:
        ac_str = "🔌 AC" if on_ac else "🔋 batería"
        parts.append(f"  {ac_str}: {batt}% ({batt_state})")
    parts.append(f"  SSID: {data.get('ssid', '?')}")
    parts.append(f"  IP pública: {data.get('public_ip', '?')}")
    return "\n".join(parts)


def _mac_wake_request(razon: str) -> str:
    """Evalúa estado de la Mac y manda Telegram pidiéndole a Polo que la prenda si aplica."""
    # Recupera estado
    if not MAC_HEARTBEAT_FILE.exists():
        msg = f"⚠️ Polo, no tengo señal de tu Mac (nunca he recibido heartbeat).\nLo necesito para: {razon}\n¿Está prendida y conectada a red?"
        _send_telegram_direct(msg)
        return f"(Telegram enviado — sin heartbeat previo. Razón: {razon})"

    try:
        data = json.loads(MAC_HEARTBEAT_FILE.read_text())
    except Exception:
        data = {}

    ts_str = data.get("ts", "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_s = int((now - ts).total_seconds())
    except Exception:
        delta_s = -1

    batt = data.get("battery_pct")
    on_ac = bool(data.get("on_ac_power", False))
    state = data.get("battery_state", "unknown")

    if 0 <= delta_s <= 90:
        return f"✅ Tu Mac YA está prendida y enviando heartbeat (hace {delta_s}s). No hace falta despertarla. Razón evaluada: {razon}"

    # Mac offline — decide mensaje según batería conocida
    if batt is not None and not on_ac:
        if batt <= 10:
            msg = f"🪫 Polo, tu Mac está apagada y la última lectura de batería fue {batt}%. Conéctala al cargador.\nLa necesito para: {razon}"
        else:
            msg = f"💤 Polo, tu Mac está apagada (batería {batt}%, sin AC). Préndela cuando puedas.\nLa necesito para: {razon}"
    elif batt is not None and on_ac:
        msg = f"💤 Polo, tu Mac está apagada pero la dejaste enchufada ({batt}%). ¿Puedes prenderla?\nLa necesito para: {razon}"
    else:
        msg = f"💤 Polo, no detecto tu Mac (último heartbeat hace {delta_s // 60 if delta_s>0 else '?'} min). ¿Puedes prenderla?\nLa necesito para: {razon}"

    _send_telegram_direct(msg)
    return f"(Mac offline — Telegram enviado. delta={delta_s}s, batt={batt}%, ac={on_ac}, state={state})"


def _send_telegram_direct(text: str) -> None:
    """Envía un mensaje directo por Telegram usando las creds del env."""
    try:
        env = load_env_file(ANTHROPIC_ENV_FILE)
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID") or env.get("TELEGRAM_ALLOWED_CHAT_ID")
        if not token or not chat_id:
            log.warning("_send_telegram_direct: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_ALLOWED_CHAT_ID")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        log.exception("_send_telegram_direct falló")


# ===== Browser headless (Playwright via subprocess) =====
BROWSER_RUNNER = Path("/opt/openclaw/scripts/browser_runner.py")
BROWSER_TIMEOUT_S = 90


def _browser_call(cmd: dict) -> str:
    """Llama browser_runner.py por subprocess con JSON via stdin."""
    if not BROWSER_RUNNER.exists():
        return f"❌ {BROWSER_RUNNER} no existe. Corre install-playwright.sh en Hetzner."
    try:
        r = subprocess.run(
            ["python3", str(BROWSER_RUNNER)],
            input=json.dumps(cmd),
            capture_output=True, text=True, timeout=BROWSER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return f"❌ Browser timeout ({BROWSER_TIMEOUT_S}s) ejecutando {cmd.get('cmd')}"
    except Exception as e:
        return f"❌ Browser subprocess falló: {e}"
    out = r.stdout.strip()
    if not out:
        err = r.stderr.strip()[:500]
        return f"❌ Browser sin output. stderr: {err}"
    try:
        data = json.loads(out)
    except Exception:
        return f"❌ Browser output no es JSON: {out[:500]}"
    return _browser_format(cmd.get("cmd"), data)


def _browser_format(action: str, data: dict) -> str:
    if not data.get("ok", False):
        code = data.get("code", "error")
        return f"❌ Browser [{code}]: {data.get('error', '?')}"
    if action == "navigate":
        return f"🌐 Abierto: {data.get('title', '(sin título)')} — {data.get('url')}"
    if action == "read":
        text = data.get("text", "")
        return f"📄 *{data.get('title', '')}* — {data.get('url')}\n\n{text}"
    if action == "screenshot":
        return f"📸 Screenshot guardado: {data.get('saved_to')}\n(base64 {len(data.get('image_base64', ''))} chars disponible internamente)"
    if action == "click":
        return f"🖱️ {data.get('msg', 'click OK')} — en {data.get('url')}"
    if action == "fill":
        return f"⌨️ {data.get('msg')}"
    if action == "press":
        return f"⌨️ {data.get('msg')}"
    if action == "wait_for":
        return f"⏳ {data.get('msg')}"
    if action == "history":
        items = data.get("items", [])
        if not items:
            return "📭 Historial vacío"
        out = [f"📜 *Últimas {len(items)} páginas:*"]
        for it in items:
            out.append(f"  • [{it.get('ts','?')[:19]}] {it.get('title','?')[:60]} — {it.get('url','')[:80]}")
        return "\n".join(out)
    if action == "reset_storage":
        return f"🧹 {data.get('msg')}"
    return json.dumps(data, ensure_ascii=False)[:1000]


def _browser_navegar(url: str, wait_until: str = "networkidle", wait_extra_ms: int = 0) -> str:
    return _browser_call({"cmd": "navigate", "url": url, "wait_until": wait_until, "wait_extra_ms": wait_extra_ms})


def _browser_leer() -> str:
    return _browser_call({"cmd": "read"})


def _browser_screenshot() -> str:
    return _browser_call({"cmd": "screenshot"})


def _browser_click(selector: str, by: str = "css") -> str:
    return _browser_call({"cmd": "click", "selector": selector, "by": by})


def _browser_escribir(selector: str, value: str) -> str:
    return _browser_call({"cmd": "fill", "selector": selector, "value": value})


def _browser_press(key: str = "Enter") -> str:
    return _browser_call({"cmd": "press", "key": key})


def _browser_esperar(selector: str, timeout_ms: int = 15000) -> str:
    return _browser_call({"cmd": "wait_for", "selector": selector, "timeout_ms": timeout_ms})


def _browser_historial(n: int = 20) -> str:
    return _browser_call({"cmd": "history", "n": n})


def _browser_reset() -> str:
    return _browser_call({"cmd": "reset_storage"})


def _browser_login(url: str, vault_item: str,
                   user_selector: str = "input[type=email], input[name=email], input[name=username], input#username",
                   pass_selector: str = "input[type=password]",
                   submit_selector: str = "button[type=submit]") -> str:
    """Compuesto: navega, busca creds en vault, llena form, submit."""
    # 1) Trae username + password del vault
    user_res = _vault_obtener(vault_item, "username", f"browser_login a {url}")
    if user_res.startswith("❌"):
        return f"Login abortado: {user_res}"
    pwd_res = _vault_obtener(vault_item, "password", f"browser_login a {url}")
    if pwd_res.startswith("❌"):
        return f"Login abortado: {pwd_res}"
    # _vault_obtener devuelve "✅ username: <valor>" — extrae
    username = user_res.split(":", 1)[-1].strip() if ":" in user_res else user_res
    password = pwd_res.split(":", 1)[-1].strip() if ":" in pwd_res else pwd_res
    # Remueve prefijos "✅ username =" si aparecieron
    for prefix in ("✅", "username", "password", "="):
        username = username.replace(prefix, "").strip()
        password = password.replace(prefix, "").strip()
    parts = [_browser_call({"cmd": "navigate", "url": url})]
    parts.append(_browser_call({"cmd": "fill", "selector": user_selector, "value": username}))
    parts.append(_browser_call({"cmd": "fill", "selector": pass_selector, "value": password}))
    parts.append(_browser_call({"cmd": "click", "selector": submit_selector}))
    parts.append("⏳ Esperando 3s a que la página cargue post-login...")
    import time as _t
    _t.sleep(3)
    parts.append(_browser_call({"cmd": "read"}))
    return "\n\n".join(parts)


# ===== Vault 1Password (op CLI) =====
def _op_env() -> dict:
    """Carga token de service account 1Password en env."""
    env = load_env_file(ANTHROPIC_ENV_FILE)
    token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN") or env.get("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        return {}
    try:
        op_env_file = load_env_file(Path("/opt/openclaw/credentials/1password.env"))
        token = op_env_file.get("OP_SERVICE_ACCOUNT_TOKEN", token)
    except Exception:
        pass
    return {**os.environ, "OP_SERVICE_ACCOUNT_TOKEN": token, "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}


def _vault_op_buscar(query: str, vault: str = "") -> str:
    env = _op_env()
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return "❌ 1Password no configurado. Corre install-1password.sh en Hetzner."
    cmd = ["op", "item", "list", "--format=json"]
    if vault:
        cmd.extend(["--vault", vault])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=20)
        if r.returncode != 0:
            return f"❌ op item list falló: {r.stderr[:200]}"
        items = json.loads(r.stdout or "[]")
    except Exception as e:
        return f"❌ Error: {e}"
    # Filtra por query (substring case-insensitive en title)
    q = query.lower()
    matches = [it for it in items if q in (it.get("title", "") or "").lower()
               or any(q in (u.get("href", "") or "").lower() for u in it.get("urls", []) or [])]
    if not matches:
        return f"📭 0 items en 1Password para '{query}'"
    VAULT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with VAULT_AUDIT_LOG.open("a") as f:
        f.write(f"[{datetime.now().isoformat()}] op_search query={query}\n")
    out = [f"🔐 *{len(matches)} items en 1Password para '{query}':*"]
    for it in matches[:20]:
        title = it.get("title", "?")
        item_id = it.get("id", "?")
        vault_n = (it.get("vault") or {}).get("name", "?")
        urls = it.get("urls") or []
        url = urls[0].get("href", "") if urls else ""
        out.append(f"  • `{item_id[:8]}…` — *{title}* (vault: {vault_n})" + (f"\n    {url}" if url else ""))
    return "\n".join(out)


def _vault_op_obtener(item: str, campo: str, razon: str) -> str:
    if campo not in ("username", "password", "otp", "url", "notesPlain"):
        return f"❌ Campo inválido '{campo}'"
    env = _op_env()
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return "❌ 1Password no configurado."
    VAULT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with VAULT_AUDIT_LOG.open("a") as f:
        f.write(f"[{datetime.now().isoformat()}] op_get item={item[:50]} campo={campo} razon={razon[:100]}\n")
    try:
        r = subprocess.run(["op", "item", "get", item, "--fields", campo],
                           capture_output=True, text=True, env=env, timeout=15)
        if r.returncode != 0:
            return f"❌ op item get falló: {r.stderr[:200]}"
        val = r.stdout.strip()
        if not val:
            return f"⚠️ Item '{item}' no tiene `{campo}`"
        if campo == "password":
            masked = val[0] + "*" * max(0, len(val) - 2) + val[-1] if len(val) >= 2 else "***"
            return f"🔐 password recuperado (longitud {len(val)}, masked: `{masked}`) — disponible internamente, NO se imprime en chat."
        return f"🔐 {campo}: `{val}`"
    except Exception as e:
        return f"❌ Error: {e}"


# ===== Vault Bitwarden =====
BW_ENV_FILE = Path("/opt/openclaw/credentials/bitwarden.env")
BW_SESSION_FILE = Path("/opt/openclaw/credentials/bitwarden-session.env")
VAULT_AUDIT_LOG = HOME_OC / "logs" / "vault-access.log"


def _bw_session() -> tuple[str | None, str | None]:
    """Devuelve (session_key, error). Refresca si vence."""
    if not BW_ENV_FILE.exists():
        return None, "❌ Bitwarden no instalado. Corre install-bitwarden.sh en Hetzner."
    bw_env = load_env_file(BW_ENV_FILE)
    # Intenta usar la session guardada
    if BW_SESSION_FILE.exists():
        sess_env = load_env_file(BW_SESSION_FILE)
        session = sess_env.get("BW_SESSION", "")
        if session:
            # Verifica que sigue válida con `bw status`
            try:
                r = subprocess.run(["bw", "status"], capture_output=True, text=True,
                                   env={**os.environ, "BW_SESSION": session, "PATH": os.environ.get("PATH", "")},
                                   timeout=10)
                if r.returncode == 0:
                    try:
                        st = json.loads(r.stdout)
                        if st.get("status") == "unlocked":
                            return session, None
                    except Exception:
                        pass
            except Exception:
                pass
    # Re-unlock
    pwd = bw_env.get("BW_PASSWORD")
    if not pwd:
        return None, "❌ Falta BW_PASSWORD en bitwarden.env"
    try:
        env_unlock = {**os.environ, "BW_PASSWORD": pwd, "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
        r = subprocess.run(["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                           capture_output=True, text=True, env=env_unlock, timeout=20)
        if r.returncode != 0 or not r.stdout.strip():
            return None, f"❌ bw unlock falló: {r.stderr[:200]}"
        session = r.stdout.strip()
        BW_SESSION_FILE.write_text(f"BW_SESSION={session}\n")
        try:
            os.chmod(BW_SESSION_FILE, 0o600)
        except Exception:
            pass
        return session, None
    except Exception as e:
        return None, f"❌ bw unlock excepción: {e}"


def _vault_audit(action: str, item: str, campo: str, razon: str):
    VAULT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with VAULT_AUDIT_LOG.open("a") as f:
            f.write(f"[{datetime.now().isoformat()}] {action} item={item[:50]} campo={campo} razon={razon[:100]}\n")
    except Exception:
        pass


def _vault_buscar(query: str) -> str:
    session, err = _bw_session()
    if err:
        return err
    _vault_audit("search", query, "-", f"buscar:{query}")
    try:
        r = subprocess.run(["bw", "list", "items", "--search", query],
                           capture_output=True, text=True,
                           env={**os.environ, "BW_SESSION": session, "PATH": os.environ.get("PATH", "")},
                           timeout=15)
        if r.returncode != 0:
            return f"❌ bw list falló: {r.stderr[:200]}"
        items = json.loads(r.stdout or "[]")
    except Exception as e:
        return f"❌ Búsqueda vault falló: {e}"
    if not items:
        return f"📭 0 items para '{query}'"
    out = [f"🔐 *{len(items)} items para '{query}':*"]
    for it in items[:20]:
        name = it.get("name", "?")
        item_id = it.get("id", "?")
        login = it.get("login") or {}
        username = login.get("username", "")
        uris = login.get("uris") or []
        url = uris[0].get("uri", "") if uris else ""
        out.append(f"  • `{item_id[:8]}…` — *{name}*" + (f"  ({username})" if username else "") + (f"\n    {url}" if url else ""))
    return "\n".join(out)


def _vault_obtener(item_id: str, campo: str, razon: str) -> str:
    if campo not in ("username", "password", "totp", "uri", "notes"):
        return f"❌ Campo inválido '{campo}' (usa username, password, totp, uri, notes)"
    session, err = _bw_session()
    if err:
        return err
    _vault_audit("get", item_id, campo, razon)
    # Mapear campo → flag de bw get
    if campo == "totp":
        cmd = ["bw", "get", "totp", item_id]
    elif campo == "password":
        cmd = ["bw", "get", "password", item_id]
    elif campo == "username":
        cmd = ["bw", "get", "username", item_id]
    elif campo == "uri":
        cmd = ["bw", "get", "uri", item_id]
    elif campo == "notes":
        cmd = ["bw", "get", "notes", item_id]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           env={**os.environ, "BW_SESSION": session, "PATH": os.environ.get("PATH", "")},
                           timeout=15)
        if r.returncode != 0:
            return f"❌ bw get falló: {r.stderr[:200]}"
        val = r.stdout.strip()
        if not val:
            return f"⚠️ Item `{item_id}` no tiene `{campo}` configurado"
        # NUNCA muestres password en chat — solo confirma + da el primer/último char
        if campo == "password":
            masked = val[0] + "*" * max(0, len(val) - 2) + val[-1] if len(val) >= 2 else "***"
            return f"🔐 password recuperado (longitud {len(val)}, masked: `{masked}`) — disponible internamente, NO se imprime en chat."
        return f"🔐 {campo}: `{val}`"
    except Exception as e:
        return f"❌ vault get excepción: {e}"


# ===== Kawiil Central (Vercel + Supabase, producción) =====
# NO se clona el repo. Louis opera directo contra la BD de producción y la
# URL pública de Vercel para verificar disponibilidad.
KAWIIL_CENTRAL_AUDIT_LOG = HOME_OC / "logs" / "kawiil-central-ops.log"


def _kawiil_central_creds() -> tuple[str, str, str, str]:
    """Devuelve (vercel_url, supabase_url, supabase_key, database_url). Soporta envs nuevas KAWIIL_CENTRAL_* y legacy KAWIIL_OS_*."""
    env = load_env_file(ANTHROPIC_ENV_FILE)
    vercel_url = (
        os.environ.get("KAWIIL_CENTRAL_VERCEL_URL")
        or env.get("KAWIIL_CENTRAL_VERCEL_URL")
        or "https://www.kawiil-central.mx"
    )
    sup_url = (
        os.environ.get("KAWIIL_CENTRAL_SUPABASE_URL")
        or env.get("KAWIIL_CENTRAL_SUPABASE_URL")
        or os.environ.get("KAWIIL_OS_SUPABASE_URL")  # legacy
        or env.get("KAWIIL_OS_SUPABASE_URL")
        or ""
    )
    sup_key = (
        os.environ.get("KAWIIL_CENTRAL_SUPABASE_KEY")
        or env.get("KAWIIL_CENTRAL_SUPABASE_KEY")
        or os.environ.get("KAWIIL_OS_SUPABASE_KEY")
        or env.get("KAWIIL_OS_SUPABASE_KEY")
        or ""
    )
    db_url = (
        os.environ.get("KAWIIL_CENTRAL_DATABASE_URL")
        or env.get("KAWIIL_CENTRAL_DATABASE_URL")
        or os.environ.get("KAWIIL_OS_DATABASE_URL")
        or env.get("KAWIIL_OS_DATABASE_URL")
        or ""
    )
    return vercel_url, sup_url, sup_key, db_url


def _kawiil_central_audit(razon: str, sql: str) -> None:
    KAWIIL_CENTRAL_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with KAWIIL_CENTRAL_AUDIT_LOG.open("a") as f:
            f.write(f"[{datetime.now().isoformat()}] razon={razon}\n  SQL: {sql[:500]}\n")
    except Exception:
        log.exception("audit log falló")


def _kawiil_central_pg():
    """Devuelve (conn, err) — psycopg2 contra DATABASE_URL."""
    _, _, _, db_url = _kawiil_central_creds()
    if not db_url:
        return None, "❌ Falta KAWIIL_CENTRAL_DATABASE_URL — corre setup-kawiil-central-creds.sh en Hetzner."
    try:
        import psycopg2  # type: ignore
    except ImportError:
        return None, "❌ psycopg2 no instalado. En Hetzner: `pip3 install --break-system-packages psycopg2-binary`"
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        return conn, None
    except Exception as e:
        return None, f"❌ Postgres conectividad: {e}"


def _kawiil_central_query(sql: str, razon: str) -> str:
    """Ejecuta SQL con guardrails. Resultado tabular formateado."""
    sql_upper = sql.strip().upper()
    blocked = ("DROP ", "TRUNCATE ", "ALTER TABLE ", "ALTER COLUMN ", "GRANT ", "REVOKE ", "CREATE TABLE ", "CREATE SCHEMA ")
    for kw in blocked:
        if kw in sql_upper:
            return f"🔒 SQL bloqueado — contiene `{kw.strip()}`. Cambios de schema viven en migraciones de la app (Supabase Studio / CLI)."
    if (sql_upper.startswith("DELETE ") or " DELETE FROM " in sql_upper):
        if "DELETE_CONFIRM" not in razon.upper():
            return "🔒 DELETE bloqueado. Si realmente quieres borrar, incluye `DELETE_CONFIRM` en `razon` y pide confirmación a Polo explícita ANTES de volver a llamar."
    conn, err = _kawiil_central_pg()
    if err:
        return err
    _kawiil_central_audit(razon, sql)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql)
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(100)
            out = [" | ".join(cols)]
            out.append(" | ".join("-" * max(3, len(c)) for c in cols))
            for r in rows:
                out.append(" | ".join(("" if v is None else str(v))[:80] for v in r))
            conn.close()
            return f"✅ {len(rows)} filas (max 100):\n```\n" + "\n".join(out) + "\n```"
        else:
            rc = cur.rowcount
            conn.close()
            return f"✅ Ejecutado. Filas afectadas: {rc}"
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return f"❌ Error SQL: {e}"


def _kawiil_central_estado() -> str:
    vercel_url, sup_url, sup_key, db_url = _kawiil_central_creds()
    parts = ["🌐 *Kawiil Central* (producción)"]
    parts.append(f"  Vercel: {vercel_url}")
    parts.append(f"  Supabase URL: {'✓' if sup_url else '✗ (falta — corre setup-kawiil-central-creds.sh)'}")
    parts.append(f"  Supabase KEY: {'✓' if sup_key else '✗ (falta)'}")
    parts.append(f"  Database URL: {'✓' if db_url else '✗ (falta — sin esto no hay SQL)'}")
    # Ping Vercel
    try:
        req = urllib.request.Request(vercel_url, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            parts.append(f"  Vercel ping: HTTP {resp.status} ✓")
    except Exception as e:
        parts.append(f"  Vercel ping: ✗ ({type(e).__name__})")
    # Conteos en BD
    if db_url:
        conn, err = _kawiil_central_pg()
        if err:
            parts.append(f"  Postgres: {err}")
        else:
            try:
                cur = conn.cursor()
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
                tables = [r[0] for r in cur.fetchall()]
                parts.append(f"  Tablas (public): {len(tables)} — " + ", ".join(tables[:12]) + (" …" if len(tables) > 12 else ""))
                # Intenta contar tasks/projects (nombres comunes)
                for guess in ("tasks", "projects", "task", "project", "todos", "issues"):
                    if guess in tables:
                        try:
                            cur.execute(f"SELECT count(*) FROM public.{guess}")
                            n = cur.fetchone()[0]
                            parts.append(f"  • {guess}: {n}")
                        except Exception:
                            pass
                conn.close()
            except Exception as e:
                parts.append(f"  Postgres query: ✗ ({e})")
                try:
                    conn.close()
                except Exception:
                    pass
    return "\n".join(parts)


def _kawiil_central_tablas() -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT t.table_name,
                   COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname = t.table_name LIMIT 1), 0) AS approx_rows
            FROM information_schema.tables t
            WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_name
        """)
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return "📂 No hay tablas en schema `public`."
        out = ["📂 *Tablas de kawiil-central (schema public):*"]
        for name, approx in rows:
            out.append(f"  • `{name}` (~{approx} filas)")
        return "\n".join(out)
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return f"❌ Error: {e}"


def _kawiil_central_describir(tabla: str) -> str:
    if not tabla.replace("_", "").isalnum():
        return "❌ Nombre de tabla inválido."
    conn, err = _kawiil_central_pg()
    if err:
        return err
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (tabla,))
        cols = cur.fetchall()
        if not cols:
            conn.close()
            return f"❌ Tabla `{tabla}` no existe en public."
        out = [f"📋 *Schema `public.{tabla}`:*"]
        for name, dtype, nullable, default in cols:
            null_s = "NULL" if nullable == "YES" else "NOT NULL"
            def_s = f" DEFAULT {default}" if default else ""
            out.append(f"  • `{name}` {dtype} {null_s}{def_s}")
        # Foreign keys
        cur.execute("""
            SELECT kcu.column_name, ccu.table_name AS foreign_table, ccu.column_name AS foreign_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s
        """, (tabla,))
        fks = cur.fetchall()
        if fks:
            out.append("\n  Foreign keys:")
            for col, ftable, fcol in fks:
                out.append(f"    {col} → {ftable}.{fcol}")
        conn.close()
        return "\n".join(out)
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return f"❌ Error describiendo: {e}"


def _kawiil_central_find_table(conn, candidates: tuple) -> str | None:
    """Devuelve el primer nombre de tabla que exista en public."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    existing = {r[0] for r in cur.fetchall()}
    for c in candidates:
        if c in existing:
            return c
    return None


def _kawiil_central_tareas(estado: str = "", proyecto_id: str = "",
                            asignado_a: str = "", limit: int = 20) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("tasks", "task", "todos", "issues", "tareas"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de tareas (probé: tasks, task, todos, issues, tareas). Usa `kawiil_central_tablas` para ver schema y luego `kawiil_central_query` con SQL directo."
    cur = conn.cursor()
    # Detecta columnas existentes
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols = {r[0] for r in cur.fetchall()}
    where = []
    params = []
    if estado and ("status" in cols or "state" in cols or "estado" in cols):
        col = "status" if "status" in cols else ("state" if "state" in cols else "estado")
        where.append(f"{col} = %s")
        params.append(estado)
    if proyecto_id and ("project_id" in cols or "proyecto_id" in cols):
        col = "project_id" if "project_id" in cols else "proyecto_id"
        where.append(f"{col}::text = %s")
        params.append(proyecto_id)
    if asignado_a:
        for col in ("assignee_id", "assigned_to", "asignado_a", "owner_id", "responsible"):
            if col in cols:
                where.append(f"{col}::text = %s")
                params.append(asignado_a)
                break
    sql = f"SELECT * FROM public.{tabla}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order_col = "updated_at" if "updated_at" in cols else ("created_at" if "created_at" in cols else "id")
    sql += f" ORDER BY {order_col} DESC LIMIT %s"
    params.append(limit)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        conn.close()
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ Query falló: {e}\n  SQL probado: {sql}"
    if not rows:
        return f"📭 0 tareas en `{tabla}` con esos filtros."
    out = [f"📋 *{len(rows)} tareas* (de `{tabla}`, max {limit}):"]
    # Muestra columnas más relevantes
    priority_cols = ["id", "title", "titulo", "name", "status", "estado", "priority", "prioridad",
                     "assignee_id", "asignado_a", "due_date", "deadline", "project_id", "proyecto_id"]
    show_cols = [c for c in priority_cols if c in colnames]
    if not show_cols:
        show_cols = colnames[:6]
    for r in rows:
        row_dict = dict(zip(colnames, r))
        parts = []
        for c in show_cols:
            v = row_dict.get(c)
            if v is not None and v != "":
                parts.append(f"{c}={v}")
        out.append("  • " + ", ".join(parts))
    return "\n".join(out)


def _kawiil_central_tareas_proximas(dias: int = 7, estado_excluir: str = "hecho",
                                     asignado_a: str = "") -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("tasks", "task", "tareas"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de tareas."
    cur = conn.cursor()
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols_info = {r[0]: r[1] for r in cur.fetchall()}
    cols = set(cols_info.keys())
    # Detecta columna deadline
    deadline_col = None
    for c in ("due_date", "deadline", "fecha_limite", "fecha_vencimiento", "fecha_due", "expires_at"):
        if c in cols:
            deadline_col = c; break
    if not deadline_col:
        conn.close()
        return f"❌ No encontré columna de deadline en `{tabla}` (probé due_date, deadline, fecha_limite, fecha_vencimiento, expires_at). Columnas: {sorted(cols)}"
    estado_col = next((c for c in ("status", "estado", "state") if c in cols), None)
    titulo_col = next((c for c in ("title", "titulo", "name", "nombre") if c in cols), "id")
    asign_col = next((c for c in ("assignee_id", "assigned_to", "asignado_a", "owner_id", "responsible") if c in cols), None)
    where = []
    params = []
    if dias >= 0:
        where.append(f"{deadline_col}::date <= (CURRENT_DATE + INTERVAL '{dias} days')")
        where.append(f"{deadline_col} IS NOT NULL")
    else:
        # Vencidas: deadline en el pasado
        where.append(f"{deadline_col}::date < CURRENT_DATE")
        where.append(f"{deadline_col} IS NOT NULL")
    if estado_excluir and estado_col:
        where.append(f"({estado_col} IS NULL OR {estado_col} != %s)")
        params.append(estado_excluir)
    if asignado_a and asign_col:
        where.append(f"{asign_col}::text = %s")
        params.append(asignado_a)
    sql = f"SELECT * FROM public.{tabla} WHERE " + " AND ".join(where) + f" ORDER BY {deadline_col} ASC NULLS LAST LIMIT 50"
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        conn.close()
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ Query falló: {e}\n  SQL: {sql}"
    if not rows:
        rango = f"vencidas (dias={dias})" if dias < 0 else f"próximos {dias} días"
        return f"✅ 0 tareas {rango} (excluyendo estado='{estado_excluir}')"
    from datetime import date as _date
    today = _date.today()
    out = []
    out.append(f"📋 *{len(rows)} tareas* (ventana {dias}d, excluye estado='{estado_excluir}'):")
    for r in rows:
        rd = dict(zip(colnames, r))
        dl = rd.get(deadline_col)
        title = rd.get(titulo_col, str(rd.get("id", "?")))
        status = rd.get(estado_col, "?") if estado_col else "?"
        marker = ""
        if dl:
            try:
                dl_date = dl if hasattr(dl, "date") and not callable(dl.date) else (dl.date() if hasattr(dl, "date") else dl)
                if hasattr(dl_date, "date"):
                    dl_date = dl_date.date()
                diff = (dl_date - today).days
                if diff < 0:
                    marker = f" 🔴 VENCIDA ({-diff}d atrás)"
                elif diff == 0:
                    marker = " 🟠 HOY"
                elif diff <= 3:
                    marker = f" 🟡 en {diff}d"
                else:
                    marker = f" ⚪ en {diff}d"
            except Exception:
                marker = ""
        out.append(f"  • [{status}] *{str(title)[:80]}*{marker}")
        out.append(f"    id=`{rd.get('id', '?')}`  deadline={dl}")
    return "\n".join(out)


def _kawiil_central_tarea_detalle(tarea_id: str) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla_tasks = _kawiil_central_find_table(conn, ("tasks", "task", "tareas"))
    tabla_comments = _kawiil_central_find_table(conn, ("task_comments", "comments", "task_updates", "updates"))
    tabla_projects = _kawiil_central_find_table(conn, ("projects", "project", "proyectos"))
    tabla_clients = _kawiil_central_find_table(conn, ("clients", "client", "clientes"))
    if not tabla_tasks:
        conn.close()
        return "❌ No encontré tabla de tareas."
    cur = conn.cursor()
    # Tarea base
    try:
        cur.execute(f"SELECT * FROM public.{tabla_tasks} WHERE id::text = %s LIMIT 1", (str(tarea_id),))
        row = cur.fetchone()
        if not row:
            conn.close()
            return f"❌ Tarea `{tarea_id}` no existe en `{tabla_tasks}`."
        colnames = [d[0] for d in cur.description]
        task = dict(zip(colnames, row))
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ Query tarea falló: {e}"

    out = [f"📋 *Tarea {tarea_id}*"]
    # Pretty print
    skip_cols = ("created_at", "updated_at", "deleted_at")
    for k, v in task.items():
        if k in skip_cols or v is None or v == "":
            continue
        v_str = str(v)
        if len(v_str) > 200:
            v_str = v_str[:200] + "…"
        out.append(f"  *{k}:* {v_str}")
    if task.get("created_at"):
        out.append(f"  _creada: {task['created_at']}_")
    if task.get("updated_at"):
        out.append(f"  _actualizada: {task['updated_at']}_")

    # Proyecto asociado
    proj_id = task.get("project_id") or task.get("proyecto_id")
    if proj_id and tabla_projects:
        try:
            cur.execute(f"SELECT * FROM public.{tabla_projects} WHERE id::text = %s LIMIT 1", (str(proj_id),))
            r = cur.fetchone()
            if r:
                pcols = [d[0] for d in cur.description]
                proj = dict(zip(pcols, r))
                pname = proj.get("name") or proj.get("nombre") or proj.get("title") or "?"
                out.append(f"\n📁 *Proyecto:* {pname}  (id=`{proj_id}`)")
                # Cliente del proyecto
                cli_id = proj.get("client_id") or proj.get("cliente_id")
                if cli_id and tabla_clients:
                    cur.execute(f"SELECT * FROM public.{tabla_clients} WHERE id::text = %s LIMIT 1", (str(cli_id),))
                    r2 = cur.fetchone()
                    if r2:
                        cli = dict(zip([d[0] for d in cur.description], r2))
                        cname = cli.get("name") or cli.get("nombre") or cli.get("company") or "?"
                        out.append(f"👤 *Cliente:* {cname}  (id=`{cli_id}`)")
        except Exception as e:
            out.append(f"  (proyecto/cliente lookup falló: {e})")

    # Comentarios
    if tabla_comments:
        try:
            # Detecta FK
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla_comments,))
            ccols = {r[0] for r in cur.fetchall()}
            fk = next((c for c in ("task_id", "tarea_id") if c in ccols), None)
            if fk:
                order = "created_at" if "created_at" in ccols else "id"
                cur.execute(f"SELECT * FROM public.{tabla_comments} WHERE {fk}::text = %s ORDER BY {order} ASC LIMIT 30", (str(tarea_id),))
                comments = cur.fetchall()
                if comments:
                    cnames = [d[0] for d in cur.description]
                    out.append(f"\n💬 *Comentarios ({len(comments)}):*")
                    content_col = next((c for c in ("content", "body", "texto", "message", "comentario") if c in ccols), None)
                    author_col = next((c for c in ("author_id", "user_id", "autor", "created_by") if c in ccols), None)
                    for c_row in comments[-10:]:  # últimos 10
                        cd = dict(zip(cnames, c_row))
                        text = (cd.get(content_col) or "")[:300] if content_col else json.dumps({k:v for k,v in cd.items() if k not in ("id", "created_at")}, default=str)[:300]
                        ts = cd.get("created_at", "")
                        author = cd.get(author_col, "?") if author_col else "?"
                        out.append(f"  • [{ts}] {author}: {text}")
        except Exception as e:
            out.append(f"  (comentarios lookup falló: {e})")

    conn.close()
    return "\n".join(out)


def _kawiil_central_pipeline(limit_por_etapa: int = 5) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("leads", "lead", "prospectos", "opportunities"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de leads."
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols = {r[0] for r in cur.fetchall()}
    estado_col = next((c for c in ("status", "stage", "etapa", "estado") if c in cols), None)
    if not estado_col:
        conn.close()
        return f"❌ No encontré columna de etapa/status en `{tabla}`. Columnas: {sorted(cols)}"
    name_col = next((c for c in ("name", "nombre", "title", "company") if c in cols), "id")
    amount_col = next((c for c in ("amount", "value", "monto", "valor", "deal_value") if c in cols), None)
    try:
        # Conteo por etapa
        cur.execute(f"SELECT {estado_col}, count(*) FROM public.{tabla} GROUP BY {estado_col} ORDER BY count(*) DESC")
        stages = cur.fetchall()
        out = [f"📊 *Pipeline de leads ({tabla}):*"]
        for stage, n in stages:
            line = f"\n  *{stage or '(sin etapa)'}: {n} leads*"
            if amount_col:
                cur.execute(f"SELECT COALESCE(sum({amount_col}), 0) FROM public.{tabla} WHERE {estado_col} = %s OR ({estado_col} IS NULL AND %s IS NULL)", (stage, stage))
                total = cur.fetchone()[0]
                line += f"  (valor: {total})"
            out.append(line)
            # Top N en esta etapa
            cur.execute(f"SELECT id, {name_col} FROM public.{tabla} WHERE {estado_col} = %s OR ({estado_col} IS NULL AND %s IS NULL) ORDER BY id DESC LIMIT %s",
                       (stage, stage, limit_por_etapa))
            items = cur.fetchall()
            for it in items:
                out.append(f"    • `{str(it[0])[:8]}…` {it[1]}")
        conn.close()
        return "\n".join(out)
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ Pipeline query falló: {e}"


def _kawiil_central_clientes(query: str = "", limit: int = 30) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("clients", "client", "clientes", "companies"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de clientes."
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols = {r[0] for r in cur.fetchall()}
    name_col = next((c for c in ("name", "nombre", "company", "razon_social") if c in cols), "id")
    where = ""
    params = []
    if query:
        where = f" WHERE {name_col} ILIKE %s"
        params.append(f"%{query}%")
    sql = f"SELECT id, {name_col} FROM public.{tabla}{where} ORDER BY {name_col} LIMIT %s"
    params.append(limit)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            conn.close()
            return f"📭 0 clientes para '{query}'"
        out = [f"👥 *{len(rows)} clientes*" + (f" para '{query}'" if query else "") + ":"]
        # Count tareas/proyectos por cliente
        tabla_p = _kawiil_central_find_table(conn, ("projects", "proyectos"))
        tabla_t = _kawiil_central_find_table(conn, ("tasks", "tareas"))
        for cid, cname in rows:
            line = f"  • `{str(cid)[:8]}…` *{cname}*"
            try:
                if tabla_p:
                    cur.execute(f"SELECT count(*) FROM public.{tabla_p} WHERE client_id::text = %s OR cliente_id::text = %s", (str(cid), str(cid)))
                    n = cur.fetchone()[0]
                    if n > 0: line += f"  📁{n}"
            except Exception:
                pass
            out.append(line)
        conn.close()
        return "\n".join(out)
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ Query falló: {e}"


def _kawiil_central_proyectos(estado: str = "", limit: int = 20) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("projects", "project", "proyectos"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de proyectos. Usa `kawiil_central_tablas` y `kawiil_central_query`."
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols = {r[0] for r in cur.fetchall()}
    where = []
    params = []
    if estado and ("status" in cols or "state" in cols or "estado" in cols):
        col = "status" if "status" in cols else ("state" if "state" in cols else "estado")
        where.append(f"{col} = %s")
        params.append(estado)
    sql = f"SELECT * FROM public.{tabla}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order_col = "updated_at" if "updated_at" in cols else ("created_at" if "created_at" in cols else "id")
    sql += f" ORDER BY {order_col} DESC LIMIT %s"
    params.append(limit)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        conn.close()
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ Query falló: {e}\n  SQL: {sql}"
    if not rows:
        return f"📭 0 proyectos en `{tabla}`."
    out = [f"📁 *{len(rows)} proyectos* (de `{tabla}`):"]
    priority_cols = ["id", "name", "nombre", "title", "status", "estado", "owner_id", "created_at"]
    show_cols = [c for c in priority_cols if c in colnames] or colnames[:5]
    for r in rows:
        row_dict = dict(zip(colnames, r))
        parts = []
        for c in show_cols:
            v = row_dict.get(c)
            if v is not None and v != "":
                parts.append(f"{c}={v}")
        out.append("  • " + ", ".join(parts))
    return "\n".join(out)


def _kawiil_central_crear_tarea(titulo: str, proyecto_id: str, descripcion: str = "",
                                 asignado_a: str = "", prioridad: str = "",
                                 deadline: str = "", campos_extra: dict = None) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("tasks", "task", "tareas", "todos", "issues"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de tareas."
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols = {r[0] for r in cur.fetchall()}
    # Map flexible
    data = {}
    if "title" in cols: data["title"] = titulo
    elif "titulo" in cols: data["titulo"] = titulo
    elif "name" in cols: data["name"] = titulo
    if proyecto_id:
        if "project_id" in cols: data["project_id"] = proyecto_id
        elif "proyecto_id" in cols: data["proyecto_id"] = proyecto_id
    if descripcion:
        for c in ("description", "descripcion", "body", "details"):
            if c in cols:
                data[c] = descripcion; break
    if asignado_a:
        for c in ("assignee_id", "assigned_to", "asignado_a", "owner_id", "responsible"):
            if c in cols:
                data[c] = asignado_a; break
    if prioridad:
        for c in ("priority", "prioridad"):
            if c in cols:
                data[c] = prioridad; break
    if deadline:
        for c in ("due_date", "deadline", "fecha_limite"):
            if c in cols:
                data[c] = deadline; break
    if campos_extra:
        for k, v in campos_extra.items():
            if k in cols:
                data[k] = v
    if not data:
        conn.close()
        return f"❌ No mapeé ningún campo a la tabla `{tabla}`. Columnas: {sorted(cols)}. Usa kawiil_central_query con INSERT directo."
    cols_sql = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql = f"INSERT INTO public.{tabla} ({cols_sql}) VALUES ({placeholders}) RETURNING id"
    try:
        conn.autocommit = True
        cur.execute(sql, list(data.values()))
        new_id = cur.fetchone()[0]
        _kawiil_central_audit(f"crear_tarea: {titulo}", sql + " :: " + json.dumps(data, default=str))
        conn.close()
        return f"✅ Tarea creada en `{tabla}` con id `{new_id}`\n  Campos: {data}"
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ INSERT falló: {e}\n  SQL: {sql}\n  Data: {data}"


def _kawiil_central_actualizar_tarea(tarea_id: str, cambios: dict) -> str:
    if not cambios:
        return "❌ `cambios` vacío — nada que actualizar."
    conn, err = _kawiil_central_pg()
    if err:
        return err
    tabla = _kawiil_central_find_table(conn, ("tasks", "task", "tareas", "todos", "issues"))
    if not tabla:
        conn.close()
        return "❌ No encontré tabla de tareas."
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
    cols = {r[0] for r in cur.fetchall()}
    # Filtra cambios al subset que existe
    valid = {k: v for k, v in cambios.items() if k in cols}
    if not valid:
        conn.close()
        return f"❌ Ninguno de los campos pasados existe en `{tabla}`. Columnas: {sorted(cols)}"
    if "updated_at" in cols and "updated_at" not in valid:
        valid["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = %s" for k in valid.keys())
    sql = f"UPDATE public.{tabla} SET {set_clause} WHERE id::text = %s RETURNING id"
    try:
        conn.autocommit = True
        cur.execute(sql, list(valid.values()) + [str(tarea_id)])
        row = cur.fetchone()
        _kawiil_central_audit(f"actualizar_tarea {tarea_id}", sql + " :: " + json.dumps(valid, default=str))
        conn.close()
        if not row:
            return f"⚠️ UPDATE no afectó filas — ¿id `{tarea_id}` existe?"
        return f"✅ Tarea `{tarea_id}` actualizada en `{tabla}`. Cambios: {valid}"
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return f"❌ UPDATE falló: {e}"


def _kawiil_central_avance(tarea_id: str, texto: str, porcentaje: int = None) -> str:
    conn, err = _kawiil_central_pg()
    if err:
        return err
    # Busca tabla de comentarios/updates
    tabla = _kawiil_central_find_table(conn, ("task_updates", "task_comments", "comments", "updates", "avances", "task_avances"))
    cur = conn.cursor()
    if tabla:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla,))
        cols = {r[0] for r in cur.fetchall()}
        data = {}
        for c in ("task_id", "tarea_id"):
            if c in cols:
                data[c] = tarea_id; break
        for c in ("content", "texto", "body", "message", "comentario"):
            if c in cols:
                data[c] = texto; break
        if porcentaje is not None:
            for c in ("progress", "porcentaje", "percent", "completion"):
                if c in cols:
                    data[c] = porcentaje; break
        if not data:
            conn.close()
            return f"⚠️ Encontré `{tabla}` pero no mapeé campos. Columnas: {sorted(cols)}"
        cols_sql = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO public.{tabla} ({cols_sql}) VALUES ({placeholders}) RETURNING id"
        try:
            conn.autocommit = True
            cur.execute(sql, list(data.values()))
            new_id = cur.fetchone()[0]
            _kawiil_central_audit(f"avance en {tarea_id}", sql)
            conn.close()
            return f"✅ Avance registrado en `{tabla}` (id `{new_id}`) sobre tarea `{tarea_id}`."
        except Exception as e:
            try: conn.close()
            except Exception: pass
            return f"❌ INSERT avance falló: {e}"
    # Fallback: si la tarea tiene un campo notes/description acumulable
    tabla_t = _kawiil_central_find_table(conn, ("tasks", "task", "tareas"))
    if tabla_t:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (tabla_t,))
        tcols = {r[0] for r in cur.fetchall()}
        for c in ("notes", "comments", "history", "avances"):
            if c in tcols:
                marker = f"\n[{datetime.now().isoformat()}] {texto}"
                if porcentaje is not None:
                    marker += f" ({porcentaje}%)"
                try:
                    conn.autocommit = True
                    cur.execute(f"UPDATE public.{tabla_t} SET {c} = COALESCE({c}, '') || %s WHERE id::text = %s RETURNING id",
                                (marker, str(tarea_id)))
                    row = cur.fetchone()
                    conn.close()
                    if row:
                        return f"✅ Avance anexado a `{tabla_t}.{c}` de tarea `{tarea_id}` (no encontré tabla dedicada de comentarios)."
                except Exception as e:
                    try: conn.close()
                    except Exception: pass
                    return f"❌ Append a {tabla_t}.{c} falló: {e}"
    conn.close()
    return "❌ No encontré tabla de comentarios/updates ni campo notes/history en tareas. Crea la estructura en kawiil-central primero, o usa kawiil_central_query con SQL específico."


# ===== Recordatorios (scheduler queue) =====
import uuid as _uuid

_td = timedelta  # alias para compat con código de abajo
TZ_CDMX = timezone(timedelta(hours=-6))

REMINDERS_DIR = HOME_OC / "reminders"
QUEUE_FILE = REMINDERS_DIR / "queue.jsonl"


def _read_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    out = []
    for line in QUEUE_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _write_queue(items: list):
    REMINDERS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    tmp.replace(QUEUE_FILE)


def _agendar_recordatorio(mensaje: str, fecha_hora: str, canal: str = "telegram",
                          modo: str = "enrich", recurrencia: str = None) -> str:
    """Agrega un recordatorio al queue del scheduler."""
    # Valida fecha
    try:
        dt = datetime.fromisoformat(fecha_hora)
    except Exception as e:
        return f"ERROR: fecha_hora inválida '{fecha_hora}'. Usa ISO 8601 con tz, ej: '2026-05-26T08:00:00-06:00'. ({e})"
    if dt.tzinfo is None:
        # Asume CDMX
        dt = dt.replace(tzinfo=TZ_CDMX)
        fecha_hora = dt.isoformat()
    entry = {
        "id": str(_uuid.uuid4())[:8],
        "fire_at": fecha_hora,
        "message": mensaje,
        "channel": canal,
        "mode": modo,
        "recurrence": recurrencia,
        "created_at": datetime.now(TZ_CDMX).isoformat(),
        "source": "user",
    }
    queue = _read_queue()
    queue.append(entry)
    queue.sort(key=lambda e: e.get("fire_at", ""))
    _write_queue(queue)
    rec_txt = f" (recurrente: {recurrencia})" if recurrencia else ""
    pretty_dt = dt.strftime("%Y-%m-%d %H:%M")
    return f"OK recordatorio agendado [id={entry['id']}] para {pretty_dt} CDMX{rec_txt} → canal={canal}, modo={modo}\nMensaje: {mensaje[:120]}"


def _listar_recordatorios() -> str:
    queue = _read_queue()
    if not queue:
        return "(no hay recordatorios pendientes)"
    out = [f"=== {len(queue)} recordatorio(s) pendientes ==="]
    for e in queue[:30]:
        rec = f" [recurrente: {e.get('recurrence')}]" if e.get("recurrence") else ""
        out.append(f"  • [{e.get('id','?')}] {e.get('fire_at','?')} → {e.get('message','')[:80]}{rec}")
    if len(queue) > 30:
        out.append(f"  ... y {len(queue) - 30} más")
    return "\n".join(out)


def _cancelar_recordatorio(rid: str) -> str:
    queue = _read_queue()
    nuevo = [e for e in queue if e.get("id") != rid]
    if len(nuevo) == len(queue):
        return f"ERROR: no encontré recordatorio con id '{rid}'. Usa listar_recordatorios para ver los IDs."
    _write_queue(nuevo)
    return f"OK recordatorio '{rid}' cancelado."


# ===== Sub-agentes =====
import re as _re

_AGENT_NAME_RE = _re.compile(r"^[a-z][a-z0-9-]{1,40}$")


# Stack de agentes activos en el hilo actual — permite saber qué agente invocó a
# cuál (para dibujar las conexiones en el dashboard). Es best-effort, no thread-safe
# estricto, suficiente para visualización.
_AGENT_STACK: list = []


def log_agent_activity(evento: str, agente: str, modelo: str = "", detalle: str = "") -> None:
    """Anexa un evento a la bitácora de actividad de agentes (best-effort, nunca rompe).

    evento: 'start' | 'end' | 'error' | 'created'
    El dashboard del gateway lee este archivo para animar los círculos.
    """
    try:
        parent = _AGENT_STACK[-1] if _AGENT_STACK else None
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "evento": evento,
            "agente": agente,
            "modelo": modelo,
            "parent": parent,
            "detalle": (detalle or "")[:200],
        }
        AGENT_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AGENT_ACTIVITY_FILE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # Rotación simple: si pasa de ~5000 líneas, deja las últimas 2000.
        try:
            if AGENT_ACTIVITY_FILE.stat().st_size > 2_000_000:
                lines = AGENT_ACTIVITY_FILE.read_text().splitlines()[-2000:]
                AGENT_ACTIVITY_FILE.write_text("\n".join(lines) + "\n")
        except Exception:
            pass
    except Exception:
        pass  # nunca dejar que la bitácora rompa la ejecución del agente


def _crear_agente(nombre: str, especialidad: str, prompt: str, modelo: str = "claude-sonnet-4-6") -> str:
    """Registra un sub-agente en AGENTS_DIR/{nombre}.md."""
    if not _AGENT_NAME_RE.match(nombre):
        return f"ERROR: nombre '{nombre}' inválido. Usa kebab-case (a-z, 0-9, guiones), 2-41 chars."
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / f"{nombre}.md"
    if path.exists():
        return f"ERROR: agente '{nombre}' ya existe. Si quieres actualizarlo borra primero o usa otro nombre."
    body = (
        f"---\n"
        f"nombre: {nombre}\n"
        f"especialidad: {especialidad}\n"
        f"modelo: {modelo}\n"
        f"creado: {datetime.now().isoformat()}\n"
        f"---\n\n"
        f"{prompt}\n"
    )
    path.write_text(body)
    log_agent_activity("created", nombre, modelo, especialidad)
    return f"OK: agente '{nombre}' creado en {path.name} (especialidad: {especialidad}, modelo: {modelo})"


def _parse_agent_file(path: Path) -> dict:
    """Lee un .md con frontmatter YAML simple y devuelve dict + body."""
    text = path.read_text()
    meta = {"nombre": path.stem, "especialidad": "(sin descripción)", "modelo": "claude-sonnet-4-6"}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 5:].lstrip()
    meta["prompt"] = body
    return meta


def _listar_agentes() -> str:
    if not AGENTS_DIR.exists():
        return "(no hay sub-agentes registrados aún — usa `crear_agente` para hacer el primero)"
    files = sorted(AGENTS_DIR.glob("*.md"))
    if not files:
        return "(no hay sub-agentes registrados aún)"
    out = [f"=== {len(files)} sub-agente(s) registrados ===\n"]
    for f in files:
        meta = _parse_agent_file(f)
        out.append(f"• {meta['nombre']}  [{meta.get('modelo','?')}]")
        out.append(f"    {meta.get('especialidad','(sin desc)')}")
    return "\n".join(out)


def _invocar_agente_via_ollama(nombre: str, sub_system: str, user_msg: str) -> str | None:
    resp = call_ollama(
        sub_system, [], user_msg,
        history_file=None,
        model=OLLAMA_FAST_MODEL,
        timeout=OLLAMA_QUALITY_TIMEOUT,
    )
    if not resp:
        return None
    return f"[{nombre} respondió vía Ollama]\n\n{resp}"


def _invocar_agente_via_deepseek(nombre: str, sub_system: str, user_msg: str, modelo: str) -> str | None:
    resp = call_deepseek(sub_system, [], user_msg, model=modelo)
    if not resp:
        return None
    return f"[{nombre} respondió vía DeepSeek]\n\n{resp}"


def _invocar_agente(
    nombre: str,
    tarea: str,
    contexto: str = "",
    modelo_override: str | None = None,
) -> str:
    """Wrapper con bitácora de actividad (start/end) para el dashboard visual.

    Empuja el agente al stack para que las invocaciones anidadas (un agente que
    llama a otro vía consejo_experto_legal) queden registradas con su `parent`.
    """
    if not _AGENT_NAME_RE.match(nombre):
        return f"ERROR: nombre '{nombre}' inválido."
    path = AGENTS_DIR / f"{nombre}.md"
    if not path.exists():
        return f"ERROR: agente '{nombre}' no existe. Usa `listar_agentes` para ver disponibles."
    meta = _parse_agent_file(path)
    modelo_log = (modelo_override or meta.get("modelo", "claude-sonnet-4-6")).strip()
    log_agent_activity("start", nombre, modelo_log, tarea)
    _AGENT_STACK.append(nombre)
    try:
        out = _invocar_agente_impl(nombre, tarea, contexto, modelo_override, meta)
        evento = "error" if out.startswith("ERROR") else "end"
        log_agent_activity(evento, nombre, modelo_log, out[:200])
        return out
    except Exception as e:
        log_agent_activity("error", nombre, modelo_log, str(e))
        raise
    finally:
        if _AGENT_STACK and _AGENT_STACK[-1] == nombre:
            _AGENT_STACK.pop()


def _invocar_agente_impl(
    nombre: str,
    tarea: str,
    contexto: str = "",
    modelo_override: str | None = None,
    meta: dict | None = None,
) -> str:
    """Ejecuta una sub-llamada al modelo del agente con su prompt + tarea."""
    path = AGENTS_DIR / f"{nombre}.md"
    if meta is None:
        meta = _parse_agent_file(path)
    modelo = (modelo_override or meta.get("modelo", "claude-sonnet-4-6")).strip()
    sub_system = meta["prompt"]

    # Inyectar conocimiento indexado para agentes kawiil-* (DOF/SJF context)
    knowledge_ctx = ""
    if nombre.startswith("kawiil-"):
        try:
            knowledge_ctx = _load_agent_knowledge(nombre)
        except Exception as e:
            log.debug(f"No pude cargar knowledge de {nombre}: {e}")

    ctx_parts = []
    if contexto:
        ctx_parts.append(contexto)
    if knowledge_ctx:
        ctx_parts.append(knowledge_ctx)
    full_contexto = "\n\n".join(ctx_parts)

    user_msg = tarea if not full_contexto else f"{tarea}\n\n## Contexto adicional\n{full_contexto}"

    if modelo.lower().startswith("deepseek"):
        out = _invocar_agente_via_deepseek(nombre, sub_system, user_msg, modelo)
        return out or f"ERROR: DeepSeek no respondió al agente '{nombre}'"

    if modelo.lower().startswith("ollama") or modelo == "llama":
        out = _invocar_agente_via_ollama(nombre, sub_system, user_msg)
        return out or f"ERROR: Ollama no respondió al agente '{nombre}'"

    try:
        api_key = load_anthropic_key()
    except Exception as e:
        if AGENT_FALLBACK_OLLAMA:
            out = _invocar_agente_via_ollama(nombre, sub_system, user_msg)
            if out:
                return out
        return f"ERROR cargando key Anthropic: {e}"
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    body = {
        "model": modelo if modelo.startswith("claude") else "claude-sonnet-4-6",
        "max_tokens": 2048,
        "system": sub_system,
        "messages": [{"role": "user", "content": user_msg}],
    }
    try:
        resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=120)
    except urllib.error.HTTPError as e:
        err_body = (getattr(e, "body", "") or "").lower()
        if AGENT_FALLBACK_OLLAMA or "credit" in err_body:
            out = _invocar_agente_via_ollama(nombre, sub_system, user_msg)
            if out:
                return out
        return f"ERROR llamando al agente '{nombre}': HTTP {e.code}"
    except Exception as e:
        if AGENT_FALLBACK_OLLAMA:
            out = _invocar_agente_via_ollama(nombre, sub_system, user_msg)
            if out:
                return out
        return f"ERROR llamando al agente '{nombre}': {e}"
    text_blocks = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    out = "".join(text_blocks).strip() or "(sin respuesta del agente)"
    return f"[{nombre} respondió]\n\n{out}"


# Mapeo área → prefijos legal-* (categorías de claude-for-legal)
_LEGAL_AREA_PREFIXES = {
    "ai-governance": ["legal-ai-governance"],
    "privacy":       ["legal-privacy"],
    "ip":            ["legal-ip"],
    "employment":    ["legal-employment"],
    "commercial":    ["legal-commercial"],
    "corporate":     ["legal-corporate"],
    "regulatory":    ["legal-regulatory"],
    "product":       ["legal-product"],
    "general":       ["legal-"],  # cualquiera
}


def _consejo_experto_legal(area: str, pregunta: str, contexto: str = "", max_expertos: int = 3) -> str:
    """Encuentra agentes legal-* relevantes, los invoca y sintetiza."""
    max_expertos = max(1, min(int(max_expertos or 3), 5))
    area = (area or "general").lower().strip()
    prefixes = _LEGAL_AREA_PREFIXES.get(area, _LEGAL_AREA_PREFIXES["general"])

    if not AGENTS_DIR.exists():
        return "❌ No hay directorio de agentes en /opt/openclaw/spaces/general/agents/"

    # Lista candidatos por prefijo
    candidatos = []
    for f in AGENTS_DIR.glob("*.md"):
        name = f.stem
        if any(name.startswith(p) for p in prefixes):
            candidatos.append(f)
    if not candidatos:
        return f"❌ No encontré agentes legal-* con prefijo {prefixes}. ¿Corriste import-legal-agents.sh?"

    # Ranking por keyword match en la pregunta
    query_lower = (pregunta + " " + contexto).lower()
    query_words = set(w for w in query_lower.replace(",", " ").replace(".", " ").split() if len(w) > 3)
    scored = []
    for f in candidatos:
        try:
            head = f.read_text()[:2000].lower()
            score = sum(1 for w in query_words if w in head)
            # Bonus: si el nombre matchea palabra clave
            for w in query_words:
                if w in f.stem.lower():
                    score += 3
            scored.append((score, f))
        except Exception:
            scored.append((0, f))
    scored.sort(reverse=True, key=lambda x: x[0])

    seleccionados = [f for s, f in scored[:max_expertos]]
    if all(s == 0 for s, _ in scored[:max_expertos]):
        # Si no hay matches, toma los primeros (fallback)
        seleccionados = candidatos[:max_expertos]

    # Invoca cada experto
    opiniones = []
    nombres_consultados = []
    for f in seleccionados:
        nombre = f.stem
        nombres_consultados.append(nombre)
        try:
            log.info(f"consejo_experto_legal: consultando {nombre}")
            resp = _invocar_agente(nombre, pregunta, contexto)
            opiniones.append(f"### {nombre}\n{resp}")
        except Exception as e:
            opiniones.append(f"### {nombre}\n(error consultando: {e})")

    raw_consejo = "\n\n---\n\n".join(opiniones)
    _kawiil_central_audit(f"consejo_experto_legal area={area}", pregunta[:300]) if 'KAWIIL_CENTRAL_AUDIT_LOG' in globals() else None

    # Encolar en prioridad para que el bg-indexer refuerce el conocimiento sobre este tema
    _legal_enqueue_priority(
        f"kawiil-{area}" if (AGENTS_DIR / f"kawiil-{area}.md").exists() else "kawiil-nelli",
        pregunta[:200], razon=f"consulta legal area={area}"
    )

    # ── MEXICANIZACIÓN OBLIGATORIA ──────────────────────────────────────────
    # Los agentes internacionales (legal-* de claude-for-legal, contexto US) son
    # SOLO REFERENCIA. Un agente MEXICANO revisa su razonamiento y produce la
    # versión válida y OBLIGATORIA en México, citando la norma local aplicable.
    # Si existe el agente compliance mexicano (kawiil-nelli), él hace la
    # mexicanización (y queda registrado en el dashboard como agente trabajando).
    tarea_mex = (
        f"PREGUNTA ORIGINAL:\n{pregunta}\n\n"
        f"CONTEXTO:\n{contexto or '(sin contexto adicional)'}\n\n"
        f"REFERENCIA INTERNACIONAL (expertos US — usar SOLO como punto de partida, "
        f"NO como respuesta final):\n\n{raw_consejo[:16000]}"
    )
    mex_agent = "kawiil-nelli" if (AGENTS_DIR / "kawiil-nelli.md").exists() else None
    sintesis = None
    if mex_agent:
        try:
            sintesis = _invocar_agente(mex_agent, MEXICANIZE_DOCTRINE + "\n\n" + tarea_mex, "")
            # _invocar_agente prefija "[nombre respondió]"; lo quitamos para el render.
            if sintesis and sintesis.startswith("["):
                nl = sintesis.find("\n")
                if nl > 0:
                    sintesis = sintesis[nl:].lstrip()
        except Exception as e:
            sintesis = None
            log.warning(f"mexicanización vía {mex_agent} falló: {e}")
    if not sintesis or sintesis.startswith("ERROR"):
        # Fallback: Claude Sonnet con la misma doctrina obligatoria.
        try:
            api_key = load_anthropic_key()
            body = {
                "model": CLAUDE_SONNET,
                "max_tokens": 2000,
                "system": MEXICANIZE_DOCTRINE,
                "messages": [{"role": "user", "content": tarea_mex}],
            }
            headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
            resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=90)
            text_blocks = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
            sintesis = "".join(text_blocks).strip() or "(sin síntesis)"
        except Exception as e:
            sintesis = f"(mexicanización falló: {e})"

    quien = mex_agent or "Claude (fallback)"
    header = f"⚖️ *Dictamen legal mexicanizado — área:* `{area}`\n"
    header += f"*Referencia internacional ({len(nombres_consultados)}):* " + ", ".join(f"`{n}`" for n in nombres_consultados) + "\n"
    header += f"*Mexicanizado por:* `{quien}`\n"
    header += f"*Pregunta:* {pregunta[:200]}\n\n"
    return header + "## Versión obligatoria en México\n\n" + sintesis + "\n\n---\n\n## Referencia internacional (para deep-dive)\n\n" + raw_consejo[:6000]


# Doctrina que convierte la referencia internacional en la versión OBLIGATORIA en
# México. Se inyecta tanto cuando mexicaniza kawiil-nelli como en el fallback Claude.
MEXICANIZE_DOCTRINE = (
    "Eres un abogado MEXICANO senior. Recibes el análisis de expertos legales "
    "internacionales (principalmente EE.UU.) como REFERENCIA TÉCNICA, nunca como "
    "respuesta final. Tu trabajo es REVISAR ese razonamiento y producir la versión "
    "VÁLIDA Y OBLIGATORIA conforme al derecho mexicano. Reglas:\n"
    "1. NUNCA copies la conclusión extranjera tal cual. Tradúcela al marco mexicano "
    "aplicable: Constitución, CFF, LFPIORPI, LFPDPPP, LFT, Código de Comercio, CNBV, "
    "UIF, SAT, INAI, Condusef, NOMs y la jurisprudencia SCJN/TFJA que aplique.\n"
    "2. Separa SIEMPRE en dos bloques claros:\n"
    "   • **OBLIGATORIO EN MÉXICO** — lo que la ley mexicana exige, citando artículo y "
    "ordenamiento específico. Marca deadlines en negrita.\n"
    "   • **Buena práctica internacional (opcional)** — lo que viene de la referencia US "
    "y conviene pero no es exigible aquí.\n"
    "3. Si la práctica extranjera NO aplica o es contraria a la norma mexicana, dilo "
    "explícitamente ('esto NO aplica en México porque…').\n"
    "4. Si falta una norma mexicana específica que deberías citar y no la tienes, dilo "
    "y sugiere verificar en DOF/SJF con legal_buscar — no inventes artículos.\n"
    "5. Cierra con un 'Bottom line' ejecutivo de 2-3 líneas para el director.\n"
    "Formato compacto, bullets, en español de México."
)


def execute_tool(name: str, args: dict) -> str:
    """Ejecuta un tool y devuelve resultado como string."""
    try:
        if name == "read_memory":
            path = SPACE / args["filename"]
            if not path.exists():
                return f"(archivo {args['filename']} no existe aún)"
            return path.read_text()
        elif name == "write_memory":
            path = SPACE / args["filename"]
            path.write_text(args["content"])
            return f"OK escrito {args['filename']}"
        elif name == "append_to_memory":
            path = SPACE / args["filename"]
            with path.open("a") as f:
                f.write("\n" + args["content"] + "\n")
            return f"OK agregado a {args['filename']}"
        elif name == "create_reminder":
            script = SPACE / "scripts" / "crear-recordatorio.sh"
            if not script.exists():
                agenda = SPACE / "AGENDA.md"
                with agenda.open("a") as f:
                    f.write(f"\n- [recordatorio] {args['datetime']} — {args['text']}\n")
                return f"(no hay tool de iCloud en Hetzner, anotado en AGENDA: {args['text']} @ {args['datetime']})"
            r = subprocess.run(
                [str(script), args["text"], args["datetime"]],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                return f"OK recordatorio creado: {r.stdout.strip()}"
            return f"ERROR creando recordatorio: {r.stderr.strip()}"
        elif name == "save_learning":
            path = SPACE / "LEARNINGS.md"
            entry = (
                f"\n- [{datetime.now().strftime('%Y-%m-%d')}] {args['topic']}: {args['rule']}\n"
                f"  Contexto: {args['context']}\n"
            )
            with path.open("a") as f:
                f.write(entry)
            return f"OK aprendido: {args['topic']}"
        elif name == "agendar_recordatorio":
            return _agendar_recordatorio(
                args["mensaje"], args["fecha_hora"],
                args.get("canal", "telegram"), args.get("modo", "enrich"),
                args.get("recurrencia"),
            )
        elif name == "listar_recordatorios":
            return _listar_recordatorios()
        elif name == "cancelar_recordatorio":
            return _cancelar_recordatorio(args["id"])
        elif name == "legal_estado":
            return _legal_estado(args.get("modulo", "ambos"))
        elif name == "legal_buscar":
            return _legal_buscar(args["modulo"], args["query"], args.get("limit", 10))
        elif name == "legal_ultimo":
            return _legal_ultimo(args["modulo"], args.get("n", 10))
        elif name == "legal_briefing":
            return _legal_briefing()
        elif name == "legal_indexar":
            agente = args.get("agente", "").strip()
            forzar = bool(args.get("forzar", False))
            limite = max(10, min(int(args.get("limite", 30)), 100))
            if agente.lower() == "todos":
                return _legal_indexar_todos(limite_por_agente=limite)
            return _legal_indexar_agente(agente, forzar=forzar, limite=limite)
        elif name == "legal_conocimiento":
            agente = args.get("agente", "").strip()
            idx = _knowledge_index(agente)
            kdir = KNOWLEDGE_BASE / agente
            resumen_file = kdir / "resumen-semanal.md"
            parts = [
                f"📚 Conocimiento indexado — *{agente}*",
                f"  • Total docs: {idx.get('total', 0)}",
                f"  • Área: {idx.get('label', '?')}",
                f"  • Última indexación: {idx.get('last_indexed', '(nunca)') or '(nunca)'}",
            ]
            if resumen_file.exists():
                resumen = resumen_file.read_text()[:3000]
                parts.append(f"\n{resumen}")
            else:
                parts.append("  • Resumen semanal: no disponible aún (corre legal_indexar primero)")
            return "\n".join(parts)
        elif name == "mac_estado":
            return _mac_estado()
        elif name == "mac_wake_request":
            return _mac_wake_request(args["razon"])
        elif name == "browser_navegar":
            return _browser_navegar(args["url"], args.get("wait_until", "networkidle"), args.get("wait_extra_ms", 0))
        elif name == "browser_leer":
            return _browser_leer()
        elif name == "browser_screenshot":
            return _browser_screenshot()
        elif name == "browser_click":
            return _browser_click(args["selector"], args.get("by", "css"))
        elif name == "browser_escribir":
            return _browser_escribir(args["selector"], args["value"])
        elif name == "browser_press":
            return _browser_press(args.get("key", "Enter"))
        elif name == "browser_esperar":
            return _browser_esperar(args["selector"], args.get("timeout_ms", 15000))
        elif name == "browser_login":
            return _browser_login(args["url"], args["vault_item"],
                                   args.get("user_selector", "input[type=email], input[name=email], input[name=username], input#username"),
                                   args.get("pass_selector", "input[type=password]"),
                                   args.get("submit_selector", "button[type=submit]"))
        elif name == "browser_historial":
            return _browser_historial(args.get("n", 20))
        elif name == "browser_reset":
            return _browser_reset()
        elif name == "vault_buscar":
            return _vault_buscar(args["query"])
        elif name == "vault_obtener":
            return _vault_obtener(args["item_id"], args["campo"], args["razon"])
        elif name == "vault_op_buscar":
            return _vault_op_buscar(args["query"], args.get("vault", ""))
        elif name == "vault_op_obtener":
            return _vault_op_obtener(args["item"], args["campo"], args["razon"])
        elif name == "kawiil_central_estado":
            return _kawiil_central_estado()
        elif name == "kawiil_central_tablas":
            return _kawiil_central_tablas()
        elif name == "kawiil_central_describir":
            return _kawiil_central_describir(args["tabla"])
        elif name == "kawiil_central_query":
            return _kawiil_central_query(args["sql"], args["razon"])
        elif name == "kawiil_central_tareas":
            return _kawiil_central_tareas(args.get("estado", ""), args.get("proyecto_id", ""),
                                          args.get("asignado_a", ""), args.get("limit", 20))
        elif name == "kawiil_central_proyectos":
            return _kawiil_central_proyectos(args.get("estado", ""), args.get("limit", 20))
        elif name == "kawiil_central_tareas_proximas":
            return _kawiil_central_tareas_proximas(args.get("dias", 7), args.get("estado_excluir", "hecho"), args.get("asignado_a", ""))
        elif name == "kawiil_central_tarea_detalle":
            return _kawiil_central_tarea_detalle(args["tarea_id"])
        elif name == "kawiil_central_pipeline":
            return _kawiil_central_pipeline(args.get("limit_por_etapa", 5))
        elif name == "kawiil_central_clientes":
            return _kawiil_central_clientes(args.get("query", ""), args.get("limit", 30))
        elif name == "kawiil_central_crear_tarea":
            return _kawiil_central_crear_tarea(args["titulo"], args["proyecto_id"],
                                                args.get("descripcion", ""), args.get("asignado_a", ""),
                                                args.get("prioridad", ""), args.get("deadline", ""),
                                                args.get("campos_extra"))
        elif name == "kawiil_central_actualizar_tarea":
            return _kawiil_central_actualizar_tarea(args["tarea_id"], args["cambios"])
        elif name == "kawiil_central_avance":
            return _kawiil_central_avance(args["tarea_id"], args["texto"], args.get("porcentaje"))
        elif name == "leer_mi_codigo":
            import self_update as _su
            return _su.leer_mi_codigo(args["archivo"], args.get("offset", 0), args.get("limit", 200))
        elif name == "editar_mi_codigo":
            import self_update as _su
            return _su.editar_mi_codigo(args["archivo"], args["old_string"], args["new_string"], args.get("descripcion", ""))
        elif name == "reiniciar_mi_servicio":
            import self_update as _su
            return _su.reiniciar_mi_servicio(args["servicio"])
        elif name == "ver_mis_backups":
            import self_update as _su
            return _su.ver_mis_backups(args.get("archivo"))
        elif name == "restaurar_mi_codigo":
            import self_update as _su
            return _su.restaurar_mi_codigo(args["archivo"], args["backup_id"])
        elif name == "verificar_conexiones":
            return _verificar_conexiones(args.get("incluir_m365", True))
        elif name == "crear_agente":
            return _crear_agente(
                args["nombre"], args["especialidad"], args["prompt"],
                args.get("modelo", "claude-sonnet-4-6"),
            )
        elif name == "listar_agentes":
            return _listar_agentes()
        elif name in ("invocar_agente", "delegar_agente"):
            return _invocar_agente(
                args["nombre"],
                args["tarea"],
                args.get("contexto", ""),
                args.get("modelo_override"),
            )
        elif name == "consejo_experto_legal":
            return _consejo_experto_legal(args["area"], args["pregunta"], args.get("contexto", ""), args.get("max_expertos", 3))
        elif name.startswith("m365_"):
            return _run_m365_tool(name, args)
        else:
            return f"ERROR: tool desconocido {name}"
    except Exception as e:
        log.exception(f"Tool {name} falló")
        return f"ERROR ejecutando {name}: {e}"


# ===== Routing =====
def needs_claude(user_message: str) -> bool:
    """Claude solo con prefijo explícito (/sonnet, /profundo, …). El chat normal va a DeepSeek."""
    if not user_message:
        return False
    msg = user_message.strip().lower()
    if msg.startswith(OLLAMA_FORCE_PREFIXES):
        return False
    return msg.startswith(CLAUDE_FORCE_PREFIXES)


def needs_tools(user_message: str) -> bool:
    """True si el mensaje pide datos/acciones que requieren herramientas: búsqueda legal
    (DOF/tesis/SJF), correo/calendario M365, kawiil-central, browser, vault, etc.
    → se enruta a Claude con tools (análisis/indexación/conocimiento).
    Los overrides explícitos de chat (/ds, /llama, /oss, /haiku) tienen prioridad y NO cuentan."""
    if not user_message:
        return False
    msg = user_message.strip()
    if msg.lower().startswith(OLLAMA_FORCE_PREFIXES + OLLAMA_QUALITY_PREFIXES + DEEPSEEK_FORCE_PREFIXES + HAIKU_FORCE_PREFIXES):
        return False
    return bool(TOOL_REGEX.search(msg))


def strip_override_prefix(user_message: str) -> str:
    """Quita /sonnet, /llama, /oss, etc. del inicio antes de enviar al modelo."""
    cleaned = user_message
    for prefix in CLAUDE_FORCE_PREFIXES + OLLAMA_FORCE_PREFIXES + OLLAMA_QUALITY_PREFIXES + HAIKU_FORCE_PREFIXES + DEEPSEEK_FORCE_PREFIXES:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned or user_message


def call_ollama(
    system_prompt: str,
    history: list,
    user_message: str,
    history_file: Path | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> str | None:
    """Llama Ollama. Sin tools. Retorna None si falla."""
    ollama_model = model or _resolve_ollama_model(user_message)[0]
    ollama_timeout = timeout if timeout is not None else _resolve_ollama_model(user_message)[1]
    snapshot = build_operational_snapshot(compact=True)
    first_of_day = is_first_conversation_today(history_file)
    chat_mode = _is_ollama_chat_mode(user_message, history)
    internal_ctx = build_ollama_internal_context(
        user_message, history, snapshot, first_of_day, chat_mode=chat_mode,
    )
    sys_p = _trim_system_for_ollama(system_prompt, chat_mode=chat_mode, ollama_model=ollama_model) + internal_ctx
    hist = _trim_history_for_ollama(history)
    polo_msg = strip_override_prefix((user_message or "").strip()) or "(mensaje vacío)"
    messages = [{"role": "system", "content": sys_p}]
    for h in hist:
        if h["role"] in ("user", "assistant") and isinstance(h["content"], str):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": polo_msg})
    if "gpt-oss" in ollama_model.lower():
        predict = 512
    elif chat_mode:
        predict = 350
    else:
        predict = 280
    body = {
        "model": ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.6, "num_ctx": 8192, "num_predict": predict},
    }
    try:
        resp = http_post_json(f"{OLLAMA_BASE}/api/chat", headers={}, body=body, timeout=ollama_timeout)
    except Exception as e:
        log.warning(f"Ollama falló ({e})")
        return None
    content = resp.get("message", {}).get("content", "")
    if not content or not content.strip():
        log.warning("Ollama devolvió vacío")
        return None
    return sanitize_ollama_response(content.strip())


def call_deepseek(system_prompt: str, history: list, user_message: str, model: str | None = None) -> str | None:
    raw = []
    for h in history:
        if h["role"] in ("user", "assistant") and isinstance(h["content"], str) and h["content"].strip():
            raw.append({"role": h["role"], "content": h["content"].strip()})
    cleaned_input = strip_override_prefix((user_message or "").strip()) or "(mensaje vacío)"
    raw.append({"role": "user", "content": cleaned_input})
    # Colapsa mensajes consecutivos del mismo rol (DeepSeek también rechaza secuencias inválidas)
    hist_clean = []
    for m in raw:
        if hist_clean and hist_clean[-1]["role"] == m["role"]:
            hist_clean[-1]["content"] += "\n" + m["content"]
        else:
            hist_clean.append(m)
    while hist_clean and hist_clean[0]["role"] != "user":
        hist_clean.pop(0)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt[:50_000]})
    messages.extend(hist_clean)
    selected_model = (model or DEEPSEEK_MODEL).strip()
    if selected_model.startswith("deepseek/"):
        selected_model = selected_model.split("/", 1)[1]
    headers = {"Authorization": f"Bearer {load_deepseek_key()}"}
    body = {
        "model": selected_model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
        "stream": False,
    }
    try:
        resp = http_post_json(DEEPSEEK_API_BASE, headers, body, timeout=120)
    except urllib.error.HTTPError as e:
        err_body = getattr(e, "body", "") or ""
        log.error(f"DeepSeek falló — code={e.code} body={err_body[:500]}")
        return f"(error DeepSeek HTTP {e.code}: {err_body[:300] or 'body vacío'})"
    except Exception as e:
        log.exception("DeepSeek API falló")
        return f"(error llamando a DeepSeek: {e})"
    choices = resp.get("choices") or []
    if not choices:
        return "(sin respuesta de DeepSeek)"
    msg = choices[0].get("message") or {}
    return (msg.get("content") or "").strip() or "(respuesta vacía de DeepSeek)"


def call_haiku(api_key: str, system_prompt: str, history: list, user_message: str) -> str:
    """Llama Haiku SIN tools — para chat rápido. ~1-3s."""
    messages = []
    for h in history:
        if h["role"] in ("user", "assistant") and isinstance(h["content"], str) and h["content"].strip():
            messages.append({"role": h["role"], "content": h["content"]})
    if not user_message or not user_message.strip():
        return "(error: user_message vacío para Haiku)"
    messages.append({"role": "user", "content": user_message})
    # Asegura que messages alterna user/assistant correctamente — Anthropic requiere
    # que empiece con user y alterne. Si hay duplicados consecutivos del mismo rol,
    # los colapsamos.
    cleaned = []
    for m in messages:
        if cleaned and cleaned[-1]["role"] == m["role"]:
            cleaned[-1]["content"] = cleaned[-1]["content"] + "\n\n" + m["content"]
        else:
            cleaned.append(m)
    if cleaned and cleaned[0]["role"] != "user":
        cleaned = cleaned[1:]  # quita el primer assistant si por algo quedó al inicio
    if not cleaned:
        return "(error: messages vacío después de limpieza)"
    # Trunca system prompt si está enorme (>50k chars ≈ ~12k tokens). Haiku tiene 200k context
    # pero llenarlo es lento y caro. Mantenemos los primeros 50k.
    sys_p = system_prompt[:50_000] if system_prompt else ""
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    body = {
        "model": CLAUDE_HAIKU,
        "max_tokens": 2048,
        "system": sys_p,
        "messages": cleaned,
    }
    try:
        resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=60)
    except urllib.error.HTTPError as e:
        err_body = getattr(e, "body", "") or ""
        log.error(f"Haiku falló — code={e.code} body={err_body[:500]}")
        # Fallback a Sonnet si el modelo fue rechazado
        body_lower = err_body.lower()
        if e.code == 400 and ("model" in body_lower or "not_found" in body_lower or "deprecated" in body_lower):
            log.warning("Modelo Haiku rechazado por API — fallback a Sonnet")
            body["model"] = CLAUDE_SONNET
            try:
                resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=60)
            except urllib.error.HTTPError as e2:
                err_body2 = getattr(e2, "body", "") or ""
                return f"(Haiku→Sonnet fallback falló: HTTP {e2.code}: {err_body2[:200]})"
            except Exception as e2:
                return f"(Haiku→Sonnet fallback falló: {e2})"
        else:
            return f"(error Haiku HTTP {e.code}: {err_body[:300] or 'body vacío'})"
    except Exception as e:
        log.exception("Haiku API falló (exception)")
        return f"(error llamando a Haiku: {e})"
    text_blocks = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    return "".join(text_blocks).strip() or "(sin respuesta)"


def call_claude_with_image(api_key: str, system_prompt: str, image_b64: str, media_type: str, caption: str = "") -> str:
    """
    Manda imagen + caption a Claude Sonnet (multimodal). Sin tools — solo análisis.
    media_type: 'image/jpeg', 'image/png', 'image/gif', 'image/webp'
    """
    user_text = caption.strip() if caption and caption.strip() else "Analiza esta imagen. Descríbeme qué ves y dime si hay alguna acción que deba tomar."
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    body = {
        "model": CLAUDE_SONNET,
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": user_text},
            ],
        }],
    }
    try:
        resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=120)
    except Exception as e:
        log.exception("Vision API falló")
        return f"(error analizando imagen: {e})"
    blocks = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    return "".join(blocks).strip() or "(sin respuesta del análisis)"


def call_claude(api_key: str, system_prompt: str, history: list, user_message: str) -> str:
    """Llama Claude Sonnet con tools. Loop hasta que termine.

    Acumula el texto de CADA turn separadamente. Al final devuelve el último turn
    con contenido — esto evita que se pierda texto cuando Claude devuelve
    text+tool_use en un mismo turn y después responde vacío.
    """
    # Construye mensajes colapsando consecutivos del mismo rol.
    # Anthropic rechaza con 400 si hay dos "user" o dos "assistant" seguidos
    # (puede pasar cuando el bridge escribe historial duplicado).
    raw_msgs = []
    for h in history:
        if h["role"] in ("user", "assistant") and isinstance(h["content"], str) and h["content"].strip():
            raw_msgs.append({"role": h["role"], "content": h["content"].strip()})
    raw_msgs.append({"role": "user", "content": (user_message or "").strip() or "(vacío)"})
    messages = []
    for m in raw_msgs:
        if messages and messages[-1]["role"] == m["role"]:
            messages[-1]["content"] += "\n" + m["content"]
        else:
            messages.append({"role": m["role"], "content": m["content"]})
    # Asegura que el primer mensaje sea "user"
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    max_loops = 8
    turn_texts = []   # texto emitido por cada turn (puede ser "")
    tools_executed = []  # nombres de tools ejecutados (para fallback message)
    memory_tool_results = []  # confirmaciones append/write_memory
    for _ in range(max_loops):
        body = {
            "model": CLAUDE_SONNET,
            "max_tokens": 4096,
            "system": system_prompt,
            "tools": TOOLS_DEFINITION,
            "messages": messages,
        }
        try:
            resp = http_post_json(ANTHROPIC_API_BASE, headers, body, timeout=180)
        except Exception as e:
            log.exception("Anthropic API falló")
            return f"(error llamando a Claude: {e})"
        content = resp.get("content", [])
        stop_reason = resp.get("stop_reason")
        assistant_blocks = []
        tool_calls = []
        turn_text = ""
        for block in content:
            assistant_blocks.append(block)
            if block.get("type") == "text":
                turn_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(block)
        turn_texts.append(turn_text)
        messages.append({"role": "assistant", "content": assistant_blocks})
        if stop_reason != "tool_use" or not tool_calls:
            break
        tool_results = []
        for tc in tool_calls:
            tools_executed.append(tc["name"])
            result = execute_tool(tc["name"], tc.get("input", {}))
            if tc["name"] in ("append_to_memory", "write_memory"):
                memory_tool_results.append(result)
            tool_results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": result})
        messages.append({"role": "user", "content": tool_results})

    mem_confirm = _format_memory_tool_confirmations(memory_tool_results)

    # Devolver el último turn con texto no vacío
    for t in reversed(turn_texts):
        if t and t.strip():
            out = t.strip()
            if mem_confirm:
                return f"{out}\n\n{mem_confirm}"
            return out
    # Ningún turn devolvió texto — fallback informativo en lugar de "(sin respuesta)"
    if mem_confirm:
        return mem_confirm
    if tools_executed:
        return (
            f"Ejecuté: {', '.join(tools_executed)}. "
            "Pero Claude no devolvió texto final — revisa con `verificar_conexiones` "
            "o mándame la instrucción de nuevo siendo más explícito sobre qué quieres que conteste."
        )
    return "(no obtuve respuesta de Claude — vuelve a intentar o usa /llama para forzar Ollama)"


def call_llm(
    api_key: str,
    system_prompt: str,
    history: list,
    user_message: str,
    history_file: Path | None = None,
) -> tuple:
    """
    Routea al modelo correcto. Ollama por default; Claude solo con prefijo explícito.

      • Default y /llama → Ollama (sin fallback a Haiku)
      • /sonnet, /claude, /fuerte, /profundo → Sonnet con tools
      • /haiku → Haiku sin tools
    Returns: (response, model_used).
    """
    msg = (user_message or "").strip().lower()
    first_of_day = is_first_conversation_today(history_file)

    # Aprendizaje a prueba de fallos: "anota en AGENDA: …" se guarda directo,
    # sin gastar créditos y aunque Anthropic esté sin saldo.
    det_mem = try_deterministic_memory_write(user_message, strict=True)
    if det_mem is not None:
        _mark_last_route("memoria-directa")
        return det_mem, "memoria-directa"

    def _ollama_route(tag: str) -> tuple:
        if _needs_sonnet_hint(user_message) and not _sonnet_hint_already_shown():
            _mark_sonnet_hint_shown()
            log.info("→ Aviso /sonnet (correos/M365 sin prefijo)")
            return _sonnet_hint_response(), "sonnet-hint"

        snapshot = build_operational_snapshot(compact=True)
        if should_deterministic_operational_response(user_message, history):
            log.info(f"→ Briefing determinístico (fast path) — {tag}")
            response, det_tag = deterministic_operational_response(snapshot)
            try:
                save_last_briefing(response, snapshot)
            except Exception:
                log.warning("No pude guardar last-briefing.json", exc_info=True)
            return response, det_tag

        ollama_model, _ = _resolve_ollama_model(user_message)
        chat_mode = _is_ollama_chat_mode(user_message, history)
        log.info(f"→ Ollama ({ollama_model}) {'[charla]' if chat_mode else ''} — {tag}")
        response = call_ollama(system_prompt, history, user_message, history_file=history_file)
        if response is not None and response.strip():
            _mark_last_route("ollama")
            if first_of_day or _wants_follow_up_briefing(user_message) or wants_explicit_briefing(user_message):
                try:
                    save_last_briefing(response, snapshot)
                except Exception:
                    log.warning("No pude guardar last-briefing.json", exc_info=True)
            return response, "ollama"
        if _had_briefing_this_session(history):
            log.warning(f"Ollama timeout — fallback charla ({tag})")
            return _ollama_chat_timeout_fallback(user_message, snapshot), "ollama-chat-fallback"
        if snapshot and "sin pendientes abiertos" not in snapshot.lower():
            log.warning(f"Ollama timeout — briefing único ({tag})")
            fb, _ = deterministic_operational_response(snapshot)
            return fb, "deterministic-briefing"
        log.warning(f"Ollama no respondió ({tag})")
        return _ollama_unavailable_msg(), "ollama-error"

    def _deepseek_route(tag: str) -> tuple:
        # Saludo / briefing explícito → respuesta determinística instantánea (sin modelo).
        snapshot = build_operational_snapshot(compact=True)
        if should_deterministic_operational_response(user_message, history):
            log.info(f"→ Briefing determinístico (fast path) — {tag}")
            response, det_tag = deterministic_operational_response(snapshot)
            try:
                save_last_briefing(response, snapshot)
            except Exception:
                log.warning("No pude guardar last-briefing.json", exc_info=True)
            return response, det_tag
        # Conversación fluida → DeepSeek (rápido, API, sin timeouts de CPU). Le anteponemos
        # el snapshot operativo para que tenga contexto real de AGENDA/IMPORTANT.
        log.info(f"→ DeepSeek (chat) — {tag}")
        ds_system = f"[CONTEXTO OPERATIVO ACTUAL]\n{snapshot}\n\n{system_prompt}"
        try:
            response = call_deepseek(ds_system, history, user_message)
        except Exception as e:
            log.warning(f"DeepSeek excepción ({e})")
            response = None
        if response and response.strip() and not response.lstrip().startswith(("(error", "(sin respuesta", "(respuesta vac")):
            _mark_last_route("deepseek")
            if first_of_day or _wants_follow_up_briefing(user_message) or wants_explicit_briefing(user_message):
                try:
                    save_last_briefing(response, snapshot)
                except Exception:
                    log.warning("No pude guardar last-briefing.json", exc_info=True)
            return response, "deepseek"
        # DeepSeek falló → briefing determinístico (NO caemos en Ollama lento de CPU).
        log.warning(f"DeepSeek no respondió ({tag}) — fallback determinístico")
        fb, _ = deterministic_operational_response(snapshot)
        return fb, "deepseek-fallback"

    if msg.startswith(OLLAMA_QUALITY_PREFIXES):
        return _ollama_route("override /oss")

    # Override Ollama explícito (sin fallback a Claude)
    if msg.startswith(OLLAMA_FORCE_PREFIXES):
        return _ollama_route("override /llama")

    # Override DeepSeek explícito (/deepseek, /ds)
    if msg.startswith(DEEPSEEK_FORCE_PREFIXES):
        log.info(f"→ DeepSeek ({DEEPSEEK_MODEL}) — override /deepseek")
        response = call_deepseek(system_prompt, history, user_message)
        return response or "(DeepSeek no respondió)", "deepseek"

    # Override Haiku explícito
    if msg.startswith(HAIKU_FORCE_PREFIXES):
        log.info(f"→ Haiku ({CLAUDE_HAIKU}) — override /haiku")
        cleaned = strip_override_prefix(user_message)
        return _call_claude_with_billing_check(call_haiku, api_key, system_prompt, history, cleaned), "haiku"

    # Análisis / búsqueda / datos / tools → Sonnet con tools.
    # (prefijo /sonnet, o auto: memoria, agentes legales, o cualquier keyword de herramienta)
    if needs_claude(user_message) or needs_sonnet_auto(user_message) or needs_tools(user_message):
        reason = "prefijo /sonnet" if needs_claude(user_message) else (
            "escritura memoria" if needs_memory_write(user_message) else
            "agentes legales" if needs_legal_sonnet(user_message) else
            "herramienta/datos"
        )
        log.info(f"→ Sonnet ({CLAUDE_SONNET}) — {reason}")
        cleaned = strip_override_prefix(user_message)
        response = call_claude(api_key, system_prompt, history, cleaned) or ""
        if _is_billing_error(response):
            # Sin créditos: si era una nota de memoria, sálvala en local para no perder el aprendizaje.
            if needs_memory_write(user_message):
                fb = try_deterministic_memory_write(user_message, strict=False)
                if fb is not None:
                    log.info("→ Memoria directa (fallback sin créditos)")
                    return fb, "memoria-directa-fallback"
            if needs_sonnet_auto(user_message) and not needs_claude(user_message):
                return _sonnet_auto_billing_msg(user_message), "sonnet-billing-error"
            return _billing_error_msg(), "sonnet-billing-error"
        tag = "sonnet"
        if needs_memory_write(user_message) and not needs_claude(user_message):
            tag = "sonnet-memory"
        elif needs_legal_sonnet(user_message) and not needs_claude(user_message):
            tag = "sonnet-legal"
        return response, tag

    # Default: conversación fluida → DeepSeek
    return _deepseek_route("chat default")


def _is_billing_error(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return (
        "credit balance is too low" in low
        or ("invalid_request_error" in low and "credit" in low)
        or ("error haiku http 400" in low and "credit" in low)
    )


def _billing_error_msg() -> str:
    """Mensaje cuando Anthropic rechaza por créditos (solo rutas /sonnet, /haiku, /profundo)."""
    return ("⚠️ Polo, tu cuenta Anthropic se quedó sin créditos.\n\n"
            "El chat normal sigue en Ollama local (escríbeme sin prefijo).\n"
            "Para análisis profundo, correos, calendario, agentes legales o visión necesitas Claude:\n"
            "1. https://console.anthropic.com/settings/billing\n"
            "2. Agrega saldo (mínimo 10 USD) o Auto-reload\n"
            "3. Espera 1-2 min y usa /sonnet o /profundo al inicio del mensaje\n\n"
            "Ejemplo: /sonnet revisa mi inbox de hoy y propón borrador de respuesta")


def _call_claude_with_billing_check(fn, api_key, system_prompt, history, user_message):
    """Wrapper que detecta error de billing y lo traduce a mensaje útil para Polo."""
    result = fn(api_key, system_prompt, history, user_message)
    if not result:
        return result
    result_low = result.lower()
    if ("credit balance is too low" in result_low or
        ("invalid_request_error" in result_low and "credits" in result_low) or
        "anthropic api" in result_low and "low" in result_low and "balance" in result_low):
        return _billing_error_msg()
    return result


# ===== Formatters por canal =====
def format_for_telegram(text: str) -> str:
    """
    Convierte Markdown estándar (lo que Claude produce) a Markdown legacy de Telegram.
    Reglas:
      - `**bold**` → `*bold*`
      - Headers `#`, `##`, `###` → línea en negrita
      - El resto se mantiene
    """
    if not text:
        return text
    # 1) Headers a negrita en línea propia
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    # 2) ** ** → * * (negrita). Evita captura greedy.
    # Reemplazo de a pares: cada ** se vuelve * (más simple y correcto que regex de pares balanceados).
    # Estrategia: ** consecutivos → un solo *
    text = text.replace("**", "*")
    # 3) Telegram Markdown legacy NO acepta ___ ni ~~. Lo dejamos pasar literal o lo borramos.
    text = re.sub(r"~~(.+?)~~", r"\1", text)  # tachado: quitar
    return text


def format_for_slack(text: str) -> str:
    """
    Convierte Markdown estándar a Slack mrkdwn.
    Reglas:
      - `**bold**` → `*bold*`
      - Headers → `*Header*\n`
      - `[text](url)` → `<url|text>`
      - `_italic_` se mantiene
    """
    if not text:
        return text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = text.replace("**", "*")
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    return text


# ===== Historial común =====
def load_history(history_file: Path, max_turns: int = 20) -> list:
    if not history_file.exists():
        return []
    msgs = []
    for line in history_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except Exception:
            pass
    return msgs[-max_turns:]


def append_history(history_file: Path, role: str, content):
    entry = {"ts": datetime.now().isoformat(), "role": role, "content": content}
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
