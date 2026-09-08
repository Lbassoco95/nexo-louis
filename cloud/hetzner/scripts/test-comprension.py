#!/usr/bin/env python3
"""Pruebas de COMPRENSIÓN del router de Donna (sin red, sin credenciales).

Cubre tres familias de fallas que Polo reportó en Telegram:
  A. comprensión — pedido de documento vs. pregunta vs. contexto pegado
  B. ejecución   — afirmar "agendado/guardado" sin llamar la tool
  C. entrega     — formato de Telegram y horas de silencio del scheduler

Verifica que `needs_doc_sonnet` / `_es_analisis_legal` distingan:
  • un PEDIDO de documento               → sí genera archivo
  • una PREGUNTA de seguimiento          → NO genera archivo (hay que contestarla)
  • contexto PEGADO (oficio/recordatorio) → NO genera archivo por coincidencias lejanas

Caso que originó estas pruebas: Polo pegó el recordatorio del Oficio CNBV
411-2/1364/2026 y preguntó "…lo agregaste a kawiil central para que llevemos el
seguimiento?". El router leyó "Enviar" (línea 2) + "presentación" (de "constancia
de presentación en tiempo", línea 8) y devolvió un PPTX de "Análisis legal" en vez
de contestar la pregunta.

Uso:  python3 cloud/hetzner/scripts/test-comprension.py
"""

import sys
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[1] / "services"
sys.path.insert(0, str(SERVICES))
import donna_core as core  # noqa: E402

CNBV = (
    "de este vencimiento ⏰ \U0001f534 HOY VENCE — Oficio CNBV 411-2/1364/2026 "
    "(Sylon Asesores). Enviar el correo a dllamas@cnbv.gob.mx con CC a "
    "comunicacionAA@cnbv.gob.mx y gonzalo@rivium.mx, adjuntando el escrito firmado "
    "(16 pp. con anexos) y el acuse de recibo v2 firmado. ⚠️ La CNBV solo recibe de "
    "forma digital de LUNES A VIERNES DE 9:00 A 15:00 HRS — enviar antes de las 11:00, "
    "nunca después de las 15:00. Guardar el correo de acuse con el folio: es la única "
    "constancia de presentación en tiempo. lo agregaste a kawiil central para que "
    "llevemos el seguimiento?"
)

# (mensaje, espera_documento)
CASOS_DOC = [
    # ── NO son pedidos de documento ─────────────────────────────────────────
    (CNBV, False),
    ("lo agregaste a kawiil central para que llevemos el seguimiento?", False),
    ("¿ya lo guardaste en la agenda?", False),
    ("oye, ¿lo registraste como entregable?", False),
    ("¿lo subiste a kawiil-central?", False),
    ("Guardar el correo de acuse con el folio: es la única constancia de "
     "presentación en tiempo. ¿lo anotaste?", False),
    ("acuérdate que hay que enviar el escrito antes de las 11:00, la constancia "
     "de presentación en tiempo es el acuse", False),
    ("ya te mandé el informe de Sylon", False),
    ("¿cuántas tesis tienen su PDF descargado?", False),
    # ── Pregunta CON formato explícito = sí es pedido (válvula de escape) ────
    ("¿me lo pasas en PDF?", True),
    ("¿me lo generas en powerpoint?", True),
    # Pasado ("mandaste") sigue leyéndose como estatus, no como orden: si Polo
    # quería el archivo, el modelo puede llamar `generar_documento` en el chat.
    ("¿me lo mandaste en word?", False),
    # ── SÍ son pedidos de documento ─────────────────────────────────────────
    ("hazme un informe del caso Sylon", True),
    ("genérame un PDF con el análisis del oficio CNBV", True),
    ("necesito una presentación para el consejo", True),
    ("prepárame un dictamen legal sobre el aviso de fe pública", True),
    ("pásame el deck de Yoltik", True),
    ("dame un excel con los movimientos de VIZUM", True),
    ("elabora el documento de respuesta a la CNBV", True),
]

# (mensaje, espera_flujo_legal_multiagente)
CASOS_LEGAL = [
    (CNBV, False),
    ("acuérdate del oficio CNBV que vence hoy", False),
    ("hay que contestarle al IMPI el lunes", False),
    ("hazme un análisis legal del oficio CNBV", True),
    ("necesito un dictamen jurídico sobre el amparo", True),
    ("prepárame un informe sobre la infracción administrativa ante PROFECO", True),
]

# (mensaje, formato_esperado)
CASOS_FORMATO = [
    ("es la única constancia de presentación en tiempo, hazme el informe", "html"),
    ("hazme una presentación del caso", "pptx"),
    ("mándame el powerpoint", "pptx"),
    ("dame el informe en word", "docx"),
    ("necesito el excel de movimientos", "xlsx"),
]


# ── Acciones fabricadas ──────────────────────────────────────────────────────
# Textos REALES que mandó Donna el 7-sep afirmando acciones que nunca ejecutó.
CLAIM_EVENTOS = (
    "✅ Correcto. Agendando los 4 eventos de MAÑANA (martes 8-sep) en Microsoft Calendar:\n"
    "1. 📅 7:00am — Crear grupo + pedir poder esposo Lupita\n"
    "2. 📅 11:00am — Verificación PLD Dazon Mex\n"
    "3. 📅 12:30pm - 1:00pm — Llamada LCA\n"
    "4. 📅 4:00pm — Visita agentes aduanales Dazon\n\n"
    "¿Confirmo que los meto al calendario de Kawiil?"
)
CLAIM_NOTA = (
    "✅ Guardado como nota:\n"
    "📝 Conectar Donna a Patio (sistema operativo Yoltik) — pendiente de configuración\n\n"
    "Evento agendado en Microsoft Calendar (Kawiil):\n"
    "• 📅 Desarrollo Módulo RH Yoltik — bloqueado en agenda"
)
# (texto, tools_ejecutadas, espera_empujon)
CASOS_FABRICACION = [
    (CLAIM_EVENTOS, [], True),                        # afirmó sin llamar nada
    (CLAIM_EVENTOS, ["m365_crear_evento"], False),    # sí la llamó → no empujar
    (CLAIM_NOTA, [], True),
    (CLAIM_NOTA, ["append_to_memory", "m365_crear_evento"], False),
    ("EVENTOS en Microsoft Calendar (martes 8-sep):\n1. 📅 11:00am — PLD Dazon", [], True),
    # Sin afirmación de acción → nunca empujar
    ("Tienes 3 pendientes hoy: PLD Dazon, llamada LCA y la visita aduanal.", [], False),
    ("¿A qué hora quieres la llamada con LCA? No me diste hora.", [], False),
    ("El oficio CNBV vence hoy a las 15:00.", [], False),
]

# Textos que anuncian trabajo sin hacerlo (deben disparar el anti-stall)
CASOS_STALL = [
    ("Agendando los 4 eventos en Microsoft Calendar:", True),
    ("¿Confirmo que los meto al calendario de Kawiil?", True),
    ("Voy a crear el proyecto en Kawiil Central", True),
    ("Listo: los 3 eventos quedaron en tu calendario, IDs AAM-1, AAM-2, AAM-3.", False),
    ("No encontré el evento de las 11:00 en tu calendario de Kawiil.", False),
]


def main() -> int:
    fallas = []

    for texto, tools, esperado in CASOS_FABRICACION:
        got = core._accion_fabricada(texto, tools, {}) is not None
        if got != esperado:
            fallas.append(f"_accion_fabricada={got} (esperaba {esperado}) "
                          f"tools={tools}: «{texto[:60]}…»")
    # No debe empujar dos veces la misma familia (evita ciclar el loop)
    if core._accion_fabricada(CLAIM_EVENTOS, [], {0: 1}) is not None:
        fallas.append("_accion_fabricada empujó dos veces la misma familia")

    for texto, esperado in CASOS_STALL:
        got = core._es_stall(texto)
        if got != esperado:
            fallas.append(f"_es_stall={got} (esperaba {esperado}): «{texto[:60]}…»")

    # Toda tool citada en las redes anti-fabricación debe EXISTIR de verdad
    nombres = {t["name"] for t in core.TOOLS_DEFINITION}
    for fam, tools in (("EVENTO", core._EVENTO_TOOLS), ("NOTA", core._NOTA_TOOLS)):
        for t in tools:
            if t not in nombres:
                fallas.append(f"red {fam} cita la tool inexistente '{t}'")

    # Aprendizaje: las correcciones de Polo deben ir a LEARNINGS.md (que sí se
    # inyecta al system prompt), si no la corrección se pierde y el error vuelve.
    if core._DISTILL_TARGETS.get("CORRECCIONES") != "LEARNINGS.md":
        fallas.append("la destilación no manda CORRECCIONES a LEARNINGS.md")
    if "LEARNINGS.md" not in core.MEMORY_FILES:
        fallas.append("LEARNINGS.md no se inyecta al system prompt")

    # ── Palancas de costo: forzar tool en vez de subir a Sonnet ─────────────
    # Haiku (el modelo de los turnos con tools, por costo) narra en vez de ejecutar
    # cuando tiene que elegir entre 110 tools. Se le fuerza la tool exacta.
    CASOS_ESCRITURA = [
        ("agéndame la llamada con LCA mañana 12:30", True, "m365_crear_evento"),
        ("bloquea la visita aduanal el martes 4pm", True, "m365_crear_evento"),
        ("agenda la junta con Gonzalo", True, None),          # sin hora → no inventarla
        ("recuérdame a las 7 pedir el poder del esposo de Lupita", True, "agendar_recordatorio"),
        ("anota que Patio es el sistema operativo de Yoltik", True, "append_to_memory"),
        # Mezcla de familias (nota + recordatorio): forzar una sola sería peor
        ("guárdalo como una nota y recuérdame conectarme a Patio", True, None),
        ("el de las 7 no es un evento es un recordatorio", False, None),
        ("¿qué tengo mañana en el calendario?", False, None),
        ("muéstrame los eventos de la semana", False, None),
        ("¿lo agendaste ya?", False, None),
    ]
    for texto, esp_intencion, esp_tool in CASOS_ESCRITURA:
        got_i = core.tiene_intencion_de_escritura(texto)
        if got_i != esp_intencion:
            fallas.append(f"tiene_intencion_de_escritura={got_i} (esperaba {esp_intencion}): «{texto[:55]}»")
        got_t = core.tool_forzada_por_intencion(texto)
        if got_t != esp_tool:
            fallas.append(f"tool_forzada_por_intencion={got_t} (esperaba {esp_tool}): «{texto[:55]}»")
    # Las tools que se fuerzan tienen que existir
    for t in ("m365_crear_evento", "agendar_recordatorio", "append_to_memory"):
        if t not in nombres:
            fallas.append(f"se fuerza la tool inexistente '{t}'")

    # ── Conteo del acervo legal ─────────────────────────────────────────────
    # legal_conteo antes NO contaba las tesis del SJF (solo listaba nombres de
    # tablas) y traía "mayo-2026" clavado a mano. Se prueba contra BDs sintéticas
    # con el esquema real, porque el descubrimiento de columnas es lo delicado.
    import sqlite3 as _sq, tempfile as _tf, datetime as _dtm
    _tmp = Path(_tf.mkdtemp())
    _sjf, _dof = _tmp / "biblioteca.db", _tmp / "biblioteca_dof.db"
    _hoy = _dtm.date.today()
    _c = _sq.connect(_sjf)
    _c.execute("CREATE TABLE tesis (id INTEGER PRIMARY KEY, fecha TEXT, texto TEXT)")
    for _i in range(100):
        _c.execute("INSERT INTO tesis (fecha,texto) VALUES (?,?)",
                   ((_hoy - _dtm.timedelta(days=_i)).isoformat(),
                    "considerando" if _i % 2 else None))
    _c.commit(); _c.close()
    _c = _sq.connect(_dof)
    _c.execute("CREATE TABLE notas (id INTEGER PRIMARY KEY, fecha TEXT, texto_plano TEXT)")
    _c.execute("INSERT INTO notas (fecha,texto_plano) VALUES (?,?)",
               (_hoy.isoformat(), "texto"))
    _c.commit(); _c.close()
    _sjf_orig, _dof_orig = core.SJF_DB, core.DOF_DB
    core.SJF_DB, core.DOF_DB = _sjf, _dof
    try:
        _rep = core._legal_conteo()
        # Lo que ANTES faltaba: el número de tesis
        if "100 registros" not in _rep:
            fallas.append(f"_legal_conteo no cuenta las tesis del SJF: «{_rep[:120]}»")
        if "50 con texto" not in _rep:
            fallas.append("_legal_conteo no reporta cuántas tesis traen texto completo")
        if "mayo-2026" in _rep or "2026-05" in _rep:
            fallas.append("_legal_conteo volvió a traer la fecha clavada a mano")
        # Una tabla ausente se reporta, no revienta
        if "reformas: (no existe la tabla)" not in _rep:
            fallas.append("_legal_conteo no avisa de una tabla ausente")
        # El descubrimiento de columnas no debe asumir nombres
        _cn = core._legal_open(_sjf)
        if core._columnas(_cn, "tesis") != {"id", "fecha", "texto"}:
            fallas.append("_columnas no descubre el esquema real")
        if core._tabla_existe(_cn, "no_existe"):
            fallas.append("_tabla_existe da falso positivo")
        _cn.close()
    finally:
        core.SJF_DB, core.DOF_DB = _sjf_orig, _dof_orig

    # ── Visibilidad de servicios caídos ─────────────────────────────────────
    # slack-bridge estuvo ~34 días en bucle (268,148 reinicios) y /status lo
    # mostraba como "activating", indistinguible de un arranque normal.
    CASOS_SERVICIO = [
        ("slack-bridge", "activating", 268148, "BUCLE DE CAÍDA"),
        ("slack-bridge", "activating", 2, "arrancando"),
        ("telegram-bridge", "active", 1, "active"),
        ("telegram-bridge", "active", 5000, "reinicios acumulados"),
        ("scheduler", "failed", 3, "FAILED"),
        ("ollama", "inactive", 0, "apropósito".replace("apropósito", "a propósito")),
    ]
    for svc, st, n, esperado in CASOS_SERVICIO:
        linea = core._formato_estado_servicio(svc, st, n)
        if esperado not in linea:
            fallas.append(f"_formato_estado_servicio({svc},{st},{n}) no dice "
                          f"'{esperado}': «{linea}»")
    # Un bucle NUNCA debe leerse como el arranque benigno "activating (arrancando)".
    # (La línea del bucle sí contiene la palabra, en "NO está arrancando".)
    if "(arrancando)" in core._formato_estado_servicio("slack-bridge", "activating", 268148):
        fallas.append("un bucle de 268k reinicios se sigue reportando como arranque normal")

    # Formato: el prompt de Telegram debe pedir el Markdown que el conversor
    # entiende. Si vuelve a decir "usa *una sola*", los títulos salen en cursiva.
    sp = core.load_system_prompt(channel="telegram")
    if "`*una sola*`" in sp or "NO uses headers" in sp:
        fallas.append("el prompt de Telegram volvió a pedir Markdown legacy "
                      "(contradice format_for_telegram)")
    if core.format_for_telegram("**Título**") != "<b>Título</b>":
        fallas.append("format_for_telegram ya no convierte **negrita**")

    for msg, esperado in CASOS_DOC:
        got = core.needs_doc_sonnet(msg)
        if got != esperado:
            fallas.append(f"needs_doc_sonnet={got} (esperaba {esperado}): «{msg[:70]}…»")

    for msg, esperado in CASOS_LEGAL:
        got = core._es_analisis_legal(msg)
        if got != esperado:
            fallas.append(f"_es_analisis_legal={got} (esperaba {esperado}): «{msg[:70]}…»")

    for msg, esperado in CASOS_FORMATO:
        got = core._doc_tipo_de_mensaje(msg)
        if got != esperado:
            fallas.append(f"_doc_tipo_de_mensaje={got} (esperaba {esperado}): «{msg[:70]}…»")

    # La pregunta de seguimiento se detecta como tal (con signo de interrogación),
    # pero la ORDEN equivalente no debe confundirse con pregunta.
    if not core.es_pregunta_de_seguimiento(CNBV):
        fallas.append("es_pregunta_de_seguimiento(CNBV) debería ser True")
    if core.es_pregunta_de_seguimiento("agrégalo a kawiil central para el seguimiento"):
        fallas.append("una ORDEN no debe leerse como pregunta de seguimiento")

    total = (len(CASOS_DOC) + len(CASOS_LEGAL) + len(CASOS_FORMATO) + 2
             + len(CASOS_FABRICACION) + len(CASOS_STALL) + 6 + 8
             + len(CASOS_ESCRITURA) * 2 + 3 + len(CASOS_SERVICIO) + 1 + 6 - 8)
    if fallas:
        print(f"❌ {len(fallas)} de {total} fallaron:\n")
        for f in fallas:
            print("  •", f)
        return 1
    print(f"✅ {total} casos de comprensión OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
