"""El oficio de asistente: se mide con código lo medible, se autoevalúa, propone reglas (por aceptar) y arranca con reglas base que Polo controla."""
import os, sys, json, tempfile, unittest
from pathlib import Path
from datetime import datetime, timedelta
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_oficio as O, donna_aprender as AP


class FakeCore:
    SPACE = TMP


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    (TMP / "app-history.jsonl").write_text("")


def historial(pares):
    ahora = datetime.now()
    with open(TMP / "app-history.jsonl", "w") as f:
        for i, (u, a) in enumerate(pares):
            f.write(json.dumps({"ts": (ahora - timedelta(minutes=60 - 2 * i)).isoformat(), "role": "user", "content": u}) + "\n")
            f.write(json.dumps({"ts": (ahora - timedelta(minutes=60 - 2 * i - 1)).isoformat(), "role": "assistant", "content": a}) + "\n")


VOLCADO = ("REVISIÓN CONTRA FUENTES (los datos de abajo son la evidencia)\n" + "\n".join("[agent_activity_logs] id=fd65e32f; organization_id=0000; agent_id=87e5; action=task_completed → línea %d" % i for i in range(30)) +
           "\nSin la transcripción no puedo saber. Dame la fecha/ID y la leo.")
BUENA = ("**No encuentro que se haya enviado la actualización.**\n• En Cerebro está en listo con VoBo pendiente.\n• Lo más reciente en Dropbox es la versión del 3-sep.\n\n"
         "**Qué sigue:** dime qué cambio pidieron y lo dejo como tarea con fecha; te aviso mañana si hay novedad.")


class Heuristicas(unittest.TestCase):
    def test_el_volcado_de_datos_crudos_reprueba(self):
        h = O.heuristicas(VOLCADO)
        self.assertEqual(h["breve"][0], 1)
        self.assertLessEqual(h["tono"][0], 3)
        self.assertEqual(h["busca_ella"][0], 1)                                  # «Dame la fecha/ID y la leo»
        self.assertEqual(h["cierra_ciclos"][0], 2)

    def test_la_respuesta_de_asistente_aprueba(self):
        h = O.heuristicas(BUENA)
        self.assertEqual((h["breve"][0], h["tono"][0], h["busca_ella"][0], h["cierra_ciclos"][0]), (5, 5, 5, 5))

    def test_el_informe_nuevo_de_revision_pasa_las_reglas_de_oficio(self):
        import donna_revision as RV
        brief = RV.brief_asistente({"entregable": {"estado": "listo", "vobo": "pendiente", "actualizado": "2026-09-10", "cliente": "Vizum"}, "claves": ["contratos", "marco", "transmisor", "dinero", "vizum"],
                                    "veredicto": "sin_evidencia", "juntas": [], "ultimo_envio": "", "ultima_junta": "", "correos": [],
                                    "dropbox": "CPS-VIZUM-Transmisor-de-Dinero-LIMPIO.docx → /x (modificado 2026-09-03)\nCPS-VIZUM-Transmisor-de-Dinero-PF.docx → /y (modificado 2026-02-19)",
                                    "notas": [{"archivo": "LEARNINGS.md", "linea": "Vizum — Criterio de Polo (7-sep-2026): cuando la autoridad pide la última versión y hay una actualización sin aprobar, NO se consolida. Se contesta con lo vigente."}],
                                    "prioritario": "Lo dejé como URGENTE en la agenda."})
        h = O.heuristicas(brief)
        self.assertGreaterEqual(h["breve"][0], 4, brief)
        self.assertEqual((h["tono"][0], h["busca_ella"][0], h["cierra_ciclos"][0]), (5, 5, 5))


class Autoevaluacion(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_si_falla_seguido_propone_regla_que_no_se_aplica_hasta_aceptarla(self):
        historial([("¿ya se mandó el contrato?", VOLCADO), ("¿y el otro contrato?", VOLCADO), ("hola", "Hola Polo, ¿en qué te ayudo?")])
        modelo = lambda p, r: {"puntajes": {"conclusion": 2, "verifica": 5, "anticipa": 3, "memoria": 4, "prioriza": 4, "prudencia": 5}, "notas": {"conclusion": "Empezó por «Lo revisé» y no por la conclusión"},
                               "regla": {"cuando": "te pidan revisar un documento", "regla": "responde primero si se envió o no, en una frase"}}
        r = O.revisar_reciente(FakeCore, modelo)
        self.assertEqual(r["evaluadas"], 3)
        self.assertGreaterEqual(r["propuestas"], 2)
        props = AP.reglas_list(("propuesta",))
        self.assertTrue(any("máximo 5 viñetas" in x["regla"] for x in props))
        self.assertTrue(any("responde primero si se envió" in x["regla"] for x in props))
        ctx = AP.contexto_reglas()
        self.assertNotIn("responde primero si se envió", ctx)                      # por aceptar: NO se aplica
        rid = [x["id"] for x in props if "responde primero si se envió" in x["regla"]][0]
        AP.regla_accion(rid, "aceptar")
        self.assertIn("responde primero si se envió", AP.contexto_reglas())        # aceptada: ya manda
        r2 = O.revisar_reciente(FakeCore, modelo)
        self.assertEqual(r2["evaluadas"], 0)                                       # no reevalúa lo ya evaluado

    def test_resumen_por_competencia_y_rechazar_una_propuesta(self):
        historial([("a", VOLCADO), ("b", VOLCADO)])
        O.revisar_reciente(FakeCore, lambda p, r: {})
        res = {x["id"]: x for x in O.resumen()}
        self.assertEqual(res["breve"]["promedio"], 1.0)
        self.assertIn("Volcó mucho texto", res["breve"]["ultima_nota"])
        self.assertIsNone(res["prudencia"]["promedio"])                            # lo que no se pudo medir no se inventa
        for x in AP.reglas_list(("propuesta",)):
            AP.regla_accion(x["id"], "rechazar")
        self.assertEqual(AP.reglas_list(("propuesta",)), [])

    def test_si_el_modelo_falla_no_se_cae(self):
        historial([("a", VOLCADO)])
        def mal(p, r):
            raise RuntimeError("sin crédito")
        self.assertEqual(O.revisar_reciente(FakeCore, mal)["evaluadas"], 1)


class ReglasBase(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_siembra_una_vez_y_lo_que_polo_borra_no_vuelve(self):
        ctx = AP.contexto_reglas()                                                 # al armar el contexto de Donna se siembran
        self.assertIn("empieza con la conclusión en una frase", ctx)
        self.assertIn("no le pidas a Polo que busque lo que tú puedes buscar", ctx)
        n = len(AP.reglas_list(("activa",)))
        self.assertEqual(n, len(O.REGLAS_BASE))
        self.assertEqual(O.sembrar(), 0)
        rid = [r["id"] for r in AP.reglas_list(("activa",)) if "empieza con la conclusión" in r["regla"]][0]
        AP.regla_accion(rid, "borrar")
        AP.contexto_reglas()
        self.assertNotIn("empieza con la conclusión en una frase", AP.contexto_reglas())


if __name__ == "__main__":
    unittest.main()
