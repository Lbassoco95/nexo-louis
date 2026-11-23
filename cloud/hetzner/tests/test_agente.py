"""El agente de vigilancia: decide qué revisar y cuándo volver, aprende cómo trabaja Polo, respeta lo que Polo corrige y tiene red de seguridad."""
import os, sys, json, time, tempfile, unittest
from pathlib import Path
from datetime import datetime, timedelta
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_agente as A, donna_radar as R


class FakeCore:
    SPACE = TMP


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    h = TMP / "app-history.jsonl"
    now = datetime.now()
    with open(h, "w") as f:                                       # Polo escribe a las 9, 10 y 17 h en días recientes
        for d in range(1, 8):
            for hr in (9, 10, 17):
                f.write(json.dumps({"ts": (now - timedelta(days=d)).replace(hour=hr, minute=5).isoformat(), "role": "user", "content": "x"}) + "\n")
                f.write(json.dumps({"ts": (now - timedelta(days=d)).replace(hour=hr, minute=6).isoformat(), "role": "assistant", "content": "y"}) + "\n")


def fuentes(items=()):
    return {"abiertos": {"agenda": lambda h: list(items)}, "evidencias": {"slack": lambda d: []}}


def llm(dec):
    box = {"obs": None, "n": 0}

    def _l(system, user):
        box["obs"] = json.loads(user.split("OBSERVACIÓN:\n", 1)[1])
        box["n"] += 1
        return dec
    return _l, box


class Agente(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_observa_el_ritmo_real_y_decide_cuando_volver(self):
        f, box = llm({"acciones": [{"herramienta": "radar_fechas", "motivo": "empieza su día"}, {"herramienta": "cerrar_todo", "motivo": "x"}],
                      "proxima_revision_min": 5, "bitacora": "Revisé fechas porque en 40 min empieza tu día.", "aprendizajes": []})
        r = A.ciclo(FakeCore, "primera vez", fuentes(), f, notificar=lambda *a, **k: None)
        tel = box["obs"]["telemetria"]
        self.assertEqual(tel["mensajes_de_polo_30d"], 21)                                  # solo los mensajes de Polo, no los de Donna
        self.assertEqual(set(tel["por_hora"]), {"9", "10", "17"})
        self.assertEqual([a["herramienta"] for a in r["acciones"]], ["radar_fechas"])      # una herramienta inventada se ignora
        self.assertEqual(r["proxima_en_min"], 10)                                          # mínimo 10 min
        self.assertTrue(any("empieza tu día" in b["resumen"] for b in R.bitacora_list()))
        f2, _ = llm({"acciones": [], "proxima_revision_min": 99999, "bitacora": "Nada nuevo."})
        self.assertEqual(A.ciclo(FakeCore, "otra", fuentes(), f2)["proxima_en_min"], 720)  # nunca más de 12 h callada

    def test_el_agente_elige_cuando_despertar_no_hay_horario_fijo(self):
        self.assertEqual(A.toca(), "primera vez")
        f, _ = llm({"acciones": [], "proxima_revision_min": 120, "bitacora": "ok"})
        A.ciclo(FakeCore, "primera vez", fuentes(), f)
        self.assertEqual(A.toca(), "")                                                     # aún no le toca
        self.assertEqual(A.toca(int(time.time()) + 121 * 60), "me tocaba según lo que decidí")
        A.estado_set("ultima_ts", int(time.time()) - 11 * 60)
        import donna_personas
        donna_personas.guardar_sesion("Junta", 60, [], [{"t": "00:01", "voz": 0, "es": "algo"}])
        self.assertEqual(A.toca(), "hay una junta nueva")                                  # una junta nueva lo despierta antes de lo planeado

    def test_no_repite_revisiones_caras_y_solo_lee_juntas_nuevas(self):
        calls = []
        orig = R.radar_fechas
        R.radar_fechas = lambda fu, hoy=None, notificar=None: calls.append(1) or {"nuevos": 0, "revisados": 0}
        try:
            f, _ = llm({"acciones": [{"herramienta": "radar_fechas", "motivo": "a"}], "proxima_revision_min": 30, "bitacora": "x"})
            A.ciclo(FakeCore, "1", fuentes(), f)
            r = A.ciclo(FakeCore, "2", fuentes(), f)
            self.assertEqual(len(calls), 1)
            self.assertIn("omitida", r["acciones"][0])
        finally:
            R.radar_fechas = orig

    def test_aprende_refuerza_contradice_y_respeta_a_polo(self):
        ap = lambda h, conf=0.6, ev="30 días": {"clave": "horario.activo", "tema": "horario", "hecho": h, "evidencia": ev, "confianza": conf}
        A.aprender([ap("Polo trabaja de 9 a 18 h con picos a las 10 y a las 17")])
        k = A.conocimiento_list()[0]
        c0 = k["confianza"]
        A.aprender([ap("Polo trabaja de 9 a 18 h con picos a las 10 y a las 17")])
        self.assertGreater(A.conocimiento_list()[0]["confianza"], c0)                       # repetir refuerza
        A.aprender([ap("Casi no usa Donna por las tardes, solo escribe temprano", 0.4)])
        self.assertLess(A.conocimiento_list()[0]["confianza"], c0 + 0.08)                  # contradecir baja la confianza
        A.corregir(k["id"], "corregir", "Polo trabaja de 7 a 20 h y contesta mensajes hasta tarde")
        A.aprender([ap("Polo trabaja de 9 a 18 h", 0.95)])
        k2 = A.conocimiento_list()[0]
        self.assertEqual((k2["fuente"], k2["confianza"]), ("polo", 1.0))
        self.assertIn("7 a 20", k2["hecho"])                                                # lo que Polo corrigió ya no lo pisa el agente
        A.corregir(k["id"], "olvidar")
        A.aprender([ap("Polo trabaja de 9 a 18 h otra vez")])
        self.assertEqual(A.conocimiento_list(), [])                                         # lo olvidado no vuelve

    def test_solo_aprende_de_trabajo(self):
        n = A.aprender([{"clave": "salud.sueno", "tema": "salud", "hecho": "Polo duerme poco y está cansado", "confianza": 0.8},
                        {"clave": "ritmo.cierres", "tema": "ritmo", "hecho": "Cierra la mayoría de sus pendientes los viernes por la tarde", "confianza": 0.7},
                        {"clave": "x", "tema": "ritmo", "hecho": "corto", "confianza": 0.7}])
        self.assertEqual(n, 1)
        self.assertEqual([k["clave"] for k in A.conocimiento_list()], ["ritmo.cierres"])

    def test_lo_aprendido_vuelve_al_siguiente_ciclo_y_al_contexto_de_donna(self):
        for _ in range(2):                                                                   # la confianza se gana repitiéndose: una sola observación no entra al contexto del chat
            A.aprender([{"clave": "avisos.horas", "tema": "avisos", "hecho": "Atiende los avisos entre las 9 y las 10 h", "confianza": 0.8}])
        A.aprender([{"clave": "baja.conf", "tema": "ritmo", "hecho": "Algo poco seguro sobre su ritmo de trabajo", "confianza": 0.3}])
        f, box = llm({"acciones": [], "proxima_revision_min": 60, "bitacora": "x"})
        A.ciclo(FakeCore, "m", fuentes(), f)
        self.assertEqual(len(box["obs"]["lo_que_ya_se_de_polo"]), 2)
        ctx = A.contexto_corto()
        self.assertIn("Atiende los avisos", ctx)
        self.assertNotIn("poco seguro", ctx)                                                # al chat solo llega lo que tiene confianza

    def test_si_el_modelo_falla_hay_red_de_seguridad_y_tope_diario(self):
        def mal(s, u):
            raise RuntimeError("sin crédito")
        done = []
        orig = (R.radar_fechas, R.procesar_cierres)
        R.radar_fechas = lambda fu, hoy=None, notificar=None: done.append("f") or {"nuevos": 0}
        R.procesar_cierres = lambda core, fu, juez, hoy=None, notificar=None, aplicar_fn=None: done.append("c") or {"propuestos": 0}
        try:
            r = A.ciclo(FakeCore, "m", fuentes(), mal, juez=lambda i, e: {})
            self.assertEqual((r["modo"], r["proxima_en_min"], done), ("basico", 180, ["f", "c"]))
            A.estado_set("llamadas", {"dia": datetime.now(A.TZ).strftime("%Y-%m-%d"), "n": A.MAX_CICLOS_DIA})
            f, box = llm({"acciones": []})
            A.estado_set("herramientas", {})
            self.assertEqual(A.ciclo(FakeCore, "tope", fuentes(), f, juez=lambda i, e: {})["modo"], "basico")
            self.assertEqual(box["n"], 0)                                                   # con el tope diario ya no llama al modelo
        finally:
            R.radar_fechas, R.procesar_cierres = orig

    def test_tick_corre_en_hilo_y_no_se_atropella(self):
        corridas = []
        import donna_agente as AG
        orig = AG.ciclo
        AG.ciclo = lambda core, motivo, fu, **k: corridas.append(motivo) or {}
        orig_f = R.fuentes_por_defecto
        R.fuentes_por_defecto = lambda core: fuentes()
        try:
            self.assertTrue(AG.tick(FakeCore, hilo=False))
            self.assertEqual(corridas, ["primera vez"])
        finally:
            AG.ciclo, R.fuentes_por_defecto = orig, orig_f


class SituacionYPlan(unittest.TestCase):
    def setUp(self):
        limpiar()

    SIT = {"clave": "acumulacion.vencidos", "titulo": "Acumulación grave de vencidos", "gravedad": "urgente",
           "detalle": "Patio: 69 de 93 abiertos ya vencieron (74%). Agenda: 27 de 60 (45%).",
           "plan": {"resumen": "Triage en bloques de 30 min en tu hora más activa, empezando por lo más viejo.",
                    "pasos": [{"paso": "Archivar lo vencido de más de 90 días (con respaldo)", "cuando": "2026-11-24T10:00"},
                              {"paso": "Replantear fechas de lo vencido de 8 a 90 días", "cuando": "2026-11-25T10:00"},
                              {"paso": "corto", "cuando": ""}]}}

    def test_una_foto_de_la_carga_no_se_guarda_como_aprendizaje_y_la_confianza_se_gana(self):
        n = A.aprender([{"clave": "proyectos.carga", "tema": "proyectos", "hecho": "93 abiertos en patio, 69 vencidos (74% de los abiertos ya pasaron fecha)", "confianza": 0.95},
                        {"clave": "ritmo.decide", "tema": "ritmo", "hecho": "Suele decidir las propuestas en menos de una hora", "confianza": 0.95}])
        self.assertEqual(n, 1)
        k = A.conocimiento_list()[0]
        self.assertEqual(k["clave"], "ritmo.decide")
        self.assertLessEqual(k["confianza"], 0.55)                                          # aunque el modelo diga 95%, una sola observación no da certeza
        # lo que ya estaba guardado como foto (versión anterior) se limpia solo
        c = A._c()
        c.execute("INSERT INTO conocimiento(id,clave,tema,hecho,confianza,creado,actualizado) VALUES('x','p.v','proyectos','Patio abiertos: 93, vencidos: 69 (74%)',0.95,1,1)")
        c.commit(); c.close()
        self.assertEqual(A.purgar_situaciones(), 1)
        self.assertNotIn("p.v", [k["clave"] for k in A.conocimiento_list()])

    def test_urgente_avisa_con_el_plan_una_sola_vez_y_con_tope_diario(self):
        n = []
        sits = A.decidir({}, lambda s_, u_: {"acciones": [], "situaciones": [self.SIT]})["situaciones"]
        self.assertEqual(len(sits[0]["pasos"]), 2)                                          # el paso demasiado corto se descarta
        r = A.procesar_situaciones(sits, lambda t, b="", **k: n.append((t, b, k)))
        self.assertTrue(r[0]["avisado"])
        self.assertEqual(n[0][2]["link"], "plan:" + r[0]["plan"])
        self.assertIn("Cómo propongo abordarlo", n[0][2]["full_body"])
        self.assertIn("Archivar lo vencido", n[0][2]["full_body"])
        self.assertEqual(A.procesar_situaciones(sits, lambda *a, **k: self.fail("no repite")), [])    # misma situación abierta: no insiste
        for i in range(3):
            otra = dict(sits[0], clave="otra.%d" % i)
            A.procesar_situaciones([otra], lambda t, b="", **k: n.append(t))
        self.assertEqual(len(n), A.MAX_AVISOS_DIA)                                          # máximo 2 avisos al día

    def test_atencion_se_muestra_en_la_app_pero_no_avisa(self):
        sit = A.decidir({}, lambda s_, u_: {"situaciones": [dict(self.SIT, gravedad="atencion", clave="calma")]})["situaciones"]
        r = A.procesar_situaciones(sit, lambda *a, **k: self.fail("solo urgente avisa"))
        self.assertFalse(r[0]["avisado"])
        self.assertEqual(A.planes()[0]["gravedad"], "atencion")

    def test_aceptar_el_plan_vuelve_cada_paso_un_seguimiento_con_fecha(self):
        sits = A.decidir({}, lambda s_, u_: {"situaciones": [self.SIT]})["situaciones"]
        pid = A.procesar_situaciones(sits, lambda *a, **k: None)[0]["plan"]
        r = A.plan_accion(pid, "aceptar")
        self.assertEqual(r["seguimientos"], 2)
        segs = D.seg_list()
        self.assertEqual(sorted(x["origen"] for x in segs), ["plan", "plan"])
        self.assertTrue(all(x["proxima_revision"] > 0 for x in segs))
        with self.assertRaises(ValueError):
            A.plan_accion(pid, "aceptar")                                                   # no se acepta dos veces
        self.assertEqual(A.planes()[0]["estado"], "aceptado")

    def test_despues_y_descartar(self):
        sits = A.decidir({}, lambda s_, u_: {"situaciones": [self.SIT]})["situaciones"]
        pid = A.procesar_situaciones(sits, lambda *a, **k: None)[0]["plan"]
        A.plan_accion(pid, "despues")
        A.plan_accion(pid, "descartar")
        self.assertEqual(A.planes(), [])

    def test_el_ciclo_completo_trae_el_contexto_para_proponer_un_plan_real(self):
        items = [{"tipo": "patio", "ref": "p%d" % i, "titulo": "Tarea %d" % i, "fecha": "2026-06-01"} for i in range(5)] + [{"tipo": "agenda", "ref": "a1", "titulo": "Reciente", "fecha": "2026-11-20"}]
        f, box = llm({"acciones": [], "proxima_revision_min": 60, "bitacora": "x", "situaciones": [self.SIT]})
        n = []
        r = A.ciclo(FakeCore, "m", fuentes(items), f, notificar=lambda t, b="", **k: n.append(t))
        self.assertEqual(box["obs"]["carga"]["vencidas_por_antiguedad"]["patio"], {"más de 90 días": 5})   # agrupa por antigüedad, no lista todo
        self.assertEqual(box["obs"]["carga"]["vencidas"], {"patio": 5, "agenda": 1})
        self.assertIn("temas_recientes_de_conversacion", box["obs"])
        self.assertEqual(r["situaciones"][0]["avisado"], True)
        self.assertTrue(any("Acumulación" in t for t in n))


if __name__ == "__main__":
    unittest.main()
