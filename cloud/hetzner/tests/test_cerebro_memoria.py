"""memoria_registrar: lo que se trabaja en Cowork queda en COWORK.md (y en LEARNINGS si hay aprendizaje), sin credenciales ni duplicados."""
import importlib
import sys
from pathlib import Path

import pytest

SERV = Path(__file__).resolve().parent.parent / "services"


@pytest.fixture()
def cerebro(tmp_path, monkeypatch):
    sp, en = tmp_path / "spaces", tmp_path / "entregables"
    sp.mkdir(); en.mkdir()
    monkeypatch.setenv("OPENCLAW_SPACES", str(sp))
    monkeypatch.setenv("ENTREGABLES_PATH", str(en))
    monkeypatch.setenv("CEREBRO_CACHE_TTL", "0")
    sys.path.insert(0, str(SERV))
    sys.modules.pop("cerebro_kawiil_mcp", None)
    return importlib.import_module("cerebro_kawiil_mcp"), sp


def test_registra_y_donna_lo_puede_leer(cerebro):
    m, sp = cerebro
    r = m.memoria_registrar("Escrito CNBV Vizum", "Se redactó la respuesta al oficio y quedó en borrador para Vo.Bo.", tipo="trabajo",
                            proyecto="Vizum", hecho="- Respuesta redactada", pendiente="- Polo · Vo.Bo. · viernes")
    assert r.startswith("✅")
    t = (sp / "COWORK.md").read_text()
    assert "Escrito CNBV Vizum" in t and "Polo · Vo.Bo. · viernes" in t
    assert "Escrito CNBV Vizum" in m.memoria_leer("COWORK")          # Cowork también puede releerlo


def test_aprendizaje_va_tambien_a_learnings(cerebro):
    m, sp = cerebro
    r = m.memoria_registrar("Formato de respuestas", "Polo pidió respuestas cortas con cifras primero.", tipo="aprendizaje",
                            aprendizaje="Prefiere la cifra primero y la explicación después")
    assert "LEARNINGS.md" in r
    assert "Prefiere la cifra primero" in (sp / "LEARNINGS.md").read_text()


def test_credenciales_se_retiran(cerebro):
    m, sp = cerebro
    r = m.memoria_registrar("Acceso proveedor", "Se configuró el acceso; la contraseña: Prueba7Clave42 quedó en el gestor.")
    assert "Prueba7Clave42" not in (sp / "COWORK.md").read_text()
    assert "credencial" in r


def test_no_duplica_el_mismo_dia(cerebro):
    m, sp = cerebro
    args = ("Junta Kailash", "Se acordó retomar la app móvil con Héctor y fijar fecha de entrega.")
    assert m.memoria_registrar(*args).startswith("✅")
    assert "no dupliqué" in m.memoria_registrar(*args)
    assert (sp / "COWORK.md").read_text().count("Junta Kailash") == 1


def test_validaciones(cerebro):
    m, _ = cerebro
    assert "tipo inválido" in m.memoria_registrar("Tema", "resumen suficientemente largo", tipo="otro")
    assert "Falta contenido" in m.memoria_registrar("ab", "corto")


def test_rotacion_archiva_lo_viejo_sin_perderlo(cerebro):
    m, sp = cerebro
    m.COWORK_FILE_MAX, m.COWORK_KEEP = 3000, 1500
    for i in range(40):
        m.memoria_registrar(f"Trabajo {i:02d}", "Resumen de trabajo número %d con texto suficiente para pesar." % i * 2)
    vivo, arch = (sp / "COWORK.md").read_text(), (sp / "COWORK-ARCHIVO.md").read_text()
    assert len(vivo) < 3500 and "Trabajo 39" in vivo and "Trabajo 00" not in vivo
    assert "Trabajo 00" in arch                                        # lo viejo se archivó, no se borró


def test_donna_lo_carga_como_memoria(tmp_path, monkeypatch):
    monkeypatch.setenv("PUSH_STATE_DIR", str(tmp_path / "st"))
    sys.path.insert(0, str(SERV))
    import louis_core as core
    assert "COWORK.md" in core.MEMORY_FILES
    largo = "# COWORK\n" + "".join(f"### e{i}\nx\n" for i in range(3000)) + "### ULTIMO-REGISTRO\nfinal\n"
    out = core._budget_memory("COWORK.md", largo)
    assert "ULTIMO-REGISTRO" in out and len(out) < 8000                # conserva lo más reciente y respeta el presupuesto
