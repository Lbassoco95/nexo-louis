"""Bandeja de notificaciones de la app: almacén, botones de confirmación y su seguridad."""
import os, sys, json, time, tempfile, subprocess, unittest
from pathlib import Path
os.environ["TZ"] = "America/Mexico_City"
time.tzset()
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x")
import louis_push as P
import louis_core as core
core.SPACE = TMP
import openclaw_gateway as g

STEWARD = "🧭 *Seguimiento diario*\n🧹 *Propongo cerrar (2)*\n• 3. algo\n_Responde «cierra 3, 5» o «cierra todos los sugeridos». No cierro nada sin tu OK._"
SLACK = "🌅 *Seguimiento de Slack*\n_Responde «agrega altas 1, 3» o «agrega todas las altas». No agrego nada sin tu OK._"


def _limpiar_db():
    import donna_db
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    donna_db._INIT_DONE.discard(str(TMP / "st" / "donna.db"))


class Bandeja(unittest.TestCase):
    def setUp(self):
        _limpiar_db()

    def test_orden_leidas_y_conteo(self):
        a = P.inbox_add("uno", "x"); b = P.inbox_add("dos", "y"); c = P.inbox_add("tres", "z")
        self.assertEqual([r["title"] for r in P.inbox_list()], ["tres", "dos", "uno"])  # lo más nuevo primero
        self.assertEqual(P.unread_count(), 3)
        self.assertEqual(P.inbox_mark_read({a["id"]}), 1)
        self.assertEqual(P.unread_count(), 2)
        self.assertEqual(P.inbox_mark_read(None), 2)
        self.assertEqual(P.unread_count(), 0)

    def test_tope_de_listado_200_y_nada_se_pierde(self):
        for i in range(P.INBOX_MAX + 15):
            P.inbox_add(f"n{i}", "x")
        self.assertEqual(len(P.inbox_list(10000)), P.INBOX_MAX)          # el listado se acota; la base conserva todo
        self.assertEqual(P.inbox_list(1)[0]["title"], f"n{P.INBOX_MAX + 14}")

    def test_reglas_de_botones(self):
        self.assertEqual([a["id"] for a in P.actions_for(STEWARD)], ["steward_cerrar_sugeridos"])
        self.assertEqual([a["id"] for a in P.actions_for(SLACK)], ["altas_todas"])
        self.assertEqual(P.actions_for("Hola, ¿qué tal? cierra la puerta"), [])

    def test_markdown_de_telegram_a_app(self):
        self.assertEqual(P.tg_md_to_app("*Seguimiento* y _cursiva_"), "**Seguimiento** y _cursiva_")
        self.assertEqual(P.tg_md_to_app("ya **negrita**"), "ya **negrita**")

    def test_notify_guarda_aunque_no_haya_telefonos(self):
        P.SUBS_FILE.unlink(missing_ok=True)
        self.assertEqual(P.notify("Aviso", "cuerpo", full_body="cuerpo largo", actions=[{"id": "x", "label": "X"}]), 0)
        r = P.inbox_list(1)[0]
        self.assertEqual((r["title"], r["body"], r["actions"][0]["id"]), ("Aviso", "cuerpo largo", "x"))

    def test_payload_lleva_nid_y_badge(self):
        sent = []
        P._read_subs = lambda: [{"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": "a", "auth": "b"}}]
        P._send_one = lambda sub, payload, ttl, urg, topic="": sent.append(json.loads(payload)) or 201
        try:
            self.assertEqual(P.notify("T", "B"), 1)
        finally:
            import importlib; importlib.reload(P)
        self.assertEqual(sent[0]["tab"], "notifs")
        self.assertTrue(sent[0]["nid"]); self.assertEqual(sent[0]["badge"], 1)

    def test_notify_text_extrae_acciones_y_guarda_texto_completo(self):
        cap = {}
        orig = P.notify_async
        P.notify_async = lambda *a, **k: cap.update(args=a, kw=k)
        try:
            P.notify_text_async(STEWARD)
        finally:
            P.notify_async = orig
        self.assertEqual(cap["kw"]["actions"][0]["id"], "steward_cerrar_sugeridos")
        fb = cap["kw"]["full_body"]
        self.assertIn("**Propongo cerrar (2)**", fb)            # markdown de la app
        self.assertNotIn("Seguimiento diario", fb)              # el título NO se repite en el cuerpo
        self.assertEqual(cap["args"][0], "🧭 Seguimiento diario")  # título del push (conserva el emoji)
        self.assertTrue(len(fb) > len(cap["args"][1]))          # el cuerpo completo es más largo que el del push

    def test_mensaje_de_una_sola_linea_no_repite_texto(self):
        cap = {}
        orig = P.notify_async
        P.notify_async = lambda *a, **k: cap.update(args=a, kw=k)
        try:
            P.notify_text_async("Solo una línea")
        finally:
            P.notify_async = orig
        self.assertEqual(cap["kw"]["full_body"], "")

    def test_dos_procesos_no_pierden_avisos(self):
        code = ("import sys,os;sys.path.insert(0,%r);os.environ['PUSH_STATE_DIR']=%r;import louis_push as P\n"
                "[P.inbox_add('p','x') for _ in range(25)]") % (SERVICES, str(TMP / "st"))
        ps = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(3)]
        [p.wait() for p in ps]
        self.assertEqual(len(P.inbox_list(200)), 75)


class Acciones(unittest.TestCase):
    def setUp(self):
        _limpiar_db()
        self.hoy = g._fecha_cdmx(int(time.time()))
        self.calls = []
        core.aplicar_cierres_steward = lambda sel: self.calls.append(sel) or "Cerré 2 pendientes."
        core.aplicar_altas_seguimiento = lambda sel: self.calls.append(sel) or "Agregué 3 altas."
        core._steward_load_state = lambda: {"date": self.hoy}
        self.rec = P.inbox_add("Seguimiento", "x", actions=P.actions_for(STEWARD))

    def test_exito_y_es_idempotente(self):
        code, out = g._run_notif_action(self.rec["id"], "steward_cerrar_sugeridos")
        self.assertEqual((code, out["result"]), (200, "Cerré 2 pendientes."))
        self.assertEqual(self.calls, ["todos los sugeridos"])
        code2, out2 = g._run_notif_action(self.rec["id"], "steward_cerrar_sugeridos")
        self.assertEqual((code2, out2.get("ya")), (200, True))
        self.assertEqual(len(self.calls), 1)                      # NO se ejecutó dos veces
        self.assertTrue(P.inbox_get(self.rec["id"])["read"])

    def test_rechaza_propuesta_vieja(self):
        core._steward_load_state = lambda: {"date": "2020-01-01"}  # ya hay otra propuesta distinta
        code, out = g._run_notif_action(self.rec["id"], "steward_cerrar_sugeridos")
        self.assertEqual(code, 409); self.assertEqual(self.calls, [])
        self.assertNotIn("steward_cerrar_sugeridos", P.inbox_get(self.rec["id"])["done"])

    def test_solo_acciones_que_el_aviso_ofrece(self):
        self.assertEqual(g._run_notif_action(self.rec["id"], "altas_todas")[0], 400)     # existe pero no la ofrece
        self.assertEqual(g._run_notif_action(self.rec["id"], "borrar_todo")[0], 400)     # no existe
        self.assertEqual(g._run_notif_action("noexiste", "altas_todas")[0], 404)
        self.assertEqual(self.calls, [])

    def test_altas_con_su_propio_estado(self):
        core.SLACK_FOLLOWUP_STATE = TMP / "slack_followup.json"
        core.SLACK_FOLLOWUP_STATE.write_text(json.dumps({"fecha": self.hoy, "altas": [{"n": 1}]}))
        r = P.inbox_add("Slack", "x", actions=P.actions_for(SLACK))
        code, out = g._run_notif_action(r["id"], "altas_todas")
        self.assertEqual((code, out["result"]), (200, "Agregué 3 altas."))
        self.assertEqual(self.calls, ["todas"])


if __name__ == "__main__":
    unittest.main()
