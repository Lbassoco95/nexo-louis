"""Correo y calendario de Outlook como fuentes del radar: evidencia para cerrar, reuniones en el radar de fechas, solo lectura y a prueba de fallas."""
import os, sys, tempfile, unittest
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_radar as R, donna_m365 as M
import louis_core as core
core.SPACE = TMP

HOY = date(2026, 11, 23)
AHORA = datetime(2026, 11, 23, 9, 0, tzinfo=timezone(timedelta(hours=-6)))


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))


class FakeM365:
    """Imita m365.py: tokens por tenant y respuestas de Graph. Registra las llamadas para comprobar que SOLO se lee."""
    def __init__(self, tenants=("kawiil", "yoltik"), falla=()):
        self.CREDS_DIR = Path(tempfile.mkdtemp(dir=TMP))
        for t in tenants:
            (self.CREDS_DIR / ("m365-%s-tokens.json" % t)).write_text("{}")
        self.falla, self.llamadas = set(falla), []

    def get_access_token(self, t):
        if t in self.falla:
            raise RuntimeError("token vencido")
        return "tok-" + t

    def graph_get(self, tok, path):
        self.llamadas.append((tok, path))
        t = tok[4:]
        if "sentitems" in path:
            return {"value": [{"id": "AAA" + t, "subject": "Carta de Lupita", "toRecipients": [{"emailAddress": {"name": "Correduría Pérez", "address": "c@x.mx"}}],
                               "sentDateTime": "2026-11-22T10:05:00", "bodyPreview": "<p>Adjunto la carta firmada</p>"}]}
        if "inbox" in path:
            return {"value": [{"id": "BBB" + t, "subject": "RE: Contrato Vizum", "from": {"emailAddress": {"name": "Fernando Martínez", "address": "f@x.mx"}},
                               "receivedDateTime": "2026-11-22T16:00:00", "bodyPreview": "Ya firmamos el contrato, gracias"}]}
        if "calendarview" in path:
            if "startDateTime=2026-11-1" in path or "2026-11-13" in path:      # ventana pasada
                return {"value": [{"id": "CCC" + t, "subject": "Junta con Fernando", "start": {"dateTime": "2026-11-20T11:00:00"}, "attendees": [{"emailAddress": {"name": "Fernando Martínez"}}],
                                   "responseStatus": {"response": "accepted"}}]}
            return {"value": [
                {"id": "D1" + t, "subject": "Junta Vizum", "start": {"dateTime": "2026-11-23T10:00:00"}, "responseStatus": {"response": "accepted"}},
                {"id": "D2" + t, "subject": "Comité Dazon", "start": {"dateTime": "2026-11-24T09:30:00"}, "responseStatus": {"response": "notResponded"}, "isOrganizer": False},
                {"id": "D3" + t, "subject": "Revisión mensual", "start": {"dateTime": "2026-11-27T12:00:00"}, "responseStatus": {"response": "accepted"}},
                {"id": "D4" + t, "subject": "Cancelada", "start": {"dateTime": "2026-11-23T12:00:00"}, "isCancelled": True},
                {"id": "D5" + t, "subject": "Rechazada", "start": {"dateTime": "2026-11-23T13:00:00"}, "responseStatus": {"response": "declined"}}]}
        return {"value": []}


class Fuentes(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_correo_y_calendario_como_evidencia(self):
        m = FakeM365()
        ev = M.correo_evidencias(10, m, AHORA)
        env = [e for e in ev if e["fuente"] == "correo_enviado"]
        self.assertEqual(len(env), 2)                                              # un tenant por cada cuenta
        self.assertIn("Correduría Pérez", env[0]["texto"])
        self.assertNotIn("<p>", env[0]["texto"])                                   # sin HTML
        self.assertTrue(any(e["fuente"] == "correo_recibido" and "firmamos" in e["texto"] for e in ev))
        cal = M.calendario_evidencias(10, m, AHORA)
        self.assertIn("Fernando Martínez", cal[0]["texto"])
        self.assertTrue(all(c[1].count("/me/") == 1 for c in m.llamadas))
        self.assertTrue(all("POST" not in c[1] and "send" not in c[1].lower() for c in m.llamadas))   # solo lectura

    def test_reuniones_para_el_radar_sin_canceladas_ni_rechazadas(self):
        its = M.calendario_abiertos(HOY, FakeM365(("kawiil",)))
        titulos = [i["titulo"] for i in its]
        self.assertEqual(titulos, ["Junta Vizum", "Responder invitación: Comité Dazon", "Revisión mensual"])
        self.assertTrue(all(i["cerrable"] is False for i in its))
        self.assertEqual(its[0]["hora"], "10:00")

    def test_un_tenant_caido_no_tumba_al_otro_y_sin_tokens_no_rompe(self):
        ev = M.correo_evidencias(10, FakeM365(falla=("yoltik",)), AHORA)
        self.assertEqual(len([e for e in ev if e["fuente"] == "correo_enviado"]), 1)
        with self.assertRaises(RuntimeError):
            M.correo_evidencias(10, FakeM365(falla=("kawiil", "yoltik")), AHORA)
        self.assertEqual(M.tenants_disponibles(FakeM365(())), [])

    def test_radar_avisa_reuniones_solo_hoy_y_manana_y_las_invitaciones_sin_responder(self):
        f = {"abiertos": {k: (lambda h, v=v: v) for k, v in {"calendario": M.calendario_abiertos(HOY, FakeM365(("kawiil",)))}.items()}, "evidencias": {}}
        n = []
        r = R.radar_fechas(f, HOY, lambda t, b="", **k: n.append(b))
        sec = {s["id"]: s for s in D.resumen_get(r["resumen"])["datos"]["secciones"]}
        self.assertEqual(set(sec), {"hoy", "manana"})                              # «Revisión mensual» (4 días) no avisa
        self.assertEqual(sec["hoy"]["items"][0]["cuando"], "hoy 10:00")
        self.assertEqual(sec["manana"]["items"][0]["texto"], "Responder invitación: Comité Dazon")

    def test_un_correo_enviado_propone_cerrar_el_pendiente_y_las_reuniones_no_se_cierran(self):
        m = FakeM365(("kawiil",))
        ab = {"agenda": lambda h: [{"tipo": "agenda", "ref": "- [ ] Enviar carta de Lupita a la Correduría", "titulo": "Enviar carta de Lupita a la Correduría", "fecha": "", "regulatorio": False}],
              "calendario": lambda h: M.calendario_abiertos(h, m)}
        f = {"abiertos": ab, "evidencias": {"correo": lambda d: M.correo_evidencias(d, m, AHORA)}}
        vistos = []
        r = R.procesar_cierres(core, f, lambda it, ev: vistos.append((it["tipo"], ev["fuente"])) or {"resuelto": True, "confianza": 0.9, "razon": "enviado"}, HOY, lambda *a, **k: None)
        self.assertEqual(r["propuestos"], 1)
        self.assertTrue(all(t == "agenda" for t, _ in vistos))                     # las reuniones nunca llegan al juez
        p = R.propuestas()[0]
        self.assertEqual(p["fuente"], "correo_enviado")
        self.assertIn("Correduría Pérez", p["evidencia"])

    def test_fuentes_por_defecto_suma_outlook_solo_si_hay_token(self):
        import louis_core
        orig = M.tenants_disponibles
        try:
            M.tenants_disponibles = lambda m=None: []
            self.assertNotIn("correo", R.fuentes_por_defecto(louis_core)["evidencias"])
            M.tenants_disponibles = lambda m=None: ["kawiil"]
            f = R.fuentes_por_defecto(louis_core)
            self.assertIn("correo", f["evidencias"])
            self.assertIn("calendario", f["abiertos"])
            self.assertIn("Correo Outlook (kawiil)", R.fuentes_conectadas())
        finally:
            M.tenants_disponibles = orig


if __name__ == "__main__":
    unittest.main()
