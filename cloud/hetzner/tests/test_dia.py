"""«Tu día»: preferencias configurables, no molestar (cruza medianoche), entrega por canal, una vez por día, recordatorios y la fecha de las líneas de junta."""
import os, sys, tempfile, unittest
from pathlib import Path
from datetime import datetime, date
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x", APP_CHATS_DIR=str(TMP / "chats"))
import donna_db as D, donna_dia as DIA
from zoneinfo import ZoneInfo
TZ = ZoneInfo("America/Mexico_City")


class FakeCore:
    def __init__(self, hoy=None):
        self.hoy = hoy or []
        self.tg = []

    def _read_space_file(self, f):
        return ""

    def agenda_por_fechas(self, texto, hoy=None):
        return {"hoy": self.hoy, "vencidas": [{"texto": "x"}] * 3}

    def _encolar_notificacion(self, t, canal="telegram"):
        self.tg.append(t)


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    DIA._CAL.update(t=0.0, fecha="", items=[])


def a(h, m=0):
    return datetime(2026, 11, 23, h, m, tzinfo=TZ)


CAL = lambda hoy: [{"titulo": "Junta Vizum", "hora": "10:00", "fecha": hoy.isoformat(), "sin_responder": False}]


class Prefs(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_defaults_y_guardado_parcial(self):
        self.assertEqual(DIA.prefs_get()["manana"]["hora"], "08:00")
        DIA.prefs_set({"manana": {"hora": "07:15", "canales": ["push", "telegram"]}})
        p = DIA.prefs_get()
        self.assertEqual(p["manana"]["hora"], "07:15")
        self.assertEqual(p["manana"]["canales"], ["push", "telegram"])
        self.assertEqual(p["cierre"]["hora"], "18:30")                            # lo no tocado se conserva

    def test_validaciones(self):
        for patch in ({"manana": {"hora": "8am"}}, {"manana": {"canales": []}}, {"manana": {"canales": ["sms"]}}, {"recordar": {"minutos": 2}}, {"recordar": {"minutos": "x"}}):
            with self.assertRaises(ValueError):
                DIA.prefs_set(patch)
        DIA.prefs_set({"incluye": {"tareas": False}, "inventado": {"x": 1}})
        self.assertFalse(DIA.prefs_get()["incluye"]["tareas"])

    def test_no_molestar_cruza_medianoche(self):
        p = DIA.prefs_get()
        self.assertTrue(DIA.en_silencio(a(23), p))
        self.assertTrue(DIA.en_silencio(a(3), p))
        self.assertFalse(DIA.en_silencio(a(8), p))
        p["no_molestar"]["activo"] = False
        self.assertFalse(DIA.en_silencio(a(23), p))


class Entrega(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.n = []
        self.notif = lambda *x, **k: self.n.append((x, k))

    def test_texto_como_asistente(self):
        c = FakeCore([{"texto": "Enviar contrato — resp: Ana [capturado 2026-11-22] (de la junta «Escuchar 2026-11-22 21:14», 00:02)", "hora": ""}])
        d = DIA.armar(c, a(8), calendario=CAL)
        t = DIA.texto(d, "manana", a(8))
        self.assertIn("Junta Vizum", t)
        self.assertIn("Enviar contrato", t)
        self.assertNotIn("21:14", t)
        self.assertNotIn("capturado", t)
        self.assertIn("3 vencidas", t)
        self.assertTrue(t.rstrip().endswith("?"))

    def test_canales_elegidos(self):
        c = FakeCore()
        r = DIA.entregar(c, "manana", ["push", "telegram"], a(8), self.notif, usuarios=["admin"], calendario=CAL)
        self.assertEqual(r["canales"], ["push", "telegram"])
        self.assertEqual(len(self.n), 1)
        self.assertEqual(len(c.tg), 1)
        r = DIA.entregar(c, "manana", ["chat"], a(8), self.notif, usuarios=["admin"], calendario=CAL)
        self.assertEqual(r["canales"], ["chat"])
        self.assertEqual(len(self.n), 1)                                            # push no se repitió

    def test_tick_una_vez_al_dia_y_respeta_silencio(self):
        c = FakeCore()
        self.assertEqual(DIA.tick(c, a(7, 30), self.notif, ["admin"], CAL), [])      # antes de la hora
        self.assertEqual(DIA.tick(c, a(23), self.notif, ["admin"], CAL), [])         # en silencio
        r1 = DIA.tick(c, a(8, 1), self.notif, ["admin"], CAL)
        self.assertIn("manana", r1)
        r2 = DIA.tick(c, a(8, 2), self.notif, ["admin"], CAL)
        self.assertNotIn("manana", r2)
        DIA.prefs_set({"manana": {"activo": False}})

    def test_recordatorio_antes_de_la_hora_una_sola_vez(self):
        DIA.prefs_set({"manana": {"activo": False}})
        c = FakeCore()
        self.assertEqual(DIA.tick(c, a(8, 0), self.notif, ["admin"], CAL), [])      # faltan 120 min
        r = DIA.tick(c, a(9, 40), self.notif, ["admin"], CAL)                        # faltan 20 min (< 30)
        self.assertEqual(len(r), 1)
        self.assertIn("Junta Vizum", self.n[-1][0][1])
        self.assertEqual(DIA.tick(c, a(9, 45), self.notif, ["admin"], CAL), [])      # ya avisó


class FechaDeJunta(unittest.TestCase):
    def test_la_hora_del_titulo_de_la_transcripcion_no_es_la_hora_de_la_tarea(self):
        import louis_core as L
        f, h = L.parse_agenda_fecha("Enviar contrato · [capturado 2026-11-22] (de la junta «Escuchar 2026-11-22 21:14», 00:02)", date(2026, 11, 23))
        self.assertIsNone(h)
        f, h = L.parse_agenda_fecha("Enviar contrato · [capturado 2026-11-22] [junta «Escuchar 2026-11-22 21:14» 00:02]", date(2026, 11, 23))
        self.assertIsNone(h)


if __name__ == "__main__":
    unittest.main()
