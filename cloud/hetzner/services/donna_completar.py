#!/usr/bin/env python3
"""donna_completar.py — COMPLETAR el texto que le falta a una junta guardada usando su audio.

Caso real: una junta tiene audio de varias horas (o de varios tramos ligados a mano) pero el texto en vivo solo cubre una parte, porque la transcripción se cayó o porque ese audio nunca tuvo texto.
Aquí se mide qué partes del audio NO tienen texto, se omite el silencio, y SOLO esas partes se mandan a OpenAI (transcripción + quién habló). El texto nuevo se AGREGA al final de la misma junta
(nunca reemplaza ni toca lo que ya hay). Los audios que se encimen en el reloj de la junta (típico al ligar a mano audios sueltos, todos empiezan en 0) se acomodan uno tras otro.
Solo corre cuando Polo lo pide con un botón y confirma que el audio sale hacia OpenAI.
"""
import re
import shutil
import subprocess
import time
from pathlib import Path

import donna_audio
import donna_diariza
import donna_personas
import donna_reconstruir as R

GAP_S = 150                 # un hueco de texto de al menos esto (s) cuenta como «sin transcribir»
PEGA_S = 90                 # una intervención cubre hasta la siguiente, con este máximo
COLA_S = 45                 # lo que cubre la última intervención
MAX_FALTA_S = 12 * 3600     # tope por pedido
VOZ_MARGEN_S = 5.0          # un minuto cuenta como «con voz» si sus segundos más fuertes superan el piso de ruido en al menos esto (dB)
MARGEN_S = 30               # a cada tramo con voz se le agrega este margen por lado


def _clave(sesion_id: str) -> str:
    return "junta-" + sesion_id


def estado(sesion_id: str) -> dict:
    return R.estado(_clave(sesion_id))


# ------------------------------------------------------------------ qué partes del audio ya tienen texto
def _cobertura(sesion_id: str) -> list:
    """Intervalos (segundos del reloj de la junta) que ya tienen texto: cada intervención cubre hasta la siguiente (máx. PEGA_S) y la última COLA_S."""
    donna_personas.asegurar_esquema()
    c = donna_personas._c()
    try:
        horas = [donna_audio._ms_hora(r["hora"]) / 1000.0 for r in c.execute("SELECT hora FROM intervenciones WHERE sesion_id=? AND hora!='' ORDER BY id", (sesion_id,))]
    finally:
        c.close()
    ts = sorted(set(horas))
    iv = []
    for i, t in enumerate(ts):
        fin = min(ts[i + 1], t + PEGA_S) if i + 1 < len(ts) else t + COLA_S
        if iv and t <= iv[-1][1] + 1:
            iv[-1][1] = max(iv[-1][1], fin)
        else:
            iv.append([t, fin])
    return [(a, b) for a, b in iv]


def _unir(iv: list) -> list:
    out = []
    for a, b in sorted((float(x[0]), float(x[1])) for x in iv):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _huecos(cob: list, a: float, b: float, gap: float = GAP_S) -> list:
    """Partes de [a, b] sin texto que duran al menos `gap` segundos."""
    out, cur = [], a
    for ca, cb in cob:
        if cb <= cur:
            continue
        if ca >= b:
            break
        if ca - cur >= gap:
            out.append((cur, min(ca, b)))
        cur = max(cur, cb)
    if b - cur >= gap:
        out.append((cur, b))
    return out


def _layout(sesion_id: str):
    """→ (tramos con su lugar en el reloj, lo que habría que mover). Los que se encimen se acomodan después; no toca la base."""
    tr = donna_audio.tramos(sesion_id)
    infos = [{"aid": t["aid"], "ini": t["offset_ms"] / 1000.0, "dur": donna_audio.duracion_tramo(t["aid"]), "creado": t["creado"], "activo": donna_audio.activo(t)} for t in tr]
    infos = [i for i in infos if i["dur"] > 0]
    mover = donna_audio.reubicar(sesion_id, aplicar=False)
    nuevo = {m["aid"]: m["a_ms"] / 1000.0 for m in mover}
    for i in infos:
        i["ini"] = nuevo.get(i["aid"], i["ini"])
        i["movido"] = i["aid"] in nuevo
    return sorted(infos, key=lambda i: i["ini"]), mover


def planear(sesion_id: str) -> dict:
    """Qué falta transcribir en esta junta (sin gastar nada): por audio, los huecos sin texto. → {tramos, falta_s, reubicar, activo, estado}"""
    infos, mover = _layout(sesion_id)
    est = estado(sesion_id)
    cob = _unir(_cobertura(sesion_id) + [tuple(x) for x in est.get("revisado", [])])           # lo ya revisado (aunque fuera silencio) no se vuelve a ofrecer
    out, total = [], 0.0
    for i in infos:
        hs = _huecos(cob, i["ini"], i["ini"] + i["dur"])
        falta = sum(b - a for a, b in hs)
        total += falta
        out.append({"aid": i["aid"], "desde_s": round(i["ini"]), "dur_s": round(i["dur"]), "creado": i["creado"], "movido": i["movido"], "huecos": [[round(a), round(b)] for a, b in hs], "falta_s": round(falta)})
    return {"tramos": out, "falta_s": round(total), "reubicar": mover, "activo": any(i["activo"] for i in infos), "estado": est}


# ------------------------------------------------------------------ omitir el silencio
def _serie_rms(audio: Path) -> list:
    """Nivel RMS (dBFS) de cada segundo del audio (ffmpeg astats)."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(audio), "-af",
                        "aresample=16000,asetnsamples=n=16000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=7200)
    out = []
    for m in re.finditer(r"lavfi\.astats\.Overall\.RMS_level=(\S+)", r.stdout):
        x = m.group(1)
        out.append(-90.0 if x in ("-inf", "inf") else max(-90.0, float(x)))
    return out


def _tramos_con_voz(serie: list, a: float, b: float, piso: float) -> list:
    """Dentro de [a, b] (segundos del audio), los pedazos donde HAY voz: un minuto cuenta si sus segundos más fuertes pasan el piso de ruido; se unen los que estén a menos de 5 min y se les da margen."""
    activos = []
    m = int(a // 60)
    while m * 60 < b:
        seg = serie[m * 60:(m + 1) * 60]
        if len(seg) >= 3:
            fuertes = sorted(seg)[int(len(seg) * 0.9):]                    # el 10 % de los segundos más fuertes de ese minuto
            if fuertes and sum(fuertes) / len(fuertes) > piso + VOZ_MARGEN_S:
                activos.append(m)
        m += 1
    spans, ini, prev = [], None, None
    for m in activos:
        if ini is None:
            ini = prev = m
        elif m - prev <= 5:
            prev = m
        else:
            spans.append((ini, prev))
            ini = prev = m
    if ini is not None:
        spans.append((ini, prev))
    out = []
    for i, f in spans:
        s, e = max(a, i * 60 - MARGEN_S), min(b, (f + 1) * 60 + MARGEN_S)
        if e - s >= 20:
            out.append((s, e))
    return out


# ------------------------------------------------------------------ completar
def procesar(sesion_id: str, usuario: str = "admin", llamar=None, key: str = None, trabajo_base: Path = None, traducir=None) -> dict:
    """Pasada completa. Nunca lanza: deja el resultado (o el error) en la tabla. Agrega el texto nuevo al final de la MISMA junta, audio por audio (si se interrumpe, lo ya hecho se conserva)."""
    clave = _clave(sesion_id)
    try:
        pc = donna_personas._c()
        try:
            fila = pc.execute("SELECT usuario FROM sesiones_escucha WHERE id=?", (sesion_id,)).fetchone()
        finally:
            pc.close()
        if not fila or (fila["usuario"] != usuario and usuario != "admin"):
            return {"estado": "error", "error": "junta no encontrada"}
        plan = planear(sesion_id)
        if plan["activo"]:
            return {"estado": "error", "error": "Esta junta tiene un audio que se está grabando ahora mismo. Termina y guarda primero."}
        if plan["falta_s"] <= 0:
            R._guardar(clave, "lista", {**{k: v for k, v in plan["estado"].items() if k in ("turnos", "min_revisados", "min_sin_voz", "audios")}, "revisado": plan["estado"].get("revisado", []), "sin_hueco": True})
            return {"estado": "lista", "turnos": 0, "sin_hueco": True}
        if plan["falta_s"] > MAX_FALTA_S:
            return {"estado": "error", "error": "Faltan más de 12 horas de texto: pídelo por partes (borra o separa los audios que no necesitas)."}
    except Exception as ex:
        return {"estado": "error", "error": str(ex)[:300]}
    revisado = [tuple(x) for x in plan["estado"].get("revisado", [])]
    guardar = lambda est, datos: R._guardar(clave, est, {**datos, "revisado": revisado})        # lo ya revisado viaja en cada estado: si se interrumpe, no se repite ni se vuelve a gastar
    guardar("procesando", {"progreso": "preparando"})
    base_dir = (trabajo_base or donna_audio.DIR.parent / "diariza_tmp") / ("comp-" + sesion_id)
    try:
        import app_media
        key = key if key is not None else app_media.openai_key()
        if not key and llamar is None:
            raise RuntimeError("Falta OPENAI_API_KEY en el servidor")
        if plan["reubicar"]:
            donna_audio.reubicar(sesion_id, aplicar=True)               # los audios que se encimaban se acomodan uno tras otro
        plan = planear(sesion_id)
        pc = donna_personas._c()
        try:
            sig_voz = int(pc.execute("SELECT COALESCE(MAX(voz_n),0) FROM intervenciones WHERE sesion_id=?", (sesion_id,)).fetchone()[0]) + 1
        finally:
            pc.close()
        nuevos = revisados_s = omitidos = 0.0
        traducidos = sin_traducir = 0
        total_t = len([t for t in plan["tramos"] if t["huecos"]])
        hechos = 0
        for t in plan["tramos"]:
            if not t["huecos"]:
                continue
            hechos += 1
            audio = donna_audio.ensamblar(t["aid"])
            serie = _serie_rms(audio)
            piso = sorted(serie)[int(len(serie) * 0.2)] if serie else -60.0
            lineas, ult = [], 0.0
            piezas_hechas = [0]
            for hi, (ha, hb) in enumerate(t["huecos"]):
                a, b = ha - t["desde_s"], hb - t["desde_s"]
                spans = _tramos_con_voz(serie, a, b, piso)
                omitidos += (b - a) - sum(e - s for s, e in spans)
                for si, (s, e) in enumerate(spans):
                    prog = lambda k, n, h=hechos: guardar("procesando", {"progreso": "audio %d de %d · pieza %d de %d" % (h, total_t, k + 1, n)})
                    segs, info = donna_diariza.diarizar_archivo(key, audio, base_dir / t["aid"] / ("h%d_%d" % (hi, si)), llamar, prog, e - s, desde_s=s)
                    revisados_s += e - s
                    turnos = R._turnos(segs)
                    voz = {}
                    for tn in turnos:
                        if tn["spk"] not in voz:
                            voz[tn["spk"]] = sig_voz
                            sig_voz += 1
                        lineas.append({"t": R._hora(tn["ini"] + t["desde_s"]), "voz": voz[tn["spk"]], "es": tn["texto"], "orig": "", "idioma": ""})
                        ult = max(ult, tn["fin"] + t["desde_s"])
            if lineas:
                h, sn = R.aplicar_traduccion(lineas, traducir)                                      # lo que se habló en chino queda en español (el original se conserva)
                traducidos += h
                sin_traducir += sn
                cuando = time.strftime("%d-%b-%Y a las %H:%M", time.localtime(t["creado"]))
                marca = {"t": lineas[0]["t"], "voz": 0, "es": "— Texto reconstruido desde el audio grabado el %s —" % cuando, "orig": "", "idioma": ""}
                donna_personas.anexar_lineas(sesion_id, [marca] + lineas, usuario, int(ult))
                nuevos += len(lineas)
            revisado = _unir(revisado + [(ha, hb) for ha, hb in t["huecos"]])                   # todo el hueco queda revisado (con texto o sin voz)
        guardar("lista", {"turnos": int(nuevos), "min_revisados": round(revisados_s / 60), "min_sin_voz": round(omitidos / 60), "audios": total_t, "traducidos": traducidos, "sin_traducir": sin_traducir})
        return {"estado": "lista", "turnos": int(nuevos), "min_revisados": round(revisados_s / 60), "min_sin_voz": round(omitidos / 60), "traducidos": traducidos, "sin_traducir": sin_traducir}
    except Exception as ex:
        guardar("error", {"error": str(ex)[:300]})
        return {"estado": "error", "error": str(ex)[:300]}
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
