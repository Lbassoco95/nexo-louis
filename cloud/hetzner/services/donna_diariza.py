#!/usr/bin/env python3
"""donna_diariza.py — segunda pasada de VOCES sobre el audio completo de una junta (cuando ya se guardó).

En vivo, las voces se distinguen solo por el tono y se equivocan cuando la gente se interrumpe. Aquí el audio guardado (donna_audio) se manda a OpenAI
(`gpt-4o-transcribe-diarize`: transcribe y etiqueta quién habló, con tiempos) y el resultado se ALINEA con la transcripción en vivo. NO se cambia nada solo:
queda una PROPUESTA («en el 04:31 hablaba la Voz 3, no la 1») que Polo revisa y aplica.

Ojo: el formato de la API se implementó según su documentación pública pero NO se ha probado contra la API real en este repositorio;
`tools/probar-diarizacion.py` lo prueba con un audio de verdad antes de confiar en él. Cada llamada queda registrada (tiempos y resultado) para depurar.
"""
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import donna_audio
import donna_db
import donna_personas

MODELO = os.environ.get("DONNA_MODELO_DIARIZA", "gpt-4o-transcribe-diarize")
URL = os.environ.get("DONNA_URL_DIARIZA", "https://api.openai.com/v1/audio/transcriptions")
MAX_BYTES = int(os.environ.get("DONNA_DIARIZA_MAX_BYTES", str(24 * 1024 * 1024)))
MAX_S = int(float(os.environ.get("DONNA_DIARIZA_MAX_H", "4")) * 3600)            # la revisión de voces cubre hasta 4 h de audio por junta (tope de tiempo y de costo)
PIEZA_S = int(os.environ.get("DONNA_DIARIZA_PIEZA_S", "1200"))                  # OpenAI acepta como máximo 1400 s por llamada: piezas de 20 min
LAG_MS = 1500                                                                    # el texto de un turno llega ~1.5 s después de que empieza a hablar
UMBRAL = 0.6                                                                     # un turno cambia de voz solo si esa voz ocupa ≥60 % de su tiempo
SCHEMA = "CREATE TABLE IF NOT EXISTS junta_voces (sesion_id TEXT PRIMARY KEY, ts INTEGER NOT NULL, estado TEXT NOT NULL, datos TEXT NOT NULL DEFAULT '{}');"


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    return c


# ------------------------------------------------------------------ audio → piezas que OpenAI acepta
def _dur(p: Path) -> float:
    return donna_audio._duracion(p)


def preparar(audio: Path, trabajo: Path, max_s: float = None) -> list:
    """→ [(inicio_s, ruta_mp3)]. Mono, 16 kHz, 32 kbps (≈14 MB por hora). Si pasa del límite, se parte en piezas."""
    trabajo.mkdir(parents=True, exist_ok=True)
    dur = _dur(audio)
    if dur <= 0:
        raise ValueError("no pude leer el audio de la junta")
    piezas, ini, k = [], 0.0, 0
    paso = min(float(PIEZA_S), MAX_BYTES / 4000.0)                          # nunca más de PIEZA_S segundos por llamada, ni de lo que cabe en el límite de tamaño
    tope = min(dur, max_s) if max_s is not None else dur                    # solo se preparan los primeros `max_s` segundos
    while ini < tope - 0.5:
        out = trabajo / ("pieza%02d.mp3" % k)
        cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(audio), "-ss", "%.2f" % ini, "-t", "%.2f" % paso, "-af", donna_audio.AF_VOZ, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)]
        r = subprocess.run(cmd, capture_output=True, timeout=1800)
        if r.returncode != 0 or not out.exists():
            raise ValueError("ffmpeg no pudo preparar el audio")
        piezas.append((ini, out))
        ini += paso
        k += 1
    return piezas


def _referencias(segs: list, pieza: Path, trabajo: Path, max_voces: int = 4) -> list:
    """Muestras de voz (4–9 s) de las voces más habladas de la primera pieza, para que las etiquetas sigan siendo las mismas en las demás piezas."""
    por = defaultdict(list)
    for s in segs:
        por[s["spk"]].append(s)
    out = []
    for lab, L in sorted(por.items(), key=lambda kv: -sum(x["fin"] - x["ini"] for x in kv[1]))[:max_voces]:
        buenos = [x for x in L if x["fin"] - x["ini"] >= 4] or L
        s = max(buenos, key=lambda x: x["fin"] - x["ini"])
        dur = min(9.0, max(2.0, s["fin"] - s["ini"]))
        w = trabajo / ("ref_%s.wav" % re.sub(r"\W", "", lab))
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(pieza), "-ss", "%.2f" % s["ini"], "-t", "%.2f" % dur, "-ac", "1", "-ar", "16000", str(w)], capture_output=True, timeout=120)
        if w.exists() and w.stat().st_size > 1000:
            out.append((lab, "data:audio/wav;base64," + base64.b64encode(w.read_bytes()).decode()))
    return out


# ------------------------------------------------------------------ llamada a OpenAI
def _multipart(campos: list, archivo: Path) -> tuple:
    b = "----donna" + secrets.token_hex(8)
    cuerpo = bytearray()
    for k, v in campos:
        cuerpo += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode()
    cuerpo += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: audio/mpeg\r\n\r\n' % (b, archivo.name)).encode() + archivo.read_bytes() + ("\r\n--%s--\r\n" % b).encode()
    return bytes(cuerpo), "multipart/form-data; boundary=" + b


def llamar_openai(key: str, archivo: Path, referencias: list = None) -> dict:
    campos = [("model", MODELO), ("response_format", "diarized_json"), ("chunking_strategy", "auto")]
    for nombre, data_url in (referencias or []):
        campos += [("known_speaker_names[]", nombre), ("known_speaker_references[]", data_url)]
    cuerpo, ctype = _multipart(campos, archivo)
    req = urllib.request.Request(URL, data=cuerpo, headers={"Authorization": "Bearer " + key, "Content-Type": ctype})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=1800).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError("OpenAI respondió %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))


def leer_segmentos(resp: dict) -> list:
    """Respuesta de la API → [{spk, ini, fin, texto}] (segundos). Tolera nombres de campo distintos."""
    out = []
    for s in (resp.get("segments") or []) if isinstance(resp, dict) else []:
        if not isinstance(s, dict):
            continue
        try:
            spk = str(s.get("speaker") or s.get("speaker_label") or s.get("speaker_id") or "?")
            ini, fin = float(s.get("start", s.get("start_time"))), float(s.get("end", s.get("end_time")))
        except (TypeError, ValueError):
            continue
        if fin > ini:
            out.append({"spk": spk, "ini": ini, "fin": fin, "texto": str(s.get("text") or "").strip()})
    return out


def diarizar_archivo(key: str, audio: Path, trabajo: Path, llamar=None, progreso=None, max_s: float = None) -> tuple:
    """Todo el audio de un tramo → (segmentos con tiempos absolutos DEL TRAMO en segundos, info). Si hay varias piezas intenta mantener las mismas etiquetas con muestras de voz."""
    llamar = llamar or (lambda a, refs=None: llamar_openai(key, a, refs))
    piezas = preparar(audio, trabajo, max_s)
    todos, refs, info = [], None, {"piezas": len(piezas), "etiquetas_alineadas": len(piezas) == 1, "omitido_s": max(0.0, _dur(audio) - max_s) if max_s is not None else 0.0}
    for k, (ini, mp3) in enumerate(piezas):
        if progreso:
            progreso(k, len(piezas))
        try:
            resp = llamar(mp3, refs)
        except RuntimeError:
            if refs:                                                  # la API no aceptó las muestras de voz: seguimos sin ellas (las etiquetas de cada pieza irán por separado)
                refs, info["etiquetas_alineadas"] = None, False
                resp = llamar(mp3, None)
            else:
                raise
        segs = leer_segmentos(resp)
        if not segs and not (resp.get("text") or "").strip():
            continue
        for s in segs:
            s["ini"] += ini
            s["fin"] += ini
            s["pieza"] = k
            if not info["etiquetas_alineadas"]:
                s["spk"] = "p%d:%s" % (k, s["spk"])
        todos += segs
        if k == 0 and len(piezas) > 1:
            refs = _referencias(segs, mp3, trabajo)
            info["etiquetas_alineadas"] = bool(refs)
            if not refs:
                for s in todos:
                    s["spk"] = "p0:" + s["spk"]
    return todos, info


# ------------------------------------------------------------------ alinear con la transcripción en vivo y proponer
def _solape(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def proponer(turnos: list, segs: list, umbral: float = UMBRAL) -> dict:
    """turnos: [{id, ini_ms, fin_ms, voz_n, nombre?, texto?}] · segs: [{spk, ini_ms, fin_ms}] en el MISMO reloj.
    Cada etiqueta del identificador se empareja (una a una, por tiempo en común) con una voz de la transcripción; los turnos cuya voz dominante es otra se proponen para cambiar."""
    M = defaultdict(float)                                   # (grupo, etiqueta, voz) → ms en común
    dom = {}
    gr = lambda s: s.get("grp", "")
    for i, t in enumerate(turnos):
        a0, a1 = t["ini_ms"] - LAG_MS, t["fin_ms"] - LAG_MS
        por = defaultdict(float)
        for s in segs:
            ov = _solape(a0, a1, s["ini_ms"], s["fin_ms"])
            if ov > 0:
                por[(gr(s), s["spk"])] += ov
        total = sum(por.values())
        if total <= 0:
            continue
        lab, mx = max(por.items(), key=lambda kv: kv[1])
        dom[i] = (lab, mx / total, len(por))
        for (g2, l2), ov in por.items():
            M[(g2, l2, t["voz_n"])] += ov
    asignado, voz_usada = {}, set()                           # asignado[(grupo, etiqueta)] = voz ; voz_usada = {(grupo, voz)}
    for (g2, lab, voz), seg_ms in sorted(M.items(), key=lambda kv: -kv[1]):
        if voz and (g2, lab) not in asignado and (g2, voz) not in voz_usada:
            asignado[(g2, lab)] = voz
            voz_usada.add((g2, voz))
    sig = max([t["voz_n"] for t in turnos] + [0])
    nuevas = {}
    horas = defaultdict(float)
    for (g2, l2, v), x in M.items():
        horas[(g2, l2)] += x
    for k2 in sorted(horas, key=lambda k: -horas[k]):
        if k2 not in asignado and horas[k2] >= 2000:          # ≥2 s hablados: es una voz de verdad
            sig += 1
            asignado[k2] = sig
            nuevas[sig] = "%s:%s" % k2
    nombre_de = {t["voz_n"]: t.get("nombre") for t in turnos if t.get("nombre") and t["voz_n"]}
    cambios, mixtos = [], []
    for i, t in enumerate(turnos):
        if i not in dom:
            continue
        lab, share, n = dom[i]
        destino = asignado.get(lab)
        if not destino:
            continue
        if share < umbral:
            if n >= 2:
                mixtos.append({"id": t["id"], "hora": t.get("hora", ""), "texto": (t.get("texto") or "")[:140]})
            continue
        if destino != t["voz_n"]:
            cambios.append({"id": t["id"], "hora": t.get("hora", ""), "texto": (t.get("texto") or "")[:140], "de": t["voz_n"], "a": destino,
                            "nombre_de": nombre_de.get(t["voz_n"], ""), "nombre_a": nombre_de.get(destino, ""), "nueva": destino in nuevas, "conf": round(share, 2)})
    return {"cambios": cambios, "mixtos": mixtos, "voces_nuevas": sorted(nuevas), "etiquetas": {"%s:%s" % k: v for k, v in asignado.items()}}


def _turnos_de(sesion_id: str) -> list:
    donna_personas.asegurar_esquema()
    c = donna_personas._c()
    try:
        filas = [dict(r) for r in c.execute("SELECT i.id, i.hora, i.texto, i.voz_n, i.persona_id, p.nombre FROM intervenciones i LEFT JOIN personas p ON p.id=i.persona_id WHERE i.sesion_id=? ORDER BY i.id", (sesion_id,))]
    finally:
        c.close()
    from donna_audio import _ms_hora
    out = []
    for k, f in enumerate(filas):
        ini = _ms_hora(f["hora"])
        sig = _ms_hora(filas[k + 1]["hora"]) if k + 1 < len(filas) else ini + 8000
        out.append({"id": f["id"], "hora": f["hora"], "texto": f["texto"], "voz_n": int(f["voz_n"] or 0), "nombre": f["nombre"] or "", "ini_ms": ini, "fin_ms": max(sig, ini + 1500)})
    return out


# ------------------------------------------------------------------ orquestación
def _guardar(sesion_id: str, estado: str, datos: dict):
    c = _c()
    try:
        c.execute("INSERT OR REPLACE INTO junta_voces(sesion_id,ts,estado,datos) VALUES(?,?,?,?)", (sesion_id, int(time.time()), estado, json.dumps(datos, ensure_ascii=False)))
        c.commit()
    finally:
        c.close()


def estado(sesion_id: str) -> dict:
    c = _c()
    try:
        r = c.execute("SELECT ts, estado, datos FROM junta_voces WHERE sesion_id=?", (sesion_id,)).fetchone()
    finally:
        c.close()
    if not r:
        a = donna_audio.resumen(sesion_id)
        return {"estado": "sin_audio" if not a["tramos"] else "pendiente", "audio": a}
    d = json.loads(r["datos"] or "{}")
    d.update(estado=r["estado"], ts=r["ts"], audio=donna_audio.resumen(sesion_id))
    return d


def procesar(sesion_id: str, llamar=None, key: str = None, trabajo_base: Path = None) -> dict:
    """Corre la pasada completa. Nunca lanza: deja el resultado (o el error) en la tabla para que la app lo muestre."""
    tr = donna_audio.tramos(sesion_id)
    if not tr:
        _guardar(sesion_id, "sin_audio", {})
        return {"estado": "sin_audio"}
    _guardar(sesion_id, "procesando", {})
    try:
        import app_media
        key = key if key is not None else app_media.openai_key()
        if not key and llamar is None:
            raise RuntimeError("Falta OPENAI_API_KEY en el servidor")
        base = (trabajo_base or donna_audio.DIR.parent / "diariza_tmp") / sesion_id
        segs_ms, info, minutos = [], {"piezas": 0, "etiquetas_alineadas": True}, 0.0
        durs = {t["aid"]: _dur(donna_audio.ensamblar(t["aid"])) for t in tr}
        presupuesto, omitido = float(MAX_S), 0.0                            # hasta 4 h de audio en total
        usables = {}
        for t in tr:
            usa = min(durs[t["aid"]], max(0.0, presupuesto))
            usables[t["aid"]] = usa
            omitido += durs[t["aid"]] - usa
            presupuesto -= usa
        total_piezas = sum(int(-(-usables[a] // PIEZA_S)) for a in usables if usables[a] > 0.5)
        hechas = [0]
        for t in tr:
            if usables[t["aid"]] <= 0.5:
                continue
            audio = donna_audio.ensamblar(t["aid"])
            base_hechas = hechas[0]
            prog = lambda k, n, b=base_hechas: _guardar(sesion_id, "procesando", {"progreso": "pieza %d de %d" % (b + k + 1, total_piezas), "piezas": total_piezas})
            segs, inf = diarizar_archivo(key, audio, base / t["aid"], llamar, prog, usables[t["aid"]])
            hechas[0] += inf["piezas"]
            info["piezas"] += inf["piezas"]
            info["etiquetas_alineadas"] = info["etiquetas_alineadas"] and inf["etiquetas_alineadas"]
            minutos += _dur(audio) / 60
            for s in segs:
                grp = t["aid"] if inf["etiquetas_alineadas"] else "%s/%s" % (t["aid"], s.get("pieza", 0))      # etiquetas propias de cada tramo (y de cada pieza si no se alinearon)
                segs_ms.append({"spk": s["spk"], "grp": grp, "ini_ms": int(t["offset_ms"] + s["ini"] * 1000), "fin_ms": int(t["offset_ms"] + s["fin"] * 1000), "texto": s["texto"]})
        if not segs_ms:
            raise RuntimeError("El identificador no devolvió voces (¿audio vacío o muy bajo?)")
        turnos = _turnos_de(sesion_id)
        prop = proponer(turnos, segs_ms)
        info.pop("omitido_s", None)
        prop.update(modelo=MODELO, minutos=round(sum(usables.values()) / 60, 1), omitido_min=round(omitido / 60), segmentos=len(segs_ms), voces_detectadas=len({(s["grp"], s["spk"]) for s in segs_ms}), **info)
        _guardar(sesion_id, "lista", prop)
        return {"estado": "lista", **prop}
    except Exception as e:
        _guardar(sesion_id, "error", {"error": str(e)[:300]})
        return {"estado": "error", "error": str(e)[:300]}
    finally:
        shutil.rmtree((trabajo_base or donna_audio.DIR.parent / "diariza_tmp") / sesion_id, ignore_errors=True)


def aplicar(sesion_id: str, ids=None, usuario: str = "admin") -> dict:
    """Aplica los cambios propuestos (todos o los ids elegidos): la intervención pasa a la voz propuesta y a su persona, si ya tiene nombre."""
    c = _c()
    try:
        r = c.execute("SELECT estado, datos FROM junta_voces WHERE sesion_id=?", (sesion_id,)).fetchone()
        if not r or r["estado"] != "lista":
            raise ValueError("No hay una propuesta de voces lista para esta junta.")
        d = json.loads(r["datos"])
        sel = {int(x) for x in ids} if ids not in (None, "todas") else None
        cambios = [x for x in d.get("cambios", []) if not x.get("aplicado") and (sel is None or x["id"] in sel)]
        pc = donna_personas._c()
        try:
            ses = pc.execute("SELECT usuario FROM sesiones_escucha WHERE id=?", (sesion_id,)).fetchone()
            if not ses or (ses["usuario"] != usuario and usuario != "admin"):
                raise ValueError("junta no encontrada")
            voz_persona = {}
            for f in pc.execute("SELECT voz_n, persona_id FROM intervenciones WHERE sesion_id=? AND persona_id IS NOT NULL AND voz_n>0", (sesion_id,)):
                voz_persona.setdefault(f["voz_n"], f["persona_id"])
            n = 0
            for x in cambios:
                pc.execute("UPDATE intervenciones SET voz_n=?, persona_id=? WHERE id=? AND sesion_id=?", (x["a"], voz_persona.get(x["a"]), x["id"], sesion_id))
                x["aplicado"] = True
                n += 1
            pc.commit()
        finally:
            pc.close()
        c.execute("UPDATE junta_voces SET datos=? WHERE sesion_id=?", (json.dumps(d, ensure_ascii=False), sesion_id))
        c.commit()
        return {"aplicados": n}
    finally:
        c.close()
