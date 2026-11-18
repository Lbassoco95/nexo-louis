"""Codec WebSocket de la sonda de traducción en vivo (tools/probar-traduccion-en-vivo.py)."""
import importlib.util, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("sonda", Path(__file__).resolve().parent.parent / "tools" / "probar-traduccion-en-vivo.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


class Codec(unittest.TestCase):
    def test_ida_y_vuelta_todos_los_tamanos(self):
        for n in (0, 5, 125, 126, 200, 65535, 70000):
            for mask in (True, False):
                pl = b"x" * n
                op, data, resto = S.decode_frame(S.encode_frame(pl, 1, mask) + b"RESTO")
                self.assertEqual((op, data, resto), (1, pl, b"RESTO"), (n, mask))

    def test_incompleto_pide_mas_bytes(self):
        self.assertIsNone(S.decode_frame(b"\x81"))
        self.assertIsNone(S.decode_frame(S.encode_frame(b"abc", 1, False)[:-1]))

    def test_resumen_no_imprime_audios_largos(self):
        out = S.resumen('{"type":"x","delta":"' + "A" * 5000 + '"}')
        self.assertIn("<5000 chars>", out); self.assertLess(len(out), 120)


if __name__ == "__main__":
    unittest.main()
