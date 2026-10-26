"""Pruebas de cerebro_calidad con fragmentos reales del Cerebro (26-oct-2026)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))
import cerebro_calidad as cc  # noqa: E402

HOY = date(2026, 10, 26)

AGENDA_REAL = """# AGENDA
## Para HOY
- [ ] HOY 15-oct · DoctoCliq: cese de Emmanuel ("hoy mismo", Vivi 08:25): finiquito  · [auto 2026-10-15]
- [ ] HOY 15-oct · Comentarios al informe del Calpulli antes del Consejo  · [auto 2026-10-15]
- [ ] Litigios · Reivindicatoria exp. 37/2016 (juzgado 25): Habib no figura autorizado  · [auto 2026-10-15]
- [ ] Litigios · Sucesorio exp. 2301/2014 (juzgado 9 familiar): acuerdos del 6-oct  · [auto 2026-10-15]
- [ ] Rivium · enviar en ceros los reportes R03 J-0314 y R27 B-2711 (Gonzalo, 5-oct)  · [auto 2026-10-15]
- [ ] Escrito de Temporalidad  · [visto 2026-10-15]
- [ ] Pendiente fresco de ayer  · [auto 2026-10-25]
- [ ] Pendiente sin sello
- [x] Cerrado, no cuenta  · [auto 2026-10-01]
""" + "".join(f"- [ ] Relleno {i}  · [auto 2026-10-15]\n" for i in range(69))


def test_agenda_77_completos_y_sin_truncar():
    items = cc.parse_agenda(AGENDA_REAL, HOY)
    assert len(items) == 77
    out = cc.render_agenda(items, HOY)
    assert "Pendientes abiertos: 77 (mostrando 1–77)" in out
    assert "Relleno 68" in out and "faltan" not in out


def test_paginacion():
    items = cc.parse_agenda(AGENDA_REAL, HOY)
    out = cc.render_agenda(items, HOY, offset=0, limite=20)
    assert "faltan 57; pide offset=20" in out


def test_sellos_viejos_y_hoy_vencido():
    items = {i["texto"][:12]: i for i in cc.parse_agenda(AGENDA_REAL, HOY)}
    assert any(f.startswith("SIN REVISAR (sello de hace 11 d)") for f in items["HOY 15-oct ·"]["flags"])
    assert any(f == "VENCIDO: decía HOY 2026-10-15" for f in items["HOY 15-oct ·"]["flags"])
    fresco = [i for i in cc.parse_agenda(AGENDA_REAL, HOY) if "fresco" in i["texto"]][0]
    assert fresco["flags"] == []
    sin = [i for i in cc.parse_agenda(AGENDA_REAL, HOY) if "sin sello" in i["texto"]][0]
    assert sin["flags"][0].startswith("SIN REVISAR")


def test_render_no_dice_hoy_con_fecha_pasada():
    out = cc.render_agenda(cc.parse_agenda(AGENDA_REAL, HOY), HOY)
    for l in out.splitlines():
        assert not l.lstrip(" 0123456789.").startswith("HOY 15-oct")
    assert "(era HOY 15-oct" in out


def test_fecha_visto_no_se_presenta_como_actualizacion_real():
    it = [i for i in cc.parse_agenda(AGENDA_REAL, HOY) if "Temporalidad" in i["texto"]][0]
    assert not it["fecha_es_real"]
    assert "primer visto" in cc.render_item(it)


def test_regulatorio_sin_acuse_se_marca():
    it = [i for i in cc.parse_agenda(AGENDA_REAL, HOY) if "R27" in i["texto"]][0]
    assert "sin evidencia de acuse/folio" in it["flags"]


def test_normalizar_solo_forma_e_idempotente():
    nuevo, n = cc.normalizar_agenda(AGENDA_REAL, HOY)
    assert n > 0 and "(era HOY 15-oct)" in nuevo and "[SIN REVISAR al 2026-10-26]" in nuevo
    assert "finiquito" in nuevo  # el fondo no cambia
    nuevo2, n2 = cc.normalizar_agenda(nuevo, HOY)
    assert n2 == 0 and nuevo2 == nuevo


IMPORTANT = """# IMPORTANT
## CRÍTICO (siempre interrumpir)

### 1. Plazos regulatorios CNBV en vivo
- Cualquier oficio CNBV con plazo (20 días hábiles típicos) tiene prioridad absoluta.
- Vizum tiene 2 frentes activos: IDNP (Oficio 311-751972/2026) y observaciones (Oficio 221/DGPORPIA-14556950/2026, deadline original 14-may-2026 → **PRÓRROGA OTORGADA: 1 junio 2026** (Oficio 221/DGPORPIA-14557680/2026, 30-abr-2026). 10 días hábiles adicionales, 19-may al 1-jun-2026. ⚠️ QUEDAN ~5 DÍAS HÁBILES.).
- Cuando llega oficio nuevo: 1) acuse en ≤2 días hábiles.
- Fecha límite operativa vigente: 31-dic-2026 (evidencia de medidas del oficio 14558848/2026).

## IMPORTANTE (mismo día)
- Onboarding KYB.
"""


def test_plazo_cnbv_de_mayo_es_historico():
    cl = cc.clasificar_critico(IMPORTANT, HOY)
    assert len(cl["historicos"]) == 1 and "1 junio 2026" in cl["historicos"][0][1]
    assert len(cl["vigentes"]) == 1 and "31-dic-2026" in cl["vigentes"][0][1]
    assert len(cl["reglas"]) == 2  # reglas sin fecha: no son plazos


def test_separar_vigente_historico():
    out = cc.separar_vigente_historico(IMPORTANT, HOY)
    crit = out.split("## HISTÓRICO")[0]
    assert "PRÓRROGA OTORGADA" not in crit and "QUEDAN ~5" not in crit
    hist = out.split("## HISTÓRICO")[1].split("## IMPORTANTE")[0]
    assert "[venció 2026-06-01]" in hist and "PRÓRROGA OTORGADA" in hist
    assert "## IMPORTANTE" in out and "Onboarding KYB" in out


def test_ultima_fecha_journal():
    j = "## 2026-05-23\n- x\n\n- [2026-05-28 19:12] Vizum\n"
    assert cc.ultima_fecha_journal(j, HOY) == date(2026, 5, 28)


def test_credencial_de_bitacora_se_retira():
    linea = ("- Recap: Sylon Capital audit, Credix SOFOM, K'awiil inventory password-protected (Prueba7Clave42).\n"
             "  Ref: `80cc4707`.")
    limpio, h = cc.redactar_credenciales(linea)
    assert "Prueba7Clave42" not in limpio and cc.MARCA_CRED in limpio
    assert len(h) == 1 and "Prueba7Clave42" not in str(h) and h[0]["linea"] == 1
    # idempotente
    assert cc.redactar_credenciales(limpio)[1] == []


def test_redaccion_no_toca_texto_normal():
    t = "Viri/Jesús · clave SAT 84121600; factura exenta; token de acceso del cliente pendiente\nFolio CNBV 30176"
    assert cc.redactar_credenciales(t) == (t, [])


def test_tokens_conocidos():
    limpio, h = cc.redactar_credenciales("usa sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 y ghp_" + "a" * 36)
    assert "sk-ant" not in limpio and "ghp_" not in limpio and len(h) == 2


def _e(archivo, titulo, estado, fecha, cuerpo="", cliente="Vizum Technologies"):
    return {"archivo": archivo, "titulo": titulo, "estado": estado, "fecha_actualizacion": fecha,
            "cuerpo": cuerpo, "cliente": cliente}


def test_oc006_no_se_da_por_superado_ni_enviado_por_el_acuse_oc005():
    oc5 = _e("a", "Acuse de recibo VIZUM-CASFIM22443/OC-005/2026 — Oficio 221/DGPORPIA-14558785/2026",
             "archivado", "2026-09-28",
             "## Historial\n- 2026-09-28 — `archivado`: auto-archivado sin Vo.Bo. en 21d")
    oc6 = _e("b", "Oficio VIZUM-CASFIM22443/OC-006/2026 — Respuesta al Oficio CNBV 221/DGPORPIA-14558785/2026",
             "borrador", "2026-09-08",
             "Envío a más tardar el 15-sep-2026 (vence 17-sep).\n## Historial\n- 2026-09-08 — Registrado como `borrador`")
    r = cc.auditar_entregables([oc5, oc6], HOY)
    assert len(r) == 1
    assert r[0]["estado_real_sugerido"].startswith("plazo vencido, sin evidencia de envío")
    assert not any("superado" in h for h in r[0]["hallazgos"])
    assert cc.archivado_por_inactividad(oc5["cuerpo"])


def test_superado_por_version_final():
    a = _e("a", "Términos y Condiciones — Propuesta de adecuaciones (SMS y WhatsApp)", "en_vobo", "2026-08-10",
           cliente="Rivium")
    b = _e("b", "Términos y Condiciones — Versión FINAL (con adecuaciones SMS/WhatsApp)", "archivado", "2026-09-02",
           cliente="Rivium")
    r = cc.auditar_entregables([a, b], HOY)[0]
    assert r["estado_real_sugerido"] == "superado por versión posterior (versión FINAL)"


def test_enviado_sin_acuse_queda_por_validar():
    e = _e("a", "Escrito X", "borrador", "2026-10-20", "## Historial\n- 2026-10-21 — enviado a la CNBV")
    assert "sin acuse/folio (por validar)" in cc.auditar_entregables([e], HOY)[0]["estado_real_sugerido"]


def test_matching_kawiil_central():
    pend = [
        {"n": 1, "texto": "Litigios · Reivindicatoria exp. 37/2016 (juzgado 25): Habib no figura autorizado"},
        {"n": 2, "texto": "Empathy · corte de cuentas (17-sep): saldo $101,385.44, honorarios $62,471.14"},
        {"n": 3, "texto": "Rivium · enviar en ceros los reportes R03 J-0314 y R27 B-2711"},
    ]
    tareas = [
        {"id": "t1", "titulo": "Litigios: seguimiento exp. 37/2016 juzgado 25", "descripcion": ""},
        {"id": "t2", "titulo": "Facturar mensualidad Empathy", "descripcion": ""},
    ]
    r = {x["n"]: x for x in cc.emparejar(pend, tareas)}
    assert r[1]["nivel"] == "coincidencia fuerte" and r[1]["tarea_id"] == "t1"
    assert r[2]["nivel"] == "sin tarea en el tablero"   # mismo cliente pero otro asunto: no se liga
    assert r[3]["nivel"] == "sin tarea en el tablero"
    # vínculo explícito gana
    pend[1]["texto"] += " [KC:t2]"
    assert {x["n"]: x for x in cc.emparejar(pend, tareas)}[2]["nivel"] == "vinculada"


def test_r13_sylon_sin_acuse_dice_sin_evidencia():
    fuentes = {"AGENDA": "- [ ] Sylon · validar R24 (RFC/CURP)\n- Sylon R13 B-13211 de agosto: preparar reporte",
               "entregables": "Reporte R13 B-13211 Sylon agosto: enviado a la CNBV el 12-sep"}
    r = cc.evaluar_obligacion("R13 B-13211", fuentes)
    assert r["estado"] == "sin evidencia" and len(r["menciones_sin_soporte"]) == 2


def test_con_acuse_dice_presentado():
    fuentes = {"entregables": "Reporte R13 B-13211 Sylon agosto presentado en SITI. Acuse SITI folio 2026-0912345"}
    r = cc.evaluar_obligacion("R13 B-13211", fuentes)
    assert r["estado"].startswith("presentado") and "2026-0912345" in r["soportes"][0]["acuse_o_folio"]
