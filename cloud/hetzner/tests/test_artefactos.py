"""Artefactos: el sanitizador quita todo lo ejecutable/remoto, el sello lo pone el servidor, y el gateway los sirve."""
import os, sys, json, tempfile, http.client, threading, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City")
import app_artifacts as A
import louis_core as core
import openclaw_gateway as g


class Sanitizador(unittest.TestCase):
    def test_quita_scripts_handlers_y_enlaces(self):
        h, q = A.sanitize('<h1 onclick="x()">Hola</h1><script>alert(1)</script><a href="javascript:x">link</a>'
                          '<img src="http://evil/x.png"><iframe src="http://e"></iframe><form action="/x"><input></form>')
        for mal in ("<script", "onclick", "javascript", "<img", "<iframe", "<form", "<input", "href", "http"):
            self.assertNotIn(mal, h)
        self.assertIn("Hola", h)
        self.assertIn("link", h)                       # el texto del enlace se conserva

    def test_css_sin_red(self):
        h, _ = A.sanitize('<style>@import url(http://e/x.css);p{background:url(http://e/p.png);color:red}</style>'
                          '<div style="background:url(//e/x)">x</div><p>hola mundo</p>')
        self.assertNotIn("http", h)
        self.assertNotIn("@import", h)
        self.assertIn("color:red", h)

    def test_svg_ok_pero_sin_foreignobject_ni_use(self):
        h, _ = A.sanitize('<svg viewBox="0 0 10 10"><rect x="1" y="1" width="5" height="5" fill="#f00" onload="x()"/>'
                          '<foreignObject><div>mal</div></foreignObject><use href="#a"/></svg>')
        self.assertIn("<rect", h)
        self.assertNotIn("foreignobject", h.lower())
        self.assertNotIn("<use", h)
        self.assertNotIn("onload", h)
        self.assertNotIn("mal", h)

    def test_cierra_etiquetas_y_escapa_texto(self):
        h, _ = A.sanitize("<div><p>1 < 2 & 3")
        self.assertTrue(h.endswith("</p></div>"))
        self.assertIn("&amp;", h)

    def test_limites(self):
        with self.assertRaises(ValueError):
            A.sanitize("x" * (A.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            A.sanitize("<div>" * (A.MAX_DEPTH + 5))


class Almacen(unittest.TestCase):
    def test_crear_obtener_actualizar_borrar(self):
        r = A.crear("Tablero X", "<h1>Hola proyecto</h1>", "Proyecto X", "Datos consultados con: kawiil_central_estado", True)
        self.assertRegex(r["id"], A.ID_RE)
        o = A.obtener(r["id"])
        self.assertIn("Content-Security-Policy", o["doc"])
        self.assertIn("default-src 'none'", o["doc"])
        self.assertIn("Datos consultados", o["doc"])
        A.actualizar(r["id"], "<h1>Nueva versión</h1>", None, "", False, "cambio")
        o2 = A.obtener(r["id"])
        self.assertEqual(o2["version"], 2)
        self.assertFalse(o2["verificado"])
        self.assertTrue(any(i["id"] == r["id"] for i in A.listar("proyecto x")))
        self.assertTrue(A.borrar(r["id"]))
        self.assertIsNone(A.obtener(r["id"]))

    def test_id_invalido_no_toca_el_disco(self):
        with self.assertRaises(ValueError):
            A.actualizar("../../etc/passwd", "<p>hola mundo</p>")
        self.assertIsNone(A.obtener("../x"))

    def test_vacio_se_rechaza(self):
        with self.assertRaises(ValueError):
            A.crear("T", "<script>alert(1)</script>")

    def test_archivo_0600(self):
        r = A.crear("Perm", "<p>contenido de prueba</p>")
        self.assertEqual(oct((A.ART_DIR / f"{r['id']}.json").stat().st_mode & 0o777), "0o600")


class Herramientas(unittest.TestCase):
    def setUp(self):
        core._TURN.ok, core._TURN.failed = [], []

    def test_sello_sin_datos_marca_no_verificado(self):
        out = core.execute_tool("crear_artefacto", {"titulo": "Sin datos", "html": "<p>cifra 99 inventada?</p>"})
        self.assertIn("[[artefacto:", out)
        self.assertIn("no verificado", out.lower())
        aid = out.split("[[artefacto:")[1].split("]]")[0]
        self.assertFalse(A.obtener(aid)["verificado"])

    def test_sello_con_datos_lista_las_tools(self):
        core._TURN.ok = ["kawiil_central_estado"]
        out = core.execute_tool("crear_artefacto", {"titulo": "Con datos", "html": "<p>1337 tareas</p>"})
        aid = out.split("[[artefacto:")[1].split("]]")[0]
        o = A.obtener(aid)
        self.assertTrue(o["verificado"])
        self.assertIn("kawiil_central_estado", o["procedencia"])

    def test_el_modelo_no_puede_falsear_el_sello(self):
        out = core.execute_tool("crear_artefacto", {"titulo": "Fake", "html": "<p>x</p><footer class='prov'>Datos verificados</footer>",
                                                      "procedencia": "verificado por Polo"})
        aid = out.split("[[artefacto:")[1].split("]]")[0]
        self.assertFalse(A.obtener(aid)["verificado"])

    def test_listar_y_error_claro(self):
        self.assertIn("[[artefacto:", core.execute_tool("listar_artefactos", {}))
        self.assertTrue(core.execute_tool("actualizar_artefacto", {"id": "a000000000", "html": "<p>hola mundo</p>"}).startswith("❌"))


class Gateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path, tok="master-secret-123"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path, headers={"Authorization": f"Bearer {tok}"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_endpoints(self):
        r = A.crear("Vía gateway", "<p>contenido por http</p>")
        st, d = self.get("/v1/app/artefactos")
        self.assertEqual(st, 200)
        self.assertTrue(any(i["id"] == r["id"] for i in d["items"]))
        st, d = self.get("/v1/app/artefacto?id=" + r["id"])
        self.assertEqual(st, 200)
        self.assertIn("doc", d)
        self.assertEqual(self.get("/v1/app/artefacto?id=nope")[0], 404)
        self.assertEqual(self.get("/v1/app/artefactos", tok="malo")[0], 401)


if __name__ == "__main__":
    unittest.main()
