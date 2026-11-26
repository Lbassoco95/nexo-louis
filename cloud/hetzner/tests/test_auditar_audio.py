"""auditar-audio.py: cruza la base con el disco y marca lo que falta (partes con hueco, archivos borrados, audio sin junta, archivos que la base no conoce)."""
import io, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
SERVICES = os.path.join(os.path.dirname(__file__), "..", "services")
TOOL = os.path.join(os.path.dirname(__file__), "..", "tools", "auditar-audio.py")
sys.path.insert(0, SERVICES)
TMP = Path(tempfile.mkdtemp())
os.environ.update(PUSH_STATE_DIR=str(TMP / "st"), OPENCLAW_GATEWAY_TOKEN="x", MEDIA_MIN_FREE_GB="0")
import donna_db as D, donna_audio as A, donna_personas as P


def limpiar():
    for suf in ("", "-wal", "-shm"):
        (TMP / "st" / ("donna.db" + suf)).unlink(missing_ok=True)
    D._INIT_DONE.discard(str(TMP / "st" / "donna.db"))
    import shutil
    shutil.rmtree(A.DIR, ignore_errors=True)


def subir(aid, partes=4, viejo=True):
    for i in range(partes):
        A.guardar_parte(aid, "rid" + aid, "polo", i, io.BytesIO(b"x" * 200000), 200000, 0, "audio/mp4")
    if viejo:
        c = A._c()
        c.execute("UPDATE escucha_audio SET actualizado=? WHERE aid=?", (int(time.time()) - 3600, aid))
        c.commit()
        c.close()


def correr(*args) -> str:
    env = {**os.environ, "DONNA_DB": str(TMP / "st" / "donna.db"), "PUSH_STATE_DIR": str(TMP / "st")}
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env, timeout=60).stdout


class Auditoria(unittest.TestCase):
    def setUp(self):
        limpiar()

    def test_todo_completo_y_ligado(self):
        sid = P.guardar_sesion("Junta del 25", 600, [], [{"t": "00:01", "voz": 0, "es": "hola", "idioma": "es"}], usuario="polo")["sesion_id"]
        subir("audbueno001")
        A.ligar_tramo("audbueno001", sid, "polo")
        out = correr()
        self.assertIn("✓ audbueno", out)
        self.assertIn("→ junta «Junta del 25»", out)
        self.assertIn("todo completo ✓", out)

    def test_marca_huecos_borrados_sin_junta_y_archivos_que_la_base_no_conoce(self):
        subir("audhueco001", partes=5)
        (A.DIR / "audhueco001" / "000002.part").unlink()                                            # se perdió una parte (≈ 10 s)
        subir("audborrado1")
        A.borrar("audborrado1", "polo")
        subir("audsinjunta")
        (A.DIR / "audfantasma1").mkdir(parents=True)                                                # en el disco pero no en la base
        (A.DIR / "audfantasma1" / "000000.part").write_bytes(b"y" * 100000)
        out = correr()
        self.assertIn("HUECOS: faltan las partes 2", out)
        self.assertIn("BORRADO", out)
        self.assertIn("SIN JUNTA", out)
        self.assertIn("ARCHIVOS EN EL DISCO QUE LA BASE NO CONOCE", out)
        self.assertIn("audfanta", out)
        self.assertIn("revisa los de arriba", out)

    def test_la_grabacion_en_curso_se_distingue_y_solo_lee(self):
        subir("audencurso01", viejo=False)
        antes = sorted(p.name for p in (A.DIR / "audencurso01").iterdir())
        out = correr()
        self.assertIn("se está grabando ahora mismo", out)
        self.assertNotIn("⚠️ audencur", out)                                                       # una grabación en curso no es un problema
        self.assertEqual(sorted(p.name for p in (A.DIR / "audencurso01").iterdir()), antes)         # no ensambló ni cambió nada: es de solo lectura
        self.assertEqual(A.estado("audencurso01")["estado"], "grabando")

    def test_lista_el_texto_sin_guardar(self):
        c = D.conn()
        out = correr("1")
        self.assertIn("RESUMEN", out)


if __name__ == "__main__":
    unittest.main()
