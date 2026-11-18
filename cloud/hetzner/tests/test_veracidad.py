"""Freno a datos inventados: una herramienta que falla se le MUESTRA al modelo como fallo, y una respuesta con
cifras de Kawiil Central que no vienen de una consulta real lleva un aviso visible. Incluye el texto de la
captura real donde Donna presentó «ESTADO REAL DE KAWIIL CENTRAL» sin haber consultado nada."""
import os, sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), TZ="America/Mexico_City")
import louis_core as core

# Texto real de la captura (Donna, modelo haiku-tools, sin ninguna consulta a Kawiil Central)
CAPTURA = """🚨 DIAGNÓSTICO EJECUTIVO — 17 NOV 2026, 07:53 CDMX
ESTADO REAL DE KAWIIL CENTRAL
| Métrica | Valor | Status |
| Total tareas | 1,325 | ⚫ Muchas |
| Tareas "vivas" (no completadas) | ~100 | ⚠️ Manejable |
| Tareas VENCIDAS (hoy o antes) | 50 | 🔴 CRÍTICO |
| Proyectos activos | 257 | ⚫ Demasiados |
"""


class Guardia(unittest.TestCase):
    def test_la_captura_real_lleva_aviso_si_no_se_consulto(self):
        out = core.guard_unverified_business_data(CAPTURA, [], [])
        self.assertTrue(out.startswith("⚠️ **Esta respuesta no consultó Kawiil Central.**"))
        self.assertTrue(out.endswith(CAPTURA))                       # el texto original queda intacto debajo

    def test_aviso_distinto_si_la_herramienta_fallo(self):
        out = core.guard_unverified_business_data(CAPTURA, [], ["kawiil_central_estado"])
        self.assertIn("**No pude consultar Kawiil Central** (kawiil_central_estado falló)", out)

    def test_sin_aviso_si_una_consulta_real_funciono(self):
        self.assertEqual(core.guard_unverified_business_data(CAPTURA, ["kawiil_central_tareas"], []), CAPTURA)

    def test_no_molesta_en_respuestas_normales(self):
        for t in ("Aquí estoy, Polo. ¿En qué te ayudo?",
                  "Kawiil Central: tienes 3 pendientes.",                                  # pocas cifras, sin «estado real»
                  "El 15-oct, 16-oct y 17-oct hubo juntas con Kawiil Central, 2026 y 2027",  # fechas, sin afirmación de datos
                  ""):
            self.assertEqual(core.guard_unverified_business_data(t, [], []), t)

    def test_clasificacion_de_fallos(self):
        for f in ("❌ Falta KAWIIL_CENTRAL_DATABASE_URL — corre setup", "🔒 SQL bloqueado", "(error llamando)",
                  "🌐 *Kawiil Central*\n  Supabase URL: ✗ (falta)"):
            self.assertTrue(core._tool_failed(f), f)
        for ok in ("✅ 12 filas (max 100):\n```\na | b\n```", "Listo", ""):
            self.assertFalse(core._tool_failed(ok), ok)


class CicloDeHerramientas(unittest.TestCase):
    def test_el_modelo_ve_el_fallo_y_se_registra(self):
        bodies = []
        respuestas = [
            {"content": [{"type": "tool_use", "id": "t1", "name": "kawiil_central_estado", "input": {}}],
             "stop_reason": "tool_use", "usage": {}},
            {"content": [{"type": "text", "text": "No pude consultar Kawiil Central: falta la base de datos."}],
             "stop_reason": "end_turn", "usage": {}},
        ]
        core.http_post_json = lambda url, headers, body, timeout=120: (bodies.append(json.loads(json.dumps(body))) or respuestas.pop(0))
        core.execute_tool = lambda name, args: "❌ Falta KAWIIL_CENTRAL_DATABASE_URL — corre setup-kawiil-central-creds.sh"
        core._TURN.ok, core._TURN.failed = [], []
        out = core.call_claude("k", "SYS", [], "dame el diagnóstico ejecutivo de kawiil central")
        resultado_visto = bodies[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("❌ Falta KAWIIL_CENTRAL_DATABASE_URL", resultado_visto)
        self.assertIn("ESTA HERRAMIENTA (kawiil_central_estado) FALLÓ", resultado_visto)
        self.assertIn("Prohibido inventar cifras", resultado_visto)
        self.assertEqual(core._TURN.failed, ["kawiil_central_estado"])
        self.assertEqual(core._TURN.ok, [])
        self.assertTrue(out.startswith("No pude consultar"))

    def test_las_herramientas_que_funcionan_no_se_marcan(self):
        respuestas = [
            {"content": [{"type": "tool_use", "id": "t1", "name": "kawiil_central_tareas", "input": {}}],
             "stop_reason": "tool_use", "usage": {}},
            {"content": [{"type": "text", "text": "Hay 3 tareas."}], "stop_reason": "end_turn", "usage": {}},
        ]
        seen = []
        core.http_post_json = lambda url, headers, body, timeout=120: (seen.append(json.dumps(body)) or respuestas.pop(0))
        core.execute_tool = lambda name, args: "✅ 3 filas (max 100):\n```\nid | titulo\n```"
        core._TURN.ok, core._TURN.failed = [], []
        core.call_claude("k", "SYS", [], "tareas de kawiil central")
        self.assertEqual((core._TURN.ok, core._TURN.failed), (["kawiil_central_tareas"], []))
        self.assertNotIn("FALLÓ", seen[1])


class PuntoDeEntrada(unittest.TestCase):
    def test_call_llm_antepone_el_aviso(self):
        orig = core._call_llm_impl
        core._call_llm_impl = lambda *a, **k: (CAPTURA, "haiku-tools")
        try:
            resp, modelo = core.call_llm("k", "SYS", [], "diagnóstico")
        finally:
            core._call_llm_impl = orig
        self.assertEqual(modelo, "haiku-tools")
        self.assertTrue(resp.startswith("⚠️ **Esta respuesta no consultó Kawiil Central.**"))

    def test_call_llm_no_arrastra_herramientas_de_un_turno_anterior(self):
        core._TURN.ok = ["kawiil_central_tareas"]            # residuo de un turno previo en este mismo hilo
        orig = core._call_llm_impl
        core._call_llm_impl = lambda *a, **k: (CAPTURA, "deepseek")
        try:
            resp, _ = core.call_llm("k", "SYS", [], "diagnóstico")
        finally:
            core._call_llm_impl = orig
        self.assertTrue(resp.startswith("⚠️"))               # el residuo NO lo exime del aviso


class Credenciales(unittest.TestCase):
    def test_lee_del_archivo_que_sobrevive_a_los_deploys(self):
        home = Path(tempfile.mkdtemp())
        (home / "credentials").mkdir()
        (home / "credentials" / "kawiil-central.env").write_text(
            '# generado\nKAWIIL_CENTRAL_SUPABASE_URL="https://abc.supabase.co"\nKAWIIL_CENTRAL_SUPABASE_KEY="sb_secret_x"\n'
            'KAWIIL_CENTRAL_DATABASE_URL="postgresql://u:p@h:6543/postgres"\n')
        viejos = (core.HOME_OC, core.ANTHROPIC_ENV_FILE, dict(os.environ))
        core.HOME_OC, core.ANTHROPIC_ENV_FILE = home, home / ".env-que-no-existe"
        for k in [k for k in os.environ if k.startswith(("KAWIIL_CENTRAL_", "KAWIIL_OS_"))]:
            os.environ.pop(k)
        try:
            _, sup_url, sup_key, db_url = core._kawiil_central_creds()
        finally:
            core.HOME_OC, core.ANTHROPIC_ENV_FILE = viejos[0], viejos[1]
        self.assertEqual((sup_url, sup_key, db_url),
                         ("https://abc.supabase.co", "sb_secret_x", "postgresql://u:p@h:6543/postgres"))


if __name__ == "__main__":
    unittest.main()
