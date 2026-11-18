"""Una instalación a medias (faltan módulos nuevos) NO debe tumbar el servidor: login, chat y salud siguen; solo falla, con aviso claro, lo que usa el módulo faltante."""
import os, sys, json, subprocess, textwrap, unittest
from pathlib import Path

SERV = str(Path(__file__).resolve().parent.parent / "services")


class ModulosFaltantes(unittest.TestCase):
    def test_el_servidor_arranca_sin_los_modulos_nuevos(self):
        code = textwrap.dedent(f"""
            import sys, os, json, threading, http.client, tempfile
            T = tempfile.mkdtemp()
            os.environ.update(PUSH_STATE_DIR=T + "/st", OPENCLAW_GATEWAY_TOKEN="tok-123456789", OPENCLAW_HOME=T + "/oc")
            sys.path.insert(0, {SERV!r})
            for m in ("app_trabajos", "donna_voz", "ws_lite", "app_translate", "app_media"):
                sys.modules[m] = None                      # como si el archivo no estuviera instalado: import → ImportError
            import openclaw_gateway as g
            from http.server import ThreadingHTTPServer
            assert all(isinstance(getattr(g, m), g._Faltante) for m in ("app_trabajos", "donna_voz", "ws_lite", "app_translate", "app_media"))
            srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            def req(m, p, body=None):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                c.request(m, p, body=json.dumps(body) if body is not None else None, headers={{"Authorization": "Bearer tok-123456789", "Content-Type": "application/json"}})
                r = c.getresponse(); b = r.read()
                return r.status, b
            out = {{}}
            out["salud"] = req("GET", "/healthz")[0]
            out["ping"] = req("GET", "/v1/app/ping")[0]                       # la app se conecta
            out["app"] = req("GET", "/app/")[0]                               # y carga
            st, b = req("GET", "/v1/app/trabajos"); out["trabajos"] = (st, b.decode()[:200])
            st, b = req("GET", "/v1/app/media"); out["media"] = st
            print("RESULT " + json.dumps(out))
        """)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        out = json.loads(r.stdout.split("RESULT ", 1)[1])
        self.assertEqual((out["salud"], out["ping"], out["app"]), (200, 200, 200))        # conectarse SIGUE funcionando
        self.assertIn(out["trabajos"][0], (500, 502)); self.assertIn("falta el módulo", out["trabajos"][1])
        self.assertIn(out["media"], (500, 502))
        self.assertIn("MÓDULO OPCIONAL FALTANTE", r.stderr)                               # y queda dicho en el registro


if __name__ == "__main__":
    unittest.main()
