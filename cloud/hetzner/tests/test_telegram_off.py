"""Telegram apagado (archivo telegram_off): lo proactivo va a la app aunque no haya teléfono suscrito; nada sale por Telegram."""
import os, sys, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP), OPENCLAW_GATEWAY_TOKEN="x")
import louis_push as P, gamma_gen as G, briefing_doc as B


class TelegramOff(unittest.TestCase):
    def setUp(self):
        P.TELEGRAM_OFF_FLAG = TMP / "telegram_off"
        P.TELEGRAM_OFF_FLAG.unlink(missing_ok=True)
        self.avisos = []
        P.notify_text_async = lambda t, tab="": self.avisos.append(t)
        self.red = []
        import urllib.request
        self._u = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: self.red.append(a) or (_ for _ in ()).throw(AssertionError("salió a la red"))

    def tearDown(self):
        import urllib.request
        urllib.request.urlopen = self._u

    def test_bandera_apaga_sin_suscripcion(self):
        self.assertFalse(P.telegram_muted())
        P.TELEGRAM_OFF_FLAG.write_text("")
        self.assertEqual(P.count(), 0)
        self.assertTrue(P.telegram_muted())

    def test_gamma_y_agenda_van_a_la_app(self):
        P.TELEGRAM_OFF_FLAG.write_text("")
        self.assertTrue(G.telegram("Listo tu Gamma"))
        self.assertTrue(G.telegram_doc("https://x/y.png", "y.png", "Tu pieza"))
        self.assertTrue(B.send_doc(b"<html>", "Agenda.html", "Agenda de hoy"))
        self.assertEqual(len(self.avisos), 3)
        self.assertEqual(self.red, [])

    def test_sin_bandera_si_intenta_telegram(self):
        G._tg_creds = lambda: ("tok", "1")
        self.assertFalse(G.telegram("hola"))                                  # intentó la red (simulada) y falló
        self.assertEqual(len(self.red), 1)


if __name__ == "__main__":
    unittest.main()
