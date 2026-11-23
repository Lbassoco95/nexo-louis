"""Donna en la junta: responde solo con lo dicho y con fuentes que existen; el verificador frena lo inventado; anotar y proponer tareas sin crear nada."""
import os, sys, json, tempfile, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_junta as J, donna_tareas as T, donna_personas as P


class FakeCore:
    SPACE = TMP


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    (TMP / "AGENDA.md").write_text("# AGENDA\n- [ ] Enviar contrato Vizum a Fernando\n")


LINEAS = [{"t": "00:10", "n": "Fernando", "es": "El presupuesto del proyecto Vizum es de 300,000 pesos"},
          {"t": "00:40", "n": "Laura", "es": "La entrega sería el 26 de noviembre de 2026"},
          {"t": "01:05", "n": "Fernando", "es": "Yo me encargo del contrato"}]


def guion(*pasos):
    """LLM de mentira: cada paso es una lista de bloques (tool_use o texto) en el orden en que responde el modelo."""
    it = iter(pasos)

    def _l(system, messages):
        bloques = next(it)
        return {"content": bloques, "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in bloques) else "end_turn"}
    return _l


def uso(nombre, **a):
    return {"type": "tool_use", "id": "t1", "name": nombre, "input": a}


def final(**d):
    return {"type": "text", "text": json.dumps(d, ensure_ascii=False)}


class Junta(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_responde_con_lo_dicho_y_cita_la_hora(self):
        r = J.preguntar(FakeCore, "L1", "¿De cuánto es el presupuesto?", LINEAS,
                        llm=guion([uso("junta_buscar", consulta="presupuesto")], [final(respuesta="Fernando dijo que son 300,000 pesos.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "00:10"}])]))
        self.assertEqual(r["fuentes"], [{"tipo": "junta", "ref": "00:10"}])
        self.assertTrue(r["verificada"])
        self.assertEqual(r["avisos"], [])
        self.assertEqual([c["rol"] for c in J.chat("L1")], ["polo", "donna"])

    def test_una_fuente_inventada_se_quita_y_sin_fuentes_no_se_afirma(self):
        r = J.preguntar(FakeCore, "L2", "¿Quién firma?", LINEAS, llm=guion([final(respuesta="Firma Sebastián el lunes.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "09:59"}])]))
        self.assertEqual(r["respuesta"], J.NO_TENGO)
        self.assertEqual(r["fuentes"], [])
        self.assertTrue(any("09:59" in a for a in r["avisos"]))

    def test_una_cifra_que_no_esta_en_ninguna_fuente_se_senala(self):
        r = J.preguntar(FakeCore, "L3", "¿Presupuesto?", LINEAS, llm=guion([uso("junta_buscar", consulta="presupuesto")],
                        [final(respuesta="El presupuesto es de 500,000 pesos.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "00:10"}])]))
        self.assertTrue(any("500" in a for a in r["avisos"]))
        self.assertFalse(r["verificada"])

    def test_fechas_en_otro_formato_si_coinciden_con_la_fuente(self):
        r = J.preguntar(FakeCore, "L4", "¿Cuándo es la entrega?", LINEAS, llm=guion([uso("junta_buscar", consulta="entrega")],
                        [final(respuesta="La entrega es el 26/11/2026.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "00:40"}])]))
        self.assertEqual(r["avisos"], [])
        self.assertTrue(r["verificada"])

    def test_documento_se_cita_solo_si_la_herramienta_lo_devolvio(self):
        import app_media
        orig = app_media.buscar_items
        app_media.buscar_items = lambda q, n=6: [{"id": "m1", "nombre": "Contrato Vizum.pdf", "tipo": "documento", "extracto": "La penalización es del 5% mensual"}]
        try:
            ok = J.preguntar(FakeCore, "L5", "¿Penalización?", LINEAS, llm=guion([uso("documentos_buscar", consulta="penalización")],
                             [final(respuesta="Según el contrato, la penalización es del 5% mensual.", usa_datos=True, fuentes=[{"tipo": "documento", "ref": "Contrato Vizum.pdf"}])]))
            self.assertTrue(ok["verificada"] and ok["fuentes"][0]["ref"] == "Contrato Vizum.pdf")
            mal = J.preguntar(FakeCore, "L6", "¿Penalización?", LINEAS, llm=guion([uso("documentos_buscar", consulta="penalización")],
                              [final(respuesta="La penalización es del 5% mensual.", usa_datos=True, fuentes=[{"tipo": "documento", "ref": "Convenio Dazon.pdf"}])]))
            self.assertEqual(mal["respuesta"], J.NO_TENGO)                                    # citó un documento que nunca vio
        finally:
            app_media.buscar_items = orig

    def test_lo_que_el_modelo_no_formatea_o_no_respalda_no_pasa(self):
        r = J.preguntar(FakeCore, "L7", "¿Algo?", LINEAS, llm=guion([{"type": "text", "text": "Claro, la oferta es de 2 millones."}]))
        self.assertEqual(r["respuesta"], J.NO_TENGO)
        c = J.preguntar(FakeCore, "L8", "gracias", LINEAS, llm=guion([final(respuesta="Con gusto.", usa_datos=False, fuentes=[])]))
        self.assertEqual(c["respuesta"], "Con gusto.")                                        # charla sin datos: pasa

    def test_si_el_modelo_se_cicla_se_corta(self):
        r = J.preguntar(FakeCore, "L9", "¿?x", LINEAS, llm=lambda s, m: {"content": [uso("junta_buscar", consulta="x")], "stop_reason": "tool_use"})
        self.assertIn("No alcancé a verificarlo", r["respuesta"])

    def test_anotar_y_proponer_tarea_no_crean_nada_y_quedan_con_la_sesion(self):
        r = J.preguntar(FakeCore, "LA", "anota que Fernando manda el contrato y crea la tarea de enviarlo", LINEAS,
                        llm=guion([uso("anotar", texto="Fernando manda el contrato"), uso("proponer_tarea", que="Enviar el contrato de Vizum", quien="Fernando", cuando="2026-11-26", cita="Yo me encargo del contrato")],
                                  [final(respuesta="Anotado y dejé la tarea propuesta.", usa_datos=False, fuentes=[])]))
        self.assertEqual([e["tipo"] for e in r["eventos"]], ["nota", "tarea"])
        self.assertNotIn("Enviar el contrato de Vizum", (TMP / "AGENDA.md").read_text())      # solo propuesta
        J.nota_add("LA", "Revisar presupuesto el viernes", "01:30")
        sid = P.guardar_sesion("Junta", 120, [], [{"t": "00:01", "voz": 0, "es": "x"}])["sesion_id"]
        J.remapear("LA", sid)
        self.assertEqual(T.listar(sid)[0]["titulo"], "Enviar el contrato de Vizum")             # la propuesta se liga a la sesión real
        txt = J.texto_para_entregable("LA")
        self.assertIn("Notas tomadas durante la junta", txt)
        self.assertIn("Fernando manda el contrato", txt)
        self.assertIn("Consultas a Donna durante la junta", txt)

    def test_remapear_en_base_nueva_no_se_bloquea(self):
        import time
        J.nota_add("LZ", "una nota", "00:01")
        sid = P.guardar_sesion("Junta", 10, [], [{"t": "00:01", "voz": 0, "es": "x"}])["sesion_id"]
        t0 = time.time()
        J.remapear("LZ", sid)                                                   # antes se quedaba esperando 15 s a sí mismo cuando faltaba la tabla de tareas
        self.assertLess(time.time() - t0, 3)
        self.assertEqual(J.notas("LZ")[0]["texto"], "una nota")

    def test_escala_al_modelo_fuerte_solo_cuando_el_verificador_no_pudo_respaldar(self):
        malo = guion([final(respuesta="Firma Sebastián el lunes.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "09:59"}])])
        bueno = guion([uso("junta_buscar", consulta="presupuesto")], [final(respuesta="Son 300,000 pesos.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "00:10"}])])
        r = J.preguntar(FakeCore, "LE1", "¿Presupuesto?", LINEAS, llm=malo, llm_escalado=bueno)
        self.assertTrue(r["escalada"] and r["verificada"])
        self.assertEqual(r["fuentes"][0]["ref"], "00:10")
        # si el primero ya está verificado NO se llama al caro
        llamado = []
        ok1 = guion([uso("junta_buscar", consulta="presupuesto")], [final(respuesta="Son 300,000 pesos.", usa_datos=True, fuentes=[{"tipo": "junta", "ref": "00:10"}])])
        r2 = J.preguntar(FakeCore, "LE2", "¿Presupuesto?", LINEAS, llm=ok1, llm_escalado=lambda s_, m_: llamado.append(1) or {})
        self.assertEqual((r2["escalada"], llamado), (False, []))
        # si el fuerte tampoco puede, se queda con «no lo tengo» (no inventa)
        r3 = J.preguntar(FakeCore, "LE3", "¿Firma?", LINEAS, llm=malo if False else guion([final(respuesta="Firma Sebastián.", usa_datos=True, fuentes=[])]),
                         llm_escalado=guion([final(respuesta="Firma Laura.", usa_datos=True, fuentes=[])]))
        self.assertEqual(r3["respuesta"], J.NO_TENGO)
        # si el fuerte falla (sin crédito, red) no rompe la respuesta
        def roto(s_, m_):
            raise RuntimeError("sin crédito")
        r4 = J.preguntar(FakeCore, "LE4", "¿Firma?", LINEAS, llm=guion([final(respuesta="Firma Sebastián.", usa_datos=True, fuentes=[])]), llm_escalado=roto)
        self.assertEqual(r4["respuesta"], J.NO_TENGO)

    def test_validaciones(self):
        with self.assertRaises(ValueError):
            J.preguntar(FakeCore, "L", "", LINEAS, llm=guion())
        with self.assertRaises(ValueError):
            J.nota_add("L", "", "")


if __name__ == "__main__":
    unittest.main()
