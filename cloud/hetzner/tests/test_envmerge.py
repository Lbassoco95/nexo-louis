"""envmerge.sh: openclaw.env se regenera SIN perder lo que otros scripts inyectaron (Kawiil Central, 1Password…)."""
import os, subprocess, tempfile, time, unittest
from pathlib import Path

SH = str(Path(__file__).resolve().parent.parent / "openclaw" / "envmerge.sh")


def bash(script, cwd):
    r = subprocess.run(["bash", "-c", f"source {SH}\n{script}"], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.old, self.new = Path(self.d, "old.env"), Path(self.d, "new.env")

    def test_conserva_lo_inyectado_y_lo_regenerado_manda(self):
        self.old.write_text("ANTHROPIC_API_KEY=VIEJA\nKAWIIL_CENTRAL_SUPABASE_URL=https://abc.supabase.co\n"
                            "KAWIIL_CENTRAL_DATABASE_URL=postgresql://u:p=1@h:6543/db\nOP_SERVICE_ACCOUNT_TOKEN=ops_x\n"
                            "PLAYWRIGHT_BROWSERS_PATH=/opt/pw\n# comentario\n\nLINEA SIN IGUAL\n")
        self.new.write_text("ANTHROPIC_API_KEY=NUEVA\nOPENCLAW_PORT=3000\n")
        rc, out, _ = bash(f'envmerge_keep {self.old} {self.new}', self.d)
        self.assertEqual(rc, 0)
        txt = self.new.read_text().splitlines()
        self.assertIn("ANTHROPIC_API_KEY=NUEVA", txt)                 # lo regenerado manda
        self.assertNotIn("ANTHROPIC_API_KEY=VIEJA", txt)
        for k in ("KAWIIL_CENTRAL_SUPABASE_URL=https://abc.supabase.co", "KAWIIL_CENTRAL_DATABASE_URL=postgresql://u:p=1@h:6543/db",
                  "OP_SERVICE_ACCOUNT_TOKEN=ops_x", "PLAYWRIGHT_BROWSERS_PATH=/opt/pw"):
            self.assertIn(k, txt)                                     # valores con '=' intactos
        self.assertEqual(set(out.split()), {"KAWIIL_CENTRAL_SUPABASE_URL", "KAWIIL_CENTRAL_DATABASE_URL",
                                            "OP_SERVICE_ACCOUNT_TOKEN", "PLAYWRIGHT_BROWSERS_PATH"})  # imprime solo CLAVES
        self.assertNotIn("ops_x", out)                                # nunca imprime valores
        self.assertFalse(any(l.startswith("#") or "SIN IGUAL" in l for l in txt))

    def test_es_idempotente(self):
        self.old.write_text("KAWIIL_CENTRAL_SUPABASE_KEY=sb_secret\n")
        self.new.write_text("OPENCLAW_PORT=3000\n")
        bash(f'envmerge_keep {self.old} {self.new}', self.d)
        antes = self.new.read_text()
        bash(f'envmerge_keep {self.old} {self.new}', self.d)
        self.assertEqual(self.new.read_text(), antes)                 # no duplica

    def test_sin_archivo_viejo_no_falla(self):
        self.new.write_text("A=1\n")
        rc, out, _ = bash(f'envmerge_keep {self.d}/no-existe {self.new}', self.d)
        self.assertEqual((rc, out, self.new.read_text()), (0, "", "A=1\n"))

    def test_respaldo_conserva_solo_las_5_mas_recientes(self):
        f = Path(self.d, "openclaw.env")
        f.write_text("X=1\n")
        os.chmod(f, 0o600)
        for _ in range(8):
            bash(f'envmerge_backup {f}', self.d)
            time.sleep(1.05)                                          # el nombre lleva segundos
        baks = sorted(Path(self.d).glob("openclaw.env.bak.*"))
        self.assertEqual(len(baks), 5)
        self.assertTrue(all(oct(b.stat().st_mode)[-3:] == "600" for b in baks))   # los respaldos no quedan legibles para otros


if __name__ == "__main__":
    unittest.main()
