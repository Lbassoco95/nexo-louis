"""Rescate en el servidor (no volver a perder una junta) y revisión final al terminar (cada hallazgo con cita verificada por código)."""
import os, sys, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_rescate as R, donna_borrador as B, donna_junta as J, donna_personas as P, donna_tareas as T


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))


class Rescate(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_se_guarda_frase_a_frase_y_vuelve_como_turnos(self):
        for k, t, x in (("in", 1000, "Hello team"), ("out", 1500, "Hola equipo"), ("in", 9000, "你好"), ("out", 9500, "Hola"), ("out", 12000, "Frase suelta")):
            self.assertTrue(R.agregar("resc123abc", "polo", k, t, x))
        d = R.a_borrador("resc123abc", "polo")
        self.assertEqual([(x["o"], x["a"], x["b"]) for x in d["turnos"]], [("en", "Hello team", "Hola equipo"), ("zh", "你好", "Hola"), ("es", "Frase suelta", "")])
        self.assertEqual(d["duracion"], 12)
        L = R.listar("polo")
        self.assertEqual((len(L), L[0]["n"]), (1, 3))

    def test_privado_y_validado(self):
        R.agregar("resc123abc", "polo", "in", 0, "secreto")
        self.assertIsNone(R.a_borrador("resc123abc", "otro"))
        self.assertFalse(R.agregar("resc123abc", "otro", "in", 0, "intruso"))
        for rid, k, t in (("../x", "in", "a"), ("resc123abc", "zzz", "a"), ("resc123abc", "in", "   ")):
            self.assertFalse(R.agregar(rid, "polo", k, 0, t))
        self.assertEqual(R.descartar("resc123abc", "otro"), 0)
        self.assertEqual(R.descartar("resc123abc", "polo"), 1)
        self.assertEqual(R.listar("polo"), [])

    def test_se_limpia_a_los_30_dias(self):
        R.agregar("vieja12345", "polo", "in", 0, "algo")
        c = R._c(); c.execute("UPDATE escucha_rescate SET actualizado=1"); c.commit(); c.close()
        self.assertEqual(R.listar("polo"), [])

    def test_si_ya_hay_borrador_con_ese_id_no_se_duplica_en_la_lista(self):
        R.agregar("mismo12345", "polo", "in", 0, "hola")
        B.guardar("mismo12345", "polo", {"turnos": [{"o": "en", "a": "hola", "b": "", "t": 0}]})
        con_b = {b["id"] for b in B.listar("polo")}
        self.assertEqual([x for x in R.listar("polo") if x["id"] not in con_b], [])


class RevisionFinal(unittest.TestCase):
    def setUp(self):
        limpiar()
        voces = [{"n": 1, "nombre": "Fernando"}, {"n": 2, "nombre": ""}]
        lineas = [{"t": "00:05", "voz": 1, "es": "La cotización es de 120,000 pesos y vence el 5 de diciembre", "idioma": "es"},
                  {"t": "01:10", "voz": 2, "es": "Yo mando el contrato la próxima semana", "idioma": "es"}]
        self.sid = P.guardar_sesion("Junta A", 130, voces, lineas, usuario="polo")["sesion_id"]
        c = J._c()
        c.execute("INSERT INTO junta_chat(live_id,sesion_id,ts,rol,texto) VALUES('j1',?,1,'polo','anota el nombre de la ley')", (self.sid,))
        c.execute("INSERT INTO junta_chat(live_id,sesion_id,ts,rol,texto) VALUES('j1',?,2,'donna','No capturé el nombre exacto. ¿Me lo confirmas?')", (self.sid,))
        c.commit(); c.close()

        class Core: SPACE = TMP
        self.core = Core

    def llm(self, texto):
        return lambda sistema, msgs: {"stop_reason": "end_turn", "content": [{"type": "text", "text": texto}]}

    def test_hallazgos_con_cita_verificada(self):
        j = '{"hallazgos":[{"tipo":"cifra","texto":"Confirmar el monto","cita":"La cotización es de 120,000 pesos y vence el 5 de diciembre"},{"tipo":"cifra","texto":"Inventado","cita":"el precio es 900 mil dólares"}]}'
        r = J.revision_final(self.core, self.sid, "polo", llm=self.llm(j))
        tipos = [h["tipo"] for h in r["hallazgos"]]
        self.assertIn("sin_confirmar", tipos)            # Donna no pudo confirmar algo
        self.assertIn("nota_pedida", tipos)              # pidió anotar y no hay notas
        self.assertIn("voz_sin_nombre", tipos)           # Voz 2 sin nombre
        textos = " ".join(h["texto"] for h in r["hallazgos"])
        self.assertIn("Confirmar el monto", textos)
        self.assertNotIn("Inventado", textos)             # la cita no existe: el código lo descarta
        self.assertEqual(J.revision(self.sid)["estado"], "lista")

    def test_si_el_modelo_falla_igual_quedan_los_hallazgos_de_codigo(self):
        def mal(*a, **k): raise RuntimeError("sin modelo")
        r = J.revision_final(self.core, self.sid, "polo", llm=mal)
        self.assertTrue(any(h["tipo"] == "sin_confirmar" for h in r["hallazgos"]))
        self.assertEqual(J.revision(self.sid)["estado"], "lista")

    def test_sin_revision_devuelve_none(self):
        self.assertIsNone(J.revision("nada"))


if __name__ == "__main__":
    unittest.main()
