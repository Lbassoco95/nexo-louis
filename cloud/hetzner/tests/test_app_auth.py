"""Usuario/contraseña de la app: hash, sesiones, límite de intentos y alcance de las sesiones en el gateway."""
import os, sys, json, time, http.client, tempfile, threading, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="master-secret-123", TZ="America/Mexico_City")
import app_auth as A
import louis_core as core
import openclaw_gateway as g

PW = "mi frase larga y segura 2026"


def fresh():
    for f in (A.USERS_FILE, A.SESSIONS_FILE):
        f.unlink(missing_ok=True)
    A.reset_limits()
    assert A.set_password("polo", PW) == ""


class Contrasenas(unittest.TestCase):
    def test_hash_correcto_y_con_sal(self):
        h1, h2 = A.hash_password(PW), A.hash_password(PW)
        self.assertNotEqual(h1, h2)                                    # sal distinta cada vez
        self.assertTrue(h1.startswith("scrypt$"))
        self.assertTrue(A.verify_password(PW, h1))
        self.assertFalse(A.verify_password(PW + "x", h1))
        self.assertFalse(A.verify_password("", h1))
        self.assertFalse(A.verify_password(PW, "basura"))              # formato roto: falso, no excepción

    def test_politica_de_contrasenas(self):
        for mala in ("corta", "polo", "passwordpassword", "aaaaaaaaaaaaaaaa", ""):
            self.assertTrue(A.password_problem("polo", mala), mala)
        self.assertIsNone(A.password_problem("polo", PW))

    def test_se_guarda_solo_el_hash_y_con_permisos_0600(self):
        fresh()
        raw = A.USERS_FILE.read_text()
        self.assertNotIn(PW, raw)
        self.assertIn("scrypt$", raw)
        self.assertEqual(oct(A.USERS_FILE.stat().st_mode)[-3:], "600")


class Sesiones(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_login_ok_y_el_token_no_se_guarda(self):
        r = A.login("Polo", PW, "1.1.1.1", "iPhone")                  # el usuario no distingue mayúsculas
        self.assertTrue(r["ok"] and r["token"].startswith("dns_"))
        self.assertNotIn(r["token"], A.SESSIONS_FILE.read_text())      # solo su SHA-256
        self.assertEqual(oct(A.SESSIONS_FILE.stat().st_mode)[-3:], "600")
        self.assertEqual(A.check_session(r["token"])["user"], "polo")

    def test_errores_son_iguales_exista_o_no_el_usuario(self):
        a = A.login("polo", "incorrecta-pero-larga", "2.2.2.2")
        b = A.login("fantasma", "incorrecta-pero-larga", "2.2.2.3")
        self.assertEqual((a["ok"], a["error"]), (b["ok"], b["error"]))
        self.assertEqual(a["error"], "Usuario o contraseña incorrectos.")

    def test_tokens_ajenos_o_alterados_no_valen(self):
        t = A.login("polo", PW)["token"]
        for malo in ("", "master-secret-123", t[:-2] + "xx", "dns_" + "a" * 43, None):
            self.assertIsNone(A.check_session(malo), malo)

    def test_vencimiento_revocacion_y_tope(self):
        t = A.login("polo", PW)["token"]
        ses = json.loads(A.SESSIONS_FILE.read_text()); k = next(iter(ses)); ses[k]["exp"] = int(time.time()) - 1
        A.SESSIONS_FILE.write_text(json.dumps(ses))
        self.assertIsNone(A.check_session(t))                          # vencida
        t2 = A.login("polo", PW)["token"]
        self.assertTrue(A.revoke(t2)); self.assertIsNone(A.check_session(t2))
        toks = [A.login("polo", PW, f"9.9.9.{i}")["token"] for i in range(A.MAX_SESSIONS_PER_USER + 3)]
        self.assertEqual(len(A.list_sessions("polo")), A.MAX_SESSIONS_PER_USER)   # se descartan las más viejas
        self.assertIsNone(A.check_session(toks[0])); self.assertIsNotNone(A.check_session(toks[-1]))
        self.assertGreaterEqual(A.revoke_all("polo"), A.MAX_SESSIONS_PER_USER)
        self.assertIsNone(A.check_session(toks[-1]))

    def test_cambiar_contrasena_con_la_herramienta_cierra_sesiones(self):
        t = A.login("polo", PW)["token"]
        A.set_password("polo", "otra frase todavia mas larga 99")
        A.revoke_all("polo")
        self.assertIsNone(A.check_session(t))
        self.assertFalse(A.login("polo", PW)["ok"])                    # la anterior ya no entra


class Limites(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_bloqueo_por_ip_aunque_luego_use_la_contrasena_buena(self):
        for _ in range(A.LIM_IP):
            self.assertFalse(A.login("polo", "mala-mala-mala-1", "7.7.7.7")["ok"])
        r = A.login("polo", PW, "7.7.7.7")                              # ¡la correcta! pero está bloqueado
        self.assertFalse(r["ok"]); self.assertGreater(r["retry_after"], 0)
        self.assertTrue(A.login("polo", PW, "8.8.8.8")["ok"])           # otra IP no se ve afectada

    def test_bloqueo_por_usuario_si_cambian_de_ip(self):
        for i in range(A.LIM_USER):
            A.login("polo", "mala-mala-mala-1", f"10.0.0.{i}")
        self.assertGreater(A.login("polo", PW, "10.0.9.9").get("retry_after", 0), 0)

    def test_bloqueo_global_contra_ataques_distribuidos(self):
        for i in range(A.LIM_GLOBAL):
            A.login(f"u{i}", "mala-mala-mala-1", f"11.0.{i}.1")
        self.assertGreater(A.login("polo", PW, "12.12.12.12").get("retry_after", 0), 0)


class Gateway(unittest.TestCase):
    """Con el servidor HTTP real: qué puede y qué NO puede hacer una sesión."""
    @classmethod
    def setUpClass(cls):
        core.build_app_hoy = lambda: {"generado": "x", "abiertos": 1, "cerrados": 0, "urgentes": [], "vencimientos": [], "entregables_listos": []}
        core.execute_tool = lambda n, a: "EJECUTADA:" + n
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        fresh()

    def call(self, method, path, body=None, token=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = "Bearer " + token
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse(); raw = r.read()
        try:
            data = json.loads(raw)
        except Exception:
            data = raw
        return r.status, data, dict(r.getheaders())

    def entrar(self):
        s, d, _ = self.call("POST", "/v1/app/login", {"user": "polo", "password": PW})
        self.assertEqual(s, 200); return d["token"]

    def test_login_no_exige_autenticacion_y_da_sesion(self):
        s, d, _ = self.call("POST", "/v1/app/login", {"user": "polo", "password": PW})
        self.assertEqual(s, 200); self.assertTrue(d["token"].startswith("dns_")); self.assertEqual(d["user"], "polo")

    def test_credenciales_malas_401_y_nada_se_filtra(self):
        s, d, _ = self.call("POST", "/v1/app/login", {"user": "polo", "password": "equivocada-pero-larga"})
        self.assertEqual(s, 401); self.assertEqual(d, {"error": "Usuario o contraseña incorrectos."})
        s, _, _ = self.call("POST", "/v1/app/login", {})
        self.assertEqual(s, 401)

    def test_429_con_retry_after(self):
        for _ in range(A.LIM_IP):
            self.call("POST", "/v1/app/login", {"user": "polo", "password": "mala-mala-mala-1"})
        s, d, h = self.call("POST", "/v1/app/login", {"user": "polo", "password": PW})
        self.assertEqual(s, 429); self.assertGreater(int(h["Retry-After"]), 0); self.assertGreater(d["retry_after"], 0)

    def test_la_sesion_usa_la_app(self):
        t = self.entrar()
        self.assertEqual(self.call("GET", "/v1/app/ping", token=t)[1], {"ok": True, "modo": "sesion", "user": "polo"})
        self.assertEqual(self.call("GET", "/v1/app/hoy", token=t)[0], 200)

    def test_la_sesion_NO_alcanza_lo_administrativo(self):
        t = self.entrar()
        self.assertEqual(self.call("POST", "/v1/tools/mac_bash", {"args": {}}, token=t)[0], 401)   # ejecutar herramientas
        self.assertEqual(self.call("GET", "/v1/agents", token=t)[0], 401)
        self.assertEqual(self.call("GET", "/v1/tools", token=t)[0], 401)
        self.assertEqual(self.call("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]}, token=t)[0], 401)

    def test_el_token_maestro_sigue_funcionando_en_todo(self):
        m = "master-secret-123"
        self.assertEqual(self.call("GET", "/v1/app/ping", token=m)[1]["modo"], "admin")
        self.assertEqual(self.call("POST", "/v1/tools/reloj", {"args": {}}, token=m)[1]["result"], "EJECUTADA:reloj")
        self.assertEqual(self.call("GET", "/v1/agents", token=m)[0], 200)

    def test_sin_token_o_con_basura_401(self):
        for t in (None, "", "dns_inventado", "master-secret-124"):
            self.assertEqual(self.call("GET", "/v1/app/hoy", token=t)[0], 401, t)

    def test_cerrar_sesion_la_invalida_en_el_servidor(self):
        t = self.entrar()
        self.assertEqual(self.call("POST", "/v1/app/logout", {}, token=t)[1], {"ok": True})
        self.assertEqual(self.call("GET", "/v1/app/hoy", token=t)[0], 401)

    def test_cerrar_todas_las_sesiones(self):
        t1, t2 = self.entrar(), self.entrar()
        self.assertEqual(self.call("POST", "/v1/app/sessions/revoke-all", {}, token=t1)[1]["cerradas"], 2)
        self.assertEqual(self.call("GET", "/v1/app/hoy", token=t2)[0], 401)

    def test_lista_de_sesiones_marca_la_actual(self):
        t = self.entrar(); self.entrar()
        ses = self.call("GET", "/v1/app/sessions", token=t)[1]["sesiones"]
        self.assertEqual(len(ses), 2); self.assertEqual(sum(1 for x in ses if x["current"]), 1)
        self.assertNotIn("hash", json.dumps(ses))

    def test_ip_real_detras_de_caddy(self):
        # el cliente llega por loopback con X-Real-IP; el límite se cuenta por ESA ip, no por la de Caddy
        for _ in range(A.LIM_IP):
            self.call("POST", "/v1/app/login", {"user": "polo", "password": "mala-mala-mala-1"}, headers={"X-Real-IP": "5.5.5.5"})
        self.assertEqual(self.call("POST", "/v1/app/login", {"user": "polo", "password": PW}, headers={"X-Real-IP": "5.5.5.5"})[0], 429)
        self.assertEqual(self.call("POST", "/v1/app/login", {"user": "polo", "password": PW}, headers={"X-Real-IP": "6.6.6.6"})[0], 200)


if __name__ == "__main__":
    unittest.main()
