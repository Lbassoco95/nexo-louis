"""Pruebas del extractor de leyes de biblioteca.py (sin servidor ni bases). Uso: python3 tests/test_biblioteca_leyes.py"""
import importlib.util
import pathlib

sp = importlib.util.spec_from_file_location("b", pathlib.Path(__file__).parent.parent / "services" / "biblioteca.py")
b = importlib.util.module_from_spec(sp)
sp.loader.exec_module(b)

CASOS = [
    ("DECRETO por el que se reforman y adicionan diversas disposiciones de la Ley Federal del Trabajo, en materia de subcontratación",
     [("reforma", "Ley Federal del Trabajo")]),
    ("DECRETO por el que se reforma el artículo 123 de la Constitución Política de los Estados Unidos Mexicanos",
     [("reforma", "Constitución Política de los Estados Unidos Mexicanos")]),
    ("DECRETO por el que se expide la Ley General de Salud Mental y se reforman otras disposiciones",
     [("expide", "Ley General de Salud Mental")]),
    ("DECRETO por el que se abroga la Ley de Navegación y se expide la Ley de Navegación y Comercio Marítimos",
     [("abroga", "Ley de Navegación"), ("expide", "Ley de Navegación y Comercio Marítimos")]),
    ("DECRETO por el que se adiciona un párrafo al artículo 5 del Código Civil Federal", [("reforma", "Código Civil Federal")]),
    ("DECRETO por el que se reforma el Reglamento de la Ley Federal de Competencia Económica",
     [("reforma", "Reglamento de la Ley Federal de Competencia Económica")]),
    ("DECRETO por el que se reforma la fracción I del artículo 10 de la Ley General de Educación (Segunda Sección)",
     [("reforma", "Ley General de Educación")]),
    ("DECRETO por el que se reforma la Ley General de los Derechos de Niñas, Niños y Adolescentes, en materia de adopción",
     [("reforma", "Ley General de los Derechos de Niñas, Niños y Adolescentes")]),
    ("DECRETO por el que se reforma la Ley General para la Inclusión de las Personas con Discapacidad",
     [("reforma", "Ley General para la Inclusión de las Personas con Discapacidad")]),
    ("DECRETO por el que se reforma la Ley sobre el Escudo, la Bandera y el Himno Nacionales",
     [("reforma", "Ley sobre el Escudo, la Bandera y el Himno Nacionales")]),
    ("DECRETO por el que se reforma la Ley de Adquisiciones, Arrendamientos y Servicios del Sector Público, y otras leyes",
     [("reforma", "Ley de Adquisiciones, Arrendamientos y Servicios del Sector Público")]),
    ("DECRETO por el que se expide la Ley de Ingresos de la Federación para el Ejercicio Fiscal de 2024",
     [("expide", "Ley de Ingresos de la Federación")]),
    ("DECRETO por el que se otorga una condecoración", []),
    ("", []),
]
for titulo, esperado in CASOS:
    obtenido = b.extraer_leyes(titulo)
    assert obtenido == esperado, (titulo, obtenido, esperado)
assert b.clave_ley("Ley de Navegación") == b.clave_ley("LEY DE  NAVEGACION")
print("ok:", len(CASOS), "casos")

# ── Identidad por Tailscale (BIBLIOTECA_TS_ALLOW) ──
import os
os.environ["BIBLIOTECA_TS_ALLOW"] = "polo@kawiil.mx, otra@kawiil.mx"
sp2 = importlib.util.spec_from_file_location("b2", pathlib.Path(__file__).parent.parent / "services" / "biblioteca.py")
b2 = importlib.util.module_from_spec(sp2)
sp2.loader.exec_module(b2)
ts = {"X-Forwarded-Proto": "https"}
assert b2.identidad({**ts, "Tailscale-User-Login": "Polo@Kawiil.mx"})[0] == "polo@kawiil.mx"
assert b2.identidad({**ts, "Tailscale-User-Login": "vturcott@x.com"})[0] is None      # otra cuenta de la red
assert b2.identidad({**ts})[0] is None                                                 # proxy sin identidad
assert b2.identidad({"Tailscale-User-Login": "polo@kawiil.mx"})[0] == "local"          # directo a loopback: sin cambios
assert b2.identidad({**ts, "Tailscale-User-Login": "x{y}@kawiil.mx"})[0] is None
os.environ["BIBLIOTECA_TS_ALLOW"] = ""
sp3 = importlib.util.spec_from_file_location("b3", pathlib.Path(__file__).parent.parent / "services" / "biblioteca.py")
b3 = importlib.util.module_from_spec(sp3)
sp3.loader.exec_module(b3)
assert b3.identidad({**ts, "Tailscale-User-Login": "polo@kawiil.mx"})[0] is None       # apagado por defecto
print("ok: identidad Tailscale")
