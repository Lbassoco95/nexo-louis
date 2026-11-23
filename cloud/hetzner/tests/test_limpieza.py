"""Limpieza guiada de la AGENDA dentro de Donna: agrupa, no cierra lo sensible en bloque, cierra por línea exacta, deja bitácora y se puede deshacer."""
import os, sys, tempfile, unittest
from pathlib import Path
from datetime import date
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_limpieza as L, louis_core as core_real

HOY = date(2026, 11, 23)
AGENDA = """# AGENDA
- [ ] Yoltik · cartas de confidencialidad de 2 practicantes a firma por weetrust (pedido a Jesús Turcott el 12-oct)  · [auto 2026-10-15]
- [ ] Obtener comprobante de domicilio vigente del gas a nombre de Polo (≤3 meses desde 20-oct-2026)  · [auto 2026-10-20]
- [ ] Confirmar si comprobante de gas es vigente y está a nombre de Polo vigente domicilio comprobante  · [auto 2026-10-21]
- [ ] Litigios · Amparo D.A. 17/2026 (Vizum): sin movimiento visible desde el 17-ago; confirmar estado con Fernando  · [auto 2026-10-15]
- [ ] Litigios Amparo D.A. 17/2026 Vizum: confirmar estado con Fernando  · [auto 2026-11-03]
- [ ] Sylon Capital · validar RFC/CURP R24; folio de 3 clientes; paquete registro Banxico/CONDUSEF y escritura 77,071 boleta RPC  · [auto 2026-10-15]
- [ ] Sylon Capital: validar RFC/CURP R24 que no empatan  · [auto 2026-11-03]
- [ ] Mañana 24-jun — Fernando deja demanda IB Blockchain, va a revisar devolución cajas  · [auto 2026-06-23]
- [ ] Opción A: Congelar (solo lectura, histórico)  · [visto 2026-11-21]
- [ ] Opción B: Espejo (mantener sincronizado)  · [visto 2026-11-21]
- [ ] Revisar kawiil-central y evaluar convertirlo a app  · [auto 2026-06-22]
- [ ] Revisar cotización de JC  · [auto 2026-10-20]
- [ ] Revisar cotización de JC con Carmen  · [auto 2026-11-03]
- [ ] Llamar a Ana mañana 10:00 · [auto 2026-11-22]
- [x] Ya hecho antes
"""


class Fake:
    SPACE = TMP
    agenda_por_fechas = staticmethod(core_real.agenda_por_fechas)


def nuevo():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    (TMP / "AGENDA.md").write_text(AGENDA)


def por_texto(lista, frag):
    return next(x for x in lista if frag in x["texto"])


class Limpieza(unittest.TestCase):
    def setUp(self):
        nuevo()
        self.e = L.escanear(Fake, HOY)
        self.g = self.e["grupos"]

    def test_agrupa(self):
        dup = por_texto(self.g["duplicados"], "Revisar cotización de JC")                               # el viejo, cubierto por el nuevo
        self.assertEqual(dup["conserva"], "Revisar cotización de JC con Carmen")
        self.assertTrue(por_texto(self.g["caducados"], "Fernando deja demanda"))
        self.assertEqual(len(self.g["decisiones"]), 2)
        self.assertTrue(por_texto(self.g["viejos"], "kawiil-central"))
        self.assertNotIn("Ya hecho antes", str(self.g))
        self.assertNotIn("Llamar a Ana", str(self.g))                                                  # reciente: no se toca

    def test_duplicado_con_informacion_extra_no_se_marca_seguro(self):
        sylon = por_texto(self.g["duplicados"], "paquete registro Banxico")
        self.assertTrue(sylon["sensible"])
        self.assertIn("información que el nuevo no tiene", sylon["nota"])

    def test_sensible_marcado(self):
        self.assertTrue(por_texto(self.g["duplicados"], "Amparo")["sensible"])
        self.assertFalse(por_texto(self.g["duplicados"], "Revisar cotización de JC")["sensible"])

    def test_cerrar_por_linea_exacta_y_deshacer(self):
        i = por_texto(self.g["caducados"], "Fernando deja demanda")["id"]
        r = L.cerrar(Fake, [i])
        self.assertEqual(r["cerrados"], 1)
        t = (TMP / "AGENDA.md").read_text()
        self.assertIn("- [x] Mañana 24-jun", t)
        self.assertEqual(t.count("[x]"), 2)                                                           # solo esa línea (+ la ya hecha)
        self.assertEqual(L.cerrar(Fake, [i])["cerrados"], 0)                                          # idempotente
        self.assertEqual(L.deshacer(Fake, [i])["reabiertos"], 1)
        self.assertIn("- [ ] Mañana 24-jun", (TMP / "AGENDA.md").read_text())

    def test_id_invalido_no_hace_nada(self):
        self.assertEqual(L.cerrar(Fake, ["x", "../etc", ""])["cerrados"], 0)

    def test_conservar_oculta(self):
        i = por_texto(self.g["viejos"], "kawiil-central")["id"]
        L.conservar([i])
        self.assertNotIn("kawiil-central", str(L.escanear(Fake, HOY)["grupos"]))

    def test_a_seguimiento_crea_y_cierra(self):
        i = por_texto(self.g["viejos"], "kawiil-central")["id"]
        r = L.a_seguimiento(Fake, [i], en_horas=24)
        self.assertEqual((r["seguimientos"], r["cerrados"]), (1, 1))
        self.assertTrue(any("kawiil-central" in s["titulo"] for s in D.seg_list(("activo",), 0)))


if __name__ == "__main__":
    unittest.main()
