"""Número de versión junto al nombre: una sola fuente (sw.js), la pantalla lo trae, el servidor dice cuál corre y cuál está instalada."""
import json, os, re, sys, tempfile, threading, unittest, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"), MEDIA_MIN_FREE_GB="0")
import openclaw_gateway as g


def del_sw():
    return re.search(r"louis-app-v(\d+)", open(os.path.join(SERVICES, "app", "sw.js")).read()).group(1)


class Version(unittest.TestCase):
    def test_la_version_sale_de_sw_js(self):
        self.assertEqual(g._version_instalada(), del_sw())
        self.assertRegex(g._version_instalada(), r"^\d+$")

    def test_la_pantalla_trae_el_numero_ya_puesto(self):
        html, _ct = g._app_file("/app/")
        m = re.search(rb"const APP_V='([^']*)'", html)
        self.assertEqual(m.group(1).decode(), g._VERSION_ARRANQUE)                                  # no queda el marcador sin sustituir
        self.assertNotIn(b"{{APP_VERSION}}", html)
        self.assertIn(b'id="ver-s"', html)                                                          # barra lateral (escritorio)
        self.assertIn(b'id="ver-h"', html)                                                          # encabezado (celular)

    def test_ping_dice_la_version_que_corre_y_la_instalada(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/v1/app/ping" % srv.server_address[1], headers={"Authorization": "Bearer master-x"}))
            d = json.loads(r.read())
            self.assertEqual((d["version"], d["instalada"]), (g._VERSION_ARRANQUE, del_sw()))
        finally:
            srv.shutdown()

    def test_si_los_archivos_cambian_pero_el_proceso_no_se_reinicio_se_nota(self):
        viejo = g._VERSION_ARRANQUE
        try:
            g._VERSION_ARRANQUE = str(int(del_sw()) - 1)                                           # simula un servidor que arrancó con la versión anterior
            self.assertNotEqual(g._version_instalada(), g._VERSION_ARRANQUE)
        finally:
            g._VERSION_ARRANQUE = viejo


if __name__ == "__main__":
    unittest.main()
