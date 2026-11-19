"""Conversaciones guardadas: crear, titular, listar, buscar, renombrar, borrar, aislar por usuario y validar ids."""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
os.environ.update(PUSH_STATE_DIR=tempfile.mkdtemp())
import app_chats as C


class T(unittest.TestCase):
    def test_flujo(self):
        cid = C.nuevo_id()
        self.assertEqual(C.guardar("polo", cid, []), {"id": cid, "vacio": True})
        self.assertEqual(C.listar("polo"), [])
        r = C.guardar("polo", cid, [{"r": "u", "t": "¿Cómo va Yoltik esta semana con los pendientes de todo el equipo?", "ts": 1},
                                    {"r": "a", "t": "Hay 35 proyectos", "ts": 2}, {"r": "x", "t": "basura"}, {"r": "u", "t": "  "}])
        self.assertTrue(r["titulo"].startswith("¿Cómo va Yoltik") and r["titulo"].endswith("…"))
        self.assertEqual(len(C.obtener("polo", cid)["msgs"]), 2)
        self.assertEqual(oct(os.stat(C._path("polo", cid)).st_mode & 0o777), "0o600")
        self.assertEqual(C.listar("polo", "35 proyectos")[0]["id"], cid)
        self.assertEqual(C.listar("polo", "inexistente"), [])
        C.renombrar("polo", cid, "Yoltik semanal")
        C.guardar("polo", cid, C.obtener("polo", cid)["msgs"] + [{"r": "u", "t": "gracias"}])   # re-guardar no pisa el título manual
        self.assertEqual(C.obtener("polo", cid)["titulo"], "Yoltik semanal")
        self.assertEqual(C.listar("polo")[0]["n"], 3)
        self.assertEqual(C.listar("otro"), [])                                                  # aislado por usuario
        self.assertIsNone(C.obtener("otro", cid))
        self.assertTrue(C.borrar("polo", cid)); self.assertFalse(C.borrar("polo", cid))

    def test_etiqueta_de_tablero(self):
        cid = C.nuevo_id()
        C.guardar("t1", cid, [{"r": "u", "t": "¿cuántos?", "art": "a0123456789"}, {"r": "a", "t": "uno", "art": "a0123456789"}, {"r": "u", "t": "x", "art": "../mal"}])
        m = C.obtener("t1", cid)["msgs"]
        self.assertEqual([x.get("art") for x in m], ["a0123456789", "a0123456789", None])      # etiqueta válida se conserva, inválida se descarta

    def test_validacion(self):
        for malo in ("../x", "c1", "", "cZZZZZZZZZZ"):
            with self.assertRaises(ValueError): C.guardar("polo", malo, [{"r": "u", "t": "x"}])
        with self.assertRaises(ValueError): C.renombrar("polo", C.nuevo_id(), "x")
        with self.assertRaises(ValueError): C.renombrar("polo", C.nuevo_id(), "  ")

    def test_orden_y_poda(self):
        a, b = C.nuevo_id(), C.nuevo_id()
        C.guardar("o2", a, [{"r": "u", "t": "uno"}]); C.guardar("o2", b, [{"r": "u", "t": "dos"}])
        import time; time.sleep(0.01)
        C.guardar("o2", a, [{"r": "u", "t": "uno"}, {"r": "a", "t": "otra"}])
        self.assertEqual([x["id"] for x in C.listar("o2")][0], a)                               # la más reciente primero


if __name__ == "__main__":
    unittest.main()
