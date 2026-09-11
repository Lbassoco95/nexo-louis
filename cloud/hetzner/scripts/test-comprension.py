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

# Se prueba el RUNTIME, no el paquete. /opt/openclaw/scripts/ es lo que systemd
# ejecuta; /opt/louis/services/ es solo el destino del rsync. Probar el paquete ya
# dio un reporte verde falso una vez: la lista de instalación decía `louis_core.py`
# tras el renombre, el runtime se quedó con el core viejo y las pruebas pasaron
# igual porque leían el paquete recién copiado. Fuera del server no existe el
# runtime, así que ahí se cae al repo.
_RUNTIME = Path("/opt/openclaw/scripts")
_REPO = Path(__file__).resolve().parents[1] / "services"
SERVICES = _RUNTIME if (_RUNTIME / "donna_core.py").is_file() else _REPO
print(f"· probando {SERVICES}")
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


# ── Diagnósticos NEGATIVOS inventados ───────────────────────────────────────
# Caso real: Donna dijo "❌ Slack desconectado — Token inactivo". El token era
# válido (auth.test pasaba); el error real era `not_in_channel`. Si afirma que algo
# está roto sin llamar la tool que lo comprueba, hay que forzarla a comprobarlo.
# (texto, tools_ejecutadas, espera_empujon)
CASOS_DIAGNOSTICO = [
    ("❌ Slack desconectado — Token inactivo", [], True),
    ("Slack está caído, no tengo credenciales.", [], True),
    # Sí verificó → el diagnóstico es legítimo, no se empuja
    ("❌ Slack desconectado — Token inactivo", ["slack_canales"], False),
    ("Slack está caído.", ["verificar_conexiones"], False),
    # El diagnóstico REAL y accionable no se re-verifica
    ("Slack sí funciona; la app no está invitada al canal. Corre `/invite @louis`.",
     [], False),
    ("Slack devolvió not_in_channel: falta invitar la app. El token está bien.",
     [], False),
    # Otros servicios
    ("No tengo acceso al calendario de Microsoft, la integración está caída.", [], True),
    ("El calendario está caído.", ["m365_calendario"], False),
    ("El acervo del DOF está desconectado.", [], True),
    ("El acervo del DOF está desconectado.", ["legal_estado"], False),
    ("La Mac está fuera de línea.", ["mac_estado"], False),
    ("El servicio scheduler está caído.", [], True),
    ("El servicio scheduler está caído.", ["verificar_conexiones"], False),
    # Sin diagnóstico negativo → nunca empujar
    ("Slack tiene 2 canales: capacitaciones y contabilidad.", [], False),
    ("Tienes 3 pendientes hoy.", [], False),
    ("El oficio CNBV vence hoy a las 15:00.", [], False),
]


def main() -> int:
    fallas = []

    for texto, tools, esperado in CASOS_DIAGNOSTICO:
        got = core._diagnostico_fabricado(texto, tools, {}) is not None
        if got != esperado:
            fallas.append(f"_diagnostico_fabricado={got} (esperaba {esperado}) "
                          f"tools={tools}: «{texto[:60]}…»")
    # No debe empujar dos veces el mismo servicio (evita ciclar el loop)
    if core._diagnostico_fabricado("Slack desconectado", [], {"diag:slack": 1}) is not None:
        fallas.append("_diagnostico_fabricado empujó dos veces el mismo servicio")
    # Toda tool citada en las redes de diagnóstico debe EXISTIR de verdad
    _nombres_tools = {t["name"] for t in core.TOOLS_DEFINITION}
    for clave, _re, _tools, _txt in core._DIAG_SERVICIOS:
        for t in _tools:
            if t not in _nombres_tools:
                fallas.append(f"_DIAG_SERVICIOS[{clave}] cita tool inexistente: {t}")

    # ── Traducción de errores de Slack ──────────────────────────────────────
    # `not_in_channel` NO es un problema de credenciales; decirle "token inactivo"
    # a Polo lo manda a regenerar un token que estaba bien.
    class _FakeResp(dict):
        pass

    class _FakeSlackErr(Exception):
        def __init__(self, code):
            super().__init__(f"The request to the Slack API failed. {{'ok': False, 'error': '{code}'}}")
            self.response = _FakeResp({"ok": False, "error": code})

    # (código, debe_mencionar, NO_debe_mencionar)
    # NOTA: los mensajes de los errores NO-de-token mencionan "token" y "credenciales"
    # a propósito, para NEGARLO ("el token SÍ es válido"). Por eso lo que se prohíbe
    # aquí es la afirmación de que hay que regenerar/recargar el token.
    CASOS_SLACK_ERR = [
        ("not_in_channel", "/invite", "regenerarlo"),
        ("channel_not_found", "/invite", "regenerarlo"),
        ("missing_scope", "scope", "regenerarlo"),
        ("ratelimited", "tasa", "regenerarlo"),
        ("invalid_auth", "regenerarlo", "invite"),
        ("token_revoked", "revocado", "invite"),
        ("account_inactive", "desactivada", "invite"),
    ]
    for code, debe, no_debe in CASOS_SLACK_ERR:
        msg = core._slack_error_humano(_FakeSlackErr(code), "leyendo #contabilidad")
        if core._slack_error_codigo(_FakeSlackErr(code)) != code:
            fallas.append(f"_slack_error_codigo no extrajo '{code}'")
        if debe.lower() not in msg.lower():
            fallas.append(f"_slack_error_humano({code}) no menciona '{debe}': {msg}")
        if no_debe.lower() in msg.lower():
            fallas.append(f"_slack_error_humano({code}) menciona '{no_debe}' y no debería: {msg}")
    # Solo los códigos de credencial muerta se marcan como problema de token
    for code in ("not_in_channel", "channel_not_found", "missing_scope", "ratelimited"):
        msg = core._slack_error_humano(_FakeSlackErr(code))
        if "NO es un problema de credenciales" not in msg:
            fallas.append(f"_slack_error_humano({code}) debería aclarar que no son credenciales: {msg}")
    for code in ("invalid_auth", "token_revoked", "token_expired", "account_inactive"):
        if code not in core._SLACK_ERRORES_DE_TOKEN:
            fallas.append(f"{code} debería contar como error de token")
    # Un error desconocido se reporta crudo, sin inventar diagnóstico
    desconocido = core._slack_error_humano(_FakeSlackErr("algo_raro_nuevo"), "leyendo #x")
    if "algo_raro_nuevo" not in desconocido:
        fallas.append(f"un error desconocido debe reportarse crudo: {desconocido}")
    # El handle del /invite tiene que ser el nombre REAL de la app en Slack
    if core._SLACK_APP_HANDLE != "@louis":
        fallas.append(f"_SLACK_APP_HANDLE={core._SLACK_APP_HANDLE!r}: la app en Slack se "
                      f"llama 'louis'; cambiarlo solo cuando se renombre en api.slack.com")

    # ── Interruptor del WAF del SJF ─────────────────────────────────────────
    # El 10-sep el WAF de la SCJN nos bloqueó y el acervo dejó de crecer (la tesis
    # más nueva quedó en 2026-08-28). Harvest y backfill seguían disparándose y cada
    # corrida quemaba 12 peticiones más contra un WAF que ya había dicho que no.
    import sqlite3 as _sq3, datetime as _dtm, importlib.util as _iu
    _hpath = (Path(__file__).resolve().parents[1] / "legal-scrapers" / "sjf_harvest.py")
    _casos_waf = 0
    if _hpath.is_file():
        _sp = _iu.spec_from_file_location("_h", _hpath)
        _h = _iu.module_from_spec(_sp); _sp.loader.exec_module(_h)
        _wc = _sq3.connect(":memory:")
        pruebas_waf = []
        pruebas_waf.append((_h.waf_bloqueado(_wc) == "", "sin bloqueo se puede trabajar"))
        _h.waf_marcar(_wc)
        pruebas_waf.append(("bloqueo #1" in _h.waf_bloqueado(_wc), "primer bloqueo aparta"))
        _h.waf_marcar(_wc); _h.waf_marcar(_wc); _h.waf_marcar(_wc); _h.waf_marcar(_wc)
        pruebas_waf.append(("24" in _h.waf_bloqueado(_wc), "la espera tope es 24 h"))
        _h.waf_liberar(_wc)
        pruebas_waf.append((_h.waf_bloqueado(_wc) == "",
                            "una descarga exitosa limpia el bloqueo"))
        _h.waf_marcar(_wc)
        _wc.execute("UPDATE progress SET value=? WHERE key='waf_bloqueo'",
                    ((_dtm.datetime.now() - _dtm.timedelta(hours=1))
                     .isoformat(timespec="seconds") + "|3",))
        _wc.commit()
        pruebas_waf.append((_h.waf_bloqueado(_wc) == "", "un bloqueo vencido deja pasar"))
        _wc.execute("UPDATE progress SET value='basura' WHERE key='waf_bloqueo'"); _wc.commit()
        pruebas_waf.append((_h.waf_bloqueado(_wc) == "",
                            "un valor corrupto no truena ni bloquea para siempre"))
        # El ritmo tiene que seguir siendo suave: es lo que nos bloqueó.
        _src = _hpath.read_text()
        pruebas_waf.append(('"SJF_THROTTLE_MS", "1500"' in _src,
                            "el throttle por default es de 1500 ms"))
        _unit = (Path(__file__).resolve().parents[1] / "services" / "sjf-backfill.service")
        if _unit.is_file():
            _u = _unit.read_text()
            pruebas_waf.append(("BACKFILL_THROTTLE_MS=1500" in _u and
                                "BACKFILL_BATCH=1200" in _u,
                                "la unit del backfill pide despacio"))
        for ok, nombre in pruebas_waf:
            if not ok:
                fallas.append(f"WAF: {nombre}")
        _casos_waf = len(pruebas_waf)
    else:
        fallas.append("no encontré sjf_harvest.py — el interruptor del WAF quedó sin probar")

    # ── Búsqueda legal: un cero habla del acervo, no de la ley ──────────────
    # Caso real: Donna contestó «❌ No encuentro resolución reciente de cannabis en
    # SJF/DOF». Dos causas: la consulta cruda reventaba FTS5 con paréntesis o
    # guiones, y con varias palabras FTS5 las exige TODAS.
    import sqlite3 as _sq, tempfile as _tf
    _d = Path(_tf.mkdtemp()); _db = _d / "sjf.db"
    _c = _sq.connect(_db)
    _c.executescript("""
        CREATE TABLE tesis(registro_digital INTEGER PRIMARY KEY, rubro TEXT, texto TEXT,
          epoca TEXT, instancia TEXT, materias TEXT, fecha_publicacion TEXT, ta_tj INTEGER);
        CREATE VIRTUAL TABLE tesis_fts USING fts5(rubro, texto, content='tesis',
          content_rowid='registro_digital', tokenize="unicode61 remove_diacritics 2");
        CREATE TABLE registros_404(registro_digital INTEGER PRIMARY KEY, first_seen_at TEXT);""")
    _c.executemany("INSERT INTO tesis VALUES (?,?,?,?,?,?,?,?)", [
        (2019001, "CANNABIS. LA PROHIBICIÓN ABSOLUTA DE SU CONSUMO LÚDICO ES INCONSTITUCIONAL",
         "amparo en revisión autoconsumo", "Décima", "Primera Sala", "Constitucional",
         "2019-02-15", 1),
        (2021002, "CANNABIS PSICOACTIVO. DECLARATORIA GENERAL DE INCONSTITUCIONALIDAD",
         "Pleno Ley General de Salud", "Undécima", "Pleno", "Constitucional",
         "2021-07-09", 1)])
    _c.execute("INSERT INTO tesis_fts(tesis_fts) VALUES('rebuild')")
    _c.commit(); _c.close()
    _prev_db = core.SJF_DB
    try:
        core.SJF_DB = _db
        # (consulta, debe_encontrar_algo, debe_marcarse_amplia)
        CASOS_BUSQUEDA_SJF = [
            ("cannabis", True, False),
            ("cannabis (Pleno)", True, False),        # reventaba: syntax error
            ("amparo-cannabis", True, False),         # reventaba: no such column
            ('"consumo lúdico"', True, False),        # frase exacta
            ("resolución reciente cannabis", True, True),  # daba 0 por el AND
            ("zarzaparrilla intergaláctica", False, False),
        ]
        for q, hay, esp_amplia in CASOS_BUSQUEDA_SJF:
            r = core._legal_buscar("sjf", q, 5)
            if r.startswith("Error buscando"):
                fallas.append(f"legal_buscar(«{q}») reventó: {r[:80]}")
                continue
            encontro = "resultado(s)" in r
            if encontro != hay:
                fallas.append(f"legal_buscar(«{q}»): encontró={encontro}, esperaba {hay}")
            if encontro and ("AMPLIA" in r) != esp_amplia:
                fallas.append(f"legal_buscar(«{q}») marca AMPLIA={'AMPLIA' in r}, "
                              f"esperaba {esp_amplia}")
            # Un cero SIEMPRE tiene que venir con la cobertura del acervo, para que
            # no se lea como «no existe».
            if not encontro:
                if "Cobertura del acervo" not in r or "INCOMPLETO" not in r:
                    fallas.append(f"un cero en «{q}» no reporta la cobertura: {r[:90]}")
        _casos_legal = len(CASOS_BUSQUEDA_SJF) * 2 + 1
    finally:
        core.SJF_DB = _prev_db

    # _fts_query neutraliza los operadores de FTS5 en las DOS rutas
    for entrada, esperado in (
        ("cannabis", '"cannabis"'),
        ("cannabis Pleno", '"cannabis" AND "Pleno"'),
        ("amparo-cannabis", '"amparo" AND "cannabis"'),
        ('"frase exacta"', '"frase exacta"'),
        ("", ""),
        ("(((", ""),
    ):
        got = core._fts_query(entrada)
        if got != esperado:
            fallas.append(f"_fts_query({entrada!r})={got!r}, esperaba {esperado!r}")
    if core._fts_query("cannabis Pleno", unir="OR") != '"cannabis" OR "Pleno"':
        fallas.append("_fts_query con unir='OR' no arma la consulta amplia")
    _casos_legal += 7

    # ── Edad de los pendientes (el briefing arrastraba junio) ───────────────
    # El caso real: el briefing del 9-sep traía «desayuno Francisco Romanelli,
    # martes 23-jun» como asunto del día, 78 días después.
    import datetime as _dt
    HOY = _dt.date(2026, 9, 9)
    CASOS_EDAD = [
        ("- [ ] Desayuno Romanelli — martes 23-jun", 78),   # el rezago real
        ("- [ ] Junta Vizum (alta: 2026-09-08)", 1),        # sello de alta
        ("- [ ] Oficio CNBV 2026-06-23 vence", 78),         # fecha ISO
        ("- [ ] Revisión 9 sep", 0),                        # hoy
        # Sin año se toma la ocurrencia MÁS CERCANA, futura incluida: tomar siempre
        # la pasada enterraba lo que viene (un «10 sep» leído el 9 salía a 364 días).
        ("- [ ] Pago 10 sep", -1),
        ("- [ ] Cierre anual 3 dic", -85),
        ("- [ ] Junta 15 ago", 25),
        ("- [ ] Sin fecha alguna", None),                   # no fechable
        ("- [ ] Oficio 31 feb", None),                      # fecha imposible
        ("- [ ] Nota (alta: 2026-13-45)", None),            # sello inválido
    ]
    for texto, esp in CASOS_EDAD:
        got = core.edad_item(texto, HOY)
        if got != esp:
            fallas.append(f"edad_item={got} (esperaba {esp}): «{texto}»")
    # Un ítem no fechable NUNCA se marca rezagado: mostrar de más es mejor que
    # esconder un pendiente real.
    vig, rez = core.segmentar_por_edad(
        ["- [ ] Sin fecha", "- [ ] Viejo 23-jun", "- [ ] Futuro 3 dic"],
        dias=21, hoy=HOY)
    if "- [ ] Sin fecha" not in vig:
        fallas.append("un pendiente sin fecha no debe marcarse rezagado")
    if len(rez) != 1 or "23-jun" not in rez[0]:
        fallas.append(f"segmentar_por_edad: rezagados={rez}")
    if not any("días sin cerrar" in r for r in rez):
        fallas.append("un rezagado debe traer su edad anotada")

    # El snapshot los aparta, sin duplicar y sin el prefijo doble «- - [ ]».
    import tempfile as _tmp
    _prev_space = core.SPACE
    try:
        _d = Path(_tmp.mkdtemp())
        (_d / "SEGUIMIENTOS.md").write_text(
            "- [ ] Junta de hoy 16:00 (alta: 2026-09-09)\n"
            "- [ ] Desayuno Romanelli — martes 23-jun\n"
            "- [ ] Dazon REPUVE sin fecha\n", encoding="utf-8")
        for _f in ("IMPORTANT.md", "JOURNAL.md", "CLIENTES.md"):
            (_d / _f).write_text("", encoding="utf-8")
        core.SPACE = _d
        snap = core.build_operational_snapshot()
        antes = snap.split("REZAGADOS")[0]
        if "Romanelli" in antes:
            fallas.append("el rezagado sale entre los asuntos de hoy")
        if "REZAGADOS" not in snap or "días sin cerrar" not in snap:
            fallas.append("falta la sección de rezagados con su edad")
        if snap.count("Romanelli") != 1:
            fallas.append(f"el rezagado sale {snap.count('Romanelli')} veces, debe ser 1")
        if "- - [" in snap:
            fallas.append("prefijo doble «- - [ ]» en la sección de rezagados")
        if "Dazon" not in antes:
            fallas.append("un pendiente sin fecha debe seguir entre los de hoy")
    finally:
        core.SPACE = _prev_space
    _casos_edad = len(CASOS_EDAD) + 3 + 5

    # ── Canal del briefing (briefing duplicado) ─────────────────────────────
    # Polo recibía DOS briefings a las 07:00 (texto + dashboard HTML).
    import importlib
    import os as _os
    _prev = _os.environ.get("DONNA_BRIEFING")
    try:
        sched = importlib.import_module("scheduler")
        for valor, esperado in (
            # Default = html: el dashboard es el que Polo abre. Fue 'texto' un día
            # y estuvo mal.
            (None, "html"),
            ("texto", "texto"),
            ("html", "html"),
            ("ambos", "ambos"),
            ("AMBOS", "ambos"),     # case-insensitive
            (" html ", "html"),     # tolera espacios
            ("cualquier_cosa", "html"),  # inválido → default
            ("", "html"),
        ):
            if valor is None:
                _os.environ.pop("DONNA_BRIEFING", None)
            else:
                _os.environ["DONNA_BRIEFING"] = valor
            got = sched.briefing_modo()
            if got != esperado:
                fallas.append(f"briefing_modo() con DONNA_BRIEFING={valor!r} = {got!r} "
                              f"(esperaba {esperado!r})")
        _casos_briefing = 8
    except Exception as e:
        fallas.append(f"no pude probar briefing_modo(): {e}")
        _casos_briefing = 8
    finally:
        _os.environ.pop("DONNA_BRIEFING", None)
        if _prev is not None:
            _os.environ["DONNA_BRIEFING"] = _prev

    # scheduler NO debe añadir un FileHandler: la unit ya hace
    # StandardOutput=append:logs/scheduler.log → cada línea salía duplicada.
    for mod_name in ("scheduler.py", "telegram-bridge.py"):
        src = (SERVICES / mod_name).read_text()
        if "logging.FileHandler(" in src:
            fallas.append(f"{mod_name} añade un FileHandler; la unit systemd ya "
                          f"redirige stdout al mismo archivo → log duplicado")

    # …pero quitar el FileHandler solo es correcto si el stdout NO va bufferado.
    # Con StandardOutput=append: el stdout es un archivo normal y Python lo bufferea
    # en bloques de 4 KB: el log se atrasa y se pierde lo pendiente si el proceso
    # muere de golpe. Las dos mitades del arreglo van juntas o ninguna sirve.
    _units = sorted((SERVICES if (SERVICES / "scheduler.service").is_file()
                     else Path(__file__).resolve().parents[1] / "services").glob("*.service"))
    _n_units = 0
    for unit in _units:
        src = unit.read_text()
        if "StandardOutput=append:" not in src:
            continue
        _n_units += 1
        for linea in src.splitlines():
            if linea.startswith("ExecStart=") and "/python3 " in linea and "python3 -u " not in linea:
                fallas.append(f"{unit.name} redirige stdout a un archivo pero corre "
                              f"python3 sin -u → log bufferado y perdible: {linea}")
    if _n_units == 0:
        fallas.append("no encontré ninguna unit con StandardOutput=append: — "
                      "¿se movieron los .service? esta prueba dejó de cubrir nada")

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
        # Ojo: NO buscar "2026-05" en el reporte — las BD reales contienen fechas de
        # mayo de 2026 legítimamente (rangos MIN/MAX), y la prueba fallaba en el server
        # aunque el código estuviera bien. Lo que hay que verificar es que el CÓDIGO no
        # traiga un mes clavado a mano, así que se inspecciona la fuente.
        if "mayo-2026" in _rep:
            fallas.append("_legal_conteo volvió a imprimir la etiqueta 'mayo-2026'")
        import inspect as _insp
        _fuente = _insp.getsource(core._legal_conteo) + _insp.getsource(core._conteo_tabla)
        import re as _re2
        _clavadas = _re2.findall(r"['\"]20\d\d-\d\d", _fuente)
        if _clavadas:
            fallas.append(f"_legal_conteo trae fechas clavadas en el código: {_clavadas}")
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
             + len(CASOS_ESCRITURA) * 2 + 3 + len(CASOS_SERVICIO) + 1 + 6 - 8
             # nuevos: diagnósticos negativos, errores de Slack, canal del briefing
             + len(CASOS_DIAGNOSTICO) + 1 + len(core._DIAG_SERVICIOS)
             + len(CASOS_SLACK_ERR) * 3 + 4 + 4 + 1 + 1
             + _casos_briefing + 2 + _n_units + 1 + _casos_edad + _casos_legal + _casos_waf)
    if fallas:
        print(f"❌ {len(fallas)} de {total} fallaron:\n")
        for f in fallas:
            print("  •", f)
        return 1
    print(f"✅ {total} casos de comprensión OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
