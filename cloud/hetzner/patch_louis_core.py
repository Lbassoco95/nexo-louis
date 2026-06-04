#!/usr/bin/env python3
"""
patch_louis_core.py — Aplica parches idempotentes a /opt/openclaw/scripts/louis_core.py

Añade:
  1. ENTREGABLES_PATH / BRIEFS_PATH (Cerebro Kawiil)
  2. needs_doc_sonnet, get_pending_files, _generar_pdf, generar_documento_directo
     (requeridas por telegram-bridge.py para generación de documentos)
  3. Helpers del Cerebro Kawiil (_cerebro_* + _encolar_notificacion)
  4. Tools en TOOLS_DEFINITION (cerebro_listar, cerebro_proyecto_estado,
     cerebro_crear_brief, cerebro_sync_agenda)
  5. Handlers en execute_tool

Uso:
  sudo python3 patch_louis_core.py
  sudo systemctl restart telegram-bridge slack-bridge
"""

import re
import sys
from pathlib import Path

TARGET = Path("/opt/openclaw/scripts/louis_core.py")

if not TARGET.exists():
    sys.exit(f"ERROR: no encuentro {TARGET}")

src = TARGET.read_text(encoding="utf-8")
original_size = len(src)
changes = []

# ── 1) ENTREGABLES_PATH ────────────────────────────────────────────────────
if "ENTREGABLES_PATH" not in src:
    src = src.replace(
        'AGENTS_DIR = SPACE / "agents"',
        'AGENTS_DIR = SPACE / "agents"\n'
        'ENTREGABLES_PATH = Path(os.environ.get("ENTREGABLES_PATH", str(HOME_OC / "entregables")))\n'
        'BRIEFS_PATH = ENTREGABLES_PATH / "_briefs"',
        1,
    )
    changes.append("ENTREGABLES_PATH / BRIEFS_PATH")

# ── 2-3) Todas las funciones nuevas (append al final) ─────────────────────
NEW_FUNCTIONS = r'''
# ═══════════════════════════════════════════════════════════════════════════
# GENERACIÓN DE DOCUMENTOS (telegram-bridge)
# ═══════════════════════════════════════════════════════════════════════════

_PENDING_FILES: list = []


def _enqueue_file(content: bytes, filename: str, caption: str = "") -> None:
    _PENDING_FILES.append((content, filename, caption))


def get_pending_files() -> list:
    """Retorna y vacía la cola de archivos pendientes de envío."""
    global _PENDING_FILES
    files = list(_PENDING_FILES)
    _PENDING_FILES.clear()
    return files


def needs_doc_sonnet(user_message: str) -> bool:
    """True si el usuario pide generar un documento (PDF/PPTX/XLSX)."""
    if not user_message:
        return False
    return bool(re.search(
        r"(?i)\b(genera?|crea?|elabora?|redacta?|escribe?|hace?|arm[ae])\b.{0,60}"
        r"\b(pdf|pptx?|xlsx?|documento|reporte|presentaci[oó]n|informe|contrato|oficio|brief)\b"
        r"|\b(en pdf|en pptx?|en xlsx?|como pdf)\b",
        user_message,
    ))


def _generar_pdf(titulo: str, contenido: str, autor: str = "Louis"):
    """Genera bytes de un PDF a partir de texto. Requiere reportlab."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        import io

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter,
                                rightMargin=inch, leftMargin=inch,
                                topMargin=inch, bottomMargin=inch)
        styles = getSampleStyleSheet()
        story = [Paragraph(titulo, styles["Title"]), Spacer(1, 0.25 * inch)]
        for para in contenido.split("\n\n"):
            para = para.strip()
            if para:
                safe = (para.replace("&", "&amp;")
                           .replace("<", "&lt;")
                           .replace(">", "&gt;"))
                story.append(Paragraph(safe.replace("\n", "<br/>"), styles["Normal"]))
                story.append(Spacer(1, 0.1 * inch))
        doc.build(story)
        return buf.getvalue()
    except Exception as e:
        log.warning(f"_generar_pdf falló: {e}")
        return None


def generar_documento_directo(api_key: str, system_prompt: str,
                               history: list, user_input: str) -> tuple:
    """Genera contenido del documento con Sonnet y lo encola como PDF."""
    prompt_doc = (
        system_prompt
        + "\n\nGenera el contenido completo y bien estructurado del documento solicitado. "
        "Escribe directamente el contenido, listo para convertir a PDF. Sin preámbulos."
    )
    contenido = call_claude(api_key, prompt_doc, history, user_input)
    titulo = (user_input or "documento")[:70].strip().rstrip(".?!")
    pdf = _generar_pdf(titulo, contenido, "Louis")
    if pdf:
        import datetime as _dt
        fname = (
            titulo[:40].replace(" ", "_")
            + "_"
            + _dt.datetime.now().strftime("%H%M%S")
            + ".pdf"
        )
        _enqueue_file(pdf, fname, f"📄 {titulo[:60]}")
    return contenido, CLAUDE_SONNET


# ═══════════════════════════════════════════════════════════════════════════
# CEREBRO KAWIIL — helpers de lectura directa del almacén compartido
# Louis lee del disco local (0 tokens). Cowork escribe vía MCP.
# ═══════════════════════════════════════════════════════════════════════════

def _cerebro_parsear_fm(path: Path) -> dict:
    meta: dict = {"archivo": path.name, "titulo": path.stem, "estado": "desconocido"}
    try:
        content = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
    except Exception:
        pass
    return meta


def _cerebro_listar(estado: str = "", cliente: str = "") -> str:
    if not ENTREGABLES_PATH.exists():
        return f"Cerebro no disponible en {ENTREGABLES_PATH}."
    items = []
    for f in sorted(ENTREGABLES_PATH.glob("*.md")):
        if f.name.startswith("_"):
            continue
        meta = _cerebro_parsear_fm(f)
        if estado and meta.get("estado", "").lower() != estado.lower():
            continue
        if cliente and cliente.lower() not in meta.get("cliente", "").lower():
            continue
        icono = {"borrador": "📝", "listo": "✅", "en_vobo": "🔄",
                 "aprobado": "✔️", "archivado": "📦"}.get(meta.get("estado", ""), "❓")
        items.append(
            f"{icono} {meta.get('titulo', f.stem)} | "
            f"{meta.get('cliente', '?')} | {meta.get('estado', '?')} | "
            f"{meta.get('fecha_actualizacion', '?')}"
        )
    n_briefs = len(list(BRIEFS_PATH.glob("*.md"))) if BRIEFS_PATH.exists() else 0
    if not items:
        return f"Sin entregables. Briefs pendientes: {n_briefs}"
    return "\n".join(items) + f"\n(Total: {len(items)} | Briefs: {n_briefs})"


def _cerebro_proyecto_estado(nombre: str) -> str:
    if not ENTREGABLES_PATH.exists():
        return "Cerebro no disponible."
    termino = nombre.lower()
    for f in ENTREGABLES_PATH.glob("*.md"):
        if f.name.startswith("_"):
            continue
        meta = _cerebro_parsear_fm(f)
        if termino in f.stem.lower() or termino in meta.get("titulo", "").lower():
            lineas = [f"Entregable: {meta.get('titulo', f.stem)}"]
            for campo in ("cliente", "estado", "vobo", "responsable",
                          "fecha_creacion", "fecha_actualizacion"):
                if campo in meta:
                    lineas.append(f"  {campo}: {meta[campo]}")
            return "\n".join(lineas)
    return f"No encontrado: «{nombre}»"


def _cerebro_crear_brief(tarea: str, cliente: str, insumos: str = "",
                          urgencia: str = "normal", contexto: str = "") -> str:
    BRIEFS_PATH.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now(TZ_CDMX)
    fecha = ahora.strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", tarea.lower()).strip("-")[:55]
    filepath = BRIEFS_PATH / f"{fecha}-brief-{slug}.md"
    agenda_f = SPACE / "AGENDA.md"
    agenda_ctx = "(sin entradas relevantes)"
    if agenda_f.exists():
        lineas = agenda_f.read_text(encoding="utf-8").splitlines()
        relevantes = [l for l in lineas
                      if tarea.lower()[:20] in l.lower() or cliente.lower()[:15] in l.lower()][:6]
        if relevantes:
            agenda_ctx = "\n".join(relevantes)
    previos = []
    if ENTREGABLES_PATH.exists():
        for f in ENTREGABLES_PATH.glob("*.md"):
            if not f.name.startswith("_"):
                meta = _cerebro_parsear_fm(f)
                if cliente.lower() in meta.get("cliente", "").lower():
                    previos.append(f"  • {meta.get('titulo', f.stem)} ({meta.get('estado', '?')})")
    content = (
        f"---\ntipo: brief_dispatch\ntarea: {tarea}\ncliente: {cliente}\n"
        f"urgencia: {urgencia}\nestado: pendiente\npreparado_por: Louis\n"
        f"fecha_creacion: {fecha} {ahora.strftime('%H:%M')}\n---\n\n"
        f"# Brief: {tarea}\n\nCliente: {cliente} | Urgencia: {urgencia}\n\n"
        f"## Tarea\n{tarea}\n\n"
        f"## Insumos\n{insumos or 'Consultar acervo legal y memoria según aplique.'}\n\n"
        f"## Entregables previos\n" + ("\n".join(previos) if previos else "  (ninguno)") +
        f"\n\n## AGENDA relevante\n{agenda_ctx}\n\n"
        f"## Contexto adicional\n{contexto or '(ninguno)'}\n"
    )
    filepath.write_text(content, encoding="utf-8")
    try:
        _encolar_notificacion(
            f"📋 *Brief listo en Cerebro Kawiil*\n"
            f"Tarea: {tarea}\nCliente: {cliente} | Urgencia: {urgencia}\n"
            f"Ábrelo en Cowork para tomarlo.",
            canal="telegram",
        )
    except Exception:
        pass
    return (
        f"✅ Brief: _briefs/{filepath.name} | urgencia:{urgencia} | previos:{len(previos)}\n"
        f"Polo fue notificado por Telegram."
    )


def _cerebro_sync_agenda() -> str:
    agenda_f = SPACE / "AGENDA.md"
    if not agenda_f.exists():
        return "AGENDA.md no disponible."
    if not ENTREGABLES_PATH.exists():
        return f"Cerebro no disponible en {ENTREGABLES_PATH}."
    indice: dict = {}
    for f in ENTREGABLES_PATH.glob("*.md"):
        if not f.name.startswith("_"):
            meta = _cerebro_parsear_fm(f)
            titulo = meta.get("titulo", f.stem).lower()
            indice[titulo] = meta.get("estado", "?")
            for palabra in f.stem.lower().split("-"):
                if len(palabra) > 4:
                    indice.setdefault(palabra, meta.get("estado", "?"))
    agenda_txt = agenda_f.read_text(encoding="utf-8")
    pendientes = [l.strip() for l in agenda_txt.splitlines()
                  if re.match(r"^\s*-\s*\[\s*\]\s+", l)]
    discrepancias = []
    for pend in pendientes:
        texto = re.sub(r"^\s*-\s*\[\s*\]\s+", "", pend).lower()
        for titulo_cerebro, estado_cerebro in indice.items():
            if len(titulo_cerebro) > 4 and titulo_cerebro in texto:
                if estado_cerebro in ("listo", "aprobado", "en_vobo"):
                    discrepancias.append(
                        f"• AGENDA dice pendiente → Cerebro dice «{estado_cerebro}»:\n"
                        f"  AGENDA: {pend}\n  Cerebro: {titulo_cerebro} ({estado_cerebro})"
                    )
                break
    if not discrepancias:
        return (
            f"Sincronización OK. {len(pendientes)} pendientes en AGENDA, "
            f"ninguno contradice el estado del Cerebro."
        )
    reporte = "\n".join(discrepancias)
    try:
        _encolar_notificacion(
            f"🔄 *Cerebro Kawiil — discrepancias*\n\n{reporte[:600]}\n"
            f"Louis puede actualizar AGENDA con agenda_marcar_hecho().",
            canal="telegram",
        )
    except Exception:
        pass
    return f"⚠️ {len(discrepancias)} discrepancia(s):\n\n{reporte}"


def _encolar_notificacion(mensaje: str, canal: str = "telegram") -> None:
    """Encola una notificación inmediata al scheduler."""
    REMINDERS_DIR.mkdir(parents=True, exist_ok=True)
    import uuid as _u
    entry = {
        "id": str(_u.uuid4())[:8],
        "fire_at": datetime.now(TZ_CDMX).isoformat(),
        "message": mensaje,
        "channel": canal,
        "mode": "raw",
        "recurrence": None,
        "created_at": datetime.now(TZ_CDMX).isoformat(),
        "source": "cerebro",
    }
    try:
        queue = _read_queue()
    except Exception:
        queue = []
    queue.append(entry)
    _write_queue(queue)


def _cerebro_entregables_snapshot() -> str:
    """Línea compacta del estado del cerebro para build_operational_snapshot."""
    if not ENTREGABLES_PATH.exists():
        return ""
    conteo: dict = {}
    for f in ENTREGABLES_PATH.glob("*.md"):
        if not f.name.startswith("_"):
            meta = _cerebro_parsear_fm(f)
            e = meta.get("estado", "?")
            conteo[e] = conteo.get(e, 0) + 1
    if not conteo:
        return ""
    partes = [f"{e}:{n}" for e, n in sorted(conteo.items())]
    n_briefs = len(list(BRIEFS_PATH.glob("*.md"))) if BRIEFS_PATH.exists() else 0
    return " | ".join(partes) + (f" | briefs:{n_briefs}" if n_briefs else "")
'''

missing = [fn for fn in (
    "needs_doc_sonnet", "get_pending_files", "_generar_pdf",
    "generar_documento_directo", "_cerebro_parsear_fm",
    "_cerebro_listar", "_cerebro_crear_brief", "_cerebro_sync_agenda",
    "_encolar_notificacion", "_cerebro_entregables_snapshot",
) if f"def {fn}" not in src]

if missing:
    src = src.rstrip("\n") + "\n" + NEW_FUNCTIONS
    changes.append(f"funciones: {', '.join(missing)}")

# ── 4) Tools en TOOLS_DEFINITION ──────────────────────────────────────────
CEREBRO_TOOLS = """
    # ── Cerebro Kawiil ──────────────────────────────────────────────────────
    {
        "name": "cerebro_listar",
        "description": (
            "Lista entregables del cerebro compartido Cowork↔Louis. "
            "Filtrar por estado (borrador/listo/en_vobo/aprobado/archivado) y/o cliente. "
            "Usar para saber qué ya produjo Cowork antes de reportar algo como pendiente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "estado": {"type": "string"},
                "cliente": {"type": "string"},
            },
        },
    },
    {
        "name": "cerebro_proyecto_estado",
        "description": "Estado de un entregable específico (frontmatter). Confirma estado real de un proyecto.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre": {"type": "string"}},
            "required": ["nombre"],
        },
    },
    {
        "name": "cerebro_crear_brief",
        "description": (
            "Crea un brief de dispatch para que Cowork produzca un entregable. "
            "Notifica a Polo por Telegram inmediatamente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tarea": {"type": "string"},
                "cliente": {"type": "string"},
                "insumos": {"type": "string"},
                "urgencia": {"type": "string", "enum": ["baja", "normal", "alta", "urgente"], "default": "normal"},
                "contexto": {"type": "string"},
            },
            "required": ["tarea", "cliente"],
        },
    },
    {
        "name": "cerebro_sync_agenda",
        "description": (
            "Compara pendientes de AGENDA con el cerebro. "
            "Detecta lo que Louis reporta pendiente pero ya está listo en Cowork. "
            "Notifica a Polo si hay discrepancias."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
"""

if '"cerebro_listar"' not in src:
    # Insert before the closing ] of TOOLS_DEFINITION
    # Find the pattern: last },\n] before def _run_m365_tool
    pattern = re.compile(r'(\s*\},\s*\]\s*\n\n\ndef _run_m365_tool)')
    if pattern.search(src):
        src = pattern.sub(CEREBRO_TOOLS + r'\1', src, count=1)
        changes.append("TOOLS_DEFINITION cerebro")
    else:
        # Fallback: find ]\n\n\ndef _run_m365_tool
        src = src.replace(
            "]\n\n\ndef _run_m365_tool",
            CEREBRO_TOOLS + "]\n\n\ndef _run_m365_tool",
            1,
        )
        if '"cerebro_listar"' in src:
            changes.append("TOOLS_DEFINITION cerebro (fallback)")

# ── 5) execute_tool handlers ───────────────────────────────────────────────
CEREBRO_HANDLERS = (
    '        elif name == "cerebro_listar":\n'
    '            return _cerebro_listar(args.get("estado", ""), args.get("cliente", ""))\n'
    '        elif name == "cerebro_proyecto_estado":\n'
    '            return _cerebro_proyecto_estado(args["nombre"])\n'
    '        elif name == "cerebro_crear_brief":\n'
    '            return _cerebro_crear_brief(args["tarea"], args["cliente"], '
    'args.get("insumos", ""), args.get("urgencia", "normal"), args.get("contexto", ""))\n'
    '        elif name == "cerebro_sync_agenda":\n'
    '            return _cerebro_sync_agenda()\n'
)

if "cerebro_listar" not in src.split("def execute_tool")[-1]:
    target = '        elif name.startswith("m365_"):'
    if target in src:
        src = src.replace(target, CEREBRO_HANDLERS + target, 1)
        changes.append("execute_tool handlers")

# ── Verificar sintaxis ─────────────────────────────────────────────────────
import ast
try:
    ast.parse(src)
except SyntaxError as e:
    sys.exit(f"ERROR de sintaxis tras patch: {e}\nNo se escribió nada.")

# ── Escribir ───────────────────────────────────────────────────────────────
TARGET.write_text(src, encoding="utf-8")

if changes:
    print(f"✅ Patch aplicado ({TARGET.stat().st_size} bytes):")
    for c in changes:
        print(f"   + {c}")
else:
    print("⏭  Sin cambios necesarios (todo ya estaba presente)")

print("\nPróximo paso:")
print("  sudo systemctl restart telegram-bridge slack-bridge")
print("  sudo journalctl -u telegram-bridge -n 5 --no-pager")
