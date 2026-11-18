"""Trabajos de Donna: plan por pasos, ejecución sola, pasos de «cambio» esperan confirmación, pausa/reanudar/cancelar y reinicio a medias."""
import os, sys, time, json, tempfile, threading, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"))
import app_trabajos as T

AVISOS, PROMPTS = [], []
PLAN = {"titulo": "Revisión de Yoltik", "pasos": [
    {"titulo": "Contar vencidas por responsable", "detalle": "usa patio_resumen", "tipo": "leer"},
    {"titulo": "Guardar el panorama en memoria", "detalle": "", "tipo": "memoria"},
    {"titulo": "Cerrar las tareas sembradas de mayo", "detalle": "cancelar las 28", "tipo": "cambio"}]}


def esperar(jid, estados, t=8):
    fin = time.time() + t
    while time.time() < fin:
        j = T.obtener(jid)
        if j and j["estado"] in estados:
            return j
        time.sleep(0.05)
    raise AssertionError("no llegó a %s: %s" % (estados, T.obtener(jid) and T.obtener(jid)["estado"]))


def preparar(ejecutar=None, plan=PLAN):
    del AVISOS[:], PROMPTS[:]
    T._HILOS.clear()
    for f in T.TRAB_DIR.glob("j*.json") if T.TRAB_DIR.exists() else []:
        f.unlink()

    def ej(prompt):
        PROMPTS.append(prompt)
        return (ejecutar or (lambda p: ("Resultado real del paso con cifras: 68 vencidas.", "")))(prompt)
    T.configurar(planear=lambda pedido: plan, ejecutar=ej, resumir=lambda j: "RESUMEN: " + ", ".join(p["titulo"] for p in j["pasos"] if p["estado"] == "hecho"),
                 notificar=lambda *a: AVISOS.append(a))


class Plan(unittest.TestCase):
    def test_normaliza_y_limita(self):
        t, pasos = T._normalizar_plan({"titulo": "X" * 300, "pasos": [{"titulo": f"p{i}", "tipo": "raro"} for i in range(20)] + ["", {"nada": 1}]})
        self.assertEqual(len(pasos), T.MAX_PASOS); self.assertEqual(len(t), 110)
        self.assertTrue(all(p["tipo"] == "leer" and p["estado"] == "pendiente" for p in pasos))      # tipo desconocido → leer
        with self.assertRaises(ValueError):
            T._normalizar_plan({"pasos": []})
        with self.assertRaises(ValueError):
            T._normalizar_plan("no es un dict")

    def test_validaciones(self):
        preparar()
        with self.assertRaises(ValueError):
            T.crear("corto")
        self.assertIsNone(T.obtener("../x")); self.assertIsNone(T.obtener("jffffffffff"))
        with self.assertRaises(ValueError):
            T.accion("jffffffffff", "pausar")


class Ejecucion(unittest.TestCase):
    def test_lo_de_leer_y_memoria_corre_solo_y_el_cambio_espera(self):
        preparar()
        j = T.crear("Revisa todo Yoltik y limpia las tareas viejas de Patio", esperar_plan=3)
        self.assertEqual(len(j["pasos"]), 3)                                                          # el plan ya se ve
        j = esperar(j["id"], ("espera confirmación",))
        est = [p["estado"] for p in j["pasos"]]
        self.assertEqual(est, ["hecho", "hecho", "espera"])
        self.assertEqual(j["progreso"], 66); self.assertEqual(len(PROMPTS), 2)                       # el paso de cambio NO se ejecutó
        self.assertIn("Confirmar: Cerrar las tareas sembradas de mayo", j["pendientes"])
        self.assertTrue(j["resumen"].startswith("RESUMEN"))
        self.assertTrue(AVISOS and "espera confirmación" in AVISOS[-1][0] and AVISOS[-1][2] == "trabajo:" + j["id"])
        self.assertIn("NO cambies sistemas externos", PROMPTS[0])
        self.assertIn("68 vencidas", PROMPTS[1])                                                      # cada paso recibe lo que dieron los anteriores

    def test_confirmar_ejecuta_el_cambio_y_termina(self):
        preparar()
        j = esperar(T.crear("Revisa todo Yoltik y limpia las tareas viejas de Patio")["id"], ("espera confirmación",))
        with self.assertRaises(ValueError):
            T.accion(j["id"], "confirmar", 1)                                                          # ese paso no espera
        T.accion(j["id"], "confirmar", 3)
        j = esperar(j["id"], ("terminado",))
        self.assertEqual([p["estado"] for p in j["pasos"]], ["hecho"] * 3); self.assertEqual(j["progreso"], 100)
        self.assertIn("Polo YA CONFIRMÓ", PROMPTS[-1]); self.assertEqual(j["pendientes"], [])

    def test_paso_que_falla_deja_pendiente_y_reanudar_lo_reintenta(self):
        intentos = {"n": 0}

        def ej(p):
            if "Contar vencidas" in p and intentos["n"] == 0:
                intentos["n"] += 1
                return "", "Patio no respondió"
            return "Listo con datos reales.", ""
        preparar(ej, plan={"titulo": "T", "pasos": [{"titulo": "Contar vencidas por responsable", "tipo": "leer"}, {"titulo": "Guardar", "tipo": "memoria"}]})
        j = esperar(T.crear("Revisa los datos de Patio y guárdalos en memoria")["id"], ("con pendientes",))
        self.assertEqual([p["estado"] for p in j["pasos"]], ["falló", "hecho"])
        self.assertIn("Patio no respondió", j["pasos"][0]["resultado"]); self.assertIn("Reintentar: Contar vencidas por responsable", j["pendientes"])
        T.accion(j["id"], "reanudar")
        j = esperar(j["id"], ("terminado",))
        self.assertEqual([p["estado"] for p in j["pasos"]], ["hecho", "hecho"])

    def test_pausar_reanudar_y_cancelar(self):
        liberar = threading.Event()
        preparar(lambda p: (liberar.wait(5), ("ok " * 5, ""))[1], plan={"titulo": "T", "pasos": [{"titulo": f"paso {i}", "tipo": "leer"} for i in range(4)]})
        j = T.crear("Un trabajo largo con varios pasos para probar pausa")
        time.sleep(0.4)
        T.accion(j["id"], "pausar"); liberar.set()
        j = esperar(j["id"], ("pausado",))
        hechos = sum(1 for p in j["pasos"] if p["estado"] == "hecho")
        self.assertTrue(1 <= hechos < 4)                                                              # se detuvo entre pasos, sin perder lo hecho
        T.accion(j["id"], "reanudar")
        j = esperar(j["id"], ("terminado",))
        self.assertEqual(sum(1 for p in j["pasos"] if p["estado"] == "hecho"), 4)
        # cancelar uno parado
        preparar(plan={"titulo": "T", "pasos": [{"titulo": "x", "tipo": "cambio"}]})
        k = esperar(T.crear("Algo que cambia cosas y espera confirmación")["id"], ("espera confirmación",))
        self.assertEqual(T.accion(k["id"], "cancelar")["estado"], "cancelado")
        with self.assertRaises(ValueError):
            T.accion(k["id"], "reanudar")

    def test_dentro_de_un_paso_no_se_pueden_crear_trabajos(self):
        vistos = []
        preparar(lambda p: (vistos.append(T.en_trabajo()) or ("hecho " * 5, "")), plan={"titulo": "T", "pasos": [{"titulo": "a", "tipo": "leer"}]})
        esperar(T.crear("Un trabajo cualquiera de prueba aquí")["id"], ("terminado",))
        self.assertEqual(vistos, [True]); self.assertFalse(T.en_trabajo())

    def test_sin_planeador_se_hace_como_un_solo_paso(self):
        preparar()
        T.configurar(planear=lambda p: (_ for _ in ()).throw(RuntimeError("sin modelo")))
        j = esperar(T.crear("Haz una sola cosa grande pero sin plan")["id"], ("terminado",))
        self.assertEqual(len(j["pasos"]), 1); self.assertTrue(any("No pude armar un plan" in b["t"] for b in j["bitacora"]))

    def test_reinicio_a_medias_se_reanuda(self):
        preparar()
        jid = "j" + "a" * 10
        T._escribir({"id": jid, "titulo": "Viejo", "pedido": "algo", "estado": "en curso", "creado": 1, "actualizado": 1, "usuario": "", "progreso": 33,
                     "resumen": "", "pendientes": [], "bitacora": [], "control": "", "pasos": [
                         {"titulo": "a", "detalle": "", "tipo": "leer", "estado": "hecho", "resultado": "ya", "t0": 1, "t1": 2, "confirmado": False},
                         {"titulo": "b", "detalle": "", "tipo": "leer", "estado": "en curso", "resultado": "", "t0": 3, "t1": 0, "confirmado": False}]})
        self.assertEqual(T.reanudar_pendientes(), 1)
        j = esperar(jid, ("terminado",))
        self.assertEqual([p["estado"] for p in j["pasos"]], ["hecho", "hecho"])

    def test_tope_de_trabajos_simultaneos(self):
        liberar = threading.Event()
        preparar(lambda p: (liberar.wait(5), ("ok " * 5, ""))[1], plan={"titulo": "T", "pasos": [{"titulo": "x", "tipo": "leer"}]})
        ids = [T.crear(f"Trabajo largo número {i} para saturar")["id"] for i in range(T.MAX_ACTIVOS)]
        with self.assertRaises(ValueError):
            T.crear("Uno más que ya no debe caber ahora")
        liberar.set()
        for i in ids:
            esperar(i, ("terminado",))

    def test_texto_para_donna(self):
        preparar()
        j = esperar(T.crear("Revisa todo Yoltik y limpia las tareas viejas de Patio")["id"], ("espera confirmación",))
        t = T.resumen_texto(j["id"])
        self.assertIn(f"[[trabajo:{j['id']}]]", t); self.assertIn("🕓 3.", t); self.assertIn("Pendiente: Confirmar", t)
        self.assertIn(j["id"], T.resumen_texto())


class Servidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import http.client
        from http.server import ThreadingHTTPServer
        os.environ.update(OPENCLAW_GATEWAY_TOKEN="master-secret-123", OPENCLAW_HOME=str(TMP / "oc"), OPENCLAW_SPACES=str(TMP / "spaces"))
        import openclaw_gateway as g
        import louis_core as core
        cls.core, cls.http = core, http.client
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, m, path, body=None, tok="master-secret-123"):
        c = self.http.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(m, path, body=json.dumps(body) if body is not None else None, headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_api_crear_listar_ver_confirmar(self):
        preparar()
        st, j = self.req("POST", "/v1/app/trabajos/crear", {"pedido": "Revisa todo Yoltik y limpia las tareas viejas de Patio", "esperar": 3})
        self.assertEqual(st, 200); self.assertEqual(len(j["pasos"]), 3)
        esperar(j["id"], ("espera confirmación",))
        st, lista = self.req("GET", "/v1/app/trabajos")
        self.assertTrue(any(i["id"] == j["id"] and i["espera"] == 1 for i in lista["items"]))
        st, d = self.req("GET", "/v1/app/trabajo?id=" + j["id"])
        self.assertEqual(st, 200); self.assertEqual(d["pasos"][2]["estado"], "espera")
        self.assertEqual(self.req("POST", "/v1/app/trabajo/accion", {"id": j["id"], "accion": "confirmar", "paso": 3})[0], 200)
        esperar(j["id"], ("terminado",))
        self.assertEqual(self.req("GET", "/v1/app/trabajo?id=jffffffffff")[0], 404)
        self.assertEqual(self.req("POST", "/v1/app/trabajo/accion", {"id": j["id"], "accion": "bailar"})[0], 400)
        self.assertEqual(self.req("POST", "/v1/app/trabajos/crear", {"pedido": "corto"})[0], 400)
        self.assertEqual(self.req("GET", "/v1/app/trabajos", tok="malo")[0], 401)

    def test_donna_puede_crear_y_consultar_trabajos_pero_no_anidar(self):
        preparar()
        out = self.core.execute_tool("trabajo_crear", {"pedido": "Revisa todo Yoltik y limpia las tareas viejas de Patio"})
        self.assertIn("[[trabajo:j", out); self.assertIn("3 paso(s)", out)
        jid = out.split("[[trabajo:")[1].split("]]")[0]
        esperar(jid, ("espera confirmación",))
        self.assertIn("🕓 3.", self.core.execute_tool("trabajo_estado", {"id": jid}))
        self.assertIn(jid, self.core.execute_tool("trabajo_estado", {}))
        T._TL.activo = True                                   # como si estuviera dentro de un paso
        try:
            self.assertTrue(self.core.execute_tool("trabajo_crear", {"pedido": "Otro trabajo dentro de un trabajo"}).startswith("❌ Ya estás dentro"))
        finally:
            T._TL.activo = False


if __name__ == "__main__":
    unittest.main()
