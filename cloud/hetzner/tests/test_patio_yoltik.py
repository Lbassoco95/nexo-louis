"""Patio Yoltik: candados de lectura, validación de escritura y mensajes honestos sin credenciales."""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(OPENCLAW_HOME=str(TMP), PUSH_STATE_DIR=str(TMP / "st"), TZ="America/Mexico_City")
for k in ("PATIO_YOLTIK_DATABASE_URL", "PATIO_YOLTIK_DATABASE_URL_WRITE"):
    os.environ.pop(k, None)
import patio_yoltik as P
import louis_core as core

UID = "123e4567-e89b-12d3-a456-426614174000"


class FakeCur:
    def __init__(self, log, enums=("pendiente", "completada")):
        self.log, self.enums, self._one = log, enums, None
    def execute(self, sql, params=()):
        self.log.append((sql, list(params)))
        self._one = ("Tarea X", "completada") if "update public.tasks" in sql else ("cid",)
    def fetchall(self): return [(e,) for e in self.enums]
    def fetchone(self): return self._one


class FakeConn:
    def __init__(self): self.log, self.autocommit, self.closed = [], False, False
    def cursor(self): return FakeCur(self.log)
    def close(self): self.closed = True


class SinCredenciales(unittest.TestCase):
    def test_mensajes_honestos(self):
        for out in (P.tablas(), P.tareas(), P.actualizar_tarea(UID, {"estatus": "completada"}), P.avance(UID, "hola")):
            self.assertTrue(out.startswith("❌"), out)
        self.assertIn("solo puede LEER", P.actualizar_tarea(UID, {"estatus": "completada"}))

    def test_via_herramienta_y_freno(self):
        out = core.execute_tool("patio_tareas", {})
        self.assertTrue(core._tool_failed(out))


class Candados(unittest.TestCase):
    def test_query_solo_select(self):
        for mal in ("delete from tasks", "update tasks set titulo='x'", "select 1; drop table tasks",
                    "with x as (select 1) delete from tasks", "insert into tasks values (1)", "truncate tasks"):
            self.assertTrue(P.query(mal).startswith("🔒"), mal)

    def test_id_debe_ser_uuid(self):
        self.assertIn("UUID", P.actualizar_tarea("1 or 1=1", {"estatus": "completada"}))
        self.assertIn("UUID", P.tarea_detalle("x"))
        self.assertIn("UUID", P.avance("x", "t"))

    def test_campos_no_permitidos_se_ignoran(self):
        self.assertTrue(P.actualizar_tarea(UID, {"workspace_id": UID, "id": UID}).startswith("❌ Nada que cambiar"))

    def test_describir_valida_nombre(self):
        self.assertTrue(P.describir("tasks; drop table x").startswith("❌"))


class Escritura(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConn()
        self._orig = P._conn
        P._conn = lambda write=False: (self.conn, None)

    def tearDown(self):
        P._conn = self._orig

    def test_cerrar_tarea(self):
        out = P.actualizar_tarea(UID, {"estatus": "completada", "workspace_id": "x"})
        self.assertTrue(out.startswith("✅"), out)
        sql, params = [l for l in self.conn.log if "update public.tasks" in l[0]][0]
        self.assertIn("completada_en = now()", sql)
        self.assertIn("estatus = %s::task_status", sql)
        self.assertNotIn("workspace_id", sql)                      # campo no permitido nunca llega al SQL
        self.assertEqual(params, ["completada", UID])              # valores siempre parametrizados
        self.assertTrue(self.conn.closed)

    def test_reabrir_limpia_completada_en(self):
        P.actualizar_tarea(UID, {"estatus": "pendiente"})
        sql = [l for l in self.conn.log if "update public.tasks" in l[0]][0][0]
        self.assertIn("completada_en = null", sql)

    def test_estatus_invalido(self):
        self.assertIn("inválido", P.actualizar_tarea(UID, {"estatus": "borrada"}))

    def test_prioridad_invalida(self):
        self.assertIn("inválida", P.actualizar_tarea(UID, {"prioridad": "ya"}))

    def test_avance_y_auditoria(self):
        self.assertTrue(P.avance(UID, "Se envió la propuesta").startswith("✅"))
        self.assertIn("Se envió la propuesta", P.AUDIT_LOG.read_text())


if __name__ == "__main__":
    unittest.main()


class Resumen(unittest.TestCase):
    def setUp(self):
        self._orig = P._select

    def tearDown(self):
        P._select = self._orig

    def test_bloque_que_falla_no_tumba_el_resto(self):
        def fake(sql, params=()):
            if "from public.deals" in sql:
                return None, None, "❌ Error SQL (Patio Yoltik): column no existe"
            return ["estatus", "n"], [("activo", 3)], None
        P._select = fake
        out = P.resumen()
        self.assertIn("Proyectos por estatus", out)
        self.assertIn("activo | 3", out)
        self.assertIn("Pipeline de deals", out)
        self.assertIn("no completes esos datos de memoria", out)

    def test_sin_conexion_devuelve_el_error_tal_cual(self):
        P._select = lambda sql, params=(): (None, None, "❌ Falta PATIO_YOLTIK_DATABASE_URL")
        self.assertTrue(P.resumen().startswith("❌"))

    def test_prompt_dirige_yoltik_a_patio(self):
        pr = core.load_system_prompt(channel="api")
        self.assertIn("patio_resumen", pr)
        self.assertIn("NO `kawiil_central_*`", pr)


class RuteoYCharla(unittest.TestCase):
    def test_patio_y_tableros_usan_herramientas(self):
        for m in ("cierra la tarea de Patio", "hazme un dashboard de Yoltik", "¿cómo va patio?"):
            self.assertTrue(core.needs_tools(m), m)

    def test_charla_corta_sin_agenda(self):
        app = "# CANAL ACTUAL: app móvil de Donna"
        self.assertTrue(core._is_light_app_chat(app, "¿Ya estás ahí?", False))
        self.assertFalse(core._is_light_app_chat(app, "¿Ya estás ahí?", True))       # primero del día
        self.assertFalse(core._is_light_app_chat(app, "qué pendientes tengo", False))
        self.assertFalse(core._is_light_app_chat("# CANAL ACTUAL: telegram", "hola", False))
