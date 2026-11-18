#!/usr/bin/env python3
"""donna_voz.py — acciones DIRECTAS que Donna ejecuta por voz (sin pasar por un modelo): guardar una nota, fijar una regla permanente,
crear un recordatorio. Cada una VERIFICA el resultado y devuelve «✅ …» solo si de verdad quedó hecho, o «❌ …» con el motivo.
Así lo que Donna dice en voz alta («ya lo guardé») coincide con lo que realmente pasó."""
import re
from datetime import datetime

ARCHIVOS = {
    "agenda": "AGENDA.md", "pendientes": "AGENDA.md", "importante": "IMPORTANT.md", "personal": "PERSONAL.md", "academico": "ACADEMICO.md",
    "académico": "ACADEMICO.md", "diplomado": "ACADEMICO.md", "doctorado": "ACADEMICO.md", "familia": "FAMILIA.md", "salud": "SALUD.md",
    "proyectos": "PROJECTS.md", "prospectos": "PROSPECTOS.md", "clientes": "CLIENTES.md", "finanzas": "FINANZAS.md", "viajes": "VIAJES.md",
    "bitacora": "JOURNAL.md", "bitácora": "JOURNAL.md", "diario": "JOURNAL.md", "equipo": "PEOPLE.md", "gente": "PEOPLE.md",
    "aprendizajes": "LEARNINGS.md", "reglas": "LEARNINGS.md",
}
_FALLO = ("❌", "ERROR", "Error", "(error", "🔒")


def _core():
    import louis_core as core
    return core


def _archivo(nombre: str):
    core = _core()
    n = re.sub(r"\.md$", "", (nombre or "agenda").strip().lower())
    f = ARCHIVOS.get(n) or (n.upper() + ".md")
    return f if f in core.MEMORY_FILES or f == "JOURNAL.md" else None


def _leer_cola(f: str, n: int = 4000) -> str:
    core = _core()
    p = core.SPACE / f
    try:
        return p.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def guardar_nota(texto: str, archivo: str = "agenda") -> str:
    texto = re.sub(r"\s+", " ", (texto or "")).strip()
    if len(texto) < 3:
        return "❌ No hay nada que guardar: falta el texto."
    f = _archivo(archivo)
    if not f:
        return f"❌ No existe el archivo de memoria «{archivo}». Opciones: agenda, importante, personal, academico, familia, salud, proyectos, clientes, finanzas, viajes, bitacora."
    core = _core()
    sello = datetime.now(core.get_active_tz()).strftime("%Y-%m-%d")
    r = core.execute_tool("append_to_memory", {"filename": f, "content": f"- [{sello}] {texto}\n"})
    if str(r).lstrip().startswith(_FALLO):
        return f"❌ No pude guardar en {f}: {str(r)[:160]}"
    if texto[:40].lower() not in _leer_cola(f).lower():             # verificación: se relee el archivo
        return f"❌ No pude confirmar que quedó guardado en {f}. No lo des por hecho."
    return f"✅ Guardado en {f}: «{texto[:140]}»"


def recordar_regla(regla: str, contexto: str = "") -> str:
    regla = re.sub(r"\s+", " ", (regla or "")).strip()
    if len(regla) < 5:
        return "❌ Falta la regla o preferencia que debo recordar."
    core = _core()
    r = core.execute_tool("save_learning", {"topic": regla[:60], "rule": regla, "context": (contexto or "dictada por voz")[:300]})
    if str(r).lstrip().startswith(_FALLO):
        return f"❌ No pude guardar la regla: {str(r)[:160]}"
    if regla[:40].lower() not in _leer_cola("LEARNINGS.md").lower():
        return "❌ No pude confirmar que la regla quedó guardada. No lo des por hecho."
    return f"✅ Regla guardada para siempre en LEARNINGS.md: «{regla[:140]}»"


def crear_recordatorio(mensaje: str, fecha_hora: str = "", en_minutos: int = 0) -> str:
    mensaje = re.sub(r"\s+", " ", (mensaje or "")).strip()
    if len(mensaje) < 3:
        return "❌ Falta el texto del recordatorio."
    try:
        en_minutos = int(en_minutos or 0)
    except (TypeError, ValueError):
        en_minutos = 0
    if en_minutos <= 0 and not (fecha_hora or "").strip():
        return "❌ Falta cuándo: dime la fecha y hora, o «en X minutos»."
    core = _core()
    r = str(core._agendar_recordatorio(mensaje, (fecha_hora or "").strip(), "telegram", "raw", None, en_minutos))
    if r.lstrip().startswith(_FALLO):
        return f"❌ No pude crear el recordatorio: {r[:160]}"
    return f"✅ Recordatorio creado: {r[:200]}"
