"""Voz con Donna, subida rápida (Atajos), usuarios solo-subida y registro de errores del teléfono."""
import os, sys, json, time, tempfile, http.client, threading, unittest
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


class Base(unittest.TestCase):
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


class Servidor(Base):
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
        o = M._openai_transcribe; M._openai_transcribe = lambda p, k, m=None: "quiero revisar mi plan de doctorado"
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


def sse(raw: bytes) -> list:
    out = []
    for blk in raw.decode().split("\n\n"):
        ev, data = "message", ""
        for l in blk.split("\n"):
            if l.startswith("event:"):
                ev = l[6:].strip()
            elif l.startswith("data:"):
                data += l[5:].strip()
        if data:
            out.append((ev, json.loads(data)))
    return out


class Streaming(Base):
    """Los turnos en streaming: cada parte (lo que dijiste, la respuesta, el audio) llega en cuanto está lista."""
    def setUp(self):
        self._o = (T.voz_a_texto, T.texto_a_voz)
        T.voz_a_texto = lambda audio, nombre="v.webm", idioma_pista="": VISTO.get("stt", "¿cuándo empieza mi diplomado?")
        T.texto_a_voz = lambda t, i: b"MP3:" + t.encode()[:20]
        VISTO.pop("stt", None)
        core.call_haiku = lambda key, system, hist, user, **kw: json.dumps({"origen": "es", "traduccion": "Hello. How are you today? I hope all is well.", "pinyin": ""})
        g._VSESS.clear()

    def tearDown(self):
        T.voz_a_texto, T.texto_a_voz = self._o

    def test_turno_de_voz_orden_de_eventos_y_tiempos(self):
        st, raw = self.req("PUT", "/v1/app/voice/turn?vsid=s1&nombre=v.m4a", raw=b"a" * 3000)
        ev = sse(raw)
        nombres = [e for e, _ in ev]
        self.assertEqual(st, 200)
        self.assertEqual(nombres[:3], ["stt", "audio", "respuesta"])          # VOZ PRIMERO: lo que dijiste, el audio, y DESPUÉS el texto
        self.assertEqual(nombres[-1], "done")
        self.assertLess(nombres.index("audio"), nombres.index("respuesta"))
        done = ev[-1][1]["ms"]
        self.assertTrue({"stt", "llm", "audio1", "total"} <= set(done))
        self.assertIn("MODO VOZ", VISTO["system"])

    def test_contexto_solo_de_esa_conversacion(self):
        self.req("PUT", "/v1/app/voice/turn?vsid=a1", raw=b"a" * 3000)
        VISTO["stt"] = "¿y qué más?"
        seen = {}
        orig = core.call_llm
        core.call_llm = lambda k, system, history, user, history_file=None: (seen.update(h=list(history)) or ("Ok.", "deepseek"))
        try:
            self.req("PUT", "/v1/app/voice/turn?vsid=a1", raw=b"a" * 3000)
            self.assertEqual(len(seen["h"]), 2)                                # recuerda SOLO el turno anterior de esta sesión
            self.req("PUT", "/v1/app/voice/turn?vsid=otra", raw=b"a" * 3000)
            self.assertEqual(seen["h"], [])                                    # otra conversación: sin arrastrar nada
        finally:
            core.call_llm = orig

    def test_adios_termina_sin_llamar_al_modelo(self):
        VISTO["stt"] = "Eso es todo."
        llamadas = []
        orig = core.call_llm
        core.call_llm = lambda *a, **k: llamadas.append(1) or ("x", "m")
        try:
            st, raw = self.req("PUT", "/v1/app/voice/turn?vsid=z", raw=b"a" * 3000)
        finally:
            core.call_llm = orig
        ev = sse(raw)
        self.assertTrue(ev[-1][1].get("fin")); self.assertEqual(llamadas, [])

    def test_error_se_avisa_como_evento_fail(self):
        def mal(*a, **k): raise ValueError("no se escuchó voz")
        T.voz_a_texto = mal
        st, raw = self.req("PUT", "/v1/app/voice/turn?vsid=e", raw=b"a" * 3000)
        self.assertEqual(sse(raw)[-1], ("fail", {"error": "no se escuchó voz"}))

    def test_traductor_en_streaming(self):
        st, raw = self.req("PUT", "/v1/app/translate/stream?de=es&a=en&sid=tabcdef0123&nombre=v.m4a", raw=b"a" * 3000)
        ev = sse(raw); nombres = [e for e, _ in ev]
        self.assertEqual(nombres[:3], ["stt", "audio", "tr"]); self.assertEqual(nombres[-1], "done")      # primero el audio, luego el texto
        self.assertGreaterEqual(nombres.count("audio"), 1)
        self.assertEqual(dict(ev)["tr"]["destino"], "en")
        st, raw = self.req("PUT", "/v1/app/translate/stream?de=es&a=en&voz=0", raw=b"a" * 3000)
        self.assertNotIn("audio", [e for e, _ in sse(raw)])                  # voz=0: sin audio

    def test_si_falla_la_voz_el_texto_llega_igual(self):
        def mal(t, i): raise RuntimeError("OpenAI 500")
        T.texto_a_voz = mal
        st, raw = self.req("PUT", "/v1/app/translate/stream?de=es&a=en", raw=b"a" * 3000)
        nombres = [e for e, _ in sse(raw)]
        self.assertIn("tr", nombres); self.assertEqual(nombres[-1], "fail")           # el texto se muestra y el error se avisa

    def test_turno_largo_se_habla_completo(self):
        largo = ("Esta es una frase de prueba con bastante contenido para simular una intervención larga. " * 55).strip()
        core.call_haiku = lambda key, system, hist, user, **kw: json.dumps({"origen": "es", "traduccion": largo, "pinyin": ""})
        hablado = []
        T.texto_a_voz = lambda t, i: hablado.append(t) or b"MP3"
        st, raw = self.req("PUT", "/v1/app/translate/stream?de=es&a=en", raw=b"a" * 3000)
        self.assertEqual(sse(raw)[-1][0], "done")
        self.assertGreaterEqual(len(hablado), 5)
        self.assertGreaterEqual(sum(len(x) for x in hablado), int(len(largo) * 0.97))   # no se trunca: se lee casi todo (~4800 caracteres)

    def test_usuario_atajo_no_puede_usar_voz(self):
        tok = self.token("atajo-polo", "otra frase larga y segura 2026")
        self.assertEqual(self.req("PUT", "/v1/app/voice/turn?vsid=x", raw=b"a" * 3000, tok=tok)[0], 401)


class Compartir(Base):
    def _doc(self, texto="Conversación con el proveedor. El precio unitario es de 12 dólares y entrega en 30 días. " * 3):
        return M.register_text("Proveedor Shenzhen", texto, tipo="conversacion", resumen="**Resumen** precio y plazo")["id"]

    def test_crear_abrir_y_revocar_enlace(self):
        mid = self._doc()
        st, d = self.req("POST", f"/v1/app/media/{mid}/share", {"dias": 7})
        self.assertEqual(st, 200); self.assertTrue(d["url"].endswith("/s/" + d["token"])); self.assertEqual(d["dias"], 7)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/s/" + d["token"])                                           # SIN token de la app: es público
        r = c.getresponse(); html = r.read().decode()
        self.assertEqual(r.status, 200); self.assertIn("precio unitario", html); self.assertIn("<b>Resumen</b>", html)
        self.assertIn("noindex", r.getheader("X-Robots-Tag")); self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertNotIn("<script", html)
        self.assertEqual(self.req("POST", f"/v1/app/media/{mid}/unshare", {})[1]["revocados"], 1)
        c.request("GET", "/s/" + d["token"]); self.assertEqual(c.getresponse().status, 404)

    def test_enlace_caducado_o_inventado_da_404(self):
        mid = self._doc()
        tok = M.share_create(mid, 1)["token"]
        sh = M._sh_read(); k = M._th(tok); sh[k]["exp"] = int(time.time()) - 5; M._sh_write(sh)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for ruta in ("/s/" + tok, "/s/" + "A" * 32, "/s/corto", "/s/../etc/passwd"):
            c.request("GET", ruta); r = c.getresponse(); r.read()
            self.assertIn(r.status, (404, 400), ruta)

    def test_el_html_compartido_escapa_el_contenido(self):
        mid = self._doc("<script>alert(1)</script> & <img src=x onerror=alert(2)>")
        tok = M.share_create(mid, 1)["token"]
        html = M.share_page(tok)
        self.assertNotIn("<script>alert", html); self.assertNotIn("<img", html); self.assertIn("&lt;script&gt;", html)

    def test_solo_documentos_listos(self):
        r = M.init("x.m4a", "audio/mp4", 1)
        with self.assertRaises(ValueError):
            M.share_create(r["id"], 7)

    def test_usuario_atajo_no_puede_compartir(self):
        tok = self.token("atajo-polo", "otra frase larga y segura 2026")
        self.assertEqual(self.req("POST", f"/v1/app/media/{self._doc()}/share", {}, tok=tok)[0], 401)

    def test_donna_puede_compartir_por_herramienta(self):
        mid = self._doc()
        out = core.execute_tool("adjuntos_compartir", {"id": mid, "dias": 3})
        self.assertIn("/s/", out); self.assertIn("3 día", out)
        self.assertIn("revocados: 1", core.execute_tool("adjuntos_compartir", {"id": mid, "revocar": True}))


class Frases(unittest.TestCase):
    def test_primer_trozo_corto_y_orden(self):
        t = T.dividir_frases("Hola Polo. Hoy tienes tres cosas importantes. La primera es la junta con Dazon a las diez. Luego la comida. Y al final la llamada.")
        self.assertGreaterEqual(len(t), 2); self.assertLessEqual(len(t), 5)
        self.assertTrue(t[0].startswith("Hola Polo")); self.assertEqual(" ".join(t).count("Dazon"), 1)

    def test_chino_sin_espacios(self):
        t = T.dividir_frases("我们需要确认批次的单价。请在周五之前回复我们。谢谢你的帮助！", primero=12, resto=12)
        self.assertGreaterEqual(len(t), 2)

    def test_quita_formato_para_hablar(self):
        self.assertEqual(T.texto_hablado("**Hola** [enlace](http://x.com) `código` | tabla"), "Hola enlace código tabla")

    def test_voz_por_trozos_en_orden(self):
        o = T.texto_a_voz
        T.texto_a_voz = lambda t, i: t.encode()
        try:
            out = list(T.voz_por_trozos("Primera frase larga de prueba aquí. Segunda frase larga de prueba aquí. Tercera.", "es"))
        finally:
            T.texto_a_voz = o
        self.assertEqual([i for i, _ in out], list(range(len(out))))
        self.assertTrue(out[0][1].startswith(b"Primera"))



class Avisos(unittest.TestCase):
    def test_aviso_lleva_enlace_al_documento(self):
        import louis_push as P
        P.STATE_DIR = TMP / "st2"; P.INBOX_FILE = P.STATE_DIR / "notifs.json"; P.INBOX_LOCK_FILE = P.STATE_DIR / "notifs.lock"
        r = P.inbox_add("Transcripción lista: reunión", "resumen…", link="media:mabc1234567")
        self.assertEqual(r["link"], "media:mabc1234567")
        self.assertEqual(P.inbox_list(1)[0]["link"], "media:mabc1234567")


if __name__ == "__main__":
    unittest.main()
