"""La escucha en vivo guarda el texto en el servidor apenas el transcriptor termina cada frase (transcriptor simulado, sin OpenAI)."""
import json, os, queue, sys, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"))
import app_media, ws_lite, donna_db as D, donna_rescate as R
import openclaw_gateway as g


class Upstream:
    """Hace de «OpenAI»: entrega eventos de transcripción cuando se le pide."""
    def __init__(self):
        self.q = queue.Queue()
        self.cerrado = False
        for ev in ({"type": "session.input_transcript.delta", "delta": "Hello ", "elapsed_ms": 1000},
                   {"type": "session.input_transcript.delta", "delta": "team", "elapsed_ms": 1200},
                   {"type": "session.input_transcript.done"},
                   {"type": "session.output_transcript.delta", "delta": "Hola equipo", "elapsed_ms": 1500},
                   {"type": "session.output_transcript.done"},
                   {"type": "session.input_transcript.delta", "delta": "Frase sin cerrar", "elapsed_ms": 9000}):
            self.q.put(("text", json.dumps(ev)))

    def send_text(self, t): pass
    def send_binary(self, b): pass

    def recv_message(self, timeout=1):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self): self.cerrado = True


class RescateLive(unittest.TestCase):
    def test_el_servidor_guarda_el_texto_aunque_la_pantalla_se_caiga(self):
        app_media.openai_key = lambda: "k"
        ws_lite.connect_url = lambda url, headers: Upstream()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cli = ws_lite.connect_url.__wrapped__ if hasattr(ws_lite.connect_url, "__wrapped__") else None
        import socket, base64
        s = socket.create_connection(("127.0.0.1", srv.server_address[1]))
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET /v1/app/live HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += s.recv(1024)
        self.assertIn(b"101", resp.split(b"\r\n")[0])
        c = ws_lite.Conn(s, cliente=True)
        c.send_text(json.dumps({"t": "auth", "token": "master-x", "modo": "escuchar", "to": "es", "rescate": "resc123abc"}))
        time.sleep(2.5)
        c.close()                                                  # «la pestaña se recargó»: sin guardar nada
        time.sleep(1.5)
        d = R.a_borrador("resc123abc", "admin")
        self.assertIsNotNone(d, "el servidor debió guardar el rescate")
        self.assertEqual([(t["a"], t["b"]) for t in d["turnos"]][:1], [("Hello team", "Hola equipo")])
        self.assertTrue(any("Frase sin cerrar" in t["a"] for t in d["turnos"]), "lo que quedaba sin cerrar también se guarda al terminar")
        srv.shutdown()


class FinPorSilencio(unittest.TestCase):
    def test_tras_el_tiempo_sin_voces_el_servidor_avisa_y_cierra(self):
        app_media.openai_key = lambda: "k"
        class Mudo(Upstream):
            def __init__(self):
                super().__init__()
                while not self.q.empty():
                    self.q.get()                                  # nadie habla
        ws_lite.connect_url = lambda url, headers: Mudo()
        g.LIVE_ESCUCHAR_SILENCIO = 2                              # «1 hora», acelerada para la prueba
        srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        import socket, base64
        s = socket.create_connection(("127.0.0.1", srv.server_address[1]))
        s.sendall(("GET /v1/app/live HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % base64.b64encode(os.urandom(16)).decode()).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += s.recv(1024)
        c = ws_lite.Conn(s, cliente=True)
        c.send_text(json.dumps({"t": "auth", "token": "master-x", "modo": "escuchar", "to": "es", "rescate": "mudo123abc"}))
        vistos, t0 = [], time.time()
        while time.time() - t0 < 10:
            m = c.recv_message(timeout=1)
            if m and m[0] == "text":
                vistos.append(json.loads(m[1])["t"])
                if vistos[-1] == "fin_silencio":
                    break
        g.LIVE_ESCUCHAR_SILENCIO = 3600
        srv.shutdown()
        self.assertIn("fin_silencio", vistos)
        self.assertNotIn("silencio", vistos)                      # ya no pregunta: detiene y la app guarda


class Silencio(unittest.TestCase):
    def test_no_pregunta_ni_cierra_antes_de_una_hora_sin_voces(self):
        self.assertGreaterEqual(g.LIVE_ESCUCHAR_SILENCIO, 3600)                       # «¿sigo escuchando?» solo tras 1 h sin oír a nadie
        self.assertGreater(g.LIVE_ESCUCHAR_IDLE, g.LIVE_ESCUCHAR_SILENCIO)            # y la conexión no se cierra por «inactividad» antes de ese aviso
        self.assertGreaterEqual(g.LIVE_ESCUCHAR_MAX_SECONDS, 6 * 3600)                # hasta que Polo la detenga


if __name__ == "__main__":
    unittest.main()
