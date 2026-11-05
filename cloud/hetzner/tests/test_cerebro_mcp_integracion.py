"""Los 4 casos de aceptación contra el servidor MCP (herramientas llamadas directo, datos de prueba)."""
import importlib
import sys
from pathlib import Path

import pytest

SERV = Path(__file__).resolve().parent.parent / "services"
PASSWORD = "Prueba7Clave42"   # ficticia


@pytest.fixture()
def cerebro(tmp_path, monkeypatch):
    sp, en = tmp_path / "spaces", tmp_path / "entregables"
    sp.mkdir(); en.mkdir()
    monkeypatch.setenv("OPENCLAW_SPACES", str(sp))
    monkeypatch.setenv("ENTREGABLES_PATH", str(en))
    monkeypatch.setenv("CEREBRO_CACHE_TTL", "0")
    sys.path.insert(0, str(SERV))
    (sp / "AGENDA.md").write_text("# AGENDA\n" + "".join(
        f"- [ ] Pendiente {i} · tema  · [auto 2026-10-15]\n" for i in range(77)))
    (sp / "IMPORTANT.md").write_text(
        "## CRÍTICO\n- Vizum CNBV: PRÓRROGA OTORGADA hasta 1 junio 2026 (Oficio 221/DGPORPIA-14557680/2026).\n\n## IMPORTANTE\n- x\n")
    (sp / "JOURNAL.md").write_text(
        f"## 2026-05-23\n- algo\n## 2026-04-01\n- inventory password-protected ({PASSWORD}).\n")
    (en / "2026-09-01-nota.md").write_text(
        "---\ntitulo: Nota de análisis — Oficio CNBV 14558848/2026\ncliente: VIZUM TECHNOLOGIES\nestado: en_vobo\n"
        "fecha_actualizacion: 2026-09-01\n---\n- Oficio 221/DGPORPIA-14558848/2026 (31-ago-2026), sin plazo de respuesta.\n"
        "- Fecha límite operativa: 31-dic-2026.\n## Historial\n- 2026-09-01 — en_vobo\n")
    (en / "2026-09-08-oc6.md").write_text(
        "---\ntitulo: Oficio OC-006/2026 respuesta\ncliente: Vizum\nestado: borrador\nfecha_actualizacion: 2026-09-08\n---\n"
        "Envío a más tardar el 15-sep-2026 (vence 17-sep).\n")
    (en / "2026-08-30-sylon.md").write_text(
        "---\ntitulo: Sylon R13 agosto\ncliente: Sylon Capital\nestado: borrador\nfecha_actualizacion: 2026-08-30\n---\n"
        "Reporte R13 B-13211 de agosto, preparado.\n")
    import cerebro_kawiil_mcp as m
    importlib.reload(m)
    return m, sp


def test_a_agenda_77(cerebro):
    m, _ = cerebro
    out = m.agenda_pendientes()
    assert "Pendientes abiertos: 77 (mostrando 1–77)" in out and "Pendiente 76" in out


def test_b_plazo_vizum_no_es_el_de_mayo(cerebro):
    m, _ = cerebro
    out = m.plazos_vigentes("Vizum")
    vig = out.split("VENCIDOS")[0]
    assert "31-dic-2026" in vig and "14558848" in vig
    assert "1 junio 2026" not in vig
    assert "2026-09-17" in out.split("VENCIDOS")[1]   # OC-006 vencido, no vigente
    crit = m.memoria_leer("IMPORTANT", 4000).split("## HISTÓRICO")
    assert "PRÓRROGA" not in crit[0] and "PRÓRROGA" in crit[1]


def test_c_r13_sin_acuse(cerebro):
    m, _ = cerebro
    out = m.obligacion_estado("R13 B-13211")
    assert "SIN EVIDENCIA" in out and "PRESENTADO" not in out.split("\n")[0]


def test_d_password_no_aparece_nunca(cerebro):
    m, sp = cerebro
    for out in (m.memoria_leer("JOURNAL", 4000), m.memoria_buscar("password"), m.memoria_buscar(PASSWORD),
                m.agenda_snapshot(), m.cerebro_estado()):
        assert PASSWORD not in out
    sim = m.memoria_depurar(aplicar=False)
    assert "JOURNAL.md:4" in sim and PASSWORD not in sim and PASSWORD in (sp / "JOURNAL.md").read_text()
    m.memoria_depurar(aplicar=True)
    assert PASSWORD not in (sp / "JOURNAL.md").read_text()
    assert m.memoria_buscar(PASSWORD).startswith("Sin coincidencias")
    assert list(sp.glob("JOURNAL.md.bak-*"))


def test_journal_desactualizado_se_avisa(cerebro):
    m, _ = cerebro
    assert "DESACTUALIZADA" in m.cerebro_estado()


def test_auditoria_no_da_por_enviado_el_oc006(cerebro):
    m, _ = cerebro
    out = m.entregables_auditar()
    assert "plazo vencido, sin evidencia de envío" in out and "OC-006" in out


def test_legal_frescura(cerebro, tmp_path):
    import datetime, json, sqlite3
    m, _ = cerebro
    m._hoy = lambda: datetime.date(2026, 11, 5)
    dof, sjf = tmp_path / "dof.db", tmp_path / "sjf.db"
    with sqlite3.connect(dof) as c:
        c.execute("CREATE TABLE notas (cod_nota INTEGER, fecha TEXT)")
        c.executemany("INSERT INTO notas VALUES (?,?)", [(1, "2026-10-14"), (2, "2026-12-30"), (3, None)])
        c.execute("CREATE TABLE ediciones (cod_diario INTEGER, fecha TEXT, fetched_at TEXT)")
        c.executemany("INSERT INTO ediciones VALUES (?,?,?)", [(1, "2026-10-13", "2026-10-14T18:03:46"), (2, "2026-10-14", "2026-10-14T18:03:53")])
        c.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, mode TEXT, notes TEXT)")
        c.execute("INSERT INTO runs (started_at, finished_at, mode) VALUES ('2026-10-14T18:00:00', '2026-10-14T18:05:00', 'daily')")
        c.execute("INSERT INTO runs (started_at, finished_at, mode) VALUES (strftime('%Y-%m-%dT%H:%M:%S','now'), NULL, 'generate_pdfs')")
    with sqlite3.connect(sjf) as c:
        c.execute("CREATE TABLE tesis (registro_digital INTEGER, fecha_publicacion TEXT)")
        c.executemany("INSERT INTO tesis VALUES (?,?)", [(1, "2026-08-28 10:29:00.0"), (2, None), (3, "None"), (4, "2027-01-01")])
        c.execute("CREATE TABLE progress (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        c.execute("INSERT INTO progress VALUES ('waf_bloqueo', 'secreto', '2026-10-13T13:44:11')")
    m.DOF_DB_PATH, m.SJF_DB_PATH = dof, sjf
    out = json.loads(m.legal_frescura())
    assert out["DOF"]["ultimo_contenido"] == "2026-10-14" and out["DOF"]["dias_sin_avance"] == 22   # ignora el 2026-12-30
    assert out["DOF"]["ultima_descarga"] == "2026-10-14T18:03:53"
    assert out["DOF"]["corridas"]["terminadas"] == {"daily": "2026-10-14T18:05:00"}
    assert out["DOF"]["corridas"]["sin_terminar_24h"] == 1
    assert out["SJF"]["ultimo_contenido"] == "2026-08-28" and out["SJF"]["dias_sin_avance"] == 69
    assert out["SJF"]["marcas"] == {"waf_bloqueo": "2026-10-13T13:44:11"} and "secreto" not in json.dumps(out)


def test_legal_frescura_sin_bases(cerebro, tmp_path):
    import json
    m, _ = cerebro
    m.DOF_DB_PATH = m.SJF_DB_PATH = tmp_path / "no-existe.db"
    assert json.loads(m.legal_frescura()) == {"DOF": {"disponible": False}, "SJF": {"disponible": False}}
