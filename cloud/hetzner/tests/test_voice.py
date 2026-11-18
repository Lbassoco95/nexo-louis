"""Voz con Donna, subida rápida (Atajos), usuarios solo-subida y registro de errores del teléfono."""
import os, sys, json, tempfile, http.client, threading, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City",
                  OPENCLAW_HOME=str(TMP / "oc"), OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="sk-ant-test")
import app_auth as A
import app_media as M
import app_translate as T
import louis_core as core
import openclaw_gateway as g

M.summarize = lambda t: ""
g._hist_append = lambda *a, **k: None
VISTO = {}


def fake_llm(api_key, system, history, user, history_file=None):
    VISTO["system"], VISTO["user"] = system, user
    return "Claro, Polo. Tu diplomado empieza el lunes.", "deepseek"


core.call_llm = fake_llm
core.load_anthropic_key = lambda: "k"


class Servidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        assert A.set_password("polo", "una frase larga y segura 2026") == ""
        assert A.set_password("atajo-polo", "otra frase larga y segura 2026") == ""

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, body=None, raw=None, tok="master-secret-123"):
        T._HITS.clear()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Authorization": f"Bearer {tok}"}
        data = raw
        if body is not None:
            data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        try:
            return r.status, json.loads(out or b"{}")
        except ValueError:
            return r.status, out

    def token(self, user, pw):
        return A.login(user, pw, "127.0.0.1")["token"]

    def test_voz_pregunta_a_donna_con_bloque_de_voz(self):
        st, d = self.req("POST", "/v1/app/voice/ask", {"messages": [{"role": "user", "content": "¿cuándo empieza mi diplomado?"}]})
        self.assertEqual(st, 200); self.assertIn("diplomado", d["respuesta"])
        self.assertIn("MODO VOZ", VISTO["system"]); self.assertIn("diplomado", VISTO["user"])

    def test_chat_normal_no_lleva_modo_voz(self):
        g._RESULTS["rtest0001"] = {"rid": "rtest0001", "t0": 0, "done": False, "client_gone": False}
        g._CHAT_SLOTS.acquire()
        g._chat_worker("rtest0001", "hola", [], voz=False)
        self.assertNotIn("MODO VOZ", VISTO["system"])
        g._RESULTS.pop("rtest0001", None)

    def test_transcribe_y_habla(self):
        o = M._openai_transcribe; M._openai_transcribe = lambda p, k: "quiero revisar mi plan de doctorado"
        try:
            st, d = self.req("PUT", "/v1/app/voice/transcribe?nombre=v.m4a", raw=b"a" * 3000)
        finally:
            M._openai_transcribe = o
        self.assertEqual((st, d["texto"]), (200, "quiero revisar mi plan de doctorado"))
        t = T.texto_a_voz; T.texto_a_voz = lambda x, i: b"MP3"
        try:
            st, b = self.req("POST", "/v1/app/speak", {"texto": "Hola Polo", "idioma": "es"})
        finally:
            T.texto_a_voz = t
        self.assertEqual((st, b), (200, b"MP3"))

    def test_subida_rapida_para_atajos_se_procesa(self):
        st, d = self.req("PUT", "/v1/app/media/quick?nombre=Temario%20Diplomado.txt&origen=atajo",
                         raw=("Módulo 1: Fundamentos. Entrega del ensayo el 15 de diciembre. " * 5).encode())
        self.assertEqual(st, 200); self.assertEqual(d["origen"], "atajo")
        M.process(d["id"])
        self.assertIn("ensayo", M.leer_texto(d["id"]))
        st, d2 = self.req("POST", "/v1/app/media/quick?nombre=nota.txt", raw=b"texto por POST")
        self.assertEqual(st, 200)
        self.assertEqual(self.req("PUT", "/v1/app/media/quick?nombre=x.txt", raw=b"")[0], 400)

    def test_usuario_atajo_solo_puede_subir(self):
        tok = self.token("atajo-polo", "otra frase larga y segura 2026")
        self.assertEqual(self.req("PUT", "/v1/app/media/quick?nombre=a.txt", raw=b"hola desde atajos", tok=tok)[0], 200)
        for ruta in ("/v1/app/media", "/v1/app/hoy", "/v1/app/notifs", "/v1/app/artefactos", "/v1/app/media/search?q=hola"):
            self.assertEqual(self.req("GET", ruta, tok=tok)[0], 401, ruta)
        self.assertEqual(self.req("POST", "/v1/app/voice/ask", {"messages": [{"role": "user", "content": "x"}]}, tok=tok)[0], 401)
        norm = self.token("polo", "una frase larga y segura 2026")
        self.assertEqual(self.req("GET", "/v1/app/media", tok=norm)[0], 200)             # el usuario normal sí

    def test_busqueda_estructurada(self):
        mid = M.register_text("Plan doctorado", "El anteproyecto del doctorado debe entregarse en marzo. " * 3, tipo="documento")["id"]
        st, d = self.req("GET", "/v1/app/media/search?q=anteproyecto%20doctorado")
        self.assertEqual(st, 200); self.assertTrue(any(i["id"] == mid and "marzo" in i["extracto"] for i in d["items"]))

    def test_clientlog_con_limite(self):
        g._CLOG_HITS.clear()
        for _ in range(30):
            self.assertEqual(self.req("POST", "/v1/app/clientlog", {"msg": "x"})[0], 200)
        self.assertEqual(self.req("POST", "/v1/app/clientlog", {"msg": "x"})[0], 200)    # responde igual, pero ya no registra
        self.assertGreaterEqual(len(g._CLOG_HITS), 30)


class Avisos(unittest.TestCase):
    def test_aviso_lleva_enlace_al_documento(self):
        import louis_push as P
        P.STATE_DIR = TMP / "st2"; P.INBOX_FILE = P.STATE_DIR / "notifs.json"; P.INBOX_LOCK_FILE = P.STATE_DIR / "notifs.lock"
        r = P.inbox_add("Transcripción lista: reunión", "resumen…", link="media:mabc1234567")
        self.assertEqual(r["link"], "media:mabc1234567")
        self.assertEqual(P.inbox_list(1)[0]["link"], "media:mabc1234567")


if __name__ == "__main__":
    unittest.main()
