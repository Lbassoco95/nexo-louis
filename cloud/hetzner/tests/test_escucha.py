"""Escuchar y transcribir: guardar la transcripción como ENTREGABLE (con voces y horas) + documento buscable; privacidad: solo texto."""
import os, sys, json, tempfile, http.client, threading, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(OPENCLAW_SPACES=str(TMP / "spaces"), PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City",
                  OPENCLAW_HOME=str(TMP / "oc"), ENTREGABLES_PATH=str(TMP / "entregables"))
import app_media as M
import louis_core as core
import openclaw_gateway as g
core.ENTREGABLES_PATH = TMP / "entregables"
M.summarize = lambda text: "RESUMEN-DE-PRUEBA" if len(text) > 100 else ""

LINEAS = [{"t": "00:12", "voz": 1, "nombre": "María", "tono": "aguda", "es": "Buenas tardes, gracias por recibirnos.", "orig": "下午好，谢谢你们接待我们。", "idioma": "zh", "bajo": False},
          {"t": "00:19", "voz": 2, "nombre": "Voz 2", "tono": "grave", "es": "Llegamos ayer; es nuestra primera visita a México.", "orig": "我们昨天到的。", "idioma": "zh", "bajo": True}]
VOCES = [{"n": 1, "nombre": "María", "tono": "aguda"}, {"n": 2, "nombre": "Voz 2", "tono": "grave"}]


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def post(self, body, token="master-secret-123"):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/v1/app/escucha/guardar", json.dumps(body), {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_guarda_entregable_con_voces_y_horas(self):
        st, r = self.post({"duracion": 252, "voces": VOCES, "lineas": LINEAS})
        self.assertEqual(st, 200, r)
        f = (TMP / "entregables" / r["archivo"])
        self.assertTrue(f.exists(), r)
        txt = f.read_text()
        self.assertIn("tipo: transcripcion", txt)
        self.assertIn("estado: aprobado", txt)                                      # propio del usuario: no entra a la cola de «esperan tu Vo.Bo.»
        self.assertIn("Voz 1 — María (voz aguda)", txt)
        self.assertIn("**00:12** · María · voz aguda", txt)
        self.assertIn("**00:19** · Voz 2 · voz grave", txt)
        self.assertIn("audio débil", txt)                                           # la línea de poco audio queda marcada
        self.assertIn("Duración: 4 min 12 s", txt)
        self.assertIn("no se guardó audio", txt)
        self.assertEqual(r["lineas"], 2)
        # también queda como documento que Donna puede buscar
        self.assertTrue(M.buscar_items("primera visita a México"))
        lista = core._entregables_lista_tablero()
        self.assertTrue(any(e["archivo"] == r["archivo"] and e["estado"] == "aprobado" for e in lista))
        self.assertFalse(any(e["estado"] == "listo" for e in lista))

    def test_sin_texto_es_error(self):
        for body in ({"lineas": []}, {"lineas": [{"es": "  ", "orig": ""}]}, {"nada": 1}):
            st, r = self.post(body)
            self.assertEqual(st, 400, body)

    def test_el_usuario_de_atajo_no_puede(self):
        import app_auth as A
        A.set_password("atajo-x", "otra frase larga y segura 2026")
        tok = A.login("atajo-x", "otra frase larga y segura 2026")
        tok = tok.get("token") if isinstance(tok, dict) else tok
        if tok:
            st, _ = self.post({"lineas": LINEAS}, token=tok)
            self.assertIn(st, (401, 403))


if __name__ == "__main__":
    unittest.main()
