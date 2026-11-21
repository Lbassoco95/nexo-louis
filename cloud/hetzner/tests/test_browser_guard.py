"""El navegador de Donna NUNCA paga ni teclea datos de tarjeta (Paso 1 de compras: Donna prepara, Polo paga con Apple Pay) y recuerda la página entre comandos."""
import os, sys, json, shutil, tempfile, subprocess, threading, unittest
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
SERV = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERV)
import browser_guard as G

PAG = {
    "/": b"<html><title>Cine</title><body><h1>Cartelera de prueba</h1><a href='/b'>Funci\xc3\xb3n 8pm</a></body></html>",
    "/b": ("<html><title>Asientos</title><body><h2>Selecciona asientos</h2><form onsubmit='return false'>"
           "<input name='nombre' placeholder='Tu nombre'><input name='cardnumber' autocomplete='cc-number' placeholder='Número de tarjeta'>"
           "<input name='cvv' placeholder='CVV'><button type='button' onclick=\"document.body.insertAdjacentHTML('beforeend','<p>CONTINUADO</p>')\">Continuar</button>"
           "<button type='button'>Pagar ahora</button><button type='button'>Comprar boletos</button></form></body></html>").encode(),
}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = PAG.get(self.path.split("?")[0])
        self.send_response(200 if b else 404); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(b or b"no")

    def log_message(self, *a):
        pass


class Guardia(unittest.TestCase):
    def test_clics_de_pago(self):
        for t in ("Pagar", "Pagar ahora", "Pagar con Apple Pay", "Confirmar compra", "Finalizar compra", "Realizar pago", "Comprar ahora", "Place order", "Pay now", "Continuar con PayPal", "Autorizar pago"):
            self.assertTrue(G.bloquea_clic(t), t)

    def test_clics_normales_pasan(self):
        for t in ("Comprar boletos", "Continuar", "Seleccionar asientos", "Ver horarios", "Cartelera", "Función 8:00 pm", "Siguiente", "Ciudad de México"):
            self.assertFalse(G.bloquea_clic(t), t)

    def test_campos_de_pago(self):
        casos = [dict(autocomplete="cc-number"), dict(name="cardnumber"), dict(placeholder="Número de tarjeta"), dict(name="cvv"), dict(aria_label="Código de seguridad"),
                 dict(label="Fecha de vencimiento"), dict(name="clabe"), dict(placeholder="Código SMS"), dict(name="otp"), dict(id="card-holder-name")]
        for c in casos:
            self.assertTrue(G.bloquea_campo(**c), c)

    def test_campos_normales_pasan(self):
        for c in (dict(name="nombre"), dict(name="email"), dict(placeholder="Buscar película"), dict(name="ciudad"), dict(type="text", label="Cantidad de boletos"), dict(placeholder="Código postal")):
            self.assertFalse(G.bloquea_campo(**c), c)


def _playwright_ok():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_playwright_ok(), "playwright (Python) no instalado en este entorno")
class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cmd(self, cmd):
        env = {**os.environ, "BROWSER_STATE_DIR": str(self.tmp), "BROWSER_LOG_FILE": str(self.tmp / "log")}
        r = subprocess.run([sys.executable, os.path.join(SERV, "browser_runner.py")], input=json.dumps(cmd), capture_output=True, text=True, timeout=90, env=env)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_flujo_completo(self):
        r = self.run_cmd({"cmd": "navigate", "url": self.base + "/", "wait_until": "domcontentloaded"})
        self.assertTrue(r["ok"]); self.assertIn("Cartelera de prueba", r["text"])                    # navegar ya devuelve el texto
        r = self.run_cmd({"cmd": "read"})
        self.assertIn("Cartelera de prueba", r["text"])                                               # y «leer» recuerda la página (antes salía en blanco)
        r = self.run_cmd({"cmd": "click", "selector": "Función 8pm", "by": "text"})
        self.assertTrue(r["ok"]); self.assertIn("Selecciona asientos", r["text"])
        r = self.run_cmd({"cmd": "fill", "selector": "input[name=nombre]", "value": "Polo"})
        self.assertTrue(r["ok"])
        for sel in ("input[name=cardnumber]", "input[name=cvv]"):                                     # datos de tarjeta: NO
            r = self.run_cmd({"cmd": "fill", "selector": sel, "value": "4111111111111111"})
            self.assertFalse(r["ok"]); self.assertEqual(r["code"], "bloqueado"); self.assertIn("Apple Pay", r["error"])
        r = self.run_cmd({"cmd": "click", "selector": "Pagar ahora", "by": "text"})                  # pagar: NO
        self.assertFalse(r["ok"]); self.assertEqual(r["code"], "bloqueado")
        r = self.run_cmd({"cmd": "click", "selector": "Comprar boletos", "by": "text"})              # seguir con la compra (sin pagar): sí
        self.assertTrue(r["ok"])

    def test_pasos_en_una_sola_sesion(self):
        r = self.run_cmd({"cmd": "steps", "url": self.base + "/", "wait_until": "domcontentloaded", "steps": [
            {"do": "click", "selector": "Función 8pm", "by": "text"}, {"do": "fill", "selector": "input[name=nombre]", "value": "Polo"},
            {"do": "click", "selector": "Continuar", "by": "text"}, {"do": "read"}]})
        self.assertTrue(r["ok"], r); self.assertEqual([p["ok"] for p in r["pasos"]], [True] * 4)
        self.assertIn("CONTINUADO", r["text"]); self.assertIn("Selecciona asientos", r["text"])

    def test_pasos_se_detienen_al_llegar_al_pago(self):
        r = self.run_cmd({"cmd": "steps", "url": self.base + "/b", "wait_until": "domcontentloaded", "steps": [
            {"do": "fill", "selector": "input[name=nombre]", "value": "Polo"}, {"do": "click", "selector": "Pagar ahora", "by": "text"}, {"do": "click", "selector": "Continuar", "by": "text"}]})
        self.assertFalse(r["ok"]); self.assertEqual(r["code"], "bloqueado")
        self.assertEqual(len(r["pasos"]), 2)                                                          # se detuvo en «Pagar»: el tercero ni se intentó
        self.assertTrue(r["pasos"][1].get("bloqueado"))


if __name__ == "__main__":
    unittest.main()
