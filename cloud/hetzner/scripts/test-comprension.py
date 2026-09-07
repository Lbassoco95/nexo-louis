#!/usr/bin/env python3
"""Pruebas de COMPRENSIÓN del router de Donna (sin red, sin credenciales).

Verifica que `needs_doc_sonnet` / `_es_analisis_legal` distingan:
  • un PEDIDO de documento               → sí genera archivo
  • una PREGUNTA de seguimiento          → NO genera archivo (hay que contestarla)
  • contexto PEGADO (oficio/recordatorio) → NO genera archivo por coincidencias lejanas

Caso que originó estas pruebas: Polo pegó el recordatorio del Oficio CNBV
411-2/1364/2026 y preguntó "…lo agregaste a kawiil central para que llevemos el
seguimiento?". El router leyó "Enviar" (línea 2) + "presentación" (de "constancia
de presentación en tiempo", línea 8) y devolvió un PPTX de "Análisis legal" en vez
de contestar la pregunta.

Uso:  python3 cloud/hetzner/scripts/test-comprension.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
import louis_core as core  # noqa: E402  (el módulo sigue llamándose louis_core)

CNBV = (
    "de este vencimiento ⏰ \U0001f534 HOY VENCE — Oficio CNBV 411-2/1364/2026 "
    "(Sylon Asesores). Enviar el correo a dllamas@cnbv.gob.mx con CC a "
    "comunicacionAA@cnbv.gob.mx y gonzalo@rivium.mx, adjuntando el escrito firmado "
    "(16 pp. con anexos) y el acuse de recibo v2 firmado. ⚠️ La CNBV solo recibe de "
    "forma digital de LUNES A VIERNES DE 9:00 A 15:00 HRS — enviar antes de las 11:00, "
    "nunca después de las 15:00. Guardar el correo de acuse con el folio: es la única "
    "constancia de presentación en tiempo. lo agregaste a kawiil central para que "
    "llevemos el seguimiento?"
)

# (mensaje, espera_documento)
CASOS_DOC = [
    # ── NO son pedidos de documento ─────────────────────────────────────────
    (CNBV, False),
    ("lo agregaste a kawiil central para que llevemos el seguimiento?", False),
    ("¿ya lo guardaste en la agenda?", False),
    ("oye, ¿lo registraste como entregable?", False),
    ("¿lo subiste a kawiil-central?", False),
    ("Guardar el correo de acuse con el folio: es la única constancia de "
     "presentación en tiempo. ¿lo anotaste?", False),
    ("acuérdate que hay que enviar el escrito antes de las 11:00, la constancia "
     "de presentación en tiempo es el acuse", False),
    ("ya te mandé el informe de Sylon", False),
    ("¿cuántas tesis tienen su PDF descargado?", False),
    # ── Pregunta CON formato explícito = sí es pedido (válvula de escape) ────
    ("¿me lo pasas en PDF?", True),
    ("¿me lo generas en powerpoint?", True),
    # Pasado ("mandaste") sigue leyéndose como estatus, no como orden: si Polo
    # quería el archivo, el modelo puede llamar `generar_documento` en el chat.
    ("¿me lo mandaste en word?", False),
    # ── SÍ son pedidos de documento ─────────────────────────────────────────
    ("hazme un informe del caso Sylon", True),
    ("genérame un PDF con el análisis del oficio CNBV", True),
    ("necesito una presentación para el consejo", True),
    ("prepárame un dictamen legal sobre el aviso de fe pública", True),
    ("pásame el deck de Yoltik", True),
    ("dame un excel con los movimientos de VIZUM", True),
    ("elabora el documento de respuesta a la CNBV", True),
]

# (mensaje, espera_flujo_legal_multiagente)
CASOS_LEGAL = [
    (CNBV, False),
    ("acuérdate del oficio CNBV que vence hoy", False),
    ("hay que contestarle al IMPI el lunes", False),
    ("hazme un análisis legal del oficio CNBV", True),
    ("necesito un dictamen jurídico sobre el amparo", True),
    ("prepárame un informe sobre la infracción administrativa ante PROFECO", True),
]

# (mensaje, formato_esperado)
CASOS_FORMATO = [
    ("es la única constancia de presentación en tiempo, hazme el informe", "html"),
    ("hazme una presentación del caso", "pptx"),
    ("mándame el powerpoint", "pptx"),
    ("dame el informe en word", "docx"),
    ("necesito el excel de movimientos", "xlsx"),
]


def main() -> int:
    fallas = []

    for msg, esperado in CASOS_DOC:
        got = core.needs_doc_sonnet(msg)
        if got != esperado:
            fallas.append(f"needs_doc_sonnet={got} (esperaba {esperado}): «{msg[:70]}…»")

    for msg, esperado in CASOS_LEGAL:
        got = core._es_analisis_legal(msg)
        if got != esperado:
            fallas.append(f"_es_analisis_legal={got} (esperaba {esperado}): «{msg[:70]}…»")

    for msg, esperado in CASOS_FORMATO:
        got = core._doc_tipo_de_mensaje(msg)
        if got != esperado:
            fallas.append(f"_doc_tipo_de_mensaje={got} (esperaba {esperado}): «{msg[:70]}…»")

    # La pregunta de seguimiento se detecta como tal (con signo de interrogación),
    # pero la ORDEN equivalente no debe confundirse con pregunta.
    if not core.es_pregunta_de_seguimiento(CNBV):
        fallas.append("es_pregunta_de_seguimiento(CNBV) debería ser True")
    if core.es_pregunta_de_seguimiento("agrégalo a kawiil central para el seguimiento"):
        fallas.append("una ORDEN no debe leerse como pregunta de seguimiento")

    total = len(CASOS_DOC) + len(CASOS_LEGAL) + len(CASOS_FORMATO) + 2
    if fallas:
        print(f"❌ {len(fallas)} de {total} fallaron:\n")
        for f in fallas:
            print("  •", f)
        return 1
    print(f"✅ {total} casos de comprensión OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
