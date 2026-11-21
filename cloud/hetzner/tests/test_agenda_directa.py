"""«¿Qué tengo hoy?» se responde con la AGENDA real (no con un modelo sin herramientas) y DeepSeek nunca deja ver su markup de herramientas."""
import os, sys, tempfile, unittest
from pathlib import Path
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(OPENCLAW_SPACES=str(TMP / "spaces"), PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_HOME=str(TMP / "oc"), TZ="America/Mexico_City")
import louis_core as C
C.SPACE = TMP
hoy = datetime.now(C.TZ_CDMX).date()
d = lambda n: (hoy.fromordinal(hoy.toordinal() + n)).strftime("%d-%b").lower().replace("jan", "ene").replace("apr", "abr").replace("aug", "ago").replace("dec", "dic")
(TMP / "AGENDA.md").write_text("\n".join([
    "# AGENDA", "- [ ] Llamar a Fernando 16:30 hoy", "- [ ] Revisar contrato Vizum %s 09:00" % d(1), "- [ ] Reunión con Carmen %s 10:00" % d(3),
    "- [ ] Viejo pendiente %s" % d(-30), "- [ ] Algo URGENTE sin fecha", "- [x] Ya hecha hoy"]))


class T(unittest.TestCase):
    def test_hoy_con_datos_reales(self):
        r = C.try_deterministic_agenda_query("¿Qué tengo hoy?")
        self.assertIn("Llamar a Fernando", r)
        self.assertIn("16:30", r)
        self.assertIn("Mañana", r)                       # y lo de mañana, para contexto
        self.assertIn("1 vencidos", r.replace("**", ""))   # avisa lo vencido sin volcarlo
        self.assertNotIn("Ya hecha", r)                  # lo cerrado no cuenta
        self.assertIn("Fuente: tu AGENDA", r)            # honesto sobre de dónde sale

    def test_otras_preguntas(self):
        self.assertIn("Reunión con Carmen", C.try_deterministic_agenda_query("¿Qué vence esta semana?"))
        self.assertIn("Revisar contrato", C.try_deterministic_agenda_query("qué tengo mañana"))
        self.assertIn("Viejo pendiente", C.try_deterministic_agenda_query("¿qué está vencido?"))

    def test_no_secuestra_otras_cosas(self):
        for m in ("Recuérdame llamar a Fernando mañana a las 9", "Hazme un informe de lo que tengo hoy con todos los clientes y su estado detallado, por favor",
                  "hola", "¿Cómo va Yoltik?"):
            self.assertIsNone(C.try_deterministic_agenda_query(m), m)

    def test_ruta_completa(self):
        resp, tag = C._call_llm_impl("k", "sys", [], "¿Qué tengo hoy?")
        self.assertEqual(tag, "agenda-directa")
        self.assertIn("Llamar a Fernando", resp)

    def test_deepseek_con_markup_de_herramienta_se_reintenta_con_haiku(self):
        fuga = "Déjame checar la hora real.\n<｜DSML｜calls>\n<｜DSML｜invoke name=\"reloj\">\n</｜DSML｜invoke>"
        C.call_deepseek = lambda *a, **k: fuga
        C.call_claude = lambda *a, **k: "Son las 9:00. Respuesta real con herramientas."
        resp, tag = C._call_llm_impl("k", "sys", [], "cuéntame algo interesante de la semana pasada en general")
        self.assertEqual(tag, "haiku-tools")
        self.assertNotIn("DSML", resp)

    def test_si_haiku_tambien_falla_se_limpia_el_markup(self):
        fuga = "Buenos días, Polo. Esto sí es contenido útil de bastante longitud para pasar el filtro.\n<｜DSML｜calls>\n<｜DSML｜invoke name=\"reloj\">"
        C.call_deepseek = lambda *a, **k: fuga
        C.call_claude = lambda *a, **k: ""
        resp, tag = C._call_llm_impl("k", "sys", [], "cuéntame algo interesante de la semana pasada en general")
        self.assertNotIn("DSML", resp)
        self.assertIn("contenido útil", resp)


if __name__ == "__main__":
    unittest.main()


class Compras(unittest.TestCase):
    def test_boton_de_pago_solo_en_comercios_permitidos(self):
        ok = "Listo.\n[[pagar:https://www.cinemex.com/compra/123|Cinemex · Dune · hoy 8:30 pm · 2 boletos · $190]]"
        self.assertEqual(C.sanitize_pay_markers(ok), ok)
        ok2 = "[[pagar:https://tickets.cinepolis.com/x?y=1|Cinépolis · 2 boletos]]"
        self.assertEqual(C.sanitize_pay_markers(ok2), ok2)

    def test_enlaces_falsos_no_se_vuelven_boton(self):
        for u in ("https://cinemex.com.evil.com/pago", "http://www.cinemex.com/compra", "https://pagos-seguros.example/cinepolis.com", "https://evil.com/?u=cinemex.com"):
            r = C.sanitize_pay_markers("[[pagar:%s|Cinemex 2 boletos]]" % u)
            self.assertNotIn("[[pagar:", r, u)
            self.assertIn("NO verificado", r, u)

    def test_marcador_mal_formado(self):
        r = C.sanitize_pay_markers("[[pagar:javascript:alert(1)|x]] y [[pagar:]]")
        self.assertNotIn("[[pagar:javascript", r)

    def test_llega_por_la_ruta_de_herramientas(self):
        for m in ("Necesito comprar boletos del cine", "¿qué hay en cartelera en Cinemex hoy?", "compra entradas para el concierto"):
            self.assertTrue(C.needs_tools(m), m)
