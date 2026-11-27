"""Completar el texto que le falta a una junta guardada usando su audio (omite el silencio, agrega al final de la MISMA junta, acomoda audios que se encimen)."""
import io, json, os, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"), MEDIA_MIN_FREE_GB="0")
import donna_db as D, donna_audio as A, donna_personas as P, donna_completar as C, donna_reconstruir as R
import openclaw_gateway as g


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    import shutil
    shutil.rmtree(A.DIR, ignore_errors=True)


_AUDIO = []


def audio_mixto() -> bytes:
    """11 min: 1 min de tono (hay voz) + 8 min de silencio + 2 min de tono."""
    if not _AUDIO:
        p = TMP / "mixto.webm"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=60", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-f", "lavfi", "-i", "sine=frequency=300:duration=120",
                        "-filter_complex", "[1:a]atrim=duration=480[s];[0:a]aresample=48000[a];[2:a]aresample=48000[c];[a][s][c]concat=n=3:v=0:a=1[o]", "-map", "[o]", "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
        _AUDIO.append(p.read_bytes())
    return _AUDIO[0]


def subir(aid, offset=0, usuario="polo", creado=None, viejo=True):
    d = audio_mixto()
    n = len(d) // 3 + 1
    for i in range(3):
        t = d[i * n:(i + 1) * n]
        if t:
            A.guardar_parte(aid, "rid" + aid, usuario, i, io.BytesIO(t), len(t), offset, "audio/webm;codecs=opus")
    c = A._c()
    c.execute("UPDATE escucha_audio SET actualizado=?, creado=? WHERE aid=?", (int(time.time()) - (3600 if viejo else 0), creado or int(time.time()) - 7200, aid))
    c.commit()
    c.close()


def junta(lineas):
    return P.guardar_sesion("Junta de prueba", 660, [{"n": 1, "nombre": ""}, {"n": 2, "nombre": ""}], lineas, usuario="polo")["sesion_id"]


def L(t, voz, texto):
    return {"t": t, "voz": voz, "es": texto, "orig": "", "idioma": ""}


TEXTO_VIVO = [L("00:00", 1, "Buenos días a todos."), L("00:30", 2, "Empezamos con el contrato."), L("01:00", 1, "Y ahí se cayó la transcripción.")]      # cubre ~0 a 1:45


def falso(mp3, refs=None):
    return {"segments": [{"speaker": "A", "start": 5, "end": 20, "text": "Segunda parte de la junta."}, {"speaker": "B", "start": 22, "end": 40, "text": "Y cerramos con la comisión por transacción."}]}


def sin_llamadas(mp3, refs=None):
    raise AssertionError("no debía mandar nada a OpenAI")


def filas(sid):
    return [dict(r) for r in P._c().execute("SELECT hora, voz_n, texto FROM intervenciones WHERE sesion_id=? ORDER BY id", (sid,))]


class Huecos(unittest.TestCase):
    def test_los_huecos_de_texto(self):
        cob = [(0, 100), (500, 600)]
        self.assertEqual(C._huecos(cob, 0, 700), [(100, 500)])                                      # el hueco del medio; el final de 100 s es corto y no cuenta
        self.assertEqual(C._huecos(cob, 0, 1000), [(100, 500), (600, 1000)])
        self.assertEqual(C._huecos([], 0, 400), [(0, 400)])                                         # sin texto: todo el audio
        self.assertEqual(C._huecos(cob, 0, 120), [])

    def test_los_pedazos_con_voz_omiten_el_silencio_largo(self):
        serie = [-30.0] * 60 + [-90.0] * 480 + [-30.0] * 120                                        # 1 min voz, 8 min silencio, 2 min voz
        self.assertEqual(C._tramos_con_voz(serie, 0, 660, -90.0), [(0, 90.0), (510.0, 660)])
        self.assertEqual(C._tramos_con_voz([-30.0] * 30 + [-90.0] * 150 + [-30.0] * 60, 0, 240, -90.0), [(0, 240)])      # una pausa corta (2-3 min) dentro de una junta no se parte

    def test_la_cobertura_sale_de_las_horas_del_texto(self):
        limpiar()
        sid = junta(TEXTO_VIVO)
        self.assertEqual(C._cobertura(sid), [(0.0, 90.0), (60.0, 105.0)] and [(0.0, 105.0)])


class Reubicar(unittest.TestCase):
    def setUp(self):
        limpiar()
        ahora = int(time.time())
        subir("audmanana01", creado=ahora - 20000)          # la junta de la mañana
        subir("audcorto001", creado=ahora - 9000)
        subir("audtarde001", creado=ahora - 7000)           # el más reciente: el que llevó el texto en vivo
        for aid in ("audmanana01", "audcorto001", "audtarde001"):
            A.ligar_tramo(aid, "juntaX", "polo")

    def test_los_audios_que_se_encimaban_se_acomodan_despues_del_que_llevo_el_texto(self):
        antes = {t["aid"]: t["offset_ms"] for t in A.tramos("juntaX")}
        self.assertEqual(set(antes.values()), {0})
        plan = A.reubicar("juntaX", aplicar=False)
        self.assertEqual([m["aid"] for m in plan], ["audmanana01", "audcorto001"])                  # en orden de grabación
        self.assertEqual({t["aid"]: t["offset_ms"] for t in A.tramos("juntaX")}, antes)             # solo dijo qué haría
        dur = A.duracion_tramo("audtarde001")
        mov = A.reubicar("juntaX", aplicar=True)
        despues = {t["aid"]: t["offset_ms"] for t in A.tramos("juntaX")}
        self.assertEqual(despues["audtarde001"], 0)                                                 # el ancla no se mueve
        self.assertAlmostEqual(despues["audmanana01"] / 1000, dur + 60, delta=1)
        self.assertAlmostEqual(despues["audcorto001"] / 1000, dur + 60 + A.duracion_tramo("audmanana01") + 60, delta=2)
        self.assertEqual(A.reubicar("juntaX", aplicar=True), [])                                    # ya no se encima nada: no vuelve a moverlos

    def test_si_no_se_encima_nada_no_se_mueve_nada(self):
        limpiar()
        subir("audunico001")
        A.ligar_tramo("audunico001", "juntaY", "polo")
        subir("audparte2001", offset=700000)
        A.ligar_tramo("audparte2001", "juntaY", "polo")
        self.assertEqual(A.reubicar("juntaY"), [])


class Completar(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_solo_se_manda_lo_que_falta_y_tiene_voz_y_queda_al_final_de_la_misma_junta(self):
        subir("audsolo00001")
        sid = junta(TEXTO_VIVO)
        A.ligar_tramo("audsolo00001", sid, "polo")
        plan = C.planear(sid)
        self.assertEqual(plan["tramos"][0]["huecos"], [[105, 660]])                                 # lo que ya tiene texto no se vuelve a pedir
        antes = filas(sid)
        enviados = []
        def llamar(mp3, refs=None):
            enviados.append(A._duracion(mp3))
            return falso(mp3, refs)
        r = C.procesar(sid, "polo", llamar=llamar, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "lista", r)
        self.assertEqual(len(enviados), 1)
        self.assertLess(enviados[0], 200)                                                           # ~150 s de los 555 s que faltaban: el silencio de 8 min no se mandó
        self.assertGreater(r["min_sin_voz"], 5)
        ahora = filas(sid)
        self.assertEqual(ahora[:len(antes)], antes)                                                 # lo que ya había quedó idéntico
        nuevas = ahora[len(antes):]
        self.assertEqual(nuevas[0]["voz_n"], 0)                                                     # una marca clara de que ese texto viene del audio
        self.assertIn("Texto reconstruido desde el audio", nuevas[0]["texto"])
        self.assertEqual([n["hora"] for n in nuevas[1:]], ["08:35", "08:52"])                       # 510 s (inicio de la pieza) + 5 s y + 22 s
        self.assertTrue(all(n["voz_n"] > 2 for n in nuevas[1:]))                                    # voces nuevas: no se confunden con las 1 y 2 del texto en vivo
        self.assertEqual(R.estado(C._clave(sid))["estado"], "lista")
        n = P._c().execute("SELECT lineas FROM sesiones_escucha WHERE id=?", (sid,)).fetchone()[0]
        self.assertEqual(n, len(ahora))

    def test_lo_que_se_hablo_en_chino_queda_en_espanol_en_la_misma_junta(self):
        subir("audchino0002")
        sid = junta(TEXTO_VIVO)
        A.ligar_tramo("audchino0002", sid, "polo")
        chino = lambda mp3, refs=None: {"segments": [{"speaker": "A", "start": 5, "end": 25, "text": "合同的佣金按每笔交易收取，请大家确认这一条款。"}]}
        r = C.procesar(sid, "polo", llamar=chino, key="k", trabajo_base=TMP / "w", traducir=lambda ts: ["La comisión del contrato se cobra por cada transacción; confirmen esta cláusula."] * len(ts))
        self.assertEqual((r["estado"], r["traducidos"], r["sin_traducir"]), ("lista", 1, 0))
        f = [dict(x) for x in P._c().execute("SELECT texto, original, idioma FROM intervenciones WHERE sesion_id=? ORDER BY id", (sid,))]
        nueva = [x for x in f if x["idioma"] == "zh"]
        self.assertEqual(len(nueva), 1)
        self.assertIn("por cada transacción", nueva[0]["texto"])
        self.assertIn("佣金", nueva[0]["original"])

    def test_es_idempotente_si_ya_no_falta_no_manda_nada(self):
        subir("audidem00001")
        sid = junta(TEXTO_VIVO)
        A.ligar_tramo("audidem00001", sid, "polo")
        C.procesar(sid, "polo", llamar=falso, key="k", trabajo_base=TMP / "w")
        self.assertEqual(C.planear(sid)["falta_s"], 0)                                              # el texto nuevo ya cubre lo que tenía voz (y lo que quedó es silencio corto)
        r = C.procesar(sid, "polo", llamar=sin_llamadas, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "lista")

    def test_varios_audios_ligados_a_mano_quedan_en_la_misma_junta_uno_tras_otro(self):
        ahora = int(time.time())
        subir("audmanana02", creado=ahora - 20000)
        subir("audtarde002", creado=ahora - 7000)
        sid = junta(TEXTO_VIVO)                                                                     # el texto en vivo es del audio de la tarde (el más reciente)
        for aid in ("audmanana02", "audtarde002"):
            A.ligar_tramo(aid, sid, "polo")
        plan = C.planear(sid)
        self.assertEqual(len(plan["reubicar"]), 1)
        self.assertGreater(plan["falta_s"], 700)                                                    # la mañana completa + el final de la tarde
        r = C.procesar(sid, "polo", llamar=falso, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "lista", r)
        tr = {t["aid"]: t["offset_ms"] for t in A.tramos(sid)}
        self.assertEqual(tr["audtarde002"], 0)
        self.assertGreater(tr["audmanana02"], 600000)                                               # acomodado después, sin encimarse
        horas = [f["hora"] for f in filas(sid)]
        self.assertTrue(any(h >= "11:00" for h in horas))                                           # el texto de la mañana cae en su lugar del reloj, después del de la tarde
        self.assertEqual(sum(1 for f in filas(sid) if f["voz_n"] == 0), 2)                          # una marca por cada audio reconstruido

    def test_un_error_no_deja_la_junta_a_medias_ni_gasta_de_mas(self):
        subir("audmal000001")
        sid = junta(TEXTO_VIVO)
        A.ligar_tramo("audmal000001", sid, "polo")
        def mala(mp3, refs=None): raise RuntimeError("OpenAI respondió 500: boom")
        r = C.procesar(sid, "polo", llamar=mala, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "error")
        self.assertIn("500", R.estado(C._clave(sid))["error"])
        self.assertEqual(len(filas(sid)), 3)                                                        # lo que había queda igual

    def test_no_toca_una_junta_con_audio_grabandose_ni_ajena(self):
        subir("audvivo00001", viejo=False)
        sid = junta(TEXTO_VIVO)
        c = A._c(); c.execute("UPDATE escucha_audio SET sesion_id=?, estado='grabando' WHERE aid=?", (sid, "audvivo00001")); c.commit(); c.close()
        self.assertTrue(C.planear(sid)["activo"])
        self.assertIn("se está grabando", C.procesar(sid, "polo", llamar=sin_llamadas, key="k")["error"])
        self.assertIn("no encontrada", C.procesar(sid, "otro", llamar=sin_llamadas, key="k")["error"])

    def test_sin_openai_lo_dice(self):
        subir("audsinkey001")
        sid = junta(TEXTO_VIVO)
        A.ligar_tramo("audsinkey001", sid, "polo")
        import app_media
        viejo = app_media.openai_key
        app_media.openai_key = lambda: ""
        try:
            self.assertIn("OPENAI_API_KEY", C.procesar(sid, "polo")["error"])
        finally:
            app_media.openai_key = viejo


class PorHttp(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.orig = C.procesar
        C.procesar = lambda sid, usuario="admin": self.orig(sid, usuario, llamar=falso, key="k", trabajo_base=TMP / "w")

    def tearDown(self):
        C.procesar = self.orig
        self.srv.shutdown()

    def req(self, path, body=None):
        r = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, {"Authorization": "Bearer master-x", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())

    def _junta_admin(self, aid):
        subir(aid, usuario="admin")
        sid = P.guardar_sesion("Junta", 660, [{"n": 1, "nombre": ""}], TEXTO_VIVO, usuario="admin")["sesion_id"]
        A.ligar_tramo(aid, sid, "admin")
        return sid

    def test_el_plan_el_boton_y_el_resultado(self):
        sid = self._junta_admin("audhttp00001")
        plan = self.req("/v1/app/escucha/completar?sesion=" + sid)
        self.assertEqual((plan["falta_s"], plan["activo"], plan["estado"]["estado"]), (555, False, "nada"))
        self.assertTrue(self.req("/v1/app/escucha/completar", {"sesion_id": sid})["ok"])
        for _ in range(150):
            if R.estado(C._clave(sid))["estado"] == "lista":
                break
            time.sleep(0.2)
        self.assertEqual(R.estado(C._clave(sid))["estado"], "lista")
        self.assertEqual(self.req("/v1/app/escucha/completar?sesion=" + sid)["estado"]["estado"], "lista")
        j = self.req("/v1/app/escucha/junta?id=" + sid)
        self.assertTrue(any("comisión por transacción" in l["texto"] for l in j["lineas"]))        # el texto nuevo aparece en la junta
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.req("/v1/app/escucha/completar", {"sesion_id": sid})                              # ya no falta nada: no se vuelve a gastar
        self.assertEqual(e.exception.code, 400)

    def test_junta_ajena_o_inexistente(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.req("/v1/app/escucha/completar?sesion=nadie")
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
