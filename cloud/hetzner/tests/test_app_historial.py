"""Regresión: el chat de la app NO debe tratar cada mensaje como «primera conversación del día»
(call_llm sin history_file devolvía el briefing completo en cada respuesta) y sus conversaciones
deben entrar a la destilación nocturna de memoria. Se fija la zona de México como en el servidor."""
import os, sys, json, time, tempfile, unittest
from pathlib import Path
os.environ["TZ"] = "America/Mexico_City"
time.tzset()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import louis_core as core
core.SPACE = TMP
import openclaw_gateway as g
g.APP_HISTORY_FILE = TMP / "app-history.jsonl"
REAL_CALL_LLM = core.call_llm  # las pruebas del chat lo sustituyen; las de respaldo necesitan el real


class T(unittest.TestCase):
    def test_sin_archivo_es_primer_mensaje_del_dia_el_bug_original(self):
        self.assertTrue(core.is_first_conversation_today(None))

    def test_la_app_pasa_historial_y_no_dispara_briefing(self):
        seen = {}

        def fake(api_key, sys_prompt, history, user_msg, history_file=None):
            seen.update(hf=history_file, first=core.is_first_conversation_today(history_file), sys=sys_prompt)
            return "Aquí estoy.", "stub"
        core.call_llm, core.load_anthropic_key = fake, (lambda: "k")
        g._RESULTS["t1"] = {"t0": 0, "done": False}
        g._CHAT_SLOTS.acquire()
        g._chat_worker("t1", "¿Ya estás ahí?", [])
        self.assertEqual(seen["hf"], g.APP_HISTORY_FILE)
        self.assertFalse(seen["first"])
        self.assertNotEqual(core._build_ollama_task_instruction("¿Ya estás ahí?", [], True),
                            core._build_ollama_task_instruction("¿Ya estás ahí?", [], seen["first"]))
        roles = [json.loads(l)["role"] for l in g.APP_HISTORY_FILE.read_text().splitlines()]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertIn("CANAL ACTUAL: app móvil", seen["sys"])
        self.assertNotIn("app móvil", core.load_system_prompt(channel="telegram"))

    def test_la_destilacion_nocturna_incluye_la_app(self):
        hoy = core.datetime.now(core.TZ_CDMX).strftime("%Y-%m-%d")
        tr = core._distill_collect_today(hoy)
        self.assertIn("Polo: ¿Ya estás ahí?", tr)
        self.assertIn(core.AGENT_NAME + ": Aquí estoy.", tr)



class FallbackTest(unittest.TestCase):
    """Si DeepSeek falla, «¿Ya estás ahí?» NO debe recibir el briefing completo."""
    def setUp(self):
        core.call_llm = REAL_CALL_LLM
        core.load_anthropic_key = lambda: "k"
        core.build_operational_snapshot = lambda compact=True: "SNAPSHOT con pendientes"
        core.call_deepseek = lambda *a, **k: "(error DeepSeek HTTP 402: Insufficient Balance)"
        core.should_deterministic_operational_response = lambda m, h=None: False
        core.try_deterministic_reminder = lambda m: None
        core.try_deterministic_memory_write = lambda m, strict=True: None
        core.try_deterministic_task_capture = lambda m: None
        core.try_deterministic_coach_capture = lambda m: None

    def _call(self, msg):
        # la ruta por defecto es DeepSeek (gateway /v1/status: default_chat=deepseek)
        return core.call_llm("k", "SYS", [], msg, history_file=g.APP_HISTORY_FILE)

    def test_haiku_responde_si_deepseek_falla(self):
        core.call_haiku = lambda *a, **k: "Aquí estoy, Polo."
        resp, model = self._call("¿Ya estás ahí?")
        self.assertEqual((resp, model), ("Aquí estoy, Polo.", "haiku-fallback"))

    def test_sin_ningun_modelo_aviso_corto_no_briefing(self):
        def boom(*a, **k): raise RuntimeError("sin créditos")
        core.call_haiku = boom
        resp, model = self._call("¿Ya estás ahí?")
        self.assertEqual(model, "chat-error")
        self.assertIn("no respondió", resp)
        self.assertNotIn("VENCE", resp)

    def test_si_pidio_pendientes_el_briefing_si_sirve(self):
        def boom(*a, **k): raise RuntimeError("sin créditos")
        core.call_haiku = boom
        resp, model = self._call("Resumen de mis pendientes")
        self.assertEqual(model, "deepseek-fallback")


if __name__ == "__main__":
    unittest.main()
