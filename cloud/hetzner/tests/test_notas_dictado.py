"""Lo que falló en la junta real con la CNBV: «guárdalo como nota» no se reconocía y un dictado se contestaba «no lo encontré»."""
import os, sys, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_junta as J

REAL_1 = "Esta es una sesión con la CNBV donde estamos revisando la información de la aplicación y operatividad, entonces necesitamos revisar lo que van comentando, una de las notas que hizo la comisión es que la cuenta en el onboarding dice cuenta corriente, pero en este caso comenta al autoridad que no dejarlo com cuenta de ahorro ni corriente solo cuenta, o cuenta de fondo de pagos electrónicos parque lo guardes como nota"
REAL_2 = "En la página debemos de ajustar que en donde dice Ley Fintech, entonces ajustar y colocar el nombre correcto de la ley"
REAL_3 = "En esta sección Envía y recibe dinero desde y hacia cualquier institución del país a través de SPEI. La aplicación del saldo está sujeta a la operación del sistema de pagos, de la página de internet de sylon, ver cómo ajustamos el texto, porque nosotros somos los responsable hacia el cliente de que funcione correctamente"


class Notas(unittest.TestCase):
    def setUp(self):
        for suf in ("", "-wal", "-shm"):
            (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
        D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))

    def test_reconoce_como_pide_polo_que_anoten(self):
        for f in (REAL_1, "anota que el jueves hay entrega", "que lo guardes como nota", "guárdalo como nota", "apúntalo", "toma nota de esto", "déjalo anotado", "necesito que lo anotes"):
            self.assertTrue(J.PIDE_NOTA.search(f), f)

    def test_no_dispara_sin_que_lo_pida(self):
        for f in ("¿Qué dijeron de la cuenta?", "resume los últimos dos minutos", REAL_2, "busca la cotización"):
            self.assertFalse(J.PIDE_NOTA.search(f), f)

    def test_la_herramienta_anotar_sigue_bloqueada_sin_pedido_y_abierta_con_pedido(self):
        class Core: SPACE = TMP
        ctx = J.Contexto(Core, "j1", [{"t": "00:01", "n": "Voz 1", "es": "hola"}], "polo", "¿qué dijeron?")
        self.assertIn("No lo hice", J._tool(ctx, "anotar", {"texto": "x"}))
        ctx2 = J.Contexto(Core, "j1", [{"t": "00:01", "n": "Voz 1", "es": "hola"}], "polo", REAL_1)
        self.assertEqual(J._tool(ctx2, "anotar", {"texto": "CNBV: usar «cuenta»"}), "Nota guardada.")
        self.assertEqual(len(J.notas("j1")), 1)

    def test_un_dictado_ofrece_anotar_en_vez_de_no_lo_encontre(self):
        J._responder = lambda *a, **k: {"respuesta": J.NO_TENGO, "fuentes": [], "avisos": ["x"], "verificada": False, "eventos": []}
        for dictado in (REAL_2, REAL_3):
            v = J.preguntar(None, "j2", dictado, [], "polo", llm=lambda *a, **k: None)
            self.assertTrue(v["ofrece_nota"].startswith(dictado[:30]))
            self.assertIn("¿Lo guardo como nota", v["respuesta"])
            self.assertTrue(v["verificada"])

    def test_una_pregunta_sin_respaldo_sigue_diciendo_no_lo_tengo(self):
        J._responder = lambda *a, **k: {"respuesta": J.NO_TENGO, "fuentes": [], "avisos": [], "verificada": False, "eventos": []}
        v = J.preguntar(None, "j3", "¿Cuál fue el nombre exacto de la ley que dijo la CNBV?", [], "polo", llm=lambda *a, **k: None)
        self.assertEqual(v["respuesta"], J.NO_TENGO)
        self.assertNotIn("ofrece_nota", v)


if __name__ == "__main__":
    unittest.main()
