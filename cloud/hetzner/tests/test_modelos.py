"""Modelos por rol: configurables, con costo estimado solo donde se conoce el precio."""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"))
import donna_modelos as M


class Modelos(unittest.TestCase):
    def tearDown(self):
        for k in ("DONNA_MODELO_JUNTA", "DONNA_MODELO_JUNTA_ESCALADA", "DONNA_JUNTA_ESCALAR"):
            os.environ.pop(k, None)

    def test_defaults_y_cambio_por_entorno(self):
        self.assertEqual(M.modelo("junta"), M.HAIKU)
        os.environ["DONNA_MODELO_JUNTA"] = "claude-sonnet-4-6"
        self.assertEqual(M.modelo("junta"), "claude-sonnet-4-6")
        self.assertFalse(M.escalar_activo())                       # si el barato ya es el fuerte, no hay a dónde escalar

    def test_escalar_se_puede_apagar(self):
        self.assertTrue(M.escalar_activo())
        os.environ["DONNA_JUNTA_ESCALAR"] = "0"
        self.assertFalse(M.escalar_activo())

    def test_costo_solo_si_se_conoce_el_precio(self):
        self.assertAlmostEqual(M.costo("claude-haiku-4-5-20251001", 1_000_000, 100_000), 1.5)
        self.assertIsNone(M.costo("modelo-nuevo-sin-precio", 1000, 1000))     # no se inventa un costo

    def test_precios_que_pone_polo_desde_su_consola(self):
        os.environ["DONNA_PRECIOS"] = "claude-haiku-5-5=1:5, claude-sonnet-5-5=3:15, basura, x=no:num"
        try:
            self.assertAlmostEqual(M.costo("claude-haiku-5-5", 1_000_000, 0), 1.0)
            self.assertAlmostEqual(M.costo("claude-sonnet-5-5", 0, 1_000_000), 15.0)
            self.assertIsNone(M.costo("claude-opus-5-5", 1000, 1000))     # sin precio: no se inventa
        finally:
            os.environ.pop("DONNA_PRECIOS", None)

    def test_uso_se_registra_y_resume(self):
        M.uso_registrar("junta", M.HAIKU, 2000, 300)
        M.uso_registrar("junta", M.HAIKU, 1000, 100)
        M.uso_registrar("junta_escalada", "modelo-x", 500, 50)
        r = {(x["rol"], x["modelo"]): x for x in M.uso_resumen()}
        self.assertEqual(r[("junta", M.HAIKU)]["llamadas"], 2)
        self.assertEqual(r[("junta", M.HAIKU)]["tokens_in"], 3000)
        self.assertIsNone(r[("junta_escalada", "modelo-x")]["costo_usd"])


if __name__ == "__main__":
    unittest.main()
