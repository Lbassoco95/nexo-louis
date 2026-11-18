"""Donna en tiempo real: la voz de OpenAI (de mentira aquí) + el cerebro completo de Donna como herramienta `consultar_donna`."""
import os, sys, json, time, base64, socket, tempfile, threading, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City",
                  OPENCLAW_HOME=str(TMP / "oc"), OPENAI_API_KEY="sk-test", LIVE_IDLE_SECONDS="30")
import ws_lite as W
import app_auth as A
import louis_core as core
import openclaw_gateway as g

CONSULTAS = []
g._hist_append = lambda *a, **k: CONSULTAS.append(("hist",) + a)


def fake_donna(msg, hist, registrar=True):
    CONSULTAS.append(("donna", msg, list(hist), registrar))
    return "Junta con Dazon a las 10.", "deepseek", None


g._ask_donna_voice = fake_donna


class RealtimeDeMentira:
    """Imita gpt-realtime-2.1: tras 3 trocitos de audio «oye» una frase, responde y pide la herramienta; al recibir su resultado, lo dice."""
    def __init__(self, modo="agenda"):
        self.modo, self.recibidos, self.sesion, self.items, self.cancel, self.respuestas = modo, 0, None, [], 0, 0
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

    def evento(self, ws, **e):
        ws.send_text(json.dumps(e))

    def atender(self, c):
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        head, _, resto = buf.partition(b"\r\n\r\n")
        h = {l.split(b":", 1)[0].strip().lower(): l.split(b":", 1)[1].strip() for l in head.split(b"\r\n")[1:] if b":" in l}
        self.ruta = head.split(b"\r\n")[0].decode()
        c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                   + W.accept_key(h[b"sec-websocket-key"].decode()) + "\r\n\r\n").encode())
        ws = W.Conn(c, buf=resto)
        self.evento(ws, type="session.created", session={"type": "realtime"})
        while True:
            m = ws.recv_message(1)
            if m is None:
                continue
            if m[0] == "close":
                return
            ev = json.loads(m[1]); t = ev["type"]
            if t == "session.update":
                self.sesion = ev["session"]; self.evento(ws, type="session.updated", session=ev["session"])
            elif t == "input_audio_buffer.append":
                self.recibidos += 1
                if self.recibidos == 3:
                    self.evento(ws, type="input_audio_buffer.speech_started")
                    self.evento(ws, type="conversation.item.input_audio_transcription.delta", delta="¿Qué")
                    self.evento(ws, type="conversation.item.input_audio_transcription.completed", transcript="¿Qué tengo hoy?")
                    self.evento(ws, type="response.output_audio_transcript.delta", delta="Déjame")
                    self.evento(ws, type="response.output_audio.delta", delta=base64.b64encode(b"\x01\x02" * 2400).decode())
                    self.evento(ws, type="response.output_audio_transcript.done", transcript="Déjame revisar.")
                    nombre, args = ("consultar_donna", json.dumps({"pedido": "agenda de hoy de Polo"})) if self.modo == "agenda" else ("terminar_conversacion", "{}")
                    self.evento(ws, type="response.function_call_arguments.done", call_id="call_1", name=nombre, arguments=args)
                    self.evento(ws, type="response.done", response={"output": [{"type": "function_call"}]})
            elif t == "conversation.item.create":
                self.items.append(ev["item"])
            elif t == "response.create":
                self.respuestas += 1
                if self.items and self.items[-1].get("type") == "function_call_output" or self.modo == "texto":
                    txt = "Tienes una junta con Dazon a las diez." if self.modo != "terminar" else "Hasta luego, Polo."
                    self.evento(ws, type="response.output_audio.delta", delta=base64.b64encode(b"\x03\x04" * 2400).decode())
                    self.evento(ws, type="response.output_audio_transcript.done", transcript=txt)
                    self.evento(ws, type="response.done", response={"output": [{"type": "message"}]})
            elif t == "response.cancel":
                self.cancel += 1


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.LIVE_IDLE_SECONDS = 30
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        assert A.set_password("atajo-polo", "otra frase larga y segura 2026") == ""

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def sesion(self, modo="agenda", token="master-secret-123"):
        fake = RealtimeDeMentira(modo)
        os.environ["LIVE_DONNA_URL"] = f"ws://127.0.0.1:{fake.port}/v1/realtime?model=gpt-realtime-2.1"
        c = W.connect_url(f"ws://127.0.0.1:{self.port}/v1/app/donna")
        c.send_text(json.dumps({"t": "auth", "token": token}))
        return fake, c

    def leer(self, c, hasta=4.0, quiero=lambda o: False):
        out, fin = [], time.time() + hasta
        while time.time() < fin and not quiero(out):
            m = c.recv_message(0.3)
            if m is None:
                continue
            if m[0] == "close":
                out.append(("close", m[1])); break
            out.append((m[0], json.loads(m[1]) if m[0] == "text" else m[1]))
        return out

    def hay(self, ms, t, **kw):
        return any(k == "text" and d.get("t") == t and all(d.get(a) == b for a, b in kw.items()) for k, d in ms)


class Donna(Base):
    def test_voz_cerebro_y_herramienta_de_punta_a_punta(self):
        del CONSULTAS[:]
        fake, c = self.sesion("agenda")
        self.leer(c, 2, lambda o: self.hay(o, "listo"))
        for _ in range(4):
            c.send_binary(b"\x00\x01" * 2400)
        ms = self.leer(c, 6, lambda o: self.hay(o, "out_done", text="Tienes una junta con Dazon a las diez."))
        c.close()
        self.assertTrue(self.hay(ms, "speech")); self.assertTrue(self.hay(ms, "in_done", text="¿Qué tengo hoy?"))
        self.assertTrue(self.hay(ms, "tool", name="consultar_donna")); self.assertTrue(self.hay(ms, "tool_done"))
        self.assertTrue(self.hay(ms, "out_done", text="Tienes una junta con Dazon a las diez."))
        self.assertTrue(any(k == "binary" and d[0] == 1 and len(d) == 4801 for k, d in ms))            # voz: 0x01 + PCM
        # el cerebro completo de Donna recibió el pedido, SIN registrarlo dos veces en el historial
        donna = [x for x in CONSULTAS if x[0] == "donna"]
        self.assertEqual(donna[0][1], "agenda de hoy de Polo"); self.assertFalse(donna[0][3])
        # y su resultado volvió al modelo de voz para que lo diga
        self.assertEqual(fake.items[-1]["type"], "function_call_output")
        self.assertIn("Junta con Dazon", fake.items[-1]["output"]); self.assertEqual(fake.items[-1]["call_id"], "call_1")
        self.assertGreaterEqual(fake.respuestas, 1)
        # la conversación alimenta la memoria nocturna
        hist = [x for x in CONSULTAS if x[0] == "hist"]
        self.assertIn(("hist", "user", "¿Qué tengo hoy?"), hist)
        self.assertIn(("hist", "assistant", "Tienes una junta con Dazon a las diez."), hist)

    def test_la_sesion_se_configura_con_la_forma_verificada(self):
        fake, c = self.sesion("agenda")
        self.leer(c, 2, lambda o: self.hay(o, "listo")); time.sleep(0.3)
        c.close()
        s = fake.sesion
        self.assertEqual(s["type"], "realtime")
        self.assertIn("Donna", s["instructions"]); self.assertIn("consultar_donna", s["instructions"])
        self.assertEqual(s["audio"]["output"]["voice"], "marin")
        self.assertEqual(s["audio"]["input"]["turn_detection"]["type"], "server_vad")
        self.assertTrue(s["audio"]["input"]["turn_detection"]["interrupt_response"])
        self.assertEqual([t["name"] for t in s["tools"]], ["consultar_donna", "terminar_conversacion"])
        self.assertIn("/v1/realtime?model=gpt-realtime-2.1", fake.ruta)

    def test_interrumpir_y_escribir(self):
        fake, c = self.sesion("texto")
        self.leer(c, 2, lambda o: self.hay(o, "listo"))
        c.send_text(json.dumps({"t": "interrupt"}))
        c.send_text(json.dumps({"t": "text", "text": "Hola Donna"}))
        ms = self.leer(c, 4, lambda o: self.hay(o, "out_done"))
        c.close()
        self.assertEqual(fake.cancel, 1)
        self.assertEqual(fake.items[0]["content"][0]["text"], "Hola Donna")
        self.assertTrue(self.hay(ms, "out_done", text="Tienes una junta con Dazon a las diez."))

    def test_despedida_cierra_la_sesion(self):
        fake, c = self.sesion("terminar")
        self.leer(c, 2, lambda o: self.hay(o, "listo"))
        for _ in range(4):
            c.send_binary(b"\x00\x01" * 2400)
        ms = self.leer(c, 6, lambda o: self.hay(o, "end"))
        c.close()
        self.assertTrue(self.hay(ms, "tool", name="terminar_conversacion")); self.assertTrue(self.hay(ms, "end", m="Conversación terminada"))
        self.assertIn("Despídete", fake.items[-1]["output"])

    def test_sin_autorizacion_y_usuario_solo_subida(self):
        for tok in ("malo", A.login("atajo-polo", "otra frase larga y segura 2026", "127.0.0.1")["token"]):
            fake, c = self.sesion("agenda", tok)
            self.assertTrue(self.hay(self.leer(c, 2), "error", m="No autorizado"))


class Resumen(unittest.TestCase):
    def test_el_resumen_de_voz_trae_reglas_fecha_y_memoria(self):
        b = core.build_realtime_brief()
        for esperado in ("Donna", "consultar_donna", "nunca voseo", "CONFIRMA ANTES DE CAMBIAR", "FECHA Y HORA", "terminar_conversacion"):
            self.assertIn(esperado, b)
        self.assertLessEqual(len(b), 15000)


if __name__ == "__main__":
    unittest.main()
