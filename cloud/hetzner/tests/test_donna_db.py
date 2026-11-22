"""Base de datos de Donna: avisos interactivos, resúmenes guardados como datos, seguimientos independientes de las notificaciones."""
import os, sys, json, time, tempfile, unittest
from pathlib import Path
from datetime import date
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_seg as S, donna_resumen as R, louis_push as P
import louis_core as core
core.SPACE = TMP
import openclaw_gateway as g


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))


class Avisos(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_hecho_posponer_descartar(self):
        a = P.inbox_add("Recordatorio", "x"); b = P.inbox_add("Otro", "y"); c = P.inbox_add("Tercero", "z")
        code, out = g._run_notif_action(a["id"], "hecho")
        self.assertEqual((code, out["result"]), (200, "Hecho ✓"))
        self.assertEqual(D.notif_get(a["id"])["estado"], "resuelta")
        code, out = g._run_notif_action(b["id"], "posponer_1h")
        self.assertIn("1 hora", out["result"])
        self.assertNotIn(b["id"], [r["id"] for r in P.inbox_list()])          # pospuesta: no sale hasta que le toque
        g._run_notif_action(c["id"], "descartar")
        self.assertNotIn(c["id"], [r["id"] for r in P.inbox_list()])
        self.assertEqual(g._run_notif_action(c["id"], "inventada")[0], 400)   # lista cerrada de acciones
        self.assertEqual(g._run_notif_action("nope", "hecho")[0], 404)
        evs = [e["evento"] for e in D.notif_eventos(b["id"])]
        self.assertEqual(evs, ["creada", "pospuesta"])                          # historial append-only

    def test_pospuesta_vuelve_una_vez(self):
        a = P.inbox_add("Vuelve", "x")
        D.notif_posponer(a["id"], int(time.time()) - 5)                        # ya venció
        enviados = []
        P.notify = lambda t, b="", **k: enviados.append((t, k.get("existing") and k["existing"]["id"])) or 0
        self.assertEqual(P.resurgir_pospuestas(), 1)
        self.assertEqual(enviados[0], ("⏰ Vuelve", a["id"]))
        self.assertEqual(P.resurgir_pospuestas(), 0)                           # no se repite
        self.assertIn(a["id"], [r["id"] for r in P.inbox_list()])

    def test_migra_bandeja_json_vieja(self):
        (TMP / "st").mkdir(exist_ok=True)
        (TMP / "st" / "notifs.json").write_text(json.dumps([{"id": "viejo1", "ts": 1700000000, "title": "Viejo", "body": "b", "tab": "chat", "kind": "", "link": "",
                                                           "read": True, "actions": [], "done": {}}]))
        self.assertEqual(P.inbox_list()[0]["id"], "viejo1")
        self.assertTrue((TMP / "st" / "notifs.json.migrado").exists())


class Seguimiento(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_ciclo_y_aviso_unico_con_botones(self):
        s = S.crear("Boletos Cinemex viernes — falta pagar", en_horas=0.0001, nota="Polo paga con Apple Pay", origen="compra")
        D.seg_actualizar(s["id"], proxima_revision=int(time.time()) - 1)
        enviados = []
        n = S.tick(lambda titulo, cuerpo="", **k: enviados.append((titulo, k)))
        self.assertEqual(n, 1)
        self.assertEqual(enviados[0][1]["link"], "seg:" + s["id"])
        self.assertEqual(enviados[0][1]["kind"], "seguimiento")
        self.assertEqual(S.tick(lambda *a, **k: enviados.append(1)), 0)         # ya avisó: se corre por su cadencia, no spamea
        self.assertGreater(D.seg_get(s["id"])["proxima_revision"], time.time() + 20 * 3600)
        S.accion(s["id"], "pausar")
        D.seg_actualizar(s["id"], proxima_revision=int(time.time()) - 1)
        self.assertEqual(S.tick(lambda *a, **k: 1), 0)                          # en pausa: no avisa
        S.accion(s["id"], "reanudar", en_horas=2)
        S.accion(s["id"], "hecho", nota="Ya pagó")
        self.assertEqual(D.seg_get(s["id"])["estado"], "hecho")
        self.assertEqual([e["evento"] for e in D.seg_eventos(s["id"]) if e["evento"] != "actualizado"], ["creado", "aviso", "pausado", "reanudado", "hecho"])

    def test_boton_hecho_del_aviso_cierra_el_seguimiento(self):
        s = S.crear("Revisar contrato Vizum", en_horas=1)
        a = P.inbox_add("🔎 Seguimiento: Revisar contrato Vizum", "x", kind="seguimiento", link="seg:" + s["id"])
        g._run_notif_action(a["id"], "hecho")
        self.assertEqual(D.seg_get(s["id"])["estado"], "hecho")

    def test_fechas_y_validacion(self):
        with self.assertRaises(ValueError):
            S.crear("")
        with self.assertRaises(ValueError):
            S.cuando_a_ts("mañana por la tarde")
        d = S.cuando_a_ts("2026-11-23T09:00")
        self.assertEqual(time.strftime("%H", time.gmtime(d)), "15")             # 9:00 CDMX = 15:00 UTC
        self.assertIn("• [", S.listar_texto() if S.crear("Algo útil", en_horas=3) else "")


class Resumenes(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_resumen_en_base_y_aviso_corto(self):
        hoy = date(2026, 11, 22)
        (TMP / "AGENDA.md").write_text("# AGENDA\n- [ ] Llamar a Fernando 16:30 hoy\n- [ ] Mañana 24-jun 11:00 — Pedir a Abelardo revisar accesos\n- [ ] Cena familiar\n")
        core._entregables_lista_tablero = lambda: [{"titulo": "Contrato Vizum", "estado": "listo", "cliente": "Vizum"}]
        core._read_space_file = lambda f: (TMP / f).read_text()
        enviados = []
        rid = R.publicar(core, "cierre", lambda t, b="", **k: enviados.append((t, b, k)), hoy=hoy)
        self.assertTrue(rid)
        t, b, k = enviados[0]
        self.assertEqual(k["link"], "resumen:" + rid)
        self.assertLess(len(b), 120)                                            # el aviso es una línea, no el resumen
        self.assertIn("1 para hoy", b)
        d = D.resumen_get(rid)
        ids = [x["id"] for x in d["datos"]["secciones"]]
        self.assertEqual(ids[0], "hoy")
        self.assertIn("vencidas", ids)                                          # lo de junio ya NO se presenta como «de hoy»
        hoy_items = d["datos"]["secciones"][0]["items"]
        self.assertTrue(all("junio" not in i["texto"].lower() and "24-jun" not in i["texto"] for i in hoy_items))
        self.assertEqual(D.resumen_list(5)[0]["id"], rid)

    def test_sin_nada_no_avisa(self):
        (TMP / "AGENDA.md").write_text("# AGENDA\n- [x] algo\n")
        core._entregables_lista_tablero = lambda: []
        core._read_space_file = lambda f: (TMP / f).read_text()
        self.assertIsNone(R.publicar(core, "tarde", lambda *a, **k: self.fail("no debía avisar"), hoy=date(2026, 11, 22)))


if __name__ == "__main__":
    unittest.main()
