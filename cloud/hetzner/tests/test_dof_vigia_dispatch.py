"""dof_vigia_dispatch: selección, prueba sin efectos, entrega incremental y reintento tras fallo."""
import importlib.util
import json
import sqlite3
import urllib.error
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parent.parent / "legal-scrapers" / "dof_vigia_dispatch.py"


@pytest.fixture()
def v(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("dof_vigia_dispatch", RUTA)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    db = tmp_path / "dof.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE notas (cod_nota INTEGER, fecha TEXT, edicion TEXT, titulo TEXT, "
                  "nombre_cod_orga_uno TEXT, tipo_documento TEXT, incluido INTEGER)")
        c.executemany("INSERT INTO notas VALUES (?,?,?,?,?,?,?)", [
            (1, "2026-11-05", "MAT", "ACUERDO uno", "SHCP", "ACUERDO", 1),
            (2, "2026-11-05", "MAT", "DECRETO dos", "SEGOB", "DECRETO", 1),
            (3, "2026-11-05", "MAT", "AVISO judicial", "AVISOS", None, 0),    # no incluida
            (4, "2026-11-05", "VES", "CIRCULAR tres", "CNBV", "CIRCULAR", 1),
            (5, "2026-11-04", "MAT", "OTRO día", "SHCP", "ACUERDO", 1),       # otra fecha
        ])
    monkeypatch.setattr(m, "DB_PATH", db)
    monkeypatch.setattr(m, "STATE_PATH", tmp_path / "estado.json")
    env = tmp_path / "agents.env"
    env.write_text("KAWIIL_AGENTS_URL=http://vm.test\nKAWIIL_DISPATCH_TOKEN=tok\n")
    monkeypatch.setattr(m, "AGENTS_ENV", env)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    return m


def test_selecciona_solo_incluidas_de_la_fecha(v):
    notas, total = v.seleccionar(v.DB_PATH, "2026-11-05", "2026-11-05", set(), 10)
    assert [n["cod_nota"] for n in notas] == [1, 2, 4] and total == 3


def test_respeta_el_tope_y_las_ya_entregadas(v):
    notas, total = v.seleccionar(v.DB_PATH, "2026-11-05", "2026-11-05", {1}, 1)
    assert [n["cod_nota"] for n in notas] == [2] and total == 2


def test_prueba_no_escribe_ni_envia(v, capsys, monkeypatch):
    monkeypatch.setattr(v, "_http", lambda *a, **k: pytest.fail("no debe llamar a la VM"))
    assert v.main(["--fecha", "2026-11-05", "--dias", "1"]) == 0
    assert "PRUEBA" in capsys.readouterr().out and not v.STATE_PATH.exists()


def test_tarea_solo_lleva_datos_publicos(v):
    t = v.texto_tarea({"cod_nota": 9, "fecha": "2026-11-05", "edicion": "MAT", "titulo": "ACUERDO x",
                       "dependencia": "SHCP", "tipo_documento": "ACUERDO"})
    assert "codigo=9" in t and "SHCP" in t and "texto" not in t.lower().split("título")[0]


def test_dispatch_registra_cada_entrega_y_no_repite(v, monkeypatch):
    llamadas = []

    def falso(url, token=None, body=None, timeout=30):
        if url.endswith("/api/agents"):
            return {"agents": [{"id": "a1", "name": "legal_dof_watchdog"}]}
        llamadas.append(body["description"])
        return {"id": "t"}

    monkeypatch.setattr(v, "_http", falso)
    assert v.main(["--fecha", "2026-11-05", "--dias", "1", "--dispatch"]) == 0 and len(llamadas) == 3
    assert sorted(json.loads(v.STATE_PATH.read_text())["entregadas"]) == [1, 2, 4]
    assert v.main(["--fecha", "2026-11-05", "--dias", "1", "--dispatch"]) == 0 and len(llamadas) == 3   # nada nuevo


def test_fallo_a_la_mitad_guarda_lo_entregado_y_reintenta_el_resto(v, monkeypatch):
    estado = {"n": 0}

    def cae_en_la_segunda(url, token=None, body=None, timeout=30):
        if url.endswith("/api/agents"):
            return {"agents": [{"id": "a1", "name": "legal_dof_watchdog"}]}
        estado["n"] += 1
        if estado["n"] >= 2:
            raise urllib.error.URLError("Mac dormida")
        return {"id": "t"}

    monkeypatch.setattr(v, "_http", cae_en_la_segunda)
    assert v.main(["--fecha", "2026-11-05", "--dias", "1", "--dispatch"]) == 2
    assert json.loads(v.STATE_PATH.read_text())["entregadas"] == [1]      # solo la que sí salió
    monkeypatch.setattr(v, "_http", lambda url, token=None, body=None, timeout=30:
                        {"agents": [{"id": "a1", "name": "legal_dof_watchdog"}]} if url.endswith("/api/agents") else {"id": "t"})
    assert v.main(["--fecha", "2026-11-05", "--dias", "1", "--dispatch"]) == 0
    assert sorted(json.loads(v.STATE_PATH.read_text())["entregadas"]) == [1, 2, 4]


def test_sin_credenciales_no_despacha(v, tmp_path, monkeypatch):
    (tmp_path / "agents.env").write_text("OTRA=1\n")
    assert v.main(["--fecha", "2026-11-05", "--dias", "1", "--dispatch"]) == 3


def test_ventana_de_dias_atiende_el_rezago(v):
    notas, total = v.seleccionar(v.DB_PATH, "2026-11-04", "2026-11-05", set(), 10)
    assert [n["cod_nota"] for n in notas] == [1, 2, 4, 5] and total == 4      # lo más reciente primero
    solo_hoy, _ = v.seleccionar(v.DB_PATH, "2026-11-05", "2026-11-05", set(), 10)
    assert 5 not in [n["cod_nota"] for n in solo_hoy]
