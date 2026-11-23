"""Materiales de la junta: lo que Polo sube durante Escuchar queda ligado a la junta, Donna lo puede leer y va a la minuta."""
import os, sys, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"))
import donna_db as D, donna_junta as J, app_media as M


class FakeCore:
    SPACE = TMP


class Materiales(unittest.TestCase):
    def setUp(self):
        for suf in ("", "-wal", "-shm"):
            (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
        D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
        self.mid = M.register_text("Cotización Vizum.pdf", "Cotización: licencia anual $120,000 más IVA. Vigencia 15 días.", tipo="documento", resumen="Cotización de licencia anual")["id"]

    def test_se_liga_y_no_se_duplica(self):
        self.assertEqual(J.material_add("j1", self.mid, "12:30")["nombre"], "Cotización Vizum.pdf")
        self.assertTrue(J.material_add("j1", self.mid, "12:31").get("ya"))
        L = J.materiales("j1")
        self.assertEqual((len(L), L[0]["hora"], L[0]["estado"]), (1, "12:30", "listo"))
        self.assertEqual(J.materiales("otra"), [])

    def test_validaciones(self):
        for lid, mid in (("", self.mid), ("j1", "../x"), ("j1", "m0000000000")):
            with self.assertRaises(ValueError):
                J.material_add(lid, mid)

    def test_va_a_la_minuta_con_extracto(self):
        J.material_add("j1", self.mid, "12:30", "la propuso Fernando")
        t = J.texto_para_entregable("j1")
        self.assertIn("## Materiales de la junta", t)
        self.assertIn("[12:30] «Cotización Vizum.pdf»", t)
        self.assertIn("la propuso Fernando", t)
        self.assertIn("licencia anual $120,000", t)

    def test_remapear_conserva_los_materiales(self):
        J.material_add("j1", self.mid, "12:30")
        J.remapear("j1", "sesion9")
        self.assertEqual(len(J.materiales("sesion9")), 1)
        self.assertIn("Cotización Vizum.pdf", J.texto_para_entregable("j1"))

    def test_el_asistente_los_ve_y_los_lee(self):
        J.material_add("j1", self.mid, "12:30")
        ctx = J.Contexto(FakeCore, "j1", [], "polo", "qué dice la cotización")
        self.assertIn(self.mid, J._tool(ctx, "materiales_junta", {}))
        self.assertIn("$120,000", J._tool(ctx, "documento_leer", {"id": self.mid}))
        self.assertIn("no ha subido", J._tool(J.Contexto(FakeCore, "vacia", [], "polo", "x"), "materiales_junta", {}))

    def test_el_prompt_lleva_los_materiales(self):
        J.material_add("j1", self.mid, "12:30")
        visto = {}
        def llm(sistema, msgs):
            visto["s"] = sistema
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"respuesta":"Dice 120 mil más IVA.","usa_datos":true,"fuentes":[{"tipo":"documento","ref":"Cotización Vizum.pdf"}]}'}]}
        r = J._responder(FakeCore, "j1", "qué dice la cotización", [], "polo", llm)
        self.assertIn("MATERIALES QUE POLO SUBIÓ", visto["s"])
        self.assertIn("Cotización Vizum.pdf", visto["s"])
        self.assertTrue(r["fuentes"])


if __name__ == "__main__":
    unittest.main()
