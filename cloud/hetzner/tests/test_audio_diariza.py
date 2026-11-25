"""Audio de las juntas (guardado por partes, plazo, borrado) y pasada de voces: alineación con la transcripción en vivo y propuesta aprobable."""
import io, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x", MEDIA_MIN_FREE_GB="0")
import donna_db as D, donna_audio as A, donna_diariza as DZ, donna_personas as P, donna_dia


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    import shutil
    shutil.rmtree(A.DIR, ignore_errors=True)


def audio_de_prueba(seg=25) -> bytes:
    p = TMP / "tono.webm"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=%d" % seg, "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
    return p.read_bytes()


def subir(aid, rid, datos, usuario="polo", offset=0, trozos=3):
    n = len(datos) // trozos + 1
    for i in range(trozos):
        trozo = datos[i * n:(i + 1) * n]
        if trozo:
            A.guardar_parte(aid, rid, usuario, i, io.BytesIO(trozo), len(trozo), offset, "audio/webm;codecs=opus")


class Almacen(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.datos = audio_de_prueba()

    def test_partes_se_ensamblan_en_un_audio_completo(self):
        subir("aud123abc", "rid123abc", self.datos)
        e = A.estado("aud123abc")
        self.assertEqual(e["partes"], 3)
        dur = A._duracion(A.ensamblar("aud123abc"))
        self.assertGreater(dur, 23)
        self.assertLess(dur, 27)

    def test_reenviar_una_parte_no_duplica_y_es_privado(self):
        subir("aud123abc", "rid123abc", self.datos)
        A.guardar_parte("aud123abc", "rid123abc", "polo", 0, io.BytesIO(b"x" * 100), 100)
        self.assertEqual(A.estado("aud123abc")["partes"], 3)
        with self.assertRaises(ValueError):
            A.guardar_parte("aud123abc", "rid123abc", "otro", 5, io.BytesIO(b"x"), 1)
        for aid, idx, ln in (("../x", 0, 1), ("aud123abc", -1, 1), ("aud123abc", 0, 0), ("aud123abc", 0, A.PART_MAX + 1)):
            with self.assertRaises(ValueError):
                A.guardar_parte(aid, "rid123abc", "polo", idx, io.BytesIO(b"x"), ln)

    def test_plazo_borrado_y_purga(self):
        subir("aud111abc", "rid111abc", self.datos)
        subir("aud222abc", "rid222abc", self.datos)                            # huérfana: la pestaña se cerró y nunca se guardó la junta
        self.assertEqual(A.ligar("rid111abc", "sesion1", dias=30), 1)
        self.assertEqual(A.purgar(), 0)                                        # nada vence todavía
        self.assertEqual(A.purgar(int(time.time()) + 10 * 86400), 0)           # a los 10 días tampoco
        self.assertEqual(A.purgar(int(time.time()) + 31 * 86400), 2)           # a los 31: vence la ligada (30 d) y cae la huérfana (30 d sin tocar)
        self.assertEqual(A.estado("aud111abc")["estado"], "borrado")
        self.assertFalse((A.DIR / "aud111abc").exists())
        self.assertEqual(A.borrar("sesion1", "otro"), 0)
        subir("aud333abc", "rid333abc", self.datos)
        A.ligar("rid333abc", "sesion3")
        self.assertEqual(A.borrar("sesion3", "polo"), 1)
        self.assertEqual(A.tramos("sesion3"), [])

    def test_relinkear_una_junta_reemplazada(self):
        subir("aud111abc", "rid111abc", self.datos)
        A.ligar("rid111abc", "vieja")
        self.assertEqual(A.relinkear("vieja", "nueva"), 1)
        self.assertEqual(len(A.tramos("nueva")), 1)

    def test_los_dias_salen_de_las_preferencias(self):
        donna_dia.prefs_set({"escucha": {"dias": 7}})
        self.assertEqual(A.dias_por_defecto(), 7)
        with self.assertRaises(ValueError):
            donna_dia.prefs_set({"escucha": {"dias": 0}})


class Alineacion(unittest.TestCase):
    T = lambda s, i, ini, fin, voz, n="": {"id": i, "hora": "", "texto": "t%d" % i, "voz_n": voz, "nombre": n, "ini_ms": ini, "fin_ms": fin}
    S = lambda s, lab, a, b: {"spk": lab, "ini_ms": int(a * 1000), "fin_ms": int(b * 1000)}

    def test_corrige_quien_hablo_y_crea_voz_nueva(self):
        turnos = [self.T(1, 1000, 12000, 1, "Gonzalo"), self.T(2, 12000, 22000, 1, "Gonzalo"), self.T(3, 22000, 30000, 2, "")]
        segs = [self.S("X", 0, 10), self.S("Y", 11, 20), self.S("X", 21, 28)]
        r = DZ.proponer(turnos, segs)
        cam = {c["id"]: c for c in r["cambios"]}
        self.assertEqual(set(cam), {2, 3})
        self.assertEqual((cam[2]["a"], cam[2]["nueva"]), (3, True))                   # otra persona: voz nueva
        self.assertEqual((cam[3]["de"], cam[3]["a"], cam[3]["nombre_a"]), (2, 1, "Gonzalo"))
        self.assertEqual(r["voces_nuevas"], [3])

    def test_un_turno_con_dos_voces_no_se_cambia_solo(self):
        turnos = [self.T(1, 1000, 11000, 1), self.T(2, 11000, 21000, 1)]
        segs = [self.S("X", 0, 9), self.S("X", 10, 15), self.S("Y", 15, 19.5)]
        r = DZ.proponer(turnos, segs)
        self.assertEqual([m["id"] for m in r["mixtos"]], [2])
        self.assertEqual([c["id"] for c in r["cambios"]], [])

    def test_un_turno_sin_identificar_se_identifica(self):
        turnos = [self.T(1, 1000, 11000, 1, "Ana"), self.T(2, 11000, 21000, 0)]
        segs = [self.S("A", 0, 9.5), self.S("B", 10, 20), self.S("A", 20, 22)]
        r = DZ.proponer(turnos, segs)
        c = [x for x in r["cambios"] if x["id"] == 2]
        self.assertTrue(c and c[0]["de"] == 0 and c[0]["a"] != 0)

    def test_si_ya_estaba_bien_no_propone_nada(self):
        turnos = [self.T(1, 1000, 11000, 1), self.T(2, 11000, 21000, 2)]
        segs = [self.S("A", 0, 10), self.S("B", 10.5, 20.5)]
        self.assertEqual(DZ.proponer(turnos, segs)["cambios"], [])

    def test_lee_segmentos_aunque_cambien_los_nombres_de_campo(self):
        r = DZ.leer_segmentos({"segments": [{"speaker": "A", "start": 0, "end": 2.5, "text": " hola "}, {"speaker_label": "B", "start_time": 3, "end_time": 4}, {"speaker": "C", "start": 5, "end": 5}, "basura"]})
        self.assertEqual([(s["spk"], s["fin"]) for s in r], [("A", 2.5), ("B", 4.0)])


class Pasada(unittest.TestCase):
    def setUp(self):
        limpiar()
        voces = [{"n": 1, "nombre": "Gonzalo"}, {"n": 2, "nombre": ""}]
        lineas = [{"t": "00:01", "voz": 1, "es": "Hola equipo", "idioma": "es"}, {"t": "00:12", "voz": 1, "es": "Mando el contrato", "idioma": "es"}, {"t": "00:22", "voz": 2, "es": "Perfecto", "idioma": "es"}]
        self.sid = P.guardar_sesion("Junta", 30, voces, lineas, usuario="polo")["sesion_id"]
        subir("aud123abc", "rid123abc", audio_de_prueba(30))
        A.ligar("rid123abc", self.sid)

    def falso(self, mp3, refs=None):
        return {"segments": [{"speaker": "A", "start": 0, "end": 10, "text": "hola"}, {"speaker": "B", "start": 11, "end": 20, "text": "contrato"}, {"speaker": "A", "start": 21, "end": 28, "text": "perfecto"}]}

    def test_flujo_completo_propone_y_aplica(self):
        r = DZ.procesar(self.sid, llamar=self.falso, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "lista", r)
        self.assertEqual({c["id"] for c in r["cambios"]}, {c["id"] for c in DZ.estado(self.sid)["cambios"]})
        self.assertEqual(len(r["cambios"]), 2)
        ap = DZ.aplicar(self.sid, "todas", "polo")
        self.assertEqual(ap["aplicados"], 2)
        self.assertEqual(DZ.aplicar(self.sid, "todas", "polo")["aplicados"], 0)                      # no se aplica dos veces
        filas = {f["hora"]: f["voz_n"] for f in P._c().execute("SELECT hora, voz_n FROM intervenciones WHERE sesion_id=?", (self.sid,))}
        self.assertEqual(filas["00:22"], 1)                                                           # «Perfecto» ya es de Gonzalo
        self.assertEqual(filas["00:12"], 3)                                                           # el que contestó es otra persona

    def test_sin_audio_y_con_error(self):
        sid2 = P.guardar_sesion("Otra", 10, [], [{"t": "00:01", "voz": 0, "es": "algo", "idioma": "es"}], usuario="polo")["sesion_id"]
        self.assertEqual(DZ.procesar(sid2, llamar=self.falso, key="k")["estado"], "sin_audio")
        def mala(mp3, refs=None): raise RuntimeError("OpenAI respondió 400: bad")
        r = DZ.procesar(self.sid, llamar=mala, key="k", trabajo_base=TMP / "w")
        self.assertEqual(r["estado"], "error")
        self.assertIn("400", DZ.estado(self.sid)["error"])
        with self.assertRaises(ValueError):
            DZ.aplicar(self.sid, "todas", "polo")                                                     # sin propuesta lista no se aplica nada

    def test_otro_usuario_no_aplica(self):
        DZ.procesar(self.sid, llamar=self.falso, key="k", trabajo_base=TMP / "w")
        with self.assertRaises(ValueError):
            DZ.aplicar(self.sid, "todas", "otro")

    def voces(self):
        return {f["hora"]: (f["voz_n"], f["persona_id"]) for f in P._c().execute("SELECT hora, voz_n, persona_id FROM intervenciones WHERE sesion_id=?", (self.sid,))}

    def test_elegir_otra_voz_y_deshacer(self):
        r = DZ.procesar(self.sid, llamar=self.falso, key="k", trabajo_base=TMP / "w")
        antes = self.voces()
        ids = {c["hora"]: c["id"] for c in r["cambios"]}
        self.assertEqual({v["n"] for v in DZ.estado(self.sid)["voces"]}, {1, 2})                      # la pantalla ofrece las voces de la junta
        DZ.aplicar(self.sid, [ids["00:12"]], "polo", {ids["00:12"]: 2})                               # Polo escuchó y eligió la Voz 2, no la propuesta
        self.assertEqual(self.voces()["00:12"][0], 2)
        self.assertEqual(self.voces()["00:22"], antes["00:22"])                                       # lo no marcado no se toca
        DZ.aplicar(self.sid, "todas", "polo")                                                         # el resto se aplica como se propuso
        self.assertEqual(self.voces()["00:22"][0], 1)
        self.assertEqual(DZ.deshacer(self.sid, "polo"), {"deshechos": 2, "sin_respaldo": 0})
        self.assertEqual(self.voces(), antes)                                                         # todo regresó, voz y persona
        self.assertEqual(DZ.aplicar(self.sid, "todas", "polo")["aplicados"], 2)                       # y se puede volver a aplicar
        with self.assertRaises(ValueError):
            DZ.deshacer(self.sid, "otro")

    def test_decisiones_se_guardan_y_al_aplicar_se_verifica_en_la_base(self):
        r = DZ.procesar(self.sid, llamar=self.falso, key="k", trabajo_base=TMP / "w")
        ids = {c["hora"]: c["id"] for c in r["cambios"]}
        out = DZ.decidir(self.sid, {str(ids["00:12"]): {"m": True, "v": 2}, "9999": {"m": True, "v": 1}}, "polo")
        self.assertEqual(out["guardadas"], 1)                                                         # un id que no es de esta propuesta se ignora
        self.assertEqual(DZ.estado(self.sid)["decisiones"][str(ids["00:12"])], {"m": True, "v": 2})   # lo marcado vuelve al reabrir la pantalla
        with self.assertRaises(ValueError):
            DZ.decidir(self.sid, {}, "otro")
        ap = DZ.aplicar(self.sid, [ids["00:12"]], "polo", {ids["00:12"]: 2})
        self.assertEqual((ap["aplicados"], ap["verificados"], ap["sin_cambio"]), (1, 1, 0))           # releyó la base: quedó en la voz elegida
        ap2 = DZ.aplicar(self.sid, [ids["00:22"]], "polo", {ids["00:22"]: 2})                         # eligió «como está» (ya era la Voz 2)
        self.assertEqual((ap2["aplicados"], ap2["sin_cambio"], ap2["verificados"]), (1, 1, 1))
        self.assertEqual(self.voces()["00:22"][0], 2)

    def test_fragmento_muy_bajo_se_sube_de_volumen(self):
        import re
        p = TMP / "bajo.webm"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=30", "-af", "volume=0.004", "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
        subir("audbajo01", "ridbajo01", p.read_bytes(), offset=200000)
        A.ligar("ridbajo01", self.sid)
        mp3 = A.clip(self.sid, "03:30")
        r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(mp3), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
        self.assertGreater(float(re.search(r"mean_volume:\s*(-?[0-9.]+)", r.stderr).group(1)), -30)   # la fuente está a ~-52 dB: el fragmento se oye

    def test_fragmento_del_momento_usa_el_reloj_de_cada_tramo(self):
        subir("aud999abc", "rid999abc", audio_de_prueba(30), offset=100000)                           # segundo tramo: empieza en 01:40 del reloj de la junta
        A.ligar("rid999abc", self.sid)
        mp3 = A.clip(self.sid, "00:12")
        self.assertTrue(mp3.exists())
        self.assertGreater(A._duracion(mp3), 10)
        self.assertLess(A._duracion(mp3), 18)
        self.assertTrue(A.clip(self.sid, "01:45").exists())                                           # cae en el segundo tramo
        with self.assertRaises(ValueError):
            A.clip(self.sid, "01:00")                                                                 # hueco: no hay audio de ese momento
        self.assertTrue(A.clip(self.sid, "01:35").exists())                                           # 5 s antes de que arranque el audio: se toca desde el principio
        with self.assertRaises(ValueError):
            A.clip(self.sid, "01:10")                                                                 # 30 s antes: eso sí es otro momento
        A.borrar(self.sid, "polo")
        self.assertEqual(list((A.DIR / "export").glob("c-*")), [])                                    # al borrar el audio se van también los fragmentos

    def test_frase_corta_se_marca(self):
        sid2 = P.guardar_sesion("Corta", 30, [{"n": 1, "nombre": ""}], [{"t": "00:01", "voz": 1, "es": "Sí", "idioma": "es"}, {"t": "00:03", "voz": 1, "es": "Una frase larga que dura bastante", "idioma": "es"}, {"t": "00:12", "voz": 1, "es": "Fin", "idioma": "es"}], usuario="polo")["sesion_id"]
        ids = {f["hora"]: f["id"] for f in P._c().execute("SELECT id, hora FROM intervenciones WHERE sesion_id=?", (sid2,))}
        DZ._guardar(sid2, "lista", {"cambios": [{"id": ids["00:01"], "hora": "00:01", "texto": "Sí", "de": 1, "a": 2, "conf": 1.0}, {"id": ids["00:03"], "hora": "00:03", "texto": "x", "de": 1, "a": 2, "conf": 1.0}]})
        cam = {c["hora"]: c for c in DZ.estado(sid2)["cambios"]}
        self.assertTrue(cam["00:01"]["corto"])                                                        # 2 s hasta el siguiente turno: el 100 % no es de fiar
        self.assertFalse(cam["00:03"]["corto"])                                                       # 9 s


class Limites(unittest.TestCase):
    """OpenAI acepta ~1400 s por llamada: el audio se parte en piezas cortas SIEMPRE; tope de 4 h; las etiquetas de cada tramo/pieza se emparejan por separado."""
    def setUp(self):
        limpiar()
        self.antes = (DZ.PIEZA_S, DZ.MAX_S)
        self.addCleanup(lambda: (setattr(DZ, "PIEZA_S", self.antes[0]), setattr(DZ, "MAX_S", self.antes[1])))
        p = TMP / "largo.webm"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=30", "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
        self.audio = p

    def test_siempre_en_piezas_cortas_aunque_pese_poco(self):
        DZ.PIEZA_S = 10
        piezas = DZ.preparar(self.audio, TMP / "w1")
        self.assertEqual(len(piezas), 3)
        for ini, mp3 in piezas:
            self.assertLessEqual(A._duracion(mp3), 10.6)
        self.assertEqual([round(i) for i, _ in piezas], [0, 10, 20])

    def test_tope_de_tiempo(self):
        DZ.PIEZA_S = 10
        self.assertEqual(len(DZ.preparar(self.audio, TMP / "w2", max_s=20)), 2)

    def test_el_valor_por_defecto_respeta_el_limite_de_openai(self):
        self.assertLessEqual(DZ.PIEZA_S, 1300)
        self.assertEqual(DZ.MAX_S, 4 * 3600)

    def test_procesar_respeta_el_tope_y_avisa_lo_omitido(self):
        voces = [{"n": 1, "nombre": "Gonzalo"}]
        sid = P.guardar_sesion("J", 30, voces, [{"t": "00:01", "voz": 1, "es": "hola", "idioma": "es"}], usuario="polo")["sesion_id"]
        d = self.audio.read_bytes()
        A.guardar_parte("audlargo01", "ridlargo01", "polo", 0, io.BytesIO(d), len(d), 0, "audio/webm")
        A.ligar("ridlargo01", sid)
        DZ.PIEZA_S, DZ.MAX_S = 10, 20
        llamadas, progresos = [], []
        def falso(mp3, refs=None):
            llamadas.append(refs is not None)
            return {"segments": [{"speaker": "A", "start": 0, "end": 6, "text": "x"}]}
        orig = DZ._guardar
        DZ._guardar = lambda s_id, est, datos: (progresos.append(datos.get("progreso")) if est == "procesando" else None, orig(s_id, est, datos))[1]
        try:
            r = DZ.procesar(sid, llamar=falso, key="k", trabajo_base=TMP / "w3")
        finally:
            DZ._guardar = orig
        self.assertEqual(r["estado"], "lista", r)
        self.assertEqual(len(llamadas), 2)                                  # 30 s con tope de 20 s y piezas de 10 s → 2 llamadas
        self.assertEqual(llamadas[0], False)                                # la primera sin muestras; la segunda con ellas (o sin, si la API no las acepta)
        self.assertIn("pieza 2 de 2", progresos)
        self.assertAlmostEqual(r["minutos"], 20 / 60, places=1)

    def test_si_la_api_rechaza_las_muestras_de_voz_se_sigue_sin_ellas(self):
        DZ.PIEZA_S = 10
        llamadas = []
        def rara(mp3, refs=None):
            llamadas.append(bool(refs))
            if refs:
                raise RuntimeError("OpenAI respondió 400: invalid known_speaker_references")
            return {"segments": [{"speaker": "A", "start": 0, "end": 9, "text": "hola"}]}
        segs, info = DZ.diarizar_archivo("k", self.audio, TMP / "w4", llamar=rara)
        self.assertEqual(info["etiquetas_alineadas"], False)
        self.assertTrue(any(llamadas) and llamadas.count(False) >= 3)       # reintentó sin muestras y terminó las 3 piezas
        self.assertEqual(len(segs), 3)

    def test_una_voz_del_audio_puede_emparejarse_una_vez_por_tramo(self):
        turnos = [{"id": 1, "hora": "", "texto": "a", "voz_n": 1, "nombre": "Gonzalo", "ini_ms": 1000, "fin_ms": 11000},
                  {"id": 2, "hora": "", "texto": "b", "voz_n": 1, "nombre": "Gonzalo", "ini_ms": 101000, "fin_ms": 111000}]
        segs = [{"spk": "A", "grp": "t1", "ini_ms": 0, "fin_ms": 10000}, {"spk": "A", "grp": "t2", "ini_ms": 100000, "fin_ms": 110000}]
        r = DZ.proponer(turnos, segs)
        self.assertEqual(r["cambios"], [])                                  # la «A» de cada tramo es Gonzalo: no se inventa una voz nueva
        self.assertEqual(r["voces_nuevas"], [])


class Cobertura(unittest.TestCase):
    """¿El audio quedó bueno y cuánto de la junta cubre? Huecos, tramos ilegibles, audio suelto."""
    def setUp(self):
        limpiar()

    def _tramo(self, aid, rid, seg, off, hz=220):
        p = TMP / ("c%s.webm" % aid)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=%d:duration=%d" % (hz, seg), "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
        d = p.read_bytes()
        n = len(d) // 2 + 1
        for i in range(2):
            A.guardar_parte(aid, rid, "polo", i, io.BytesIO(d[i * n:(i + 1) * n]), len(d[i * n:(i + 1) * n]), off, "audio/webm")

    def test_la_cobertura_y_los_huecos(self):
        self._tramo("tramoaaa01", "ridcob0001", 20, 100000)             # cubre 100–120 s
        self._tramo("tramobbb01", "ridcob0001", 20, 140000)             # cubre 140–160 s (hueco de 20 s no cuenta como hueco: < 30 s)
        A.ligar("ridcob0001", "sesion-cob")
        v = A.verificar("sesion-cob", 300)
        self.assertTrue(v["ok"])
        self.assertAlmostEqual(v["cubre_s"], 40, delta=3)
        self.assertEqual([h[0] for h in v["huecos"]], [0, 160])         # falta del 0 al 100 y del 160 al 300 (el hueco corto entre tramos no cuenta)
        self.assertTrue(any("cubre 0 de 5 min" in a or "cubre" in a for a in v["avisos"]))
        self.assertGreater(A.resumen("sesion-cob")["audio_s"], 35)      # la duración queda guardada: la lista la puede mostrar sin volver a medir

    def test_un_tramo_ilegible_se_avisa(self):
        A.guardar_parte("tramomalo01", "ridmalo001", "polo", 0, io.BytesIO(b"esto no es audio" * 50), 800, 0, "audio/webm")
        A.guardar_parte("tramomalo01", "ridmalo001", "polo", 1, io.BytesIO(b"tampoco" * 50), 350, 0, "audio/webm")
        A.guardar_parte("tramomalo01", "ridmalo001", "polo", 2, io.BytesIO(b"nada" * 50), 200, 0, "audio/webm")
        A.ligar("ridmalo001", "sesion-malo")
        v = A.verificar("sesion-malo", 60)
        self.assertFalse(v["ok"])
        self.assertTrue(any("no se pudo leer" in a for a in v["avisos"]))

    def test_sin_audio_lo_dice(self):
        v = A.verificar("nadie", 60)
        self.assertEqual((v["ok"], v["tramos"]), (False, []))

    def test_audio_suelto_se_lista_se_liga_y_se_descarga(self):
        self._tramo("sueltoabc1", "ridsuelto01", 8, 0)
        _c = A._c(); _c.execute("UPDATE escucha_audio SET actualizado=? WHERE aid=?", (int(time.time()) - 3600, "sueltoabc1")); _c.commit(); _c.close()     # la pestaña se cerró hace rato: ya no cuenta como grabación en curso
        self.assertEqual([h["aid"] for h in A.huerfanos("polo")], ["sueltoabc1"])
        self.assertEqual(A.huerfanos("otro"), [])
        mp3 = A.exportar_tramo("sueltoabc1")
        self.assertGreater(A._duracion(mp3), 6)
        self.assertFalse(A.ligar_tramo("sueltoabc1", "sesion-x", "otro"))      # de otra persona no
        self.assertTrue(A.ligar_tramo("sueltoabc1", "sesion-x", "polo"))
        self.assertEqual(A.huerfanos("polo"), [])
        self.assertEqual([t["aid"] for t in A.tramos("sesion-x")], ["sueltoabc1"])


class Amplificacion(unittest.TestCase):
    """Una voz baja o lejana se guarda/descarga/manda a OpenAI AMPLIFICADA (el audio original no se toca)."""
    def setUp(self):
        limpiar()

    def _volumen_medio_db(self, ruta) -> float:
        r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(ruta), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
        import re
        return float(re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr).group(1))

    def test_el_audio_exportado_y_el_de_openai_suenan_mas_fuerte_que_el_original(self):
        # voz muy baja: seno al 2 % de volumen (≈ -37 dB de pico)
        p = TMP / "baja.webm"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=12", "-af", "volume=0.02", "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
        d = p.read_bytes()
        A.guardar_parte("bajoaud001", "ridbajo001", "polo", 0, io.BytesIO(d), len(d), 0, "audio/webm")
        A.ligar("ridbajo001", "sesion-baja")
        original = self._volumen_medio_db(A.ensamblar("bajoaud001"))
        exportado = self._volumen_medio_db(A.exportar("sesion-baja"))
        piezas = DZ.preparar(A.ensamblar("bajoaud001"), TMP / "wamp")
        para_openai = self._volumen_medio_db(piezas[0][1])
        self.assertLess(original, -30)                                         # el original es de verdad bajito
        self.assertGreater(exportado - original, 12)                           # la descarga sube más de 12 dB
        self.assertGreater(para_openai - original, 12)                         # y lo que recibe OpenAI también
        self.assertLess(exportado, -0.5)                                       # sin recortar
        self.assertAlmostEqual(self._volumen_medio_db(A.ensamblar("bajoaud001")), original, delta=0.5)   # el original guardado no cambió

    def test_una_voz_normal_no_se_distorsiona(self):
        p = TMP / "normal.webm"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=8", "-af", "volume=0.5", "-c:a", "libopus", "-b:a", "24k", str(p)], capture_output=True, check=True)
        d = p.read_bytes()
        A.guardar_parte("normalaud1", "ridnormal01", "polo", 0, io.BytesIO(d), len(d), 0, "audio/webm")
        A.ligar("ridnormal01", "sesion-normal")
        self.assertLess(self._volumen_medio_db(A.exportar("sesion-normal")), -0.5)


class PorElServidor(unittest.TestCase):
    """El camino completo por HTTP: partes → guardar la junta → pasada de voces (OpenAI simulado) → aplicar → borrar."""
    def test_camino_completo(self):
        import json, threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        os.environ.update(OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "sp"))
        import app_media, openclaw_gateway as g
        limpiar()
        app_media.openai_key = lambda: "k"
        DZ.llamar_openai = lambda key, archivo, referencias=None: {"segments": [{"speaker": "A", "start": 0, "end": 10, "text": "hola"}, {"speaker": "B", "start": 11, "end": 20, "text": "contrato"}, {"speaker": "A", "start": 21, "end": 28, "text": "perfecto"}]}
        class SinTimer:
            def __init__(self, *a, **k): pass
            def start(self): pass
        real_timer, g.threading.Timer = g.threading.Timer, SinTimer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        H = {"Authorization": "Bearer x"}
        def post(path, body):
            return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, json.dumps(body).encode(), {**H, "Content-Type": "application/json"})).read())
        def get(path):
            return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, headers=H)).read())
        try:
            datos = audio_de_prueba(30)
            n = len(datos) // 3 + 1
            for i in range(3):
                trozo = datos[i * n:(i + 1) * n]
                r = urllib.request.urlopen(urllib.request.Request(base + "/v1/app/escucha/audio/chunk?aid=audhttp123&rid=ridhttp123&i=%d&off=0&mime=audio%%2Fwebm" % i, trozo, {**H}, method="PUT"))
                self.assertEqual(json.loads(r.read())["ok"], True)
            self.assertTrue(post("/v1/app/escucha/audio", {"accion": "terminar", "aid": "audhttp123"})["ok"])
            with self.assertRaises(urllib.error.HTTPError) as cm:                                   # sin sesión válida no se sube nada
                urllib.request.urlopen(urllib.request.Request(base + "/v1/app/escucha/audio/chunk?aid=x&rid=y&i=0", b"zz", method="PUT"))
            self.assertEqual(cm.exception.code, 401)
            lineas = [{"t": "00:01", "voz": 1, "es": "Hola equipo", "idioma": "es"}, {"t": "00:12", "voz": 1, "es": "Mando el contrato", "idioma": "es"}, {"t": "00:22", "voz": 2, "es": "Perfecto", "idioma": "es"}]
            out = post("/v1/app/escucha/guardar", {"borrador_id": "ridhttp123", "duracion": 30, "lineas": lineas, "voces": [{"n": 1, "nombre": "Gonzalo"}, {"n": 2, "nombre": ""}]})
            sid = out["sesion_id"]
            self.assertEqual(out["audio"]["tramos"], 1)
            self.assertEqual(get("/v1/app/escucha/voces?sesion=" + sid)["estado"], "pendiente")
            post("/v1/app/escucha/voces", {"sesion_id": sid, "accion": "procesar"})
            for _ in range(60):
                d = get("/v1/app/escucha/voces?sesion=" + sid)
                if d["estado"] in ("lista", "error"):
                    break
                time.sleep(0.5)
            self.assertEqual(d["estado"], "lista", d)
            self.assertEqual(len(d["cambios"]), 2)
            self.assertEqual(post("/v1/app/escucha/voces", {"sesion_id": sid, "accion": "aplicar", "ids": [d["cambios"][0]["id"]]})["aplicados"], 1)
            self.assertEqual(post("/v1/app/escucha/voces", {"sesion_id": sid, "accion": "aplicar"})["aplicados"], 1)
            with self.assertRaises(urllib.error.HTTPError) as cm:                                   # una junta que no existe no se consulta
                get("/v1/app/escucha/voces?sesion=nadie")
            self.assertEqual(cm.exception.code, 404)
            self.assertEqual(post("/v1/app/escucha/voces", {"sesion_id": sid, "accion": "borrar_audio"})["borrados"], 1)
            self.assertEqual(get("/v1/app/escucha/voces?sesion=" + sid)["audio"]["tramos"], 0)
        finally:
            g.threading.Timer = real_timer
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
