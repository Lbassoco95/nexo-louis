"""Personas que Donna conoce por lo que escucha: detección de nombres, guardado por sesión, asignación posterior, búsqueda y cruces."""
import os, sys, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"))
import donna_db as D, donna_personas as P


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))


def nombres(t, conocidos=()):
    return [(x["nombre"], x["tipo"]) for x in P.detectar_nombres(t, conocidos)]


class Deteccion(unittest.TestCase):
    def test_se_presenta(self):
        self.assertEqual(nombres("Hola, me llamo Fernando Martínez y soy de Kawiil."), [("Fernando Martínez", "presenta")])
        self.assertEqual(nombres("Buenas tardes, soy Sebastián Reyes."), [("Sebastián Reyes", "presenta")])
        self.assertEqual(nombres("Mi nombre es Viri"), [("Viri", "presenta")])

    def test_no_confunde_palabras_comunes(self):
        for t in ("Soy mexicano y vivo en Puebla", "Entonces, vamos a empezar", "Habla Español muy bien", "Bueno, ¿qué opinas?", "Soy Mexicano", "Pero, claro que sí"):
            self.assertEqual([x for x in nombres(t) if x[1] != "menciona"], [], t)

    def test_se_dirigen_a_alguien(self):
        self.assertIn(("Fernando", "dirige"), nombres("Fernando, ¿tú qué opinas del contrato?"))
        self.assertIn(("Viri", "dirige"), nombres("Gracias, Viri."))
        self.assertIn(("Laura", "dirige"), nombres("Entonces eso es todo, Laura?"))

    def test_menciona_conocidos(self):
        r = nombres("Dice Fernando que el contrato de Vizum ya salió", conocidos=["Fernando Martínez"])
        self.assertEqual(r, [("Fernando Martínez", "menciona")])

    def test_vocativo_desconocido_no_se_acepta(self):
        self.assertEqual(nombres("Zacarías, ven acá"), [])          # no es nombre común ni conocido: no se sugiere


class Base(unittest.TestCase):
    def setUp(self):
        limpiar()

    def _sesion(self, titulo="Junta Vizum"):
        voces = [{"n": 1, "nombre": "Fernando Martínez", "f0": 120, "hf": 0.2}, {"n": 2, "nombre": "Voz 2", "f0": 210, "hf": 0.3}, {"n": 3, "nombre": "Sebastián", "f0": 140, "hf": 0.25}]
        lineas = [{"t": "00:03", "voz": 1, "es": "Me llamo Fernando Martínez y vengo por el contrato de Vizum"},
                  {"t": "00:15", "voz": 2, "es": "Soy Laura Pérez, yo reviso la parte fiscal del contrato"},
                  {"t": "00:30", "voz": 3, "es": "Fernando, ¿tienes el borrador del contrato?"},
                  {"t": "00:40", "voz": 1, "es": "Sí, lo mando hoy"},
                  {"t": "00:50", "voz": 0, "es": "ruido de fondo sin voz"}]
        return P.guardar_sesion(titulo, 120, voces, lineas, mid="m1", archivo="t.md")

    def test_guardar_crea_personas_frases_y_sugeridos(self):
        r = self._sesion()
        nombres_ = sorted(p["nombre"] for p in r["personas"])
        self.assertEqual(nombres_, ["Fernando Martínez", "Sebastián"])               # «Voz 2» genérica NO crea persona
        sug = {s["nombre"]: s for s in r["sugeridos"]}
        self.assertEqual(sug["Laura Pérez"]["voz"], 2)                                 # se presentó la voz 2 → sugerencia con su voz
        fer = [p for p in P.listar() if p["nombre"] == "Fernando Martínez"][0]
        self.assertEqual((fer["intervenciones"], fer["sesiones"]), (2, 1))
        self.assertAlmostEqual(fer["voz"]["f0"], 120)

    def test_asignar_despues_y_reconocer_la_misma_persona(self):
        r = self._sesion()
        a = P.asignar(r["sesion_id"], 2, "Laura Pérez")
        self.assertEqual(a["frases"], 1)
        self.assertEqual(P.sesiones()[0]["sin_nombre"], [])                           # ya no queda ninguna voz sin nombre
        r2 = self._sesion("Segunda junta")
        fer = [p for p in P.listar() if p["nombre"] == "Fernando Martínez"]
        self.assertEqual(len(fer), 1)                                                  # misma persona, no duplicada
        self.assertEqual(fer[0]["sesiones"], 2)
        self.assertEqual(P.resolver("Fernando", crear=False), fer[0]["id"])           # por primer nombre único

    def test_busqueda_y_cruce_de_transcripciones(self):
        self._sesion()
        pid = P.resolver("Fernando Martínez", crear=False)
        hits = P.buscar("contrato Vizum")
        self.assertTrue(hits and any(h["persona"] == "Fernando Martínez" for h in hits))
        solo = P.buscar("contrato", persona_id=pid)
        self.assertTrue(all(h["persona_id"] == pid for h in solo))
        cr = P.cruce_transcripciones(pid)                                              # Sebastián le habla a Fernando: mención en frase ajena
        self.assertTrue(any("borrador" in x["texto"] for x in cr))

    def test_fusionar_y_eliminar(self):
        self._sesion()
        a = P.resolver("Fer"); b = P.resolver("Fernando Martínez", crear=False)
        P.asignar(P.sesiones()[0]["id"], 1, "Fer")
        d = P.fusionar(a, b)
        self.assertIn("Fer", d["alias"])
        self.assertEqual(P.resolver("Fer", crear=False), b)
        self.assertTrue(P.eliminar(b))
        self.assertIsNone(P.detalle(b))
        self.assertTrue(P.buscar("contrato"))                                          # las frases siguen en la transcripción, sin nombre

    def test_editar_validaciones(self):
        pid = P.resolver("Sebastián Reyes")
        d = P.editar(pid, empresa="Yoltik", rol="Socio", alias="Sebas, Seb")
        self.assertEqual((d["empresa"], d["alias"]), ("Yoltik", ["Sebas", "Seb"]))
        with self.assertRaises(ValueError):
            P.editar(pid, nombre="x")
        with self.assertRaises(ValueError):
            P.editar("nope", empresa="a")

    def test_cruce_kawiil_central_solo_lectura_y_sin_romper(self):
        class Cur:
            def __init__(s): s.q = ""
            def execute(s, q, p=None): s.q = q; s.p = p
            def fetchall(s):
                if "profiles" in s.q: return [("u1", "Fernando Martínez", "f@x.mx")]
                if "tasks" in s.q: return [("t1", "Revisar contrato", "pendiente", None)]
                return []
        class Conn:
            autocommit = False
            def cursor(s): return Cur()
            def close(s): pass
        class Core:
            @staticmethod
            def _kawiil_central_pg(): return Conn(), None
        r = P.cruce_kawiil_central(Core, "Fernando")
        self.assertEqual(r["usuarios"][0]["user_id"], "u1")
        self.assertEqual(r["tareas"][0]["titulo"], "Revisar contrato")
        class Mal:
            @staticmethod
            def _kawiil_central_pg(): return None, "❌ sin conexión"
        self.assertEqual(P.cruce_kawiil_central(Mal, "Fernando"), {"error": "sin conexión"})


if __name__ == "__main__":
    unittest.main()
