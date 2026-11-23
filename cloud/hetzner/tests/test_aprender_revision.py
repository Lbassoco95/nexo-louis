"""Corregir a Donna sin tocar código (reglas), mejoras que sí necesitan código, y revisar un entregable contra las fuentes (transcripción literal, correo enviado, fechas)."""
import os, sys, tempfile, unittest
from pathlib import Path
from datetime import timezone, timedelta
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_aprender as AP, donna_revision as RV, donna_personas as P, donna_m365 as M


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    (TMP / "AGENDA.md").write_text("# AGENDA\n- [ ] Algo no relacionado\n")


class Reglas(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_corregir_guarda_regla_sin_duplicar_y_entra_al_contexto(self):
        r = AP.regla_agregar("Polo pide revisar qué se decidió en una junta", "Lee la transcripción literal con juntas_listar y junta_leer, nunca el resumen; cita fecha, hora y quién", "Usé un resumen")
        self.assertTrue(r["nueva"])
        r2 = AP.regla_agregar("Polo pide revisar qué se decidió en una junta", "Lee la transcripción literal con juntas_listar y junta_leer, nunca un resumen; cita fecha, hora y quién.")
        self.assertFalse(r2["nueva"])                                           # la misma regla dicha otra vez se actualiza
        self.assertEqual(len(AP.reglas_list()), 1)
        ctx = AP.contexto_reglas()
        self.assertIn("REGLAS QUE POLO ME HA CORREGIDO", ctx)
        self.assertIn("transcripción literal", ctx)

    def test_polo_pausa_edita_y_borra(self):
        rid = AP.regla_agregar("Polo pide enviar un correo", "Antes de enviar, muestra el borrador y espera su confirmación explícita")["id"]
        AP.regla_accion(rid, "pausar")
        self.assertEqual(AP.contexto_reglas(), "")                              # una regla pausada ya no se aplica
        AP.regla_accion(rid, "activar")
        AP.regla_accion(rid, "editar", "Muestra el borrador, espera confirmación y copia a Viri")
        self.assertIn("copia a Viri", AP.contexto_reglas())
        AP.regla_accion(rid, "borrar")
        self.assertEqual(AP.reglas_list(), [])
        with self.assertRaises(ValueError):
            AP.regla_agregar("x", "corto")

    def test_mejoras_para_el_codigo_con_evidencia_y_exportables(self):
        m = AP.mejora_agregar("Ejecutar el agente Vizumi de Kawiil Central", "Polo pidió que Vizumi revisara el correo de Vizum; Donna no tiene cómo ejecutar agentes de Kawiil Central", "Polo: «Entra al correo de vizum con el agente vizumi»", "capacidad")
        again = AP.mejora_agregar("Ejecutar el agente Vizumi de Kawiil Central", "Otra vez lo mismo, con más detalle del fallo para registrar")
        self.assertTrue(m["nueva"] and not again["nueva"])
        self.assertEqual(AP.mejoras_list()[0]["veces"], 2)
        md = AP.mejoras_markdown()
        self.assertIn("# Mejoras pendientes de Donna", md)
        self.assertIn("agente vizumi", md)
        AP.mejora_estado(m["id"], "resuelta", "ya existe kc_ejecutar_agente")
        self.assertEqual(AP.mejoras_list(), [])


class FakeCore:
    SPACE = TMP
    ENTREGABLES_PATH = TMP / "entregables"
    TZ_CDMX = timezone(timedelta(hours=-6))

    @staticmethod
    def _entregables_lista_tablero():
        return [{"archivo": "cps.md", "titulo": "Contratos marco CPS Transmisor de Dinero VIZUM — Persona Física y Persona Moral (Word)", "cliente": "Vizum", "estado": "listo", "fecha": "2026-09-10 12:00"},
                {"archivo": "otro.md", "titulo": "Presentación diplomado", "cliente": "", "estado": "borrador", "fecha": "2026-11-01"}]

    @staticmethod
    def _cerebro_parsear_fm(path):
        return {"vobo": "pendiente"}

    @staticmethod
    def _dropbox_buscar(q, n=10):
        return "• CPS-VIZUM-Transmisor-de-Dinero-LIMPIO.docx (modificado 2026-09-10)\n• VERSION 29 JUNIO DE 2026.docx"


class FakeM365:
    def __init__(self, enviados):
        self.CREDS_DIR = Path(tempfile.mkdtemp(dir=TMP))
        (self.CREDS_DIR / "m365-kawiil-tokens.json").write_text("{}")
        self.enviados = enviados

    def get_access_token(self, t):
        return "tok"

    def graph_get(self, tok, path):
        return {"value": [{"subject": e["asunto"], "toRecipients": [{"emailAddress": {"name": e["para"]}}], "sentDateTime": e["fecha"] + "T10:00:00", "hasAttachments": bool(e["adj"]),
                           "attachments": [{"name": a} for a in e["adj"]]} for e in self.enviados]}


def juntas():
    P.guardar_sesion("Junta Vizum contratos", 600, [{"n": 1, "nombre": "Fernando Martínez"}, {"n": 2, "nombre": "Laura Pérez"}],
                     [{"t": "02:10", "voz": 1, "es": "Hay que actualizar el contrato marco CPS de Vizum para persona moral, falta la cláusula de cumplimiento"},
                      {"t": "02:40", "voz": 2, "es": "Yo se lo mando a Vizum esta semana con los cambios"},
                      {"t": "05:00", "voz": 1, "es": "Hablemos del diplomado después"}])
    c = P._c()
    c.execute("UPDATE sesiones_escucha SET fecha='2026-11-18'")
    c.commit(); c.close()


class Revision(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_juntas_se_listan_y_se_leen_literal(self):
        juntas()
        L = RV.juntas_listar("vizum contrato")
        self.assertEqual(len(L), 1)
        self.assertIn("Fernando Martínez", L[0]["participantes"])
        self.assertEqual(RV.juntas_listar("presupuesto inexistente"), [])
        txt = RV.junta_leer(L[0]["id"])
        self.assertIn("[02:10] Fernando Martínez: Hay que actualizar el contrato marco CPS", txt)     # literal, con hora y quién
        self.assertIn("transcripción literal", txt)

    def test_sin_envio_se_marca_prioritario_conforme_a_la_junta(self):
        juntas()
        r = RV.revisar_entregable(FakeCore, "Contratos marco CPS Transmisor de Dinero VIZUM", m365=FakeM365([]))
        self.assertEqual(r["veredicto"], "sin_evidencia")
        self.assertEqual(r["entregable"]["vobo"], "pendiente")
        self.assertEqual(r["ultima_junta"], "2026-11-18")
        self.assertIn("«Hay que actualizar el contrato marco CPS", r["texto"])                         # cita literal de la junta
        self.assertIn("CPS-VIZUM-Transmisor-de-Dinero-LIMPIO.docx", r["texto"])
        self.assertIn("URGENTE", (TMP / "AGENDA.md").read_text())
        self.assertEqual(len(D.seg_list()), 1)
        r2 = RV.revisar_entregable(FakeCore, "Contratos marco CPS Transmisor de Dinero VIZUM", m365=FakeM365([]))
        self.assertEqual((TMP / "AGENDA.md").read_text().count("URGENTE"), 1)                          # no duplica

    def test_enviado_antes_o_despues_de_la_junta(self):
        juntas()
        antes = FakeM365([{"asunto": "Contratos marco CPS Vizum", "para": "Vizum Legal", "fecha": "2026-10-01", "adj": ["CPS-VIZUM-Transmisor-de-Dinero-LIMPIO.docx"]}])
        r = RV.revisar_entregable(FakeCore, "Contratos marco CPS Transmisor de Dinero VIZUM", m365=antes, marcar_prioritario=False)
        self.assertEqual(r["veredicto"], "antes_de_junta")
        self.assertIsNone(r["prioritario"])
        despues = FakeM365([{"asunto": "Contratos marco CPS Vizum actualizado", "para": "Vizum Legal", "fecha": "2026-11-20", "adj": ["CPS-VIZUM-Transmisor-de-Dinero-v2.docx"]}])
        r2 = RV.revisar_entregable(FakeCore, "Contratos marco CPS Transmisor de Dinero VIZUM", m365=despues)
        self.assertEqual(r2["veredicto"], "enviado")
        self.assertNotIn("URGENTE", (TMP / "AGENDA.md").read_text())                                   # enviado: no se marca prioritario

    def test_si_no_hay_transcripciones_lo_dice_y_no_inventa(self):
        r = RV.revisar_entregable(FakeCore, "Contratos marco CPS Transmisor de Dinero VIZUM", m365=FakeM365([]))
        self.assertEqual(r["juntas"], [])
        self.assertIn("NO HAY TRANSCRIPCIONES GUARDADAS", r["texto"])
        self.assertIn("pedir_mejora", r["texto"])

    def test_veredicto_por_fechas(self):
        self.assertEqual(RV.veredicto_envio("2026-11-18", "", "")[0], "sin_evidencia")
        self.assertEqual(RV.veredicto_envio("2026-11-18", "2026-11-10", "")[0], "antes_de_junta")
        self.assertEqual(RV.veredicto_envio("", "2026-11-10", "2026-11-12")[0], "antes_de_cambio")
        self.assertEqual(RV.veredicto_envio("2026-11-18", "2026-11-18", "2026-11-17")[0], "enviado")

    def test_entregable_inexistente_y_agentes_de_kc(self):
        r = RV.revisar_entregable(FakeCore, "Plan de marketing 2027 para Dazon", m365=FakeM365([]), marcar_prioritario=False)
        self.assertIsNone(r["entregable"])
        self.assertIn("no lo encontré por ese nombre en Cerebro", r["texto"])
        class Cur:
            def __init__(s): s.q = ""; s.description = [("id",), ("name",), ("memoria",)]
            def execute(s, q, p=None): s.q = q
            def fetchall(s):
                if "information_schema.tables" in s.q: return [("agents",)]
                if "information_schema.columns" in s.q: return [("id", "uuid"), ("name", "text"), ("memoria", "text")]
                return [("a1", "Vizumi", "Atiende el correo de Vizum")]
        class Conn:
            autocommit = False
            def cursor(s): return Cur()
            def close(s): pass
        class K:
            @staticmethod
            def _kawiil_central_pg(): return Conn(), None
        t = RV.kc_agentes(K, "vizumi")
        self.assertIn("agents", t)
        self.assertIn("Vizumi", t)
        self.assertIn("todavía NO tiene cómo EJECUTARLOS", t)


class HerramientasDeDonna(unittest.TestCase):
    """Que las herramientas nuevas estén registradas, enrutadas al modelo con herramientas y se ejecuten de punta a punta por el despachador real."""
    def setUp(self):
        limpiar()
        import louis_core
        self.core = louis_core
        self.core.SPACE = TMP

    def test_estan_registradas_y_enrutadas(self):
        nombres = {t["name"] for t in self.core.TOOLS_DEFINITION}
        for n in ("aprender_correccion", "reglas_listar", "pedir_mejora", "juntas_listar", "junta_leer", "revisar_entregable", "kawiil_central_agentes"):
            self.assertIn(n, nombres)
        for frase in ("Corrección sobre tu respuesta anterior: así no, lee la transcripción", "¿ya se mandó la actualización del contrato?", "revisa contra la transcripción de la junta",
                      "Entra al correo de vizum con el agente vizumi", "esto está mal"):
            self.assertTrue(self.core.TOOL_REGEX.search(frase), frase)

    def test_despachador_real(self):
        x = self.core.execute_tool
        self.assertIn("Aprendido", x("aprender_correccion", {"cuando": "Polo pide revisar una junta", "regla": "Lee la transcripción literal, nunca el resumen y cita la hora", "error": "usé un resumen"}))
        self.assertIn("transcripción literal", x("reglas_listar", {}))
        self.assertIn("transcripción literal", self.core.load_system_prompt("app"))               # la regla entra al contexto de TODA conversación
        self.assertIn("Registré la mejora", x("pedir_mejora", {"titulo": "Ejecutar el agente Vizumi", "detalle": "No tengo cómo ejecutar agentes de Kawiil Central", "tipo": "capacidad"}))
        self.assertIn("No hay juntas guardadas", x("juntas_listar", {"consulta": "vizum"}))
        self.assertIn("No existe esa junta", x("junta_leer", {"sesion_id": "nope"}))
        juntas()
        self.assertIn("Junta Vizum contratos", x("juntas_listar", {"consulta": "vizum"}))
        sid = RV.juntas_listar("vizum")[0]["id"]
        self.assertIn("[02:10] Fernando Martínez", x("junta_leer", {"sesion_id": sid}))
        rep = x("revisar_entregable", {"titulo": "Contratos marco CPS Transmisor de Dinero VIZUM", "marcar_prioritario": False})
        self.assertIn("REVISIÓN CONTRA FUENTES", rep)
        self.assertIn("Hay que actualizar el contrato marco CPS", rep)
        self.assertIn("❌", x("revisar_entregable", {"titulo": ""}))


class RutasDirectas(unittest.TestCase):
    """El caso real de Polo: antes el modelo elegía mal las herramientas y le pedía la junta; ahora estas peticiones se resuelven en código."""
    MSG_REV = 'Revisa el entregable "Contratos marco CPS Transmisor de Dinero VIZUM": ¿ya se mandó la actualización según lo último dicho en las juntas?'
    MSG_AGT = "Entra al correo de vizum con el agente vizumi para saber si en el drive hay algo?"

    def setUp(self):
        limpiar()
        import louis_core
        self.core = louis_core
        self.core.SPACE = TMP
        self.core.AGENTS_DIR = TMP / "agents"
        self.orig = (RV.revisar_entregable, RV.kc_agentes)
        self.llamadas = []
        RV.revisar_entregable = lambda core, titulo, marcar=True, **k: self.llamadas.append(titulo) or {"texto": "VEREDICTO DE ENVÍO: SIN EVIDENCIA\n[02:10] Fernando: «actualizar el contrato»"}
        RV.kc_agentes = lambda core, nombre="": "Tablas de agentes en Kawiil Central: agents\n• name=Vizumi"

    def tearDown(self):
        RV.revisar_entregable, RV.kc_agentes = self.orig

    def test_revision_se_dispara_sola_con_el_titulo_correcto(self):
        r = self.core.try_deterministic_revision(self.MSG_REV)
        self.assertIn("VEREDICTO DE ENVÍO", r)
        self.assertIn("no contra resúmenes", r)
        self.assertEqual(self.llamadas, ["Contratos marco CPS Transmisor de Dinero VIZUM"])
        for no in ("¿Qué tengo hoy?", 'Dime "hola" por favor', "¿ya se mandó el contrato de Vizum?", 'Anota "comprar café para la oficina"'):
            self.assertIsNone(self.core.try_deterministic_revision(no), no)

    def test_nombrar_un_agente_de_kc_dice_la_verdad_y_registra_la_mejora(self):
        r = self.core.try_deterministic_agente(self.MSG_AGT)
        self.assertIn("No puedo ejecutar al agente vizumi todavía", r)
        self.assertIn("name=Vizumi", r)
        m = AP.mejoras_list()
        self.assertEqual(len(m), 1)
        self.assertIn("agente vizumi", m[0]["evidencia"])
        self.core.try_deterministic_agente(self.MSG_AGT)
        self.assertEqual(AP.mejoras_list()[0]["veces"], 2)                         # la misma petición suma, no duplica
        for no in ("Qué agente de seguimiento tenemos", "ejecuta el agente de ventas", "hola"):
            self.assertIsNone(self.core.try_deterministic_agente(no), no)

    def test_un_subagente_local_sigue_el_flujo_normal(self):
        (TMP / "agents").mkdir(exist_ok=True)
        (TMP / "agents" / "redactor-correos.md").write_text("---\nnombre: redactor-correos\nespecialidad: correos\n---\nEres un redactor")
        self.assertIsNone(self.core.try_deterministic_agente("Pídele al agente redactor-correos un borrador"))

    def test_el_enrutador_real_las_atiende_antes_que_el_modelo(self):
        r, ruta = self.core._call_llm_impl("sin-llave", "sistema", [], self.MSG_REV)
        self.assertEqual(ruta, "revision-directa")
        self.assertIn("VEREDICTO DE ENVÍO", r)
        r2, ruta2 = self.core._call_llm_impl("sin-llave", "sistema", [], self.MSG_AGT)
        self.assertEqual(ruta2, "agente-kc")


class RevisionConMemoriaYDonde(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_sin_transcripcion_usa_notas_como_pista_y_dice_donde_busco(self):
        (TMP / "COWORK.md").write_text("# Cowork\n- 2026-11-12 Reunión Vizum: hay que actualizar el contrato marco CPS para persona moral y mandarlo a Gonzalo\n- otra cosa\n")
        FakeCore.MEMORY_FILES = ["JOURNAL.md", "PROJECTS.md"]
        r = RV.revisar_entregable(FakeCore, "Contratos marco CPS Transmisor de Dinero VIZUM", m365=FakeM365([{"asunto": "Contratos marco CPS Vizum", "para": "Gonzalo", "fecha": "2026-02-16", "adj": []}]), marcar_prioritario=False)
        self.assertEqual(r["juntas"], [])
        self.assertEqual(r["notas"][0]["archivo"], "COWORK.md")
        self.assertIn("NOTAS/MEMORIA (son resúmenes, NO la transcripción", r["texto"])
        self.assertIn("busqué entre 0 junta(s) guardada(s) con Escuchar", r["texto"])
        self.assertIn("Correo reciente", r["texto"])


if __name__ == "__main__":
    unittest.main()
