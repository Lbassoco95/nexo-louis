"""Autoguardado de la junta de Escuchar: se guarda sola, se lista, se lee para retomarla, es privado por usuario y se limpia."""
import os, sys, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_borrador as B

T = lambda a, b="", t=0, s=0: {"o": "en", "a": a, "b": b, "t": t, "s": s, "f": 140, "l": 1, "m": 0}


class Borrador(unittest.TestCase):
    def setUp(self):
        for suf in ("", "-wal", "-shm"):
            (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
        D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))

    def test_guarda_actualiza_y_lee(self):
        B.guardar("abc123xyz", "polo", {"turnos": [T("hello", "hola", 1000)], "spk": [{"f0": 140, "hf": 0.2, "n": 3}], "names": {"0": "Fernando"}, "live_id": "j1", "duracion": 65})
        B.guardar("abc123xyz", "polo", {"turnos": [T("hello", "hola", 1000), T("ok", "vale", 9000, 1)], "duracion": 130})
        L = B.listar("polo")
        self.assertEqual((len(L), L[0]["n"], L[0]["duracion"], L[0]["ultimo"]), (1, 2, 130, "vale"))
        d = B.leer("abc123xyz", "polo")
        self.assertEqual(len(d["turnos"]), 2)
        self.assertEqual(d["turnos"][1]["s"], 1)

    def test_con_voces_y_nombres_vuelve_igual(self):
        B.guardar("juntaviva1", "polo", {"turnos": [T("hi", "hola")], "spk": [{"f0": 150, "hf": 0.3, "n": 5}], "names": {"0": "Laura"}, "live_id": "j9", "duracion": 10})
        d = B.leer("juntaviva1", "polo")
        self.assertEqual((d["spk"][0]["n"], d["names"], d["live_id"]), (5, {"0": "Laura"}, "j9"))

    def test_privado_por_usuario(self):
        B.guardar("secreto123", "polo", {"turnos": [T("x")]})
        self.assertIsNone(B.leer("secreto123", "otro"))
        with self.assertRaises(ValueError):
            B.guardar("secreto123", "otro", {"turnos": [T("y")]})
        self.assertEqual(B.descartar("secreto123", "otro"), 0)
        self.assertEqual(B.descartar("secreto123", "polo"), 1)

    def test_validaciones(self):
        for bid, datos in (("../x", {"turnos": [T("a")]}), ("abc123xyz", {"turnos": []}), ("abc123xyz", {}), ("ab", {"turnos": [T("a")]})):
            with self.assertRaises(ValueError):
                B.guardar(bid, "polo", datos)
        with self.assertRaises(ValueError):
            B.guardar("grande1234", "polo", {"turnos": [T("x" * 3000)] * 400})

    def test_limpia_los_viejos(self):
        B.guardar("viejo12345", "polo", {"turnos": [T("a")]})
        c = B._c(); c.execute("UPDATE escucha_borradores SET actualizado=1"); c.commit(); c.close()
        B.guardar("nuevo12345", "polo", {"turnos": [T("b")]})
        self.assertEqual([x["id"] for x in B.listar("polo")], ["nuevo12345"])


class RetomarGuardada(unittest.TestCase):
    def setUp(self):
        for suf in ("", "-wal", "-shm"):
            (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
        D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
        import donna_personas as P
        self.P = P
        voces = [{"n": 1, "nombre": "Fernando", "f0": 120, "hf": .2}, {"n": 2, "nombre": ""}]
        lineas = [{"t": "00:05", "voz": 1, "es": "Hola equipo", "orig": "Hello team", "idioma": "en"}, {"t": "01:10", "voz": 2, "es": "Perfecto", "orig": "", "idioma": "es"}]
        self.sid = P.guardar_sesion("Junta A", 130, voces, lineas, usuario="polo")["sesion_id"]

    def test_junta_guardada_vuelve_como_borrador(self):
        d = self.P.sesion_a_borrador(self.sid, "polo")
        self.assertEqual((len(d["turnos"]), d["reemplaza"], d["duracion"]), (2, self.sid, 130))
        self.assertEqual(d["turnos"][0]["a"], "Hello team")
        self.assertEqual(d["turnos"][0]["b"], "Hola equipo")
        self.assertEqual((d["turnos"][0]["t"], d["turnos"][1]["t"]), (5000, 70000))
        self.assertEqual(d["names"], {"0": "Fernando"})
        self.assertEqual(len(d["spk"]), 2)
        self.assertIsNone(self.P.sesion_a_borrador(self.sid, "otro"))

    def test_el_reemplazo_quita_la_vieja_y_pasa_lo_asociado(self):
        nuevo = self.P.guardar_sesion("Junta A completa", 400, [{"n": 1, "nombre": "Fernando"}], [{"t": "00:05", "voz": 1, "es": "Hola equipo", "orig": "", "idioma": "es"}], usuario="polo")["sesion_id"]
        self.assertFalse(self.P.reemplazar_sesion(self.sid, self.sid, "polo"))
        self.assertFalse(self.P.reemplazar_sesion(self.sid, nuevo, "otro"))
        self.assertTrue(self.P.reemplazar_sesion(self.sid, nuevo, "polo"))
        ids = [s["id"] for s in self.P.sesiones("polo")]
        self.assertEqual(ids, [nuevo])
        self.assertIsNone(self.P.sesion_a_borrador(self.sid, "polo"))


if __name__ == "__main__":
    unittest.main()
