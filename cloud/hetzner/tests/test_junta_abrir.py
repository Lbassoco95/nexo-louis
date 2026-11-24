"""Abrir una junta guardada (ver, oír, descargar), eliminar copias duplicadas y que un autoguardado tardío no resucite el borrador."""
import io, json, os, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-x", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"), MEDIA_MIN_FREE_GB="0")
import donna_db as D, donna_audio as A, donna_personas as P, donna_minuta as M, donna_junta as J, donna_remoto as RM
import app_media
import openclaw_gateway as g


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    import shutil
    shutil.rmtree(A.DIR, ignore_errors=True)


def tono(seg, hz) -> bytes:
    p = TMP / ("t%d.webm" % hz)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=%d:duration=%d" % (hz, seg), "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
    return p.read_bytes()


def crear_junta(usuario="polo"):
    voces = [{"n": 1, "nombre": "Gonzalo"}, {"n": 2, "nombre": ""}]
    lineas = [{"t": "00:01", "voz": 1, "es": "Hola equipo", "orig": "Hello team", "idioma": "en"}, {"t": "00:12", "voz": 2, "es": "Mando el contrato", "idioma": "es"}]
    sid = P.guardar_sesion("Transcripción Escuchar 2026-11-23 18:34", 180 * 60, voces, lineas, resumen="Se habló del contrato.", usuario=usuario)["sesion_id"]
    c = J._c()
    c.execute("INSERT INTO junta_notas(live_id,sesion_id,ts,hora,texto,origen) VALUES('j1',?,1,'00:05','Revisar cláusula 3','polo')", (sid,))
    c.execute("INSERT INTO junta_materiales(live_id,sesion_id,ts,hora,mid,nombre,tipo) VALUES('j1',?,1,'00:06','m1','Cotización.pdf','documento')", (sid,))
    c.commit(); c.close()
    return sid


class Datos(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.sid = crear_junta()

    def test_datos_y_minuta(self):
        d = M.datos(self.sid, "polo")
        self.assertEqual((d["titulo"][-5:], len(d["lineas"]), d["duracion"]), ("18:34", 2, 10800))
        self.assertEqual(d["lineas"][0]["quien"], "Gonzalo")
        self.assertEqual(d["notas"][0]["texto"], "Revisar cláusula 3")
        nombre, md = M.minuta_md(self.sid, "polo")
        self.assertTrue(nombre.endswith(".md"))
        for frag in ("# Transcripción Escuchar", "3 h 00 min", "Se habló del contrato.", "Revisar cláusula 3", "Cotización.pdf", "**[00:01] Gonzalo:** Hola equipo", "<sub>Hello team</sub>", "Voz 2"):
            self.assertIn(frag, md)
        with self.assertRaises(ValueError):
            M.datos(self.sid, "otro")

    def test_eliminar_borra_todo_lo_que_colgaba(self):
        r = P.eliminar_sesion(self.sid, "polo")
        self.assertEqual(r["intervenciones"], 2)
        with self.assertRaises(ValueError):
            M.datos(self.sid, "polo")
        c = J._c()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM junta_notas WHERE sesion_id=?", (self.sid,)).fetchone()[0], 0)
        c.close()
        with self.assertRaises(ValueError):
            P.eliminar_sesion(self.sid, "polo")
        sid2 = crear_junta()
        with self.assertRaises(ValueError):
            P.eliminar_sesion(sid2, "otro")

    def test_exportar_junta_varios_tramos_en_un_solo_mp3(self):
        for aid, hz, off in (("tramo1abc", 220, 0), ("tramo2abc", 440, 10000)):
            d = tono(8, hz)
            A.guardar_parte(aid, "rid123abc", "polo", 0, io.BytesIO(d), len(d), off, "audio/webm")
        A.ligar("rid123abc", self.sid)
        mp3 = A.exportar(self.sid)
        self.assertGreater(A._duracion(mp3), 14)                       # 8 s + 8 s
        self.assertEqual(A.exportar(self.sid), mp3)                    # segunda vez: de la caché
        A.borrar(self.sid, "polo")
        self.assertFalse(mp3.exists())
        with self.assertRaises(ValueError):
            A.exportar(self.sid)


class Duplicadas(unittest.TestCase):
    def setUp(self):
        limpiar()

    def _copias(self, n, usuario="polo"):
        ids = []
        for _ in range(n):
            ids.append(crear_junta(usuario))
            time.sleep(1.1)                                   # distinta marca de tiempo
        return ids

    def test_conserva_la_que_tiene_audio_y_nunca_borra_una_con_audio(self):
        a, b, c, d = self._copias(4)
        x = tono(5, 250)
        A.guardar_parte("audcopia02", "ridcopia02", "polo", 0, io.BytesIO(x), len(x), 0, "audio/webm")
        A.ligar("ridcopia02", b)                              # el audio quedó en la segunda copia (no en la más reciente)
        r = M.duplicadas("polo")
        self.assertEqual(r["sobran"], 3)
        self.assertEqual(r["grupos"][0]["conservar"], b)
        self.assertEqual(set(r["grupos"][0]["borrar"]), {a, c, d})
        self.assertEqual(M.limpiar_duplicadas("polo"), 3)
        self.assertEqual([s["id"] for s in P.sesiones("polo")], [b])
        self.assertEqual(A.tramos(b)[0]["aid"], "audcopia02")             # el audio sigue ahí
        self.assertEqual(M.duplicadas("polo")["sobran"], 0)

    def test_sin_audio_conserva_la_mas_reciente_y_otra_junta_distinta_no_se_toca(self):
        a, b = self._copias(2)
        otra = P.guardar_sesion("Otra junta", 600, [], [{"t": "00:01", "voz": 0, "es": "x", "idioma": "es"}], usuario="polo")["sesion_id"]
        r = M.duplicadas("polo")
        self.assertEqual((r["grupos"][0]["conservar"], r["grupos"][0]["borrar"]), (b, [a]))
        M.limpiar_duplicadas("polo")
        self.assertEqual({s["id"] for s in P.sesiones("polo")}, {b, otra})

    def test_solo_las_del_usuario(self):
        self._copias(2, "otro")
        self.assertEqual(M.duplicadas("polo")["sobran"], 0)
        self.assertEqual(M.duplicadas("otro")["sobran"], 1)


class PorElServidor(unittest.TestCase):
    def setUp(self):
        limpiar()
        with RM._LOCK:
            RM._EN_VIVO.clear()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.H = {"Authorization": "Bearer master-x"}

    def tearDown(self):
        self.srv.shutdown()

    def req(self, path, body=None):
        r = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, {**self.H, "Content-Type": "application/json"})
        return urllib.request.urlopen(r)

    def test_abrir_descargar_y_eliminar_por_http(self):
        sid = crear_junta("admin")
        d = tono(6, 300)
        A.guardar_parte("tramoabc12", "ridhttp123", "admin", 0, io.BytesIO(d), len(d), 0, "audio/webm")
        A.ligar("ridhttp123", sid)
        its = json.loads(self.req("/v1/app/escucha/sesiones").read())["items"]
        self.assertEqual(its[0]["audio"]["tramos"], 1)                                            # la lista dice cuál tiene audio
        j = json.loads(self.req("/v1/app/escucha/junta?id=" + sid).read())
        self.assertEqual((j["resumen"], len(j["lineas"])), ("Se habló del contrato.", 2))
        r = self.req("/v1/app/escucha/descargar?sesion=%s&que=minuta" % sid)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertIn("Hola equipo", r.read().decode())
        r = self.req("/v1/app/escucha/descargar?sesion=%s&que=audio" % sid)
        self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
        mp3 = r.read()
        self.assertGreater(len(mp3), 1000)
        with self.assertRaises(urllib.error.HTTPError) as cm:                                      # junta ajena o inexistente
            self.req("/v1/app/escucha/junta?id=nadie")
        self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as cm:                                      # con audio no se borra sin confirmarlo
            self.req("/v1/app/escucha/eliminar", {"sesion_id": sid})
        self.assertIn("audio", cm.exception.read().decode())
        self.assertTrue(json.loads(self.req("/v1/app/escucha/eliminar", {"sesion_id": sid, "borrar_audio": True}).read())["audio_borrado"])
        self.assertEqual(json.loads(self.req("/v1/app/escucha/sesiones").read())["items"], [])
        self.assertEqual(A.tramos(sid), [])

    def test_un_autoguardado_tardio_no_resucita_el_borrador_de_una_junta_guardada(self):
        app_media.openai_key = lambda: "k"
        t = [{"o": "es", "a": "hola", "b": "", "t": 0}]
        self.req("/v1/app/escucha/borrador", {"id": "fantasma123", "turnos": t})
        lineas = [{"t": "00:01", "voz": 0, "es": "Hola equipo", "idioma": "es"}]
        self.req("/v1/app/escucha/guardar", {"borrador_id": "fantasma123", "duracion": 5, "lineas": lineas, "voces": []})
        self.assertEqual(json.loads(self.req("/v1/app/escucha/borradores").read())["items"], [])
        r = json.loads(self.req("/v1/app/escucha/borrador", {"id": "fantasma123", "turnos": t}).read())      # el autoguardado que llega tarde
        self.assertTrue(r.get("guardada"))
        self.assertEqual(json.loads(self.req("/v1/app/escucha/borradores").read())["items"], [])
        with self.assertRaises(urllib.error.HTTPError):                                                    # y guardar otra vez la misma junta ya no duplica
            self.req("/v1/app/escucha/guardar", {"borrador_id": "fantasma123", "duracion": 5, "lineas": lineas, "voces": []})
        self.assertEqual(len(json.loads(self.req("/v1/app/escucha/sesiones").read())["items"]), 1)


class AudioPorHttp(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.H = {"Authorization": "Bearer master-x"}

    def tearDown(self):
        self.srv.shutdown()

    def req(self, path, body=None):
        r = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, {**self.H, "Content-Type": "application/json"})
        return urllib.request.urlopen(r)

    def test_estado_del_audio_y_recuperar_audio_suelto(self):
        sid = crear_junta("admin")
        d = tono(10, 260)
        A.guardar_parte("tramohttp01", "ridhttp0001", "admin", 0, io.BytesIO(d), len(d), 0, "audio/webm")      # suelto: la pestaña se cerró antes de guardar
        hu = json.loads(self.req("/v1/app/escucha/audio_huerfano").read())["items"]
        self.assertEqual([h["aid"] for h in hu], ["tramohttp01"])
        r = self.req("/v1/app/escucha/descargar?que=tramo&aid=tramohttp01")
        self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
        self.assertGreater(len(r.read()), 1000)
        self.assertTrue(json.loads(self.req("/v1/app/escucha/audio", {"accion": "ligar", "aid": "tramohttp01", "sesion_id": sid}).read())["ok"])
        self.assertEqual(json.loads(self.req("/v1/app/escucha/audio_huerfano").read())["items"], [])
        v = json.loads(self.req("/v1/app/escucha/audio_estado?sesion=" + sid).read())
        self.assertEqual((v["ok"], len(v["tramos"])), (True, 1))
        self.assertTrue(v["huecos"])                                                          # la junta dura 3 h y el audio ~10 s: avisa lo que falta
        self.assertIn("cubre", " ".join(v["avisos"]))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("/v1/app/escucha/audio", {"accion": "ligar", "aid": "tramohttp01", "sesion_id": "inexistente"})
        self.assertEqual(cm.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("/v1/app/escucha/descargar?que=tramo&aid=noexiste123")
        self.assertEqual(cm.exception.code, 404)

    def test_reintento_automatico_de_las_revisiones_que_fallaron_por_el_limite(self):
        import donna_diariza as DZ
        sid = crear_junta("admin")
        d = tono(6, 330)
        A.guardar_parte("tramoreint1", "ridreint001", "admin", 0, io.BytesIO(d), len(d), 0, "audio/webm")
        A.ligar("ridreint001", sid)
        DZ._guardar(sid, "error", {"error": "OpenAI respondió 400: audio duration 3567 seconds is longer than 1400 seconds which is the maximum for this model"})
        lanzadas = []
        g._lanzar_voces = lambda s: lanzadas.append(s) or True
        g._reintentar_voces_fallidas()
        self.assertEqual(lanzadas, [sid])
        DZ._guardar(sid, "error", {"error": "OpenAI respondió 500: otra cosa"})              # otros errores NO se reintentan solos
        lanzadas.clear()
        g._reintentar_voces_fallidas()
        self.assertEqual(lanzadas, [])
        DZ._guardar(sid, "procesando", {"progreso": "pieza 3 de 9"})                          # quedó «procesando» porque el servidor se reinició: huérfana → se relanza
        g._reintentar_voces_fallidas()
        self.assertEqual(lanzadas, [sid])
        self.assertEqual(DZ.estado(sid)["pid"], os.getpid())                                  # se anota qué proceso la está haciendo


if __name__ == "__main__":
    unittest.main()
