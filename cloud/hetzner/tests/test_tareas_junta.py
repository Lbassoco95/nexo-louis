"""De la junta a tareas propuestas: solo compromisos con cita real, nada se crea sin aprobar, destinos Agenda / Kawiil Central / Seguimiento, y aprende."""
import os, sys, json, tempfile, unittest
from pathlib import Path
from datetime import timezone, timedelta
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import donna_db as D, donna_personas as P, donna_tareas as T, donna_radar as R


class FakeCore:
    SPACE = TMP
    TZ_CDMX = timezone(timedelta(hours=-6))
    creadas = []

    @staticmethod
    def _kawiil_central_crear_tarea(titulo, proyecto_id, descripcion="", asignado_a="", prioridad="", deadline="", campos_extra=None, parent_task_id=""):
        FakeCore.creadas.append((titulo, proyecto_id, asignado_a, deadline, descripcion))
        return "✅ Tarea creada en `tasks` con id `abc-123`"


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    (TMP / "AGENDA.md").write_text("# AGENDA\n- [ ] Revisar presupuesto del diplomado con Laura\n")
    FakeCore.creadas = []


def sesion():
    voces = [{"n": 1, "nombre": "Fernando Martínez"}, {"n": 2, "nombre": "Laura Pérez"}]
    lineas = [{"t": "00:10", "voz": 1, "es": "Yo te mando el borrador del contrato de Vizum el jueves"},
              {"t": "00:25", "voz": 2, "es": "Perfecto, yo reviso la parte fiscal y se la paso a Sebastián"},
              {"t": "00:40", "voz": 1, "es": "Creo que deberíamos pensar en abrir otra oficina algún día"},
              {"t": "00:55", "voz": 2, "es": "Me encargo de revisar el presupuesto del diplomado con Laura"}]
    return P.guardar_sesion("Junta Vizum", 120, voces, lineas)["sesion_id"]


def juez(items):
    return lambda texto, fecha: items


BUENAS = [
    {"que": "Enviar el borrador del contrato de Vizum", "quien": "Fernando", "cuando": "2026-11-26", "cita": "Yo te mando el borrador del contrato de Vizum el jueves", "hora": "00:10", "confianza": 0.9},
    {"que": "Revisar la parte fiscal y pasársela a Sebastián", "quien": "Laura Pérez", "cuando": "", "cita": "yo reviso la parte fiscal y se la paso a Sebastián", "hora": "00:25", "confianza": 0.85},
]


class Junta(unittest.TestCase):
    def setUp(self):
        limpiar()
        self.sid = sesion()

    def test_extrae_con_cita_real_y_descarta_inventos_opiniones_y_duplicados(self):
        items = BUENAS + [
            {"que": "Contratar a diez ingenieros nuevos", "quien": "Fernando", "cuando": "", "cita": "vamos a contratar a diez ingenieros el próximo mes", "hora": "01:00", "confianza": 0.95},   # cita inventada
            {"que": "Abrir otra oficina", "quien": "Fernando", "cuando": "", "cita": "deberíamos pensar en abrir otra oficina algún día", "hora": "00:40", "confianza": 0.4},               # vaga: baja confianza
            {"que": "Revisar el presupuesto del diplomado con Laura", "quien": "Laura", "cuando": "", "cita": "Me encargo de revisar el presupuesto del diplomado con Laura", "hora": "00:55", "confianza": 0.9}]  # ya está en la agenda
        r = T.extraer(FakeCore, self.sid, juez(items))
        self.assertEqual((r["propuestas"], r["duplicadas"], r["sin_cita_real"]), (2, 1, 1))
        L = T.listar(self.sid)
        fer = [t for t in L if t["responsable"] == "Fernando"][0]
        self.assertEqual((fer["fecha_limite"], fer["destino"]), ("2026-11-26", "agenda"))
        self.assertIsNotNone(fer["persona_id"])                                 # «Fernando» se enlaza con la persona Fernando Martínez
        self.assertEqual(T.extraer(FakeCore, self.sid, juez(items))["propuestas"], 0)    # re-leer la junta no duplica

    def test_fechas_absurdas_se_quitan(self):
        it = dict(BUENAS[0], cuando="1999-01-01")
        T.extraer(FakeCore, self.sid, juez([it]))
        self.assertEqual(T.listar(self.sid)[0]["fecha_limite"], "")

    def test_nada_se_crea_solo_y_cada_destino_funciona(self):
        T.extraer(FakeCore, self.sid, juez(BUENAS))
        self.assertEqual(FakeCore.creadas, [])
        self.assertNotIn("borrador", (TMP / "AGENDA.md").read_text())
        a, b = T.listar(self.sid)
        ra = T.aprobar(FakeCore, a["id"], "agenda", titulo="Mandar el borrador del contrato Vizum", fecha="2099-01-01")
        self.assertTrue(ra["ok"])
        ag = (TMP / "AGENDA.md").read_text()
        self.assertIn("Mandar el borrador del contrato Vizum", ag)
        self.assertIn("[capturado", ag)
        self.assertIn("junta", ag)
        self.assertEqual(T.aprobar(FakeCore, b["id"], "kc", proyecto_id="")["ok"], False)         # Kawiil Central pide proyecto
        self.assertEqual(T.listar(self.sid)[0]["estado"], "propuesta")                            # sigue pendiente, no se perdió
        rb = T.aprobar(FakeCore, b["id"], "kc", proyecto_id="proj-1")
        self.assertTrue(rb["ok"] and rb["ref"] == "kc:abc-123")
        titulo, proy, asig, dl, desc = FakeCore.creadas[0]
        self.assertEqual((proy, asig), ("proj-1", "Laura Pérez"))
        self.assertIn("yo reviso la parte fiscal", desc)                                          # la cita viaja con la tarea
        self.assertEqual(T.listar(self.sid), [])

    def test_a_seguimiento_y_no_se_crea_dos_veces(self):
        T.extraer(FakeCore, self.sid, juez(BUENAS))
        a = T.listar(self.sid)[0]
        r = T.aprobar(FakeCore, a["id"], "seguimiento")
        self.assertTrue(r["ok"] and r["ref"].startswith("seg:"))
        self.assertEqual(D.seg_get(r["ref"][4:])["titulo"], a["titulo"])
        with self.assertRaises(ValueError):
            T.aprobar(FakeCore, a["id"], "agenda")
        with self.assertRaises(ValueError):
            T.aprobar(FakeCore, T.listar(self.sid)[0]["id"], "papelera")

    def test_rechazar_sube_la_exigencia_y_alimenta_el_dataset(self):
        T.extraer(FakeCore, self.sid, juez(BUENAS))
        u0 = T.umbral()
        a, b = T.listar(self.sid)
        T.aprobar(FakeCore, a["id"], "agenda")
        T.rechazar(b["id"], "no era tarea")
        distintas = ["Llamar al notario sobre la escritura", "Mandar la factura de diciembre al cliente", "Reservar la sala para el comité mensual"]
        for que in distintas:                                                # tres rechazos en total suben el umbral un escalón (títulos distintos: los parecidos se ven como duplicados)
            T.extraer(FakeCore, self.sid, juez([dict(BUENAS[0], que=que, cita=BUENAS[0]["cita"])]))
            for t in T.listar(self.sid):
                T.rechazar(t["id"])
        self.assertGreater(T.umbral(), u0)
        ruta = str(TMP / "t.jsonl")
        self.assertGreaterEqual(T.exportar_dataset(ruta), 2)
        with open(ruta) as f:
            et = {json.loads(l)["etiqueta"] for l in f}
        self.assertEqual(et, {0, 1})

    def test_extraer_y_avisar_notifica_una_vez_y_deja_bitacora(self):
        n = []
        r = T.extraer_y_avisar(FakeCore, self.sid, juez(BUENAS), lambda t, b="", **k: n.append((t, k)))
        self.assertEqual(r["propuestas"], 2)
        self.assertEqual(n[0][1]["link"], "tareas:" + self.sid)
        self.assertIn("2 tarea(s)", n[0][0])
        self.assertTrue(any("Leí la junta" in b["resumen"] for b in R.bitacora_list()))
        self.assertEqual(T.estado_extraccion(self.sid)["estado"], "lista")
        T.extraer_y_avisar(FakeCore, self.sid, juez(BUENAS), lambda *a, **k: self.fail("sin propuestas nuevas no se avisa"))

    def test_si_el_modelo_falla_queda_en_error_sin_romper(self):
        def mal(t, f):
            raise RuntimeError("sin crédito")
        r = T.extraer(FakeCore, self.sid, mal)
        self.assertEqual(r["propuestas"], 0)
        self.assertEqual(T.estado_extraccion(self.sid)["estado"], "error")


if __name__ == "__main__":
    unittest.main()
