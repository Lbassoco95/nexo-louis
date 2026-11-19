"""Traducción en vivo: WebSocket mínimo, puente app ⇄ OpenAI (con un OpenAI de mentira) y límites de seguridad."""
import os, sys, json, time, base64, socket, tempfile, threading, unittest, http.client
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(OPENCLAW_SPACES=str(TMP / "spaces"), PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City",
                  OPENCLAW_HOME=str(TMP / "oc"), OPENAI_API_KEY="sk-test", LIVE_IDLE_SECONDS="3")
import ws_lite as W
import app_auth as A
import openclaw_gateway as g


class Codec(unittest.TestCase):
    def test_ida_y_vuelta(self):
        for n in (0, 1, 5, 125, 126, 65535, 70000):
            for mask in (True, False):
                pl = os.urandom(n)
                fin, op, data, resto = W.decode_frame(W.encode_frame(pl, 2, mask) + b"X")
                self.assertEqual((fin, op, data, resto), (True, 2, pl, b"X"), (n, mask))

    def test_apretón_de_manos(self):
        self.assertEqual(W.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")     # ejemplo del RFC 6455

    def test_fragmentos_ping_y_cierre(self):
        a, b = socket.socketpair()
        srv, cli = W.Conn(a), W.Conn(b, cliente=True)
        b.sendall(bytes([0x01, 0x80 | 3]) + b"\0\0\0\0" + b"hol" + bytes([0x80, 0x80 | 1]) + b"\0\0\0\0" + b"a")   # texto fragmentado
        self.assertEqual(srv.recv_message(1), ("text", "hola"))
        cli._send(9, b"ping")                                                                                # ping → el servidor contesta pong y sigue
        cli.send_text("dos")
        self.assertEqual(srv.recv_message(1), ("text", "dos"))
        self.assertIsNone(srv.recv_message(0.1))
        cli.close()
        self.assertEqual(srv.recv_message(1)[0], "close")


class OpenAIDeMentira:
    """Imita al traductor: handshake, session.created/updated, y por cada 3 trocitos de audio devuelve transcripciones y voz."""
    def __init__(self):
        self.recibidos, self.updates, self.n = [], [], 0
        self.srv = socket.socket(); self.srv.bind(("127.0.0.1", 0)); self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.atender, args=(c,), daemon=True).start()

    def atender(self, c):
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        head, _, resto = buf.partition(b"\r\n\r\n")
        h = {l.split(b":", 1)[0].strip().lower(): l.split(b":", 1)[1].strip() for l in head.split(b"\r\n")[1:] if b":" in l}
        self.auth = h.get(b"authorization", b"").decode()
        c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                   + W.accept_key(h[b"sec-websocket-key"].decode()) + "\r\n\r\n").encode())
        ws = W.Conn(c, buf=resto)
        ws.send_text(json.dumps({"type": "session.created", "session": {"type": "translation"}}))
        while True:
            m = ws.recv_message(1)
            if m is None:
                continue
            if m[0] == "close":
                return
            ev = json.loads(m[1])
            if ev["type"] == "session.update":
                self.updates.append(ev["session"])
                if ev["session"]["audio"]["output"].get("language") == "es" and "input" not in ev["session"]["audio"]:              # el traductor de mentira rechaza «es» (prueba de errores)
                    ws.send_text(json.dumps({"type": "error", "error": {"message": "Unsupported output language: es"}}))
                else:
                    ws.send_text(json.dumps({"type": "session.updated"}))
            elif ev["type"] == "session.input_audio_buffer.append":
                self.n += 1
                self.recibidos.append(len(base64.b64decode(ev["audio"])))
                if self.n % 3 == 0:
                    ws.send_text(json.dumps({"type": "session.input_transcript.delta", "delta": " Buenos", "elapsed_ms": 300}))
                    ws.send_text(json.dumps({"type": "session.output_transcript.delta", "delta": "Good", "elapsed_ms": 500}))
                    ws.send_text(json.dumps({"type": "session.output_audio.delta", "delta": base64.b64encode(b"\x01\x02" * 2400).decode()}))
            else:
                ws.send_text(json.dumps({"type": "error", "error": {"message": "evento desconocido " + ev["type"]}}))


class Puente(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.LIVE_IDLE_SECONDS = 3                       # el valor lo fija el 1.er módulo que importe el gateway: aquí se fuerza
        cls.fake = OpenAIDeMentira()
        os.environ["LIVE_UPSTREAM_URL"] = f"ws://127.0.0.1:{cls.fake.port}/v1/realtime/translations?model=gpt-realtime-translate"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        assert A.set_password("polo", "una frase larga y segura 2026") == ""
        assert A.set_password("atajo-polo", "otra frase larga y segura 2026") == ""

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def conectar(self, token="master-secret-123", to="en"):
        c = W.connect_url(f"ws://127.0.0.1:{self.port}/v1/app/live")
        c.send_text(json.dumps({"t": "auth", "token": token, "to": to}))
        return c

    def leer(self, c, hasta=3.0, quiero=lambda ms: False):
        out, fin = [], time.time() + hasta
        while time.time() < fin and not quiero(out):
            m = c.recv_message(0.3)
            if m is None:
                continue
            if m[0] == "close":
                out.append(("close", m[1])); break
            out.append((m[0], json.loads(m[1]) if m[0] == "text" else m[1]))
        return out

    def test_audio_ida_y_vuelta_con_transcripciones(self):
        c = self.conectar()
        ms = self.leer(c, 2, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        self.assertTrue(any(k == "text" and d.get("t") == "listo" and d["to"] == "en" for k, d in ms))
        for _ in range(6):
            c.send_binary(b"\x00\x01" * 2400)                                            # 100 ms de audio PCM16 24 kHz
        ms = self.leer(c, 4, lambda o: any(k == "binary" for k, _ in o) and any(k == "text" and d.get("t") == "out" for k, d in o))
        c.close()
        bins = [d for k, d in ms if k == "binary"]
        self.assertTrue(bins and bins[0][0] == 1 and len(bins[0]) == 1 + 4800)           # prefijo 0x01 + PCM
        self.assertTrue(any(k == "text" and d.get("t") == "in" and "Buenos" in d["d"] for k, d in ms))
        self.assertTrue(any(k == "text" and d.get("t") == "out" and d["d"] == "Good" for k, d in ms))
        self.assertGreaterEqual(len(self.fake.recibidos), 6); self.assertEqual(set(self.fake.recibidos), {4800})
        self.assertEqual(self.fake.auth, "Bearer sk-test")                                # el servidor usa SU llave
        self.assertEqual(self.fake.updates[0]["audio"]["output"]["language"], "en")
        self.assertEqual(self.fake.updates[0]["audio"]["input"]["transcription"]["model"], "gpt-realtime-whisper")

    def test_cambiar_idioma_en_plena_sesion(self):
        n0 = len(self.fake.updates)
        c = self.conectar(to="en")
        self.leer(c, 1.5, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        c.send_text(json.dumps({"t": "lang", "to": "zh"}))
        self.leer(c, 1.0)
        c.close()
        self.assertEqual(self.fake.updates[-1], {"audio": {"output": {"language": "zh"}}})
        self.assertGreater(len(self.fake.updates), n0)

    def test_errores_del_traductor_llegan_a_la_app(self):
        c = self.conectar()
        self.leer(c, 1.5, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        c.send_text(json.dumps({"t": "lang", "to": "es"}))
        ms = self.leer(c, 2, lambda o: any(k == "text" and d.get("t") == "error" for k, d in o))
        c.close()
        self.assertTrue(any(k == "text" and d.get("t") == "error" and "Unsupported output language" in d["m"] for k, d in ms))

    def test_modo_escuchar_solo_subtitulos_en_espanol(self):
        n0 = len(self.fake.updates)
        c = W.connect_url(f"ws://127.0.0.1:{self.port}/v1/app/live")
        c.send_text(json.dumps({"t": "auth", "token": "master-secret-123", "modo": "escuchar", "to": "zh"}))   # aunque pidan otro idioma, escuchar es SIEMPRE al español
        ms = self.leer(c, 2, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        listo = [d for k, d in ms if k == "text" and d.get("t") == "listo"][0]
        self.assertEqual((listo["to"], listo["modo"], listo["max"]), ("es", "escuchar", g.LIVE_ESCUCHAR_MAX_SECONDS))
        self.assertEqual(self.fake.updates[n0]["audio"]["output"]["language"], "es")
        for _ in range(6):
            c.send_binary(b"\x00\x01" * 2400)
        ms = self.leer(c, 3, lambda o: any(k == "text" and d.get("t") == "out" for k, d in o))
        c.send_text(json.dumps({"t": "lang", "to": "en"}))                                              # cambiar de idioma no aplica en este modo
        c.send_text(json.dumps({"t": "ping"}))
        ms += self.leer(c, 1.0)
        c.close()
        self.assertTrue(any(k == "text" and d.get("t") == "out" and d["d"] == "Good" for k, d in ms))
        self.assertFalse(any(k == "binary" for k, _ in ms))                                              # no se reenvía la voz traducida
        self.assertEqual(len(self.fake.updates), n0 + 1)

    def test_ping_mantiene_viva_la_sesion_en_silencio(self):
        c = W.connect_url(f"ws://127.0.0.1:{self.port}/v1/app/live")
        c.send_text(json.dumps({"t": "auth", "token": "master-secret-123", "modo": "escuchar"}))
        self.leer(c, 1.5, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        fin = time.time() + g.LIVE_IDLE_SECONDS + 2                                                      # más que el límite de inactividad, sin audio pero con ping
        ms = []
        while time.time() < fin:
            c.send_text(json.dumps({"t": "ping"}))
            ms += self.leer(c, 1.0)
        c.close()
        self.assertFalse(any(k == "text" and d.get("t") == "end" for k, d in ms))

    def _silencio(self, silencio=1, repregunta=2):
        ant = (g.LIVE_ESCUCHAR_SILENCIO, g.LIVE_ESCUCHAR_REPREGUNTA)
        g.LIVE_ESCUCHAR_SILENCIO, g.LIVE_ESCUCHAR_REPREGUNTA = silencio, repregunta
        self.addCleanup(lambda: setattr(g, "LIVE_ESCUCHAR_SILENCIO", ant[0]) or setattr(g, "LIVE_ESCUCHAR_REPREGUNTA", ant[1]))
        c = W.connect_url(f"ws://127.0.0.1:{self.port}/v1/app/live")
        c.send_text(json.dumps({"t": "auth", "token": "master-secret-123", "modo": "escuchar"}))
        return c

    def leer_con_ping(self, c, hasta, quiero=lambda ms: False):            # el cliente real manda un ping cada 20 s; aquí cada segundo (la inactividad de prueba es de 3 s)
        out, fin = [], time.time() + hasta
        while time.time() < fin and not quiero(out):
            try:
                c.send_text(json.dumps({"t": "ping"}))
            except OSError:
                break
            out += self.leer(c, 0.8, quiero)
        return out

    def test_escuchar_pregunta_y_NUNCA_se_detiene_sola(self):
        c = self._silencio(1, 2)
        ms = self.leer_con_ping(c, 5, lambda o: any(k == "text" and d.get("t") == "silencio" for k, d in o))
        self.assertTrue(any(k == "text" and d.get("t") == "silencio" and d["repite"] == 2 for k, d in ms))        # pregunta «¿seguimos escuchando?»
        ms = self.leer_con_ping(c, 6)                                                                               # nadie responde: vuelve a preguntar, pero NO corta
        self.assertGreaterEqual(sum(1 for k, d in ms if k == "text" and d.get("t") == "silencio"), 1)             # se vuelve a preguntar
        self.assertFalse(any(k == "text" and d.get("t") == "end" for k, d in ms))                                  # y la escucha sigue viva
        c.close()

    def test_escuchar_seguir_voz_pausa_y_apagar_el_aviso(self):
        c = self._silencio(1, 100)
        self.leer_con_ping(c, 5, lambda o: any(k == "text" and d.get("t") == "silencio" for k, d in o))
        c.send_text(json.dumps({"t": "seguir"}))                                                                   # «seguir escuchando» no corta ni pierde nada
        ms = self.leer_con_ping(c, 1.6)
        self.assertFalse(any(k == "text" and d.get("t") in ("end", "error") for k, d in ms))
        for _ in range(6):
            c.send_binary(b"\x00\x01" * 2400)                                                                      # alguien habla
        ms = self.leer_con_ping(c, 3, lambda o: any(k == "text" and d.get("t") == "in" for k, d in o))
        self.assertTrue(any(k == "text" and d.get("t") == "in" for k, d in ms))                                    # el texto sigue llegando tras «seguir»
        c.send_text(json.dumps({"t": "pausa"}))                                                                    # en pausa no pregunta nada
        ms = self.leer_con_ping(c, 3)
        self.assertFalse(any(k == "text" and d.get("t") == "silencio" for k, d in ms))
        c.send_text(json.dumps({"t": "continuar"})); c.send_text(json.dumps({"t": "cfg", "aviso": False}))        # con el aviso apagado tampoco
        ms = self.leer_con_ping(c, 3)
        self.assertFalse(any(k == "text" and d.get("t") == "silencio" for k, d in ms))
        c.close()

    def test_sin_autorizacion_se_rechaza(self):
        for tok in ("malo", ""):
            c = self.conectar(tok)
            ms = self.leer(c, 2)
            self.assertTrue(any(k == "text" and d.get("t") == "error" for k, d in ms), tok)
        tok = A.login("atajo-polo", "otra frase larga y segura 2026", "127.0.0.1")["token"]
        ms = self.leer(self.conectar(tok), 2)
        self.assertTrue(any(k == "text" and d.get("t") == "error" for k, d in ms))        # usuario solo-subida: no puede traducir

    def test_usuario_normal_si_puede(self):
        tok = A.login("polo", "una frase larga y segura 2026", "127.0.0.1")["token"]
        c = self.conectar(tok)
        ms = self.leer(c, 2, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        c.close()
        self.assertTrue(any(k == "text" and d.get("t") == "listo" for k, d in ms))

    def test_limite_de_sesiones_simultaneas(self):
        a, b = self.conectar(), self.conectar()
        for c in (a, b):
            self.leer(c, 1.5, lambda o: any(k == "text" and d.get("t") == "listo" for k, d in o))
        tercera = self.conectar()
        ms = self.leer(tercera, 2)
        self.assertTrue(any(k == "text" and d.get("t") == "error" and "en curso" in d["m"] for k, d in ms))
        a.close(); b.close()
        time.sleep(0.5)

    def test_inactividad_cierra_la_sesion(self):
        c = self.conectar()
        ms = self.leer(c, 7, lambda o: any(k == "close" or (k == "text" and d.get("t") == "end") for k, d in o))
        self.assertTrue(any(k == "text" and d.get("t") == "end" and "inactividad" in d["m"] for k, d in ms))

    def test_sin_upgrade_sigue_el_camino_normal(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/v1/app/live"); r = c.getresponse(); r.read()
        self.assertEqual(r.status, 401)


if __name__ == "__main__":
    unittest.main()
