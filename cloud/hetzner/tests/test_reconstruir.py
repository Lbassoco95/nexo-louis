"""Reconstruir el texto de una junta desde su audio guardado (cuando la transcripción en vivo falló), y proteger la grabación que está corriendo ahora."""
import io, json, os, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"), MEDIA_MIN_FREE_GB="0")
import donna_db as D, donna_audio as A, donna_personas as P, donna_reconstruir as R
import openclaw_gateway as g


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    import shutil
    shutil.rmtree(A.DIR, ignore_errors=True)


def audio_de_prueba(seg=30) -> bytes:
    p = TMP / "tono.webm"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=%d" % seg, "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
    return p.read_bytes()


def subir(aid, rid=None, usuario="polo", offset=0, viejo=True):
    rid = rid or ("rid" + aid)
    d = audio_de_prueba()
    n = len(d) // 3 + 1
    for i in range(3):
        t = d[i * n:(i + 1) * n]
        if t:
            A.guardar_parte(aid, rid, usuario, i, io.BytesIO(t), len(t), offset, "audio/webm;codecs=opus")
    if viejo:                                                                    # una grabación que dejó de recibir partes hace rato (pestaña cerrada)
        c = A._c()
        c.execute("UPDATE escucha_audio SET actualizado=? WHERE aid=?", (int(time.time()) - 3600, aid))
        c.commit()
        c.close()


def falso(mp3, refs=None):
    return {"segments": [{"speaker": "A", "start": 0, "end": 5, "text": "Hola a todos"}, {"speaker": "A", "start": 5.5, "end": 9, "text": "empezamos con el contrato."},
                         {"speaker": "B", "start": 10, "end": 20, "text": "La comisión se paga por cada transacción."}]}


class Turnos(unittest.TestCase):
    def test_une_frases_seguidas_de_la_misma_voz_y_separa_lo_demas(self):
        segs = [{"spk": "A", "ini": 0, "fin": 3, "texto": "Uno"}, {"spk": "A", "ini": 4, "fin": 6, "texto": "dos"},          # pausa corta: misma intervención
                {"spk": "A", "ini": 12, "fin": 14, "texto": "tres"},                                                        # pausa larga: otra
                {"spk": "B", "ini": 14.5, "fin": 16, "texto": "cuatro"}, {"spk": "B", "ini": 16, "fin": 17, "texto": "  "}]  # otra voz; el vacío se ignora
        t = R._turnos(segs)
        self.assertEqual([(x["spk"], x["texto"]) for x in t], [("A", "Uno dos"), ("A", "tres"), ("B", "cuatro")])

    def test_la_hora_no_tiene_tope_de_minutos(self):
        self.assertEqual((R._hora(0), R._hora(64 * 60 + 50), R._hora(170 * 60 + 1)), ("00:00", "64:50", "170:01"))


class Reconstruccion(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_el_audio_suelto_se_vuelve_una_junta_con_texto_voces_y_el_mismo_audio(self):
        subir("audsuelto01")
        r = R.procesar("audsuelto01", "polo", llamar=falso, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "lista", r)
        self.assertEqual((r["turnos"], r["voces"]), (2, 2))                                        # las dos frases de A se unieron
        sid = r["sesion_id"]
        filas = [dict(x) for x in P._c().execute("SELECT hora, voz_n, texto FROM intervenciones WHERE sesion_id=? ORDER BY id", (sid,))]
        self.assertEqual([(f["hora"], f["voz_n"]) for f in filas], [("00:00", 1), ("00:10", 2)])
        self.assertEqual(filas[0]["texto"], "Hola a todos empezamos con el contrato.")
        self.assertIn("por cada transacción", filas[1]["texto"])
        self.assertEqual([t["aid"] for t in A.tramos(sid)], ["audsuelto01"])                       # el audio quedó ligado a la junta nueva
        self.assertEqual(A.huerfanos("polo"), [])
        self.assertEqual(R.estado("audsuelto01")["estado"], "lista")

    def test_la_hora_respeta_donde_empieza_el_audio(self):
        subir("audoffset01", offset=120000)
        r = R.procesar("audoffset01", "polo", llamar=falso, key="k", trabajo_base=TMP / "w")
        horas = [x["hora"] for x in P._c().execute("SELECT hora FROM intervenciones WHERE sesion_id=? ORDER BY id", (r["sesion_id"],))]
        self.assertEqual(horas, ["02:00", "02:10"])                                                # reloj de la junta: así encajan con el audio

    def test_un_error_de_la_api_o_un_audio_sin_texto_se_dice_claro(self):
        subir("audmalo0001")
        def mala(mp3, refs=None): raise RuntimeError("OpenAI respondió 400: bad")
        r = R.procesar("audmalo0001", "polo", llamar=mala, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "error")
        self.assertIn("400", R.estado("audmalo0001")["error"])
        self.assertEqual(len(A.huerfanos("polo")), 1)                                              # no se creó ninguna junta a medias
        r2 = R.procesar("audmalo0001", "polo", llamar=lambda m, refs=None: {"segments": []}, key="k", trabajo_base=TMP / "w")
        self.assertIn("no devolvió texto", r2["error"])

    def test_solo_audio_propio_suelto_y_que_no_se_este_grabando(self):
        subir("audajeno001", usuario="otro")
        self.assertIn("no encontrado", R.procesar("audajeno001", "polo", llamar=falso, key="k")["error"])
        subir("audligado001")
        A.ligar_tramo("audligado001", "una-junta", "polo")
        self.assertIn("ya está ligado", R.procesar("audligado001", "polo", llamar=falso, key="k")["error"])
        subir("audvivo0001", viejo=False)                                                          # llegó una parte hace un instante: se está grabando
        self.assertIn("se está grabando", R.procesar("audvivo0001", "polo", llamar=falso, key="k")["error"])


class GrabacionEnCurso(unittest.TestCase):
    """La grabación que corre ahora no se puede borrar, ligar ni reconstruir desde otra pantalla."""
    def setUp(self):
        limpiar()
        subir("audencurso01", viejo=False)

    def test_aparece_como_activa_y_no_se_toca(self):
        h = A.huerfanos("polo")[0]
        self.assertTrue(h["activo"])
        with self.assertRaises(ValueError):
            A.borrar("audencurso01", "polo")
        with self.assertRaises(ValueError):
            A.ligar_tramo("audencurso01", "otra-junta", "polo")
        self.assertEqual(A.estado("audencurso01")["estado"], "grabando")                           # nada cambió

    def test_al_terminar_ya_se_puede(self):
        A.terminar("audencurso01", "polo")
        self.assertFalse(A.huerfanos("polo")[0]["activo"])
        self.assertTrue(A.ligar_tramo("audencurso01", "otra-junta", "polo"))

    def test_si_dejo_de_llegar_audio_hace_rato_ya_no_cuenta_como_en_curso(self):
        c = A._c()
        c.execute("UPDATE escucha_audio SET actualizado=? WHERE aid=?", (int(time.time()) - 400, "audencurso01"))
        c.commit()
        c.close()
        self.assertFalse(A.huerfanos("polo")[0]["activo"])                                         # pestaña cerrada: el audio quedó suelto y se puede recuperar
        self.assertEqual(A.borrar("audencurso01", "polo"), 1)


class PorHttp(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.orig = R.procesar
        R.procesar = lambda aid, usuario="admin": self.orig(aid, usuario, llamar=falso, key="k", trabajo_base=TMP / "w")

    def tearDown(self):
        R.procesar = self.orig
        self.srv.shutdown()

    def req(self, path, body=None):
        r = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, {"Authorization": "Bearer master-x", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())

    def test_el_boton_reconstruye_en_segundo_plano_y_crea_la_junta(self):
        subir("audhttp0001", usuario="admin")
        its = self.req("/v1/app/escucha/audio_huerfano")["items"]
        self.assertEqual((its[0]["aid"], its[0]["activo"], its[0]["recon"]["estado"]), ("audhttp0001", False, "nada"))
        self.assertTrue(self.req("/v1/app/escucha/audio", {"accion": "reconstruir", "aid": "audhttp0001"})["ok"])
        for _ in range(100):
            if R.estado("audhttp0001")["estado"] == "lista":
                break
            time.sleep(0.2)
        self.assertEqual(R.estado("audhttp0001")["estado"], "lista")
        ses = self.req("/v1/app/escucha/sesiones")["items"]
        self.assertTrue(any(s["lineas"] == 2 and s["audio"]["tramos"] == 1 for s in ses))        # la junta nueva aparece en la lista, con su audio

    def test_no_deja_reconstruir_ni_borrar_lo_que_se_esta_grabando(self):
        subir("audhttp0002", usuario="admin", viejo=False)
        for cuerpo in ({"accion": "reconstruir", "aid": "audhttp0002"}, {"accion": "borrar", "id": "audhttp0002"}):
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.req("/v1/app/escucha/audio", cuerpo)
            self.assertEqual(e.exception.code, 400)
        self.assertEqual(self.req("/v1/app/escucha/audio_huerfano")["items"][0]["activo"], True)


class TraduccionAlEspanol(unittest.TestCase):
    """Lo que se habla en chino queda traducido al español como texto principal, con el original guardado."""
    def test_solo_se_traduce_lo_que_esta_en_chino_y_por_lotes(self):
        lineas = [{"es": "Buenos días", "orig": "", "idioma": ""}] + [{"es": "我们今天讨论合同的佣金条款第%d条" % i, "orig": "", "idioma": ""} for i in range(40)]
        lotes = []
        def trad(ts):
            lotes.append(len(ts))
            return ["ES:" + t for t in ts]
        h, sin = R.aplicar_traduccion(lineas, trad)
        self.assertEqual((h, sin), (40, 0))
        self.assertEqual(lineas[0], {"es": "Buenos días", "orig": "", "idioma": ""})                 # el español no se toca
        self.assertTrue(lineas[1]["es"].startswith("ES:我们今天"))
        self.assertEqual((lineas[1]["orig"], lineas[1]["idioma"]), ("我们今天讨论合同的佣金条款第0条", "zh"))
        self.assertTrue(all(n <= R.LOTE_N for n in lotes) and len(lotes) >= 3)                      # varios lotes, ninguno enorme

    def test_si_no_se_puede_traducir_se_conserva_el_original_y_se_cuenta(self):
        lineas = [{"es": "好的，我们继续讨论这个问题吧", "orig": "", "idioma": ""}, {"es": "合同需要在周五之前签署", "orig": "", "idioma": ""}]
        h, sin = R.aplicar_traduccion(lineas, lambda ts: None)
        self.assertEqual((h, sin), (0, 2))
        self.assertEqual((lineas[0]["es"], lineas[0]["orig"], lineas[0]["idioma"]), ("好的，我们继续讨论这个问题吧",) * 2 + ("zh",))
        def rota(ts): raise RuntimeError("se cayó")
        self.assertEqual(R.aplicar_traduccion([{"es": "我们今天讨论合同", "orig": "", "idioma": ""}], rota), (0, 1))     # una falla no tumba la reconstrucción

    def test_la_junta_reconstruida_queda_en_espanol_con_el_original_en_chino(self):
        limpiar()
        subir("audchino0001")
        chino = lambda mp3, refs=None: {"segments": [{"speaker": "A", "start": 0, "end": 9, "text": "佣金按每笔交易收取，这一点必须在合同里写清楚。"}, {"speaker": "B", "start": 12, "end": 20, "text": "Entendido, lo revisamos."}]}
        r = R.procesar("audchino0001", "polo", llamar=chino, key="k", trabajo_base=TMP / "w", traducir=lambda ts: ["La comisión se cobra por cada transacción; debe quedar claro en el contrato."] * len(ts))
        self.assertEqual((r["estado"], r["traducidos"], r["sin_traducir"]), ("lista", 1, 0))
        f = [dict(x) for x in P._c().execute("SELECT texto, original, idioma FROM intervenciones WHERE sesion_id=? ORDER BY id", (r["sesion_id"],))]
        self.assertEqual(f[0]["texto"], "La comisión se cobra por cada transacción; debe quedar claro en el contrato.")
        self.assertIn("佣金按每笔交易收取", f[0]["original"])
        self.assertEqual((f[0]["idioma"], f[1]["idioma"], f[1]["texto"]), ("zh", "", "Entendido, lo revisamos."))


class SinSaldoEnOpenAI(unittest.TestCase):
    """Sin créditos en OpenAI (429): se dice claro y con qué hacer, no un error críptico; el aviso a Polo sale una vez por hora."""
    MSG = '{ "error": { "message": "You have no credits remaining. Add credits to continue using the API at https://platform.openai.com/settings/organization/billing", "type": "insufficient_quota" } }'

    def test_reconoce_los_mensajes_de_falta_de_saldo(self):
        import donna_diariza as DZ
        for t in (self.MSG, "You exceeded your current quota", "insufficient_quota", "billing_hard_limit_reached", "Add credits to continue"):
            self.assertTrue(DZ.sin_saldo(t), t)
        for t in ("Rate limit reached for requests", "bad request", "", None):
            self.assertFalse(DZ.sin_saldo(t), t)

    def test_la_llamada_a_openai_explica_que_no_hay_saldo(self):
        import donna_diariza as DZ, urllib.request, urllib.error
        orig = urllib.request.urlopen
        def err(code, cuerpo):
            def f(*a, **k): raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(cuerpo.encode()))
            return f
        arch = TMP / "a.mp3"; arch.write_bytes(b"x" * 100)
        try:
            urllib.request.urlopen = err(429, self.MSG)
            with self.assertRaises(RuntimeError) as e:
                DZ.llamar_openai("k", arch)
            self.assertIn("no tiene saldo", str(e.exception)); self.assertIn("platform.openai.com/settings/organization/billing", str(e.exception))
            urllib.request.urlopen = err(400, '{"error": "otra cosa"}')
            with self.assertRaises(RuntimeError) as e2:
                DZ.llamar_openai("k", arch)
            self.assertIn("OpenAI respondió 400", str(e2.exception))                                # los demás errores siguen igual
        finally:
            urllib.request.urlopen = orig

    def test_reconstruir_sin_saldo_deja_un_mensaje_que_se_entiende(self):
        limpiar()
        subir("audsinsaldo01")
        import donna_diariza as DZ
        def sin(mp3, refs=None): raise RuntimeError("OpenAI no tiene saldo (error 429: sin créditos). Recarga en https://platform.openai.com/settings/organization/billing y vuelve a intentarlo.")
        r = R.procesar("audsinsaldo01", "polo", llamar=sin, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "error")
        self.assertIn("no tiene saldo", R.estado("audsinsaldo01")["error"])

    def test_el_aviso_de_sin_saldo_sale_una_vez_por_hora(self):
        import louis_push
        vistos = []
        orig = louis_push.notify_async
        louis_push.notify_async = lambda *a, **k: vistos.append(a[0])
        g._SALDO_AVISO["ts"] = 0.0
        try:
            g._aviso_sin_saldo(); g._aviso_sin_saldo(); g._aviso_sin_saldo()
            self.assertEqual(len(vistos), 1)
            g._SALDO_AVISO["ts"] = time.time() - 4000
            g._aviso_sin_saldo()
            self.assertEqual(len(vistos), 2)
        finally:
            louis_push.notify_async = orig


if __name__ == "__main__":
    unittest.main()
