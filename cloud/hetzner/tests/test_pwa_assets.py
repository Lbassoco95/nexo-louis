"""Los archivos de la app (PWA) no pueden quedar vacíos ni perder lo esencial: un sw.js vacío = sin notificaciones push."""
import json, re, unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "services" / "app"


class AssetsDeLaApp(unittest.TestCase):
    def test_service_worker_completo(self):
        t = (APP / "sw.js").read_text()
        self.assertGreater(len(t), 1500, "sw.js casi vacío")
        for esencial in ("addEventListener('push'", "addEventListener('notificationclick'", "showNotification",
                         "addEventListener('fetch'", "addEventListener('install'", "link"):
            self.assertIn(esencial, t, esencial)
        self.assertRegex(t, r"const V = 'louis-app-v\d+'")

    def test_index_y_manifest(self):
        self.assertGreater(len((APP / "index.html").read_text()), 20000)
        m = json.loads((APP / "manifest.webmanifest").read_text())
        self.assertEqual(m["start_url"], "/app/")
        self.assertGreater((APP / "icon.svg").stat().st_size, 100)
        self.assertGreater((APP / "widget-scriptable.js").stat().st_size, 500)

    def test_index_js_sin_marcas_rotas(self):
        s = (APP / "index.html").read_text()
        for marca in ("{{AGENT_NAME}}",):
            self.assertIn(marca, s)                                   # el servidor la sustituye al servir
        self.assertEqual(len(re.findall(r"<script>", s)), len(re.findall(r"</script>", s)))


if __name__ == "__main__":
    unittest.main()
