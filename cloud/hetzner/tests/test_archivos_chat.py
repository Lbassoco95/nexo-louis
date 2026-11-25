"""Archivos que Donna entrega EN EL CHAT (tarjeta con ver/descargar) en lugar de «enviarlos por Telegram»: cola por respuesta, almacén, vista previa,
conversación guardada y aviso cuando Donna afirma haber adjuntado algo que no se adjuntó."""
import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"), MEDIA_MIN_FREE_GB="0")
import app_media, app_chats
import openclaw_gateway as g
core = g.core


def vaciar_cola():
    core.get_pending_files()


class Cola(unittest.TestCase):
    def setUp(self):
        vaciar_cola()
        core._FILE_TLS.owner = None

    def tearDown(self):
        core._FILE_TLS.owner = None
        vaciar_cola()

    def test_cada_respuesta_recoge_solo_sus_archivos(self):
        core._FILE_TLS.owner = "r1"
        core._queue_file(b"AAA", "a.pdf")
        core._FILE_TLS.owner = "r2"
        core._queue_file(b"BBB", "b.pdf")
        self.assertEqual([f[1] for f in core.get_pending_files(owner="r1")], ["a.pdf"])
        self.assertEqual([f[1] for f in core.get_pending_files(owner="r1")], [])                 # ya se entregó
        self.assertEqual([f[1] for f in core.get_pending_files(owner="r2")], ["b.pdf"])          # el de la otra respuesta seguía ahí

    def test_pedir_otra_vez_el_mismo_archivo_si_se_entrega(self):
        core._FILE_TLS.owner = "r1"
        core._queue_file(b"AAA", "a.pdf")
        core._queue_file(b"AAA", "a.pdf")                                                         # duplicado en la misma entrega: una sola vez
        self.assertEqual(len(core.get_pending_files(owner="r1")), 1)
        core._FILE_TLS.owner = "r3"
        core._queue_file(b"AAA", "a.pdf")                                                         # días después lo vuelve a pedir: sí se entrega
        self.assertEqual(len(core.get_pending_files(owner="r3")), 1)

    def test_telegram_sigue_recogiendo_todo(self):
        core._queue_file(b"X1", "x.pdf")
        core._FILE_TLS.owner = "r1"
        core._queue_file(b"X2", "y.pdf")
        self.assertEqual(sorted(f[1] for f in core.get_pending_files()), ["x.pdf", "y.pdf"])

    def test_ninguna_herramienta_ni_instruccion_dice_que_manda_por_telegram(self):
        src = open(os.path.join(SERVICES, "louis_core.py")).read()
        for frase in ("SE LO MANDA a Polo por Telegram", "se lo MANDA a Polo por Telegram", "se lo manda a Polo por Telegram", "enviándolo por Telegram"):
            self.assertNotIn(frase, src)


class Almacen(unittest.TestCase):
    def test_registrar_y_leer_un_archivo_entregado(self):
        m = app_media.register_bytes(b"%PDF-1.4 hola", "Contrato.pdf", user="polo")
        self.assertEqual((m["nombre"], m["mime"], m["estado"], m["tamano"]), ("Contrato.pdf", "application/pdf", "listo", 13))
        fp, meta = app_media.file_path(m["id"])
        self.assertEqual(fp.read_bytes(), b"%PDF-1.4 hola")
        with self.assertRaises(ValueError):
            app_media.register_bytes(b"", "vacio.txt")

    def test_la_conversacion_guarda_y_limpia_los_archivos(self):
        m = app_media.register_bytes(b"hola", "nota.txt", user="polo")
        f = {"id": m["id"], "nombre": "nota.txt", "tamano": 4, "tipo": "documento", "mime": "text/plain"}
        app_chats.guardar("polo", "c0123456789", [{"r": "u", "t": "mándame la nota"}, {"r": "a", "t": "Aquí está", "f": [f, {"id": "../../etc/passwd", "nombre": "x"}, "basura"]}])
        d = app_chats.obtener("polo", "c0123456789")
        self.assertEqual([x["id"] for x in d["msgs"][1]["f"]], [m["id"]])                          # un id inventado o raro se descarta
        app_chats.agregar_respuesta("polo", "c0123456789", "Otra respuesta", "sonnet", [f])
        self.assertEqual(app_chats.obtener("polo", "c0123456789")["msgs"][-1]["f"][0]["nombre"], "nota.txt")


class Respuesta(unittest.TestCase):
    """_chat_worker con un modelo simulado: lo que las herramientas encolan llega a la respuesta; lo que el modelo afirma sin haberlo entregado se avisa."""
    def setUp(self):
        vaciar_cola()
        self._orig = (core.call_llm, core.load_anthropic_key, core.load_system_prompt, getattr(core, "_dropbox_download_bytes", None))
        core.load_anthropic_key = lambda: "k"
        core.load_system_prompt = lambda channel="api": ""
        core._dropbox_download_bytes = lambda path: (b"contenido del contrato", None)

    def tearDown(self):
        core.call_llm, core.load_anthropic_key, core.load_system_prompt, core._dropbox_download_bytes = self._orig
        vaciar_cola()

    def correr(self, rid, llm):
        core.call_llm = llm
        g._CHAT_SLOTS.acquire()
        g._RESULTS[rid] = {"rid": rid, "t0": 0, "done": False, "client_gone": False}
        g._chat_worker(rid, "mándame el contrato", [], registrar=False, user="polo")
        return g._RESULTS.pop(rid)

    def test_el_archivo_de_dropbox_llega_como_tarjeta(self):
        def llm(*a, **k):
            return core._dropbox_enviar("/Contratos/CPS-VIZUM-LIMPIO.docx") + " Listo.", "sonnet"
        r = self.correr("rid-ok-0001", llm)
        self.assertIsNone(r["error"])
        self.assertEqual([a["nombre"] for a in r["archivos"]], ["CPS-VIZUM-LIMPIO.docx"])
        self.assertNotIn("Telegram", r["text"])
        self.assertNotIn("NO se adjuntó", r["text"])
        fp, _m = app_media.file_path(r["archivos"][0]["id"])
        self.assertEqual(fp.read_bytes(), b"contenido del contrato")                              # el archivo real, descargable

    def test_decir_adjunto_sin_adjuntar_se_avisa(self):
        r = self.correr("rid-mal-0001", lambda *a, **k: ("✅ Contrato enviado. 📎 Contrato_3sep2026_FINAL.pdf — revísalo.", "haiku"))
        self.assertEqual(r["archivos"], [])
        self.assertIn("NO se adjuntó ningún archivo", r["text"])
        r2 = self.correr("rid-mal-0002", lambda *a, **k: ("Enviado por Telegram — CPS.docx", "haiku"))
        self.assertIn("NO se adjuntó ningún archivo", r2["text"])

    def test_una_respuesta_normal_no_se_toca(self):
        r = self.correr("rid-nor-0001", lambda *a, **k: ("La versión más reciente es del 3 de septiembre.", "sonnet"))
        self.assertEqual((r["text"], r["archivos"]), ("La versión más reciente es del 3 de septiembre.", []))

    def test_los_archivos_de_otra_respuesta_no_se_mezclan(self):
        core._FILE_TLS.owner = "otra-resp"
        core._queue_file(b"de otra conversacion", "otra.pdf")
        core._FILE_TLS.owner = None
        r = self.correr("rid-mix-0001", lambda *a, **k: ("Hecho", "sonnet"))
        self.assertEqual(r["archivos"], [])
        self.assertEqual([f[1] for f in core.get_pending_files(owner="otra-resp")], ["otra.pdf"])

    def test_si_falla_no_queda_nada_en_memoria(self):
        def llm(*a, **k):
            core._dropbox_enviar("/x/y.docx")
            raise RuntimeError("se cayó el modelo")
        r = self.correr("rid-err-0001", llm)
        self.assertTrue(r["error"])
        self.assertEqual(core.get_pending_files(owner="rid-err-0001"), [])


class PorHttp(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()

    def get(self, path):
        return urllib.request.urlopen(urllib.request.Request(self.base + path, headers={"Authorization": "Bearer master-x"}))

    def test_ver_y_descargar(self):
        pdf = app_media.register_bytes(b"%PDF-1.4 hola", "c.pdf", user="polo")
        txt = app_media.register_bytes("línea uno\nlínea dos".encode(), "n.txt", user="polo")
        html = app_media.register_bytes(b"<h1>DOF</h1><script>alert(1)</script>", "d.html", user="polo")
        self.assertEqual(json.loads(self.get("/v1/app/media/%s/preview" % pdf["id"]).read())["modo"], "pdf")
        p = json.loads(self.get("/v1/app/media/%s/preview" % txt["id"]).read())
        self.assertEqual((p["modo"], p["texto"]), ("texto", "línea uno\nlínea dos"))
        h = json.loads(self.get("/v1/app/media/%s/preview" % html["id"]).read())
        self.assertEqual(h["modo"], "html")                                                       # la pantalla lo muestra en un marco SIN scripts (sandbox)
        r = self.get("/v1/app/media/%s/file" % pdf["id"])
        self.assertEqual((r.read(), r.headers["Content-Type"]), (b"%PDF-1.4 hola", "application/pdf"))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/v1/app/media/m0000000000/preview")
        self.assertEqual(e.exception.code, 404)


class VisoresDeDocumentos(unittest.TestCase):
    """Word, Excel y PowerPoint se dibujan en la pantalla con librerías que viajan dentro de la app (sin internet ni instalar nada en el servidor)."""
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()

    def get(self, path, auth=True):
        h = {"Authorization": "Bearer master-x"} if auth else {}
        return urllib.request.urlopen(urllib.request.Request(self.base + path, headers=h))

    def test_cada_tipo_tiene_su_visor(self):
        esperado = {"c.docx": "docx", "h.xlsx": "xlsx", "m.xlsm": "xlsx", "p.pptx": "pptx", "d.csv": "csv", "n.md": "md", "v.pdf": "pdf", "f.png": "imagen"}
        for nombre, modo in esperado.items():
            m = app_media.register_bytes(b"x" * 20, nombre, user="polo")
            self.assertEqual(json.loads(self.get("/v1/app/media/%s/preview" % m["id"]).read())["modo"], modo, nombre)

    def test_las_librerias_de_los_visores_se_sirven_desde_la_app_sin_sesion(self):
        html = open(os.path.join(SERVICES, "app", "index.html")).read()
        nombres = set(re.findall(r"'(vendor-[A-Za-z0-9.\-]+\.js)'", html))
        self.assertGreaterEqual(len(nombres), 4)                                                   # jszip, docx-preview, xlsx, pptx-preview
        for n in nombres:
            self.assertTrue(os.path.exists(os.path.join(SERVICES, "app", n)), n + " no está en la carpeta de la app")
            r = self.get("/app/" + n, auth=False)                                                  # es código público: no lleva datos
            self.assertIn("javascript", r.headers["Content-Type"])
            self.assertIn("immutable", r.headers["Cache-Control"])
            self.assertGreater(len(r.read()), 50000)
        for malo in ("/app/vendor-.js/../../etc/passwd", "/app/vendor-x.html", "/app/vendor-no-existe.js"):
            with self.assertRaises(urllib.error.HTTPError):
                self.get(malo, auth=False)


import re

if __name__ == "__main__":
    unittest.main()
