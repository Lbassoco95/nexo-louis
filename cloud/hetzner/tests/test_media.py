"""Adjuntos de la app: subida por partes (reanudable), extracción de texto, errores honestos y endpoints."""
import os, sys, io, json, time, zipfile, tempfile, http.client, threading, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City",
                  OPENCLAW_HOME=str(TMP / "oc"))
import app_media as M
import louis_core as core
import openclaw_gateway as g

M.summarize = lambda text: "RESUMEN-DE-PRUEBA" if len(text) > 200 else ""


def subir(nombre, data, mime="", parte=7, background=False):
    r = M.init(nombre, mime, len(data))
    n = 0
    for i in range(0, max(len(data), 1), parte):
        M.save_chunk(r["id"], n, io.BytesIO(data[i:i + parte]), len(data[i:i + parte]))
        n += 1
    return r["id"], M.finish(r["id"], n, background=background)


class Subida(unittest.TestCase):
    def test_partes_se_unen_en_orden_y_archivo_0600(self):
        datos = bytes(range(256)) * 10
        mid, m = subir("Reunión 1.txt", datos)
        fp, _ = M.file_path(mid)
        self.assertEqual(fp.read_bytes(), datos)
        self.assertEqual(oct(fp.stat().st_mode & 0o777), "0o600")
        self.assertEqual(m["estado"], "en cola")
        self.assertFalse((M._dir(mid) / "parts").exists())            # partes limpiadas

    def test_reanudar_reenviar_una_parte_es_idempotente(self):
        r = M.init("a.txt", "", 6)
        M.save_chunk(r["id"], 0, io.BytesIO(b"abc"), 3)
        M.save_chunk(r["id"], 0, io.BytesIO(b"abc"), 3)               # reintento tras corte
        self.assertEqual(M.parts_have(r["id"]), [0])
        with self.assertRaises(ValueError) as c:
            M.finish(r["id"], 2)
        self.assertIn("1", str(c.exception))                          # dice qué parte falta
        M.save_chunk(r["id"], 1, io.BytesIO(b"def"), 3)
        M.finish(r["id"], 2, background=False)
        self.assertEqual(M.file_path(r["id"])[0].read_bytes(), b"abcdef")

    def test_parte_incompleta_se_rechaza(self):
        r = M.init("b.txt", "", 10)
        with self.assertRaises(ValueError):
            M.save_chunk(r["id"], 0, io.BytesIO(b"123"), 10)
        self.assertEqual(M.parts_have(r["id"]), [])

    def test_sin_espacio_en_disco_se_rechaza_claro(self):
        orig = M.free_bytes
        M.free_bytes = lambda: 1024 * 1024
        try:
            with self.assertRaises(ValueError) as c:
                M.init("grande.mov", "video/quicktime", 10 * 1024 ** 3)
            self.assertIn("espacio", str(c.exception))
        finally:
            M.free_bytes = orig

    def test_sin_tope_de_tamano_propio(self):
        r = M.init("enorme.mov", "video/quicktime", 50 * 1024 ** 3 if M.free_bytes() > 60 * 1024 ** 3 else 1)
        self.assertRegex(r["id"], M.ID_RE)

    def test_nombres_e_ids_peligrosos(self):
        self.assertEqual(M.safe_name("../../etc/passwd"), "passwd")
        self.assertNotIn("/", M.safe_name("a/b\\c.txt"))
        with self.assertRaises(ValueError):
            M.save_chunk("../x", 0, io.BytesIO(b"a"), 1)
        self.assertIsNone(M.get("../x"))

    def test_tipo(self):
        self.assertEqual(M.kind_of("foto.jpg"), "imagen")
        self.assertEqual(M.kind_of("x.m4a", "audio/mp4"), "audio")
        self.assertEqual(M.kind_of("x.mov", "video/quicktime"), "video")
        self.assertEqual(M.kind_of("x.pdf"), "documento")


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


class Extraccion(unittest.TestCase):
    def _f(self, name, data):
        p = TMP / name
        p.write_bytes(data)
        return p

    def test_docx_xlsx_pptx_txt(self):
        d = self._f("a.docx", _zip({"word/document.xml": '<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>Hola acuerdo uno</w:t></w:r></w:p><w:p><w:r><w:t>Segundo punto</w:t></w:r></w:p></w:body></w:document>'}))
        self.assertIn("Hola acuerdo uno", M.extract_document(d))
        x = self._f("a.xlsx", _zip({"xl/sharedStrings.xml": "<sst><si><t>Cliente</t></si><si><t>Monto</t></si></sst>",
                                    "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row><row><c><v>10</v></c><c><v>20</v></c></row></sheetData></worksheet>'}))
        t = M.extract_document(x)
        self.assertIn("Cliente | Monto", t); self.assertIn("10 | 20", t)
        p = self._f("a.pptx", _zip({"ppt/slides/slide1.xml": '<p:sld xmlns:p="x" xmlns:a="y"><a:t>Título de la lámina</a:t></p:sld>'}))
        self.assertIn("Título de la lámina", M.extract_document(p))
        self.assertIn("ñandú", M.extract_document(self._f("a.txt", "ñandú rápido".encode())))

    def test_formato_desconocido_dice_la_verdad(self):
        with self.assertRaises(RuntimeError):
            M.extract_document(self._f("a.bin", b"\x00\x01"))


class Proceso(unittest.TestCase):
    def test_documento_listo_buscable_y_contexto(self):
        mid, _ = subir("Acta Dazon.txt", ("El acuerdo con Dazon es enviar la propuesta a Lupita el viernes. " * 3).encode())
        M.process(mid)
        m = M.get(mid)
        self.assertEqual(m["estado"], "listo")
        self.assertIn("Lupita", M.leer_texto(mid))
        self.assertIn(mid, M.buscar("propuesta lupita"))
        self.assertIn("sin coincidencias", M.buscar("zzzxxx"))
        ctx = M.contexto_para_chat([mid, "no-existe", "../x"])
        self.assertIn(f"[ADJUNTO {mid}", ctx); self.assertIn("Lupita", ctx)

    def test_audio_se_transcribe_y_se_resume(self):
        orig = M.transcribe
        M.transcribe = lambda path, prog=None: ("[00:00:00] " + "hablamos de la cooperativa y del plazo del contrato. " * 8, "openai")
        try:
            mid, _ = subir("grabacion.m4a", b"audio-falso", "audio/mp4")
            M.process(mid)
            m = M.get(mid)
            self.assertEqual((m["estado"], m["motor"], m["resumen"]), ("listo", "openai", "RESUMEN-DE-PRUEBA"))
        finally:
            M.transcribe = orig

    def test_error_honesto_si_no_hay_motor(self):
        orig = M.transcribe
        def boom(path, prog=None): raise RuntimeError("falta ffmpeg en el servidor (sudo apt install ffmpeg)")
        M.transcribe = boom
        try:
            mid, _ = subir("nota.m4a", b"x", "audio/mp4")
            M.process(mid)
            m = M.get(mid)
            self.assertEqual(m["estado"], "error"); self.assertIn("ffmpeg", m["error"])
            self.assertIn("error", M.leer_texto(mid))
            self.assertIn("no se pudo procesar", M.contexto_para_chat([mid]))
        finally:
            M.transcribe = orig

    def test_texto_largo_se_resume_en_el_chat_y_se_lee_por_tramos(self):
        orig = M.transcribe
        M.transcribe = lambda path, prog=None: ("palabra " * 5000, "openai")
        try:
            mid, _ = subir("larga.m4a", b"x", "audio/mp4")
            M.process(mid)
            ctx = M.contexto_para_chat([mid], max_chars=1000)
            self.assertIn("adjuntos_leer", ctx); self.assertLess(len(ctx), 2500)
            self.assertIn("desde=", M.leer_texto(mid, 0, 1000))
        finally:
            M.transcribe = orig

    def test_herramientas_de_donna(self):
        mid, _ = subir("Minuta.txt", ("Se decidió contratar a Marco para el proyecto Ikán en diciembre. " * 4).encode())
        M.process(mid)
        self.assertIn(mid, core.execute_tool("adjuntos_buscar", {"consulta": "Marco Ikán"}))
        self.assertIn("contratar", core.execute_tool("adjuntos_leer", {"id": mid}))
        self.assertIn("Minuta.txt", core.execute_tool("adjuntos_listar", {}))
        self.assertTrue(core.needs_tools("[ADJUNTO m123 «x»]"))
        self.assertTrue(core.needs_tools("¿qué dijimos en la grabación de ayer?"))


class Gateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, body=None, raw=None, tok="master-secret-123"):
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

    def test_flujo_completo_por_http(self):
        st, r = self.req("POST", "/v1/app/media/init", {"nombre": "notas.txt", "mime": "text/plain", "tamano": 2_000_000})
        self.assertEqual(st, 200); mid = r["id"]
        big = os.urandom(1_500_000)                                # > 1 MB: antes habría dado 'cuerpo demasiado grande'
        self.assertEqual(self.req("PUT", f"/v1/app/media/{mid}/chunk?i=0", raw=big)[0], 200)
        self.assertEqual(self.req("PUT", f"/v1/app/media/{mid}/chunk?i=1", raw=b"fin")[0], 200)
        st, d = self.req("GET", f"/v1/app/media/{mid}/parts")
        self.assertEqual(d["partes"], [0, 1])
        orig = M.process; M.process = lambda x: None               # sin procesar en este test
        try:
            st, d = self.req("POST", f"/v1/app/media/{mid}/finish", {"partes": 2})
        finally:
            M.process = orig
        self.assertEqual(st, 200); self.assertEqual(d["tamano"], len(big) + 3)
        st, blob = self.req("GET", f"/v1/app/media/{mid}/file")
        self.assertEqual(st, 200); self.assertEqual(blob, big + b"fin")
        self.assertTrue(any(i["id"] == mid for i in self.req("GET", "/v1/app/media")[1]["items"]))

    def test_faltan_partes_y_auth(self):
        _, r = self.req("POST", "/v1/app/media/init", {"nombre": "x.txt", "tamano": 3})
        st, d = self.req("POST", f"/v1/app/media/{r['id']}/finish", {"partes": 3})
        self.assertEqual(st, 400); self.assertIn("faltan", d["error"])
        self.assertEqual(self.req("PUT", f"/v1/app/media/{r['id']}/chunk?i=0", raw=b"a", tok="malo")[0], 401)
        self.assertEqual(self.req("GET", "/v1/app/media", tok="malo")[0], 401)
        self.assertEqual(self.req("GET", "/v1/app/media/mffffffffff")[0], 404)
        self.assertEqual(self.req("PUT", "/v1/app/media/../x/chunk?i=0", raw=b"a")[0], 404)

    def test_chat_inyecta_los_adjuntos(self):
        mid, _ = subir("Contrato.txt", ("Cláusula quinta: plazo de 30 días naturales. " * 3).encode())
        M.process(mid)
        ctx = M.contexto_para_chat([mid])
        self.assertIn("plazo de 30 días", ctx)


if __name__ == "__main__":
    unittest.main()
