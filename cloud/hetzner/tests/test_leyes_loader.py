"""Carga de leyes de Diputados + búsqueda/lectura desde Cerebro (sin red)."""
import importlib.util, os, sqlite3, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp()
os.environ["DOF_BASE_DIR"] = TMP


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m
    spec.loader.exec_module(m); return m


INDEX = """<html><body><table>
<tr><td><a href="ref/lgipe.htm">Ley General de Instituciones y Procedimientos Electorales</a></td>
    <td><a href="pdf/LGIPE.pdf">PDF</a></td></tr>
<tr><td><a href="ref/lfda.htm">Ley Federal del Derecho de Autor</a></td><td><a href="pdf/LFDA.pdf">PDF</a></td></tr>
<tr><td><a href="ref/cpeum.htm">Constituci&oacute;n Pol&iacute;tica de los Estados Unidos Mexicanos</a></td></tr>
<tr><td><a href="ref/vacia.htm">Ley Escaneada</a></td></tr>
<tr><td><a href="otra.htm">Ignorar</a></td></tr></table></body></html>"""

REL = "x" * 2500
TEXTOS = {
    "LGIPE": "Ley General de Instituciones\nÚltima reforma publicada DOF 13-06-2025\n\n"
             "Artículo 57.\nOtro texto.\n\nArtículo 58.\n1. La Dirección Ejecutiva de Prerrogativas y Partidos Políticos... premio concurso.\n"
             "2. Segundo párrafo.\n\nArtículo 59.\nFin.\n" + REL,
    "LFDA": "Ley Federal del Derecho de Autor\nÚltima reforma publicada DOF 01/07/2020\n\nArtículo 30 Bis.\nBis.\n\n"
            "Artículo 30.\nEl titular de los derechos patrimoniales...\n\nArtículo 31.\nOtro.\n" + REL,
    "CPEUM": "Constitución\nÚltima reforma publicada DOF 15-04-2026\n\nArtículo 1o.\nTodas las personas...\n\nArtículo 2o.\nX\n" + REL,
    "VACIA": "corto",
}


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = _load("dof_biblioteca", ROOT / "legal-scrapers" / "dof_biblioteca.py")
        cls.d.http_get = lambda url, params=None, accept="*/*", timeout=30: (
            (200, INDEX.encode("cp1252"), {}) if url.endswith("index.htm") else
            (200, b"%PDF-" + url.rsplit("/", 1)[1][:-4].encode(), {}) if "/pdf/" in url else (404, b"", {}))
        cls.d._pdf_a_texto = lambda pdf: TEXTOS.get(pdf[5:].decode(), "")
        cls.d.time.sleep = lambda s: None

    def test_1_carga_y_filtros(self):
        r = self.d.cmd_leyes_init()
        self.assertEqual((r["nueva"], r["sin_texto"], r["fallo"]), (3, 1, 0))
        c = sqlite3.connect(self.d.DB_PATH)
        n = {x[0]: x[1:] for x in c.execute("SELECT nombre_corto, nombre_completo, version_vigente_fecha, tipo FROM leyes")}
        self.assertIn("LGIPE", n); self.assertEqual(n["LGIPE"][1], "2025-06-13")
        self.assertEqual(n["LFDA"][1], "2020-07-01")
        self.assertNotIn("VACIA", n)                         # texto corto no se guarda
        self.assertIn("CPEUM", n); self.assertEqual(n["CPEUM"][2], "CONSTITUCION")
        self.assertEqual(n["CPEUM"][0], "Constitución Política de los Estados Unidos Mexicanos")

    def test_2_idempotente_y_actualiza(self):
        r = self.d.cmd_leyes_init()
        self.assertEqual((r["nueva"], r["actualizada"], r["igual"]), (0, 0, 3))
        TEXTOS["LFDA"] += "\nArtículo 32.\nNuevo texto reformado.\n"
        r = self.d.cmd_leyes_init(solo="lfda")
        self.assertEqual((r["actualizada"], r["igual"]), (1, 0))
        n = sqlite3.connect(self.d.DB_PATH).execute("SELECT COUNT(*) FROM leyes_fts WHERE leyes_fts MATCH 'reformado'").fetchone()[0]
        self.assertEqual(n, 1)                               # FTS reindexado

    def test_3_leyes_add(self):
        self.d.http_get = lambda url, **k: (200, b"%PDF-INE", {})
        TEXTOS["INE"] = "Reglamento Interior del INE\n\nArtículo 5.\nLa comisión...\n" + REL
        self.assertEqual(self.d.cmd_leyes_add("Reglamento Interior del Instituto Nacional Electoral", "http://x/y.pdf", clave="RIINE"), "nueva")
        self.assertEqual(self.d.cmd_leyes_add("Reglamento Interior del Instituto Nacional Electoral", "http://x/y.pdf", clave="RIINE"), "igual")
        self.d.http_get = lambda url, **k: (200, b"<html>no pdf", {})
        self.assertEqual(self.d.cmd_leyes_add("X", "http://x/z"), "fallo")

    def test_4_cerebro(self):
        os.environ["DOF_DB_PATH"] = str(self.d.DB_PATH); os.environ["SJF_DB_PATH"] = TMP + "/nada.db"
        try:
            m = _load("cerebro", ROOT / "services" / "cerebro_kawiil_mcp.py")
        except ImportError as e:
            self.skipTest(f"mcp no instalado: {e}")
        f = lambda t: getattr(t, "fn", t)
        out = f(m.legal_ley)("LGIPE", "58")
        self.assertIn("2025-06-13", out); self.assertIn("Prerrogativas", out); self.assertNotIn("Artículo 59", out)
        self.assertIn("Todas las personas", f(m.legal_ley)("CPEUM", "1"))             # 1o.
        self.assertIn("derechos patrimoniales", f(m.legal_ley)("Derecho de Autor", "30"))
        self.assertNotIn("Bis.\nBis", f(m.legal_ley)("LFDA", "30"))                    # no confunde 30 con 30 Bis
        self.assertIn("No encontré", f(m.legal_ley)("LGIPE", "999"))
        self.assertIn("Varias coinciden", f(m.legal_ley)("Ley", ""))
        b = f(m.legal_buscar)("premio concurso", fuente="leyes")
        self.assertIn("LEY/LGIPE", b); self.assertIn("2025-06-13", b)
        e = f(m.legal_estado)()
        self.assertIn("LEYES:✅4 leyes", e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
