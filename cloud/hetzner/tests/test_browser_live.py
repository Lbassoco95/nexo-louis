"""Navegador en vivo: Polo inicia sesión él mismo, la sesión se guarda y se retoma; solo https público (sin red interna)."""
import os, sys, json, tempfile, threading, unittest
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

TMP = tempfile.mkdtemp()
os.environ["BROWSER_STATE_DIR"] = TMP
os.environ["BROWSER_LIVE_ALLOW_LOCAL"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
import browser_live as B

LOGIN = (b"<html><title>Entrar</title><body><form onsubmit=\"document.cookie='sesion=ok; path=/; max-age=3600';"
         b"document.title='Dentro';document.body.innerHTML='<h1 id=ok>Hola Polo</h1>';return false\">"
         b"<input id=u style='width:300px;height:40px' placeholder=usuario><button style='width:200px;height:40px'>Entrar</button></form></body></html>")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(LOGIN)

    def log_message(self, *a):
        pass


class TestLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        B.operar({"op": "close"}); cls.srv.shutdown()

    def test_flujo_login_guardar_olvidar(self):
        r = B.operar({"op": "open", "url": self.url, "w": 600, "h": 800})
        self.assertTrue(r["ok"] and r["abierto"], r)
        f = B.operar({"op": "frame"})
        self.assertTrue(f["ok"] and len(f["img"]) > 500)
        B.operar({"op": "click", "x": 100, "y": 28})              # campo usuario
        B.operar({"op": "type", "text": "polo"})
        B.operar({"op": "key", "key": "Enter"})                    # envía el formulario
        s = B.operar({"op": "status"})
        self.assertEqual(s["title"], "Dentro", s)
        B.operar({"op": "guardar"})
        B.operar({"op": "close"})
        self.assertFalse(B.operar({"op": "status"})["abierto"])
        ses = B.operar({"op": "sesiones"})["sesiones"]
        self.assertTrue(any(x["sitio"] == "127.0.0.1" for x in ses), ses)
        self.assertEqual(oct(os.stat(os.path.join(TMP, "browser-context.json")).st_mode & 0o777), "0o600")
        B.operar({"op": "open", "url": self.url, "w": 600, "h": 800})   # sesión retomada: la cookie sigue
        self.assertTrue(B.LIVE.call("status")["abierto"])
        B.operar({"op": "olvidar", "sitio": "127.0.0.1"})
        self.assertEqual(B.operar({"op": "sesiones"})["sesiones"], [])

    def test_solo_https_publico(self):
        os.environ["BROWSER_LIVE_ALLOW_LOCAL"] = "0"
        try:
            for u in ("http://example.com", "https://127.0.0.1/", "https://localhost/", "file:///etc/passwd", "https://169.254.169.254/", "javascript:alert(1)"):
                with self.assertRaises(ValueError, msg=u):
                    B.url_permitida(u)
        finally:
            os.environ["BROWSER_LIVE_ALLOW_LOCAL"] = "1"

    def test_sitios_sugeridos(self):
        ids = {x["id"] for x in B.operar({"op": "sitios"})["sitios"]}
        self.assertTrue({"cinemex", "cinepolis", "opentable"} <= ids)


if __name__ == "__main__":
    unittest.main()
