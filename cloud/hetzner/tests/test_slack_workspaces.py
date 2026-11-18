"""Slack con dos workspaces: Kawiil por default, Yoltik bajo demanda, y el resumen diario etiqueta Yoltik aparte."""
import os, sys, types, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), TZ="America/Mexico_City")


class _WC:                                    # slack_sdk de mentira (no hay red en las pruebas)
    def __init__(self, token): self.token = token
fake = types.ModuleType("slack_sdk"); fake.WebClient = _WC
sys.modules["slack_sdk"] = fake
import louis_core as core
core.HOME_OC = TMP
(TMP / "credentials").mkdir()
ENV = TMP / "credentials" / "slack.env"


class Workspaces(unittest.TestCase):
    def test_default_kawiil(self):
        ENV.write_text("SLACK_BOT_TOKEN=xoxb-kawiil\nSLACK_BOT_TOKEN_YOLTIK=xoxb-yoltik\n")
        core._slack_ws_set(None)
        c, err = core._slack_client()
        self.assertIsNone(err); self.assertEqual(c.token, "xoxb-kawiil")

    def test_yoltik_bajo_demanda(self):
        core._slack_ws_set("Yoltik")
        c, err = core._slack_client()
        self.assertEqual(c.token, "xoxb-yoltik")
        core._slack_ws_set("")                                   # vuelve al default
        self.assertEqual(core._slack_client()[0].token, "xoxb-kawiil")

    def test_yoltik_sin_token_da_error_claro(self):
        ENV.write_text("SLACK_BOT_TOKEN=xoxb-kawiil\n")
        core._slack_ws_set("yoltik")
        c, err = core._slack_client()
        self.assertIsNone(c); self.assertIn("setup-slack-yoltik", err)
        core._slack_ws_set(None)

    def test_yoltik_prefiere_token_de_usuario(self):
        ENV.write_text("SLACK_BOT_TOKEN=xoxb-kawiil\nSLACK_BOT_TOKEN_YOLTIK=xoxb-yoltik\nSLACK_USER_TOKEN_YOLTIK=xoxp-polo\n")
        core._slack_ws_set("yoltik")
        self.assertEqual(core._slack_client()[0].token, "xoxp-polo")
        core._slack_ws_set(None)
        self.assertEqual(core._slack_client()[0].token, "xoxb-kawiil")      # Kawiil no cambia

    def test_workspace_desconocido(self):
        core._slack_ws_set("otro")
        self.assertIn("desconocido", core._slack_client()[1])
        core._slack_ws_set(None)

    def test_herramientas_declaran_workspace(self):
        tools = {t["name"]: t for t in core.TOOLS_DEFINITION}
        for n in ("slack_resumen", "slack_canales", "slack_leer", "slack_dm_leer"):
            self.assertIn("workspace", tools[n]["input_schema"]["properties"], n)


class Digest(unittest.TestCase):
    def test_clientes_incluye_yoltik_etiquetado(self):
        import slack_digest as D
        D.SLACK_ENV = ENV
        ENV.write_text("SLACK_BOT_TOKEN=xoxb-kawiil\nSLACK_BOT_TOKEN_YOLTIK=xoxb-yoltik\n")
        self.assertEqual([e for e, _ in D._clients()], ["bot", "yoltik-bot"])
        ENV.write_text("SLACK_BOT_TOKEN=xoxb-kawiil\nSLACK_USER_TOKEN_YOLTIK=xoxp-p\nSLACK_BOT_TOKEN_YOLTIK=xoxb-y\n")
        self.assertEqual([e for e, _ in D._clients()], ["bot", "yoltik", "yoltik-bot"])   # usuario antes que bot
        ENV.write_text("SLACK_BOT_TOKEN=xoxb-kawiil\n")
        self.assertEqual([e for e, _ in D._clients()], ["bot"])


if __name__ == "__main__":
    unittest.main()
