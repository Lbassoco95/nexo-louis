"""«Hoy» como calendario: fechas en español, ignora etiquetas de captura, buckets y orden por fecha/hora."""
import os, sys, unittest
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
import louis_core as C

HOY = date(2026, 11, 19)  # jueves
AG = """# AGENDA
- [ ] Llamar a Fernando 16:30 hoy
- [ ] Mañana 20-nov 09:00 — revisar contrato Vizum
- [ ] Entrevista Abogado de Cumplimiento, 11 nov 2026 a las 16:00 hrs, sala Gather
- [ ] Dazon: Publicar perfil · [29-jun 16:21]
- [ ] Mañana 24-jun 11:00 — Pedir a Abelardo accesos
- [ ] Litigios: Habil vuelve el MARTES 24-NOV; crear carpeta
- [ ] Informe 2026-12-15 para el consejo
- [ ] Reunión viernes 10:00 con Carmen
- [ ] Cosa URGENTE sin fecha
- [ ] Tarea normal sin señal alguna
- [x] Ya hecha 19-nov
- [ ] Llamar a Fernando 16:30 hoy
"""


class T(unittest.TestCase):
    def test_parseo(self):
        p = lambda t: C.parse_agenda_fecha(t, HOY)
        self.assertEqual(p("Mañana 24-jun 11:00 — x"), (date(2026, 6, 24), "11:00"))
        self.assertEqual(p("11 nov 2026 a las 16:00 hrs"), (date(2026, 11, 11), "16:00"))
        self.assertEqual(p("Dazon · [29-jun 16:21]"), (None, None))                    # etiqueta de captura: no es vencimiento
        self.assertEqual(p("MARTES 24-NOV")[0], date(2026, 11, 24))
        self.assertEqual(p("2026-12-15")[0], date(2026, 12, 15))
        self.assertEqual(p("vence hoy")[0], HOY)
        self.assertEqual(p("mañana a las 9:00"), (date(2026, 11, 20), "09:00"))
        self.assertEqual(p("viernes 10:00")[0], date(2026, 11, 20))
        self.assertEqual(p("7 de octubre de 2026")[0], date(2026, 10, 7))
        self.assertEqual(p("5-ene")[0], date(2027, 1, 5))                              # sin año: el más cercano a hoy
        self.assertEqual(p("31-feb")[0], None)                                         # inválida
        self.assertEqual(p("recibido 7-oct; vuelve 13-oct")[0], date(2026, 10, 13))    # todas pasadas → la más reciente
        self.assertEqual(p("7-oct y 24-nov")[0], date(2026, 11, 24))                   # hay futura → la próxima

    def test_calendario(self):
        g = C.agenda_por_fechas(AG, HOY)
        t = lambda k: [x["texto"][:22] for x in g[k]]
        self.assertEqual(t("hoy"), ["Llamar a Fernando 16:3"])                          # duplicado quitado, hecha ignorada
        self.assertEqual(g["hoy"][0]["cuando"], "hoy · 16:30")
        self.assertEqual([x["fecha"] for x in g["manana"]], ["2026-11-20", "2026-11-20"])
        self.assertEqual([x["hora"] for x in g["manana"]], ["09:00", "10:00"])          # ordenadas por hora
        self.assertEqual([x["fecha"] for x in g["semana"]], ["2026-11-24"])
        self.assertEqual([x["fecha"] for x in g["proximas"]], ["2026-12-15"])
        self.assertEqual([x["fecha"] for x in g["vencidas"]], ["2026-11-11", "2026-06-24"])  # la más reciente primero
        self.assertIn("hace 8 d", g["vencidas"][0]["cuando"])
        self.assertEqual(t("sin_fecha"), ["Cosa URGENTE sin fecha", ])
        self.assertEqual(g["sin_fecha"][0]["cuando"], "urgente")
        self.assertEqual(sum(len(v) for v in g.values()), 8)                            # «Dazon» y «tarea normal» sin señal fuera… 

    def test_tope(self):
        txt = "\n".join("- [ ] tarea %d 20-nov" % i for i in range(300))
        self.assertEqual(sum(len(v) for v in C.agenda_por_fechas(txt, HOY, 50).values()), 50)


if __name__ == "__main__":
    unittest.main()
