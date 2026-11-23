"""El actualizador no corta una grabación: con grabación en curso no instala nada; sin ella instala y reinicia; --esperar espera; --forzar no pregunta."""
import os, sqlite3, subprocess, tempfile, threading, time, unittest
from pathlib import Path
SCRIPT = str(Path(__file__).resolve().parent.parent / "tools" / "actualizar-donna.sh")


class Actualizador(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        (self.t / "repo/cloud/hetzner/services/app").mkdir(parents=True)
        (self.t / "repo/cloud/hetzner/services/nuevo.py").write_text("x=1\n")
        (self.t / "repo/cloud/hetzner/services/app/index.html").write_text("<html>nuevo</html>")
        (self.t / "dest/app").mkdir(parents=True)
        (self.t / "dest/nuevo.py").write_text("x=0\n")
        self.db = self.t / "donna.db"
        c = sqlite3.connect(self.db)
        c.executescript("CREATE TABLE escucha_audio(aid TEXT, estado TEXT, actualizado INTEGER);CREATE TABLE escucha_rescate(id TEXT, actualizado INTEGER);CREATE TABLE escucha_borradores(id TEXT, actualizado INTEGER);")
        c.commit(); c.close()
        self.env = {**os.environ, "NO_GIT": "1", "NO_VERIFY": "1", "REPO": str(self.t / "repo"), "DEST": str(self.t / "dest"), "DONNA_DB": str(self.db), "SUDO": "",
                    "OWNER": os.popen("id -un").read().strip(), "SYSTEMCTL": "echo REINICIO", "ESPERA": "1"}

    def correr(self, *args, timeout=60):
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=self.env, timeout=timeout)

    def actividad(self, hace_s):
        c = sqlite3.connect(self.db)
        c.execute("DELETE FROM escucha_rescate")
        c.execute("INSERT INTO escucha_rescate VALUES('r1',?)", (int(time.time()) - hace_s,))
        c.commit(); c.close()

    def instalado(self):
        return (self.t / "dest/nuevo.py").read_text().strip() == "x=1" and (self.t / "dest/app/index.html").exists()

    def test_sin_grabacion_instala_y_reinicia(self):
        r = self.correr()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.instalado())
        self.assertIn("REINICIO restart openclaw-gateway scheduler telegram-bridge", r.stdout)

    def test_con_grabacion_no_toca_nada(self):
        self.actividad(10)
        r = self.correr()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.instalado())
        self.assertNotIn("REINICIO", r.stdout)
        self.assertIn("NO instalo ni reinicio", r.stdout)

    def test_una_grabacion_vieja_no_cuenta(self):
        self.actividad(600)
        self.assertTrue(self.correr().returncode == 0 and self.instalado())

    def test_forzar_instala_aunque_se_grabe(self):
        self.actividad(10)
        r = self.correr("--forzar")
        self.assertTrue(self.instalado())
        self.assertIn("REINICIO", r.stdout)

    def test_esperar_actualiza_cuando_la_grabacion_termina(self):
        self.actividad(10)
        threading.Timer(2.5, lambda: self.actividad(600)).start()               # la grabación «termina» a los 2.5 s
        r = self.correr("--esperar", timeout=90)
        self.assertIn("Espero a que termine", r.stdout)
        self.assertTrue(self.instalado())
        self.assertIn("REINICIO", r.stdout)

    def test_base_sin_tablas_o_sin_archivo_no_bloquea(self):
        self.db.unlink()
        self.assertTrue(self.correr().returncode == 0 and self.instalado())

    def test_opcion_desconocida(self):
        self.assertEqual(self.correr("--loquesea").returncode, 2)


if __name__ == "__main__":
    unittest.main()
