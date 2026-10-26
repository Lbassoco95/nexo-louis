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
