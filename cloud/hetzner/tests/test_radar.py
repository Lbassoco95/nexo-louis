"""Donna proactiva: radar de fechas por etapas, cierres con evidencia, autonomía que Polo concede, aprendizaje y bitácora."""
import os, sys, tempfile, unittest, json
from pathlib import Path
from datetime import date
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_radar as R
import louis_core as core
core.SPACE = TMP

HOY = date(2026, 11, 23)


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))


def fuentes(abiertos, evidencias):
    return {"abiertos": {k: (lambda h, v=v: v) for k, v in abiertos.items()}, "evidencias": {k: (lambda d, v=v: v) for k, v in evidencias.items()}}


class Radar(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_fechas_por_etapa_y_sin_repetir(self):
        ab = {"kc": [{"tipo": "kc", "ref": "k1", "titulo": "Entregar propuesta Vizum", "fecha": "2026-11-23"},
                     {"tipo": "kc", "ref": "k2", "titulo": "Revisión de nómina", "fecha": "2026-11-26"},
                     {"tipo": "kc", "ref": "k3", "titulo": "Algo lejano", "fecha": "2027-03-01"}],
              "patio": [{"tipo": "patio", "ref": "p1", "titulo": "Contrato Dazon", "fecha": "2026-11-10"}]}
        enviados = []
        r = R.radar_fechas(fuentes(ab, {}), HOY, lambda t, b="", **k: enviados.append((t, b, k)))
        self.assertEqual(r["nuevos"], 3)                                    # hoy, ≤3 días y vencida; lo lejano no
        self.assertIn("vencida", enviados[0][1])
        sec = {s["id"]: s for s in D.resumen_get(r["resumen"])["datos"]["secciones"]}
        self.assertEqual(set(sec), {"vencida", "hoy", "3d"})
        r2 = R.radar_fechas(fuentes(ab, {}), HOY, lambda *a, **k: self.fail("no debe repetir el aviso"))
        self.assertEqual(r2["nuevos"], 0)
        r3 = R.radar_fechas(fuentes(ab, {}), date(2026, 11, 24), lambda t, b="", **k: enviados.append(b))   # al día siguiente k1 pasa a vencida y k2 a «mañana»... k2 estaba en 3d
        self.assertGreaterEqual(r3["nuevos"], 1)
        self.assertTrue(any(b["tipo"] == "radar" for b in R.bitacora_list()))                               # queda constancia de cada corrida

    def test_una_fuente_caida_no_tumba_el_radar(self):
        def mala(h):
            raise RuntimeError("sin conexión")
        f = {"abiertos": {"kc": mala, "agenda": lambda h: [{"tipo": "agenda", "ref": "l", "titulo": "Llamar", "fecha": "2026-11-23"}]}, "evidencias": {}}
        r = R.radar_fechas(f, HOY, lambda *a, **k: None)
        self.assertEqual((r["nuevos"], list(r["errores"])), (1, ["kc"]))


class Cierres(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.aplicados = []
        self.ap = lambda c, ci: self.aplicados.append(ci["titulo"]) or (True, "ok", "pendiente")
        self.juez_si = lambda it, ev: {"resuelto": True, "confianza": 0.9, "razon": "lo dice textual"}
        self.ab = {"agenda": [{"tipo": "agenda", "ref": "- [ ] Enviar carta de Lupita a la Correduría", "titulo": "Enviar carta de Lupita a la Correduría", "fecha": "", "regulatorio": False},
                              {"tipo": "agenda", "ref": "- [ ] Reporte CNBV trimestral Vizum", "titulo": "Reporte CNBV trimestral Vizum", "fecha": "", "regulatorio": True}]}
        self.ev = {"slack": [{"fuente": "slack", "texto": "Ya se envió la carta de Lupita a la Correduría ayer", "fecha": "2026-11-22"},
                             {"fuente": "slack", "texto": "Listo el reporte CNBV trimestral Vizum, quedó enviado", "fecha": "2026-11-22"}]}

    def _correr(self, juez=None, notif=None):
        return R.procesar_cierres(core, fuentes(self.ab, self.ev), juez or self.juez_si, HOY, notif or (lambda *a, **k: None), self.ap)

    def test_propone_con_evidencia_y_no_cierra_nada_sin_permiso(self):
        n = []
        r = self._correr(notif=lambda t, b="", **k: n.append((t, k)))
        self.assertEqual((r["propuestos"], r["auto"]), (2, 0))
        self.assertEqual(self.aplicados, [])                                 # por defecto solo propone
        p = R.propuestas()
        self.assertTrue(all("slack" in x["evidencia"] for x in p))
        self.assertEqual({x["clase"] for x in p}, {"agenda:bajo", "agenda:alto"})   # lo regulatorio queda en alto riesgo
        self.assertEqual(n[0][1]["link"], "cierres:")
        self.assertEqual(self._correr()["propuestos"], 0)                    # misma evidencia: no insiste

    def test_aprobar_y_autorizar_cierra_solas_solo_las_de_bajo_riesgo(self):
        self._correr()
        baja = [x for x in R.propuestas() if x["clase"] == "agenda:bajo"][0]
        alta = [x for x in R.propuestas() if x["clase"] == "agenda:alto"][0]
        with self.assertRaises(ValueError):
            R.resolver(core, alta["id"], "aprobar", autorizar=True, aplicar_fn=self.ap)      # alto riesgo: jamás autónomo
        r = R.resolver(core, baja["id"], "aprobar", autorizar=True, aplicar_fn=self.ap)
        self.assertTrue(r["ok"] and r["autonomia"] == "agenda:bajo")
        self.assertEqual(R.autonomia_get("agenda:bajo"), "auto")
        # nueva evidencia de otro pendiente de la misma clase: ahora se cierra sola
        self.ab["agenda"] = [{"tipo": "agenda", "ref": "x", "titulo": "Mandar presentación del diplomado al comité", "fecha": "", "regulatorio": False}]
        self.ev = {"juntas": [{"fuente": "juntas", "texto": "ya mandé la presentación del diplomado al comité", "fecha": "2026-11-23"}]}
        n = []
        r2 = self._correr(notif=lambda t, b="", **k: n.append(t))
        self.assertEqual(r2["auto"], 1)
        self.assertIn("Mandar presentación del diplomado al comité", self.aplicados)
        self.assertTrue(any("Cerré" in t for t in n))

    def test_rechazar_sube_la_exigencia_y_deshacer_cuenta_como_error(self):
        self._correr()
        baja = [x for x in R.propuestas() if x["clase"] == "agenda:bajo"][0]
        u0 = R.umbral("agenda:bajo")
        R.resolver(core, baja["id"], "rechazar")
        self.assertGreater(R.umbral("agenda:bajo"), u0)
        self.assertEqual(self._correr()["propuestos"], 0)                    # rechazada: no vuelve a proponerse
        alta = [x for x in R.propuestas() if x["clase"] == "agenda:alto"][0]
        (TMP / "AGENDA.md").write_text("- [ ] Reporte CNBV trimestral Vizum\n")
        R.aplicar(core, alta["id"], aplicar_fn=lambda c, ci: (True, "ok", ""))
        (TMP / "AGENDA.md").write_text("- [x] Reporte CNBV trimestral Vizum\n")
        self.assertTrue(R.resolver(core, alta["id"], "deshacer")["ok"])
        self.assertIn("[ ] Reporte", (TMP / "AGENDA.md").read_text())
        st = {s["clase"]: s for s in R.estadisticas()}
        self.assertEqual(st["agenda:alto"]["rechazadas"], 1)

    def test_el_juez_decide_y_exige_confianza(self):
        r = self._correr(juez=lambda it, ev: {"resuelto": True, "confianza": 0.4, "razon": "quizá"})
        self.assertEqual((r["propuestos"], r["descartados"]), (0, 2))
        r = self._correr(juez=lambda it, ev: {"resuelto": False, "confianza": 0.95})
        self.assertEqual(r["propuestos"], 0)

    def test_bitacora_y_dataset_para_aprender(self):
        self._correr()
        for x in R.propuestas():
            R.resolver(core, x["id"], "rechazar" if x["clase"] == "agenda:alto" else "aprobar", aplicar_fn=self.ap)
        ruta = str(TMP / "ds.jsonl")
        self.assertEqual(R.exportar_dataset(ruta), 2)
        et = sorted(json.loads(l)["etiqueta"] for l in open(ruta))
        self.assertEqual(et, [0, 1])
        self.assertIn("Cierres: cotejé", " ".join(b["resumen"] for b in R.bitacora_list()))
        self.assertIn("Lo último que vigilé", R.estado_texto())

    def test_kc_nunca_se_cierra_si_no_se_conoce_el_estado_hecho(self):
        class Cur:
            def execute(s, q, p=None): s.q = q
            def fetchone(s): return ("pendiente",)
            def fetchall(s): return [("pendiente",), ("en_proceso",)]
        class Conn:
            autocommit = False
            def cursor(s): return Cur()
            def close(s): pass
        class C:
            @staticmethod
            def _kawiil_central_pg(): return Conn(), None
        ok, txt, _ = R._aplicar_real(C, {"item_tipo": "kc", "item_ref": "k1", "titulo": "t"})
        self.assertFalse(ok)
        self.assertIn("No sé cuál es el estado", txt)


if __name__ == "__main__":
    unittest.main()
