"""Detener una escucha desde cualquier dispositivo y no guardar dos veces la misma junta."""
import base64, json, os, queue, socket, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"), MEDIA_MIN_FREE_GB="0")
import app_media, ws_lite, donna_db as D, donna_remoto as RM, donna_borrador as B, donna_rescate as R
import openclaw_gateway as g


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    with RM._LOCK:
        RM._EN_VIVO.clear()


T1 = [{"o": "es", "a": "hola", "b": "", "t": 0}]


class Unitarias(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_activas_detecta_lo_reciente_y_solo_del_usuario(self):
        B.guardar("borrador111", "polo", {"turnos": T1})
        B.guardar("borrador222", "otro", {"turnos": T1})
        c = B._c(); c.execute("UPDATE escucha_borradores SET actualizado=1 WHERE id='borrador333'"); c.commit(); c.close()
        self.assertEqual([a["id"] for a in RM.activas("polo")], ["borrador111"])
        self.assertEqual({a["id"] for a in RM.activas("admin")}, {"borrador111", "borrador222"})
        c = B._c(); c.execute("UPDATE escucha_borradores SET actualizado=?", (int(time.time()) - 600,)); c.commit(); c.close()
        self.assertEqual(RM.activas("polo"), [])                                   # una escucha sin actividad en 2 min ya no cuenta

    def test_ordenar_con_excepto_y_vigencia(self):
        B.guardar("borrador111", "polo", {"turnos": T1})
        B.guardar("borrador222", "polo", {"turnos": T1})
        self.assertEqual(RM.ordenar("polo", excepto="borrador111"), ["borrador222"])    # no se detiene la de este dispositivo
        self.assertEqual((RM.orden_de("borrador222"), RM.orden_de("borrador111")), ("detener", ""))
        c = RM._c(); c.execute("UPDATE escucha_ordenes SET ts=?", (int(time.time()) - 700,)); c.commit(); c.close()
        self.assertEqual(RM.orden_de("borrador222"), "")                           # la orden caduca
        RM.ordenar("polo"); RM.limpiar("borrador222")
        self.assertEqual(RM.orden_de("borrador222"), "")

    def test_la_conexion_en_vivo_recibe_la_orden_al_instante(self):
        ev = RM.registrar("vivo1234ab", "polo")
        self.assertFalse(ev.is_set())
        self.assertTrue(any(a["id"] == "vivo1234ab" and a["en_vivo"] for a in RM.activas("polo")))
        self.assertEqual(RM.ordenar("polo"), ["vivo1234ab"])
        self.assertTrue(ev.is_set())
        RM.quitar("vivo1234ab")
        self.assertEqual(RM.ordenar("polo"), [])

    def test_una_conexion_que_se_reconecta_con_orden_pendiente_se_detiene(self):
        RM.registrar("vivo1234ab", "polo"); RM.ordenar("polo"); RM.quitar("vivo1234ab")
        self.assertTrue(RM.registrar("vivo1234ab", "polo").is_set())

    def test_no_se_guarda_dos_veces(self):
        self.assertFalse(RM.ya_guardada("junta123abc"))
        RM.marcar_guardada("junta123abc", "m1")
        self.assertTrue(RM.ya_guardada("junta123abc"))
        self.assertFalse(RM.ya_guardada(""))


class PorElServidor(unittest.TestCase):
    def setUp(self):
        limpiar()
        app_media.openai_key = lambda: "k"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.H = {"Authorization": "Bearer master-x"}

    def tearDown(self):
        self.srv.shutdown()

    def post(self, path, body):
        return json.loads(urllib.request.urlopen(urllib.request.Request(self.base + path, json.dumps(body).encode(), {**self.H, "Content-Type": "application/json"})).read())

    def get(self, path):
        return json.loads(urllib.request.urlopen(urllib.request.Request(self.base + path, headers=self.H)).read())

    def test_el_autoguardado_recibe_la_orden_y_la_junta_no_se_duplica(self):
        self.post("/v1/app/escucha/borrador", {"id": "pantalla1ab", "turnos": T1 * 3, "duracion": 30})
        self.assertEqual([a["id"] for a in self.get("/v1/app/escucha/activas")["items"]], ["pantalla1ab"])
        self.assertEqual(self.post("/v1/app/escucha/borrador", {"id": "pantalla1ab", "turnos": T1 * 3})["orden"], "")
        self.assertEqual(self.post("/v1/app/escucha/detener", {})["ordenes"], 1)
        self.assertEqual(self.post("/v1/app/escucha/borrador", {"id": "pantalla1ab", "turnos": T1 * 3})["orden"], "detener")       # la pantalla se entera en su siguiente autoguardado
        lineas = [{"t": "00:01", "voz": 0, "es": "Hola equipo", "idioma": "es"}]
        out = self.post("/v1/app/escucha/guardar", {"borrador_id": "pantalla1ab", "duracion": 30, "lineas": lineas, "voces": []})
        self.assertTrue(out["sesion_id"])
        with self.assertRaises(urllib.error.HTTPError) as cm:                                    # la OTRA pantalla intenta guardar lo mismo
            self.post("/v1/app/escucha/guardar", {"borrador_id": "pantalla1ab", "duracion": 30, "lineas": lineas, "voces": []})
        self.assertIn("ya se guardó", cm.exception.read().decode())
        self.assertEqual(RM.orden_de("pantalla1ab"), "")                                         # la orden se limpia al guardar
        self.assertEqual(len(self.get("/v1/app/escucha/sesiones")["items"]), 1)

    def test_la_conexion_en_vivo_se_cierra_con_detener_remoto(self):
        class Mudo:
            def __init__(s): s.q = queue.Queue()
            def send_text(s, t): pass
            def send_binary(s, b): pass
            def recv_message(s, timeout=1):
                try: return s.q.get(timeout=timeout)
                except queue.Empty: return None
            def close(s): pass
        ws_lite.connect_url = lambda url, headers: Mudo()
        sk = socket.create_connection(("127.0.0.1", self.srv.server_address[1]))
        sk.sendall(("GET /v1/app/live HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % base64.b64encode(os.urandom(16)).decode()).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += sk.recv(1024)
        c = ws_lite.Conn(sk, cliente=True)
        c.send_text(json.dumps({"t": "auth", "token": "master-x", "modo": "escuchar", "to": "es", "rescate": "enlive12345"}))
        for _ in range(20):
            if any(a["id"] == "enlive12345" and a["en_vivo"] for a in self.get("/v1/app/escucha/activas")["items"]):
                break
            time.sleep(0.2)
        else:
            self.fail("la escucha en vivo no apareció como activa")
        self.assertEqual(self.post("/v1/app/escucha/detener", {})["ordenes"], 1)
        vistos, t0 = [], time.time()
        while time.time() - t0 < 8:
            m = c.recv_message(timeout=1)
            if m and m[0] == "text":
                vistos.append(json.loads(m[1])["t"])
                if "detener_remoto" in vistos:
                    break
        self.assertIn("detener_remoto", vistos)
        time.sleep(2.5)
        self.assertEqual(RM._EN_VIVO, {})                                                        # y la conexión se quitó del registro


if __name__ == "__main__":
    unittest.main()
