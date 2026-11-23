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


if __name__ == "__main__":
    unittest.main()
