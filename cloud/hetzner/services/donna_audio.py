#!/usr/bin/env python3
"""donna_audio.py — el AUDIO de las juntas de «Escuchar», guardado en el servidor de Polo (con su consentimiento) para verificar después quién dijo qué.

La app va subiendo el audio por partes MIENTRAS graba (cada ~10 s): si se recarga la pestaña o se cae la conexión, solo se pierden unos segundos.
Cada «tramo» de grabación es una unidad (aid): al retomar una junta se abre un tramo nuevo con su desplazamiento en el tiempo de la junta.
Plazo: el audio se conserva `dias` días contados desde que la junta se guarda (por defecto 30; Ajustes) y se borra solo. Polo puede borrarlo antes.
Archivos 0600 en /opt/openclaw/state/escucha_audio/<aid>/. Nunca se sirve por rutas públicas.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import donna_db

STATE = Path(os.environ.get("PUSH_STATE_DIR") or "/opt/openclaw/state")
DIR = STATE / "escucha_audio"
SCHEMA = """CREATE TABLE IF NOT EXISTS escucha_audio (
  aid TEXT PRIMARY KEY, rid TEXT NOT NULL, usuario TEXT NOT NULL, creado INTEGER NOT NULL, actualizado INTEGER NOT NULL,
  offset_ms INTEGER NOT NULL DEFAULT 0, mime TEXT NOT NULL DEFAULT '', partes INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
  sesion_id TEXT NOT NULL DEFAULT '', estado TEXT NOT NULL DEFAULT 'grabando', vence INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_audio_rid ON escucha_audio(rid);
CREATE INDEX IF NOT EXISTS ix_audio_ses ON escucha_audio(sesion_id);"""
_ID = re.compile(r"^[a-z0-9]{6,32}$")
PART_MAX = 8 * 1024 * 1024
HUERFANO_DIAS = 30                 # audio que nunca se ligó a una junta guardada (la pestaña se cerró): se conserva 30 días desde su última parte
TIMESLICE_S = 10
# Voz baja o lejana: quita el rumble, sube el volumen (hasta +25 dB) y nivela los cambios entre quien habla cerca y quien habla lejos, sin recortar.
AF_VOZ = "highpass=f=70,dynaudnorm=f=200:g=21:m=25:p=0.95:s=6,alimiter=limit=0.95"


def valido(x) -> bool:
    return bool(_ID.match(str(x or "")))


def _c():
    c = donna_db.conn()
    c.executescript(SCHEMA)
    try:                                                      # migración: duración real de cada tramo (se mide una vez y se guarda)
        c.execute("ALTER TABLE escucha_audio ADD COLUMN dur_s REAL NOT NULL DEFAULT 0")
        c.commit()
    except Exception:
        pass
    return c


def _dir(aid: str) -> Path:
    return DIR / aid


def _libre() -> int:
    DIR.mkdir(parents=True, exist_ok=True)
    st = os.statvfs(DIR)
    return st.f_bavail * st.f_frsize


def dias_por_defecto() -> int:
    try:
        import donna_dia
        return int(donna_dia.prefs_get()["escucha"]["dias"])
    except Exception:
        return 30


def guardar_parte(aid: str, rid: str, usuario: str, idx: int, stream, length: int, offset_ms: int = 0, mime: str = "") -> dict:
    """Escribe UNA parte a disco. Idempotente (reenviar la misma parte la reemplaza). Lanza ValueError con un mensaje claro."""
    if not valido(aid) or not valido(rid):
        raise ValueError("id de audio inválido")
    if not (0 <= idx < 100000):
        raise ValueError("número de parte inválido")
    if length <= 0 or length > PART_MAX:
        raise ValueError("cada parte debe pesar entre 1 byte y 8 MB")
    if _libre() - length < int(float(os.environ.get("MEDIA_MIN_FREE_GB", "3")) * 1024 ** 3):
        raise ValueError("el servidor casi no tiene espacio libre en disco")
    c = _c()
    try:
        r = c.execute("SELECT usuario, estado FROM escucha_audio WHERE aid=?", (aid,)).fetchone()
        if r and r["usuario"] != usuario:
            raise ValueError("ese audio es de otra persona")
        if r and r["estado"] == "borrado":
            raise ValueError("ese audio ya se borró")
        d = _dir(aid)
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(d, 0o700)
        part = d / ("%06d.part" % idx)
        tmp = part.with_suffix(".tmp")
        got = 0
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            while got < length:
                blk = stream.read(min(1024 * 1024, length - got))
                if not blk:
                    break
                f.write(blk)
                got += len(blk)
        if got != length:
            tmp.unlink(missing_ok=True)
            raise ValueError("la parte llegó incompleta; reenvíala")
        os.replace(tmp, part)
        for viejo in d.glob("audio.*"):                              # el ensamblado en caché ya no vale
            viejo.unlink(missing_ok=True)
        partes = len(list(d.glob("*.part")))
        total = sum(p.stat().st_size for p in d.glob("*.part"))
        now = int(time.time())
        if not r:
            c.execute("INSERT INTO escucha_audio(aid,rid,usuario,creado,actualizado,offset_ms,mime,partes,bytes) VALUES(?,?,?,?,?,?,?,?,?)",
                      (aid, rid, usuario, now, now, max(0, int(offset_ms or 0)), str(mime)[:60], partes, total))
        else:
            c.execute("UPDATE escucha_audio SET actualizado=?, partes=?, bytes=?, mime=CASE WHEN mime='' THEN ? ELSE mime END WHERE aid=?", (now, partes, total, str(mime)[:60], aid))
        c.commit()
        return {"ok": True, "parte": idx, "partes": partes}
    finally:
        c.close()


def estado(aid: str):
    c = _c()
    try:
        r = c.execute("SELECT * FROM escucha_audio WHERE aid=?", (aid,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def terminar(aid: str, usuario: str) -> dict:
    c = _c()
    try:
        r = c.execute("SELECT usuario FROM escucha_audio WHERE aid=?", (aid,)).fetchone()
        if not r or (r["usuario"] != usuario and usuario != "admin"):
            return {"ok": False}
        c.execute("UPDATE escucha_audio SET estado='cerrado', actualizado=? WHERE aid=? AND estado='grabando'", (int(time.time()), aid))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


def ligar(rid: str, sesion_id: str, dias: int = None) -> int:
    """La junta se guardó: todo el audio de esa grabación queda ligado a la sesión y empieza a correr su plazo."""
    dias = dias or dias_por_defecto()
    c = _c()
    try:
        n = c.execute("UPDATE escucha_audio SET sesion_id=?, vence=?, estado=CASE WHEN estado='grabando' THEN 'cerrado' ELSE estado END WHERE rid=? AND estado!='borrado'",
                      (sesion_id, int(time.time()) + int(dias) * 86400, rid)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def relinkear(viejo: str, nuevo: str) -> int:
    """Una junta retomada reemplazó a la guardada antes: su audio pasa a la nueva (el plazo se renueva)."""
    c = _c()
    try:
        n = c.execute("UPDATE escucha_audio SET sesion_id=?, vence=? WHERE sesion_id=? AND estado!='borrado'", (nuevo, int(time.time()) + dias_por_defecto() * 86400, viejo)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def ultima_con_audio(usuario: str = "") -> str:
    """El id de la junta GUARDADA más reciente que todavía tiene audio (para las herramientas de prueba)."""
    c = _c()
    try:
        r = c.execute("SELECT sesion_id FROM escucha_audio WHERE sesion_id!='' AND estado!='borrado' ORDER BY actualizado DESC LIMIT 1").fetchone()
        return r["sesion_id"] if r else ""
    finally:
        c.close()


def tramos(sesion_id: str) -> list:
    c = _c()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM escucha_audio WHERE sesion_id=? AND estado!='borrado' ORDER BY offset_ms, creado", (sesion_id,))]
    finally:
        c.close()


def _ms_hora(hora: str) -> int:
    """«MM:SS» (o «H:MM:SS») del reloj de la junta → milisegundos."""
    try:
        p = [int(x) for x in str(hora).split(":")]
    except ValueError:
        return 0
    return (p[0] * 60 + p[1]) * 1000 if len(p) == 2 else (p[0] * 3600 + p[1] * 60 + p[2]) * 1000 if len(p) == 3 else 0


def _ext(mime: str) -> str:
    return "m4a" if "mp4" in (mime or "") else "ogg" if "ogg" in (mime or "") else "webm"


def _duracion(p: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def ensamblar(aid: str) -> Path:
    """Une las partes en un solo archivo de audio. Si al juntarlas «a pelo» el archivo no cubre lo que debería (algunos navegadores entregan cada parte
    como un archivo completo), cada parte se decodifica por separado y se unen ya decodificadas."""
    d = _dir(aid)
    partes = sorted(d.glob("*.part"))
    if not partes:
        raise ValueError("ese audio no tiene partes")
    r = estado(aid) or {}
    crudo = d / ("audio." + _ext(r.get("mime", "")))
    if crudo.exists() and _duracion(crudo) > 0:
        return crudo
    with open(crudo, "wb") as out:
        for p in partes:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out)
    esperado = max(1, len(partes) - 1) * TIMESLICE_S
    dur = _duracion(crudo)
    if dur >= 0.6 * esperado:
        os.chmod(crudo, 0o600)
        return crudo
    wavs = []
    for i, p in enumerate(partes):                                 # plan B: decodificar cada parte
        w = d / ("p%06d.wav" % i)
        rr = subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(p), "-ar", "16000", "-ac", "1", str(w)], capture_output=True, timeout=300)
        if rr.returncode == 0 and w.exists():
            wavs.append(w)
    if not wavs:
        raise ValueError("no pude leer el audio guardado")
    lista = d / "lista.txt"
    lista.write_text("".join("file '%s'\n" % w for w in wavs))
    union = d / "audio.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(union)], capture_output=True, timeout=600, check=True)
    for w in wavs:
        w.unlink(missing_ok=True)
    lista.unlink(missing_ok=True)
    crudo.unlink(missing_ok=True)
    os.chmod(union, 0o600)
    return union


def exportar(sesion_id: str) -> Path:
    """Un solo MP3 con TODOS los tramos de la junta, en orden (para descargar). Se guarda en caché hasta que cambie el audio."""
    tr = tramos(sesion_id)
    if not tr:
        raise ValueError("esta junta no tiene audio guardado")
    destino = DIR / "export"
    destino.mkdir(parents=True, exist_ok=True, mode=0o700)
    firma = "-".join("%s:%d" % (t["aid"], t["bytes"]) for t in tr)
    mp3 = destino / ("%s.mp3" % sesion_id)
    marca = destino / ("%s.firma" % sesion_id)
    if mp3.exists() and marca.exists() and marca.read_text() == firma:
        return mp3
    fuentes = [ensamblar(t["aid"]) for t in tr]
    cmd = ["ffmpeg", "-nostdin", "-y"]
    for f in fuentes:
        cmd += ["-i", str(f)]
    if len(fuentes) > 1:
        cmd += ["-filter_complex", "".join("[%d:a]" % i for i in range(len(fuentes))) + "concat=n=%d:v=0:a=1[c];[c]%s[out]" % (len(fuentes), AF_VOZ), "-map", "[out]"]
    else:
        cmd += ["-af", AF_VOZ]
    cmd += ["-vn", "-ac", "1", "-ar", "22050", "-b:a", "48k", str(mp3)]
    r = subprocess.run(cmd, capture_output=True, timeout=3600)
    if r.returncode != 0 or not mp3.exists():
        raise ValueError("no pude preparar el audio para descargarlo")
    os.chmod(mp3, 0o600)
    marca.write_text(firma)
    return mp3


TOLERANCIA_S = 20.0
GANANCIA_MAX_DB = 50.0                                          # tope de lo que se puede subir un fragmento muy bajo (más allá solo sube el ruido)


def _ganancia_db(fuente, desde: float, dur_s: float) -> float:
    """Cuántos dB subirle a un fragmento para que su pico llegue a ~-3 dBFS (se mide el fragmento mismo, no toda la junta)."""
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-ss", "%.2f" % desde, "-t", "%.2f" % dur_s, "-i", str(fuente), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=300)
        m = re.search(r"max_volume:\s*(-?[0-9.]+) dB", r.stderr)
        return max(0.0, min(GANANCIA_MAX_DB, -3.0 - float(m.group(1)))) if m else 0.0
    except Exception:
        return 0.0


def clip(sesion_id: str, hora: str, antes_s: float = 3.0, dur_s: float = 16.0) -> Path:
    """Un fragmento corto (MP3) del momento «MM:SS» del reloj de la junta, para ESCUCHAR y comprobar quién habla. Se busca el tramo que cubre ese momento
    (cada tramo tiene su propio inicio en el reloj de la junta, así que no se usa la posición del audio descargado completo, que no tiene los huecos)."""
    t_s = _ms_hora(hora) / 1000.0
    for t in tramos(sesion_id):
        ini = t["offset_ms"] / 1000.0
        dur = duracion_tramo(t["aid"])
        if dur > 0 and ini - TOLERANCIA_S <= t_s <= ini + dur + TOLERANCIA_S:      # un turno puede empezar unos segundos antes de que arranque el audio (o terminar después)
            desde = min(max(0.0, t_s - ini - antes_s), max(0.0, dur - 1.0))
            destino = DIR / "export"
            destino.mkdir(parents=True, exist_ok=True, mode=0o700)
            mp3 = destino / ("c-%s-%d.mp3" % (sesion_id, int(t_s * 1000)))             # el id de la junta va en el nombre: borrar() la quita junto con el audio
            if not mp3.exists():
                fuente = ensamblar(t["aid"])
                gan = _ganancia_db(fuente, desde, dur_s)
                af = "highpass=f=70,volume=%.1fdB,dynaudnorm=f=200:g=15:m=10:p=0.95:s=6,alimiter=limit=0.95" % gan
                r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-ss", "%.2f" % desde, "-t", "%.2f" % dur_s, "-i", str(fuente), "-af", af, "-vn", "-ac", "1", "-ar", "22050", "-b:a", "48k", str(mp3)],
                                   capture_output=True, timeout=300)
                if r.returncode != 0 or not mp3.exists():
                    raise ValueError("no pude preparar ese fragmento")
                os.chmod(mp3, 0o600)
            return mp3
    raise ValueError("Ese momento no tiene audio guardado (la grabación empezó después o hubo un hueco).")


ACTIVO_S = 180


def activo(fila: dict) -> bool:
    """¿Esta grabación está corriendo AHORA? (estado «grabando» y llegó una parte hace menos de 3 minutos). Una grabación en curso no se borra, no se liga ni se reconstruye."""
    return (fila or {}).get("estado") == "grabando" and time.time() - ((fila or {}).get("actualizado") or 0) < ACTIVO_S


def borrar(sesion_o_aid: str, usuario: str) -> int:
    """Borra el audio (por id de tramo o de sesión). Devuelve cuántos tramos."""
    c = _c()
    try:
        filas = [dict(r) for r in c.execute("SELECT aid, usuario, estado, actualizado FROM escucha_audio WHERE (aid=? OR sesion_id=? OR rid=?) AND estado!='borrado'", (sesion_o_aid, sesion_o_aid, sesion_o_aid))]
        if any(activo(f) and (f["usuario"] == usuario or usuario == "admin") for f in filas):
            raise ValueError("Esa grabación está en curso ahora mismo: termínala y guárdala primero (así no se pierde).")
        n = 0
        for f in filas:
            if f["usuario"] != usuario and usuario != "admin":
                continue
            shutil.rmtree(_dir(f["aid"]), ignore_errors=True)
            c.execute("UPDATE escucha_audio SET estado='borrado', bytes=0, partes=0 WHERE aid=?", (f["aid"],))
            n += 1
        for e in (DIR / "export").glob("*") if (DIR / "export").exists() else []:      # las exportaciones para descargar también se van
            if sesion_o_aid in e.name:
                e.unlink(missing_ok=True)
        c.commit()
        return n
    finally:
        c.close()


def purgar(ahora: int = None) -> int:
    """Borra el audio vencido (plazo cumplido) y el que nunca se ligó a una junta y lleva 30 días sin tocarse."""
    ahora = ahora or int(time.time())
    c = _c()
    try:
        filas = [r["aid"] for r in c.execute(
            "SELECT aid FROM escucha_audio WHERE estado!='borrado' AND ((vence>0 AND vence<?) OR (vence=0 AND actualizado<?))", (ahora, ahora - HUERFANO_DIAS * 86400))]
        for a in filas:
            shutil.rmtree(_dir(a), ignore_errors=True)
            c.execute("UPDATE escucha_audio SET estado='borrado', bytes=0, partes=0 WHERE aid=?", (a,))
        c.commit()
        return len(filas)
    finally:
        c.close()


def resumen(sesion_id: str) -> dict:
    t = tramos(sesion_id)
    conocido = bool(t) and all((x.get("dur_s") or 0) > 0 for x in t)
    return {"tramos": len(t), "bytes": sum(x["bytes"] for x in t), "vence": max([x["vence"] for x in t] or [0]),
            "audio_s": int(sum(x["dur_s"] for x in t)) if conocido else 0}


def duracion_tramo(aid: str) -> float:
    """Duración REAL del audio de un tramo (se mide una vez con ffprobe y se guarda)."""
    e = estado(aid)
    if not e or e["estado"] == "borrado":
        return 0.0
    if (e.get("dur_s") or 0) > 0:
        return float(e["dur_s"])
    try:
        d = _duracion(ensamblar(aid))
    except Exception:
        d = 0.0
    if d > 0:
        c = _c()
        try:
            c.execute("UPDATE escucha_audio SET dur_s=? WHERE aid=?", (d, aid))
            c.commit()
        finally:
            c.close()
    return d


def verificar(sesion_id: str, duracion_junta_s: float = 0) -> dict:
    """¿El audio guardado de la junta es BUENO y cuánto de la junta cubre? Revisa que cada tramo se pueda leer y que dure lo que debería (partes × 10 s),
    y calcula qué tramos de la junta (en su reloj) tienen audio y cuáles no.
    → {ok, cubre_s, total_s, huecos:[[ini_s, fin_s]], tramos:[{aid, ini_s, dur_s, partes, mb, ok}], avisos:[texto]}"""
    tr = tramos(sesion_id)
    out = {"ok": bool(tr), "cubre_s": 0, "total_s": int(duracion_junta_s), "huecos": [], "tramos": [], "avisos": []}
    if not tr:
        out["avisos"].append("Esta junta no tiene audio guardado.")
        return out
    ints = []
    for t in tr:
        d = duracion_tramo(t["aid"])
        esperado = max(1, t["partes"] - 1) * TIMESLICE_S
        bueno = d > 0 and d >= 0.6 * esperado
        if not bueno:
            out["ok"] = False
            out["avisos"].append("El tramo %s no se pudo leer completo (dura %d s y debería durar ~%d s)." % (t["aid"][:6], d, esperado))
        ini = t["offset_ms"] / 1000.0
        out["tramos"].append({"aid": t["aid"], "ini_s": int(ini), "dur_s": int(d), "partes": t["partes"], "mb": round(t["bytes"] / 1e6, 1), "ok": bueno})
        if d > 0:
            ints.append((ini, ini + d))
    ints.sort()
    union, cur = [], None
    for a, b in ints:
        if cur and a <= cur[1] + 15:                          # huecos de menos de 15 s entre tramos no cuentan (reconexiones)
            cur[1] = max(cur[1], b)
        else:
            cur = [a, b]
            union.append(cur)
    out["cubre_s"] = int(sum(b - a for a, b in union))
    total = float(duracion_junta_s or 0)
    if total > 0:
        pos = 0.0
        for a, b in union:
            if a - pos > 30:
                out["huecos"].append([int(pos), int(a)])
            pos = max(pos, b)
        if total - pos > 30:
            out["huecos"].append([int(pos), int(total)])
        if out["huecos"]:
            out["avisos"].append("El audio cubre %d de %d min; sin audio: %s." % (out["cubre_s"] // 60, total // 60, ", ".join("%s–%s" % (_hms(a), _hms(b)) for a, b in out["huecos"])))
    return out


def _hms(s) -> str:
    s = int(s)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def huerfanos(usuario: str) -> list:
    """Audio que NO quedó ligado a ninguna junta guardada (la pestaña se cerró antes de guardar, etc.)."""
    c = _c()
    try:
        filas = c.execute("SELECT aid, rid, creado, actualizado, offset_ms, partes, bytes, estado FROM escucha_audio WHERE sesion_id='' AND estado!='borrado' AND (usuario=? OR ?='admin') ORDER BY actualizado DESC", (usuario, usuario))
        out = []
        for r in filas:
            d = dict(r)
            d["activo"] = activo(d)                                # se está grabando ahora: la pantalla lo muestra sin botones
            out.append(d)
        return out
    finally:
        c.close()


def ligar_tramo(aid: str, sesion_id: str, usuario: str, dias: int = None) -> bool:
    """Liga a mano un audio huérfano a una junta guardada (recuperar un audio que quedó suelto)."""
    c = _c()
    try:
        r = c.execute("SELECT usuario, estado, actualizado FROM escucha_audio WHERE aid=? AND estado!='borrado'", (aid,)).fetchone()
        if not r or (r["usuario"] != usuario and usuario != "admin"):
            return False
        if activo(dict(r)):
            raise ValueError("Esa grabación está en curso ahora mismo: termínala y guárdala primero.")
        c.execute("UPDATE escucha_audio SET sesion_id=?, vence=?, estado=CASE WHEN estado='grabando' THEN 'cerrado' ELSE estado END WHERE aid=?", (sesion_id, int(time.time()) + int(dias or dias_por_defecto()) * 86400, aid))
        c.commit()
        return True
    finally:
        c.close()


def exportar_tramo(aid: str) -> Path:
    """Un solo MP3 de UN tramo (para descargar un audio que aún no tiene junta)."""
    destino = DIR / "export"
    destino.mkdir(parents=True, exist_ok=True, mode=0o700)
    mp3 = destino / ("t-%s.mp3" % aid)
    if mp3.exists():
        return mp3
    fuente = ensamblar(aid)
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(fuente), "-af", AF_VOZ, "-vn", "-ac", "1", "-ar", "22050", "-b:a", "48k", str(mp3)], capture_output=True, timeout=3600)
    if r.returncode != 0 or not mp3.exists():
        raise ValueError("no pude preparar el audio para descargarlo")
    os.chmod(mp3, 0o600)
    return mp3
