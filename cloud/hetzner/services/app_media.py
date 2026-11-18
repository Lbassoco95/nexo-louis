#!/usr/bin/env python3
"""app_media.py — adjuntos de la app: fotos, audios, videos, grabaciones y archivos (sin tope de tamaño propio).

Cómo funciona
  • SUBIDA POR PARTES (reanudable): init → N × chunk → finish. Cada parte se escribe directo a disco (nunca se
    carga un archivo entero en memoria), así que una grabación de horas o un video pesado no tumban el servidor y,
    si se corta la conexión, la app solo reenvía las partes que faltan. El único límite es el ESPACIO LIBRE del disco
    (se rechaza una subida si dejaría menos de MEDIA_MIN_FREE_GB, 3 GB por defecto).
  • PROCESAMIENTO en segundo plano (2 a la vez), según el tipo:
      imagen    → lectura con visión de Claude (qué se ve + todo el texto visible)
      audio/video → transcripción por tramos de 10 min (OpenAI si hay OPENAI_API_KEY; si no, whisper local),
                    con marcas de tiempo [mm:ss], y luego resumen + acuerdos + pendientes
      documento → texto de PDF / Word / Excel / PowerPoint / texto plano
    El resultado queda como texto buscable: Donna lo consulta con adjuntos_buscar / adjuntos_leer.
  • Al terminar un audio/video largo manda un aviso a la bandeja de la app.

Archivos: PUSH_STATE_DIR/media/<id>/{meta.json, data/<nombre>, text.txt}. Carpeta 0700, archivos 0600.
"""
import os
import re
import io
import json
import time
import shutil
import secrets
import zipfile
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
MEDIA_DIR = STATE_DIR / "media"
HOME_OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))
CHUNK_MAX = 32 * 1024 * 1024            # tamaño máximo de UNA parte (la app manda partes de ~8 MB)
SEG_SECONDS = 600                        # tramo de audio por transcripción
TEXT_CAP = 3_000_000                     # tope de texto guardado por adjunto
ID_RE = re.compile(r"^m[0-9a-f]{10}$")
_LOCK = threading.RLock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media")

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".webm", ".amr", ".caf", ".mp4a"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".html", ".htm", ".xml", ".tsv", ".yaml", ".yml", ".srt", ".vtt", ".rtf"}


# ------------------------------------------------------------------ utilidades
def safe_name(name: str) -> str:
    base = os.path.basename((name or "archivo").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ()áéíóúñÁÉÍÓÚÑ]", "_", base).strip(" .") or "archivo"
    return base[:120]


def _dir(mid: str) -> Path:
    if not ID_RE.match(mid or ""):
        raise ValueError("id de adjunto inválido")
    return MEDIA_DIR / mid


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _meta(mid: str):
    try:
        return json.loads((_dir(mid) / "meta.json").read_text())
    except Exception:
        return None


def _update(mid: str, **kw) -> dict:
    with _LOCK:
        m = _meta(mid) or {}
        m.update(kw)
        m["actualizado"] = int(time.time())
        _write_json(_dir(mid) / "meta.json", m)
        return m


def kind_of(name: str, mime: str = "") -> str:
    ext = Path(name or "").suffix.lower()
    mime = (mime or "").lower()
    if mime.startswith("image/") or ext in IMAGE_EXT:
        return "imagen"
    if mime.startswith("audio/") or ext in AUDIO_EXT and not mime.startswith("video/"):
        return "audio"
    if mime.startswith("video/") or ext in VIDEO_EXT:
        return "video"
    return "documento"


def free_bytes() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    st = os.statvfs(STATE_DIR)
    return st.f_bavail * st.f_frsize


def min_free() -> int:
    try:
        return int(float(os.environ.get("MEDIA_MIN_FREE_GB", "3")) * 1024 ** 3)
    except ValueError:
        return 3 * 1024 ** 3


# ------------------------------------------------------------------ subida por partes
def init(name: str, mime: str = "", size: int = 0, user: str = "", origen: str = "") -> dict:
    size = max(0, int(size or 0))
    if free_bytes() - size < min_free():
        raise ValueError("El servidor casi no tiene espacio libre en disco para este archivo "
                         f"(libres: {free_bytes() // 1024 ** 2} MB). Hay que liberar espacio antes de subirlo.")
    mid = "m" + secrets.token_hex(5)
    d = _dir(mid)
    (d / "parts").mkdir(parents=True, mode=0o700)
    os.chmod(d, 0o700)
    nombre = safe_name(name)
    rec = {"id": mid, "nombre": nombre, "mime": (mime or "")[:100], "tipo": kind_of(nombre, mime), "tamano": size,
           "estado": "subiendo", "creado": int(time.time()), "usuario": (user or "")[:60], "origen": (origen or "")[:30],
           "partes": 0, "texto_chars": 0, "resumen": "", "error": "", "motor": ""}
    with _LOCK:
        _write_json(d / "meta.json", rec)
    return {"id": mid, "chunk": 8 * 1024 * 1024, "tipo": rec["tipo"]}


def save_chunk(mid: str, index: int, stream, length: int) -> dict:
    """Escribe UNA parte directo a disco (bloques de 1 MB). Idempotente: reenviar la misma parte la reemplaza."""
    m = _meta(mid)
    if not m or m["estado"] != "subiendo":
        raise ValueError("este adjunto no está recibiendo partes")
    if not (0 <= index < 1_000_000):
        raise ValueError("número de parte inválido")
    if length <= 0 or length > CHUNK_MAX:
        raise ValueError(f"cada parte debe pesar entre 1 byte y {CHUNK_MAX // 1024 ** 2} MB")
    if free_bytes() - length < min_free():
        raise ValueError("el servidor se quedó sin espacio libre en disco")
    part = _dir(mid) / "parts" / f"{index:06d}"
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
    return {"ok": True, "parte": index, "bytes": got}


def parts_have(mid: str) -> list:
    try:
        return sorted(int(p.name) for p in (_dir(mid) / "parts").iterdir() if p.name.isdigit())
    except OSError:
        return []


def finish(mid: str, n_parts: int, background: bool = True) -> dict:
    """Une las partes 0..n-1 en el archivo final y lanza el procesamiento. Si faltan partes, dice cuáles."""
    m = _meta(mid)
    if not m:
        raise ValueError("adjunto inexistente")
    if m["estado"] not in ("subiendo",):
        return m
    have = set(parts_have(mid))
    missing = [i for i in range(int(n_parts)) if i not in have]
    if missing or n_parts <= 0:
        raise ValueError("faltan partes: " + ", ".join(map(str, missing[:20])) if missing else "no hay partes")
    d = _dir(mid)
    (d / "data").mkdir(exist_ok=True, mode=0o700)
    final = d / "data" / m["nombre"]
    with open(final, "wb") as out:
        for i in range(int(n_parts)):
            with open(d / "parts" / f"{i:06d}", "rb") as f:
                shutil.copyfileobj(f, out, 1024 * 1024)
    os.chmod(final, 0o600)
    shutil.rmtree(d / "parts", ignore_errors=True)
    m = _update(mid, estado="en cola", partes=int(n_parts), tamano=final.stat().st_size)
    if background:
        _POOL.submit(process, mid)
    return m


def resume_pending() -> int:
    """Al arrancar: retoma lo que quedó 'en cola' o 'procesando' por un reinicio del servidor."""
    n = 0
    try:
        for d in MEDIA_DIR.iterdir():
            m = _meta(d.name)
            if m and m.get("estado") in ("en cola", "procesando"):
                _POOL.submit(process, m["id"])
                n += 1
    except OSError:
        pass
    return n


# ------------------------------------------------------------------ extracción de texto
def _xml_text(data: bytes, tags: tuple) -> str:
    out = []
    try:
        for ev, el in ET.iterparse(io.BytesIO(data)):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag in tags and el.text:
                out.append(el.text)
            if tag in ("p", "tr", "row"):
                out.append("\n")
            el.clear()
    except ET.ParseError:
        pass
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def extract_document(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        raw = path.read_bytes()[:TEXT_CAP * 2]
        for enc in ("utf-8", "latin-1"):
            try:
                t = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if ext in (".html", ".htm", ".xml"):
            t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
            t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"[ \t]+", " ", t).strip()
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            return _xml_text(z.read("word/document.xml"), ("t",))
    if ext == ".pptx":
        with zipfile.ZipFile(path) as z:
            slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.findall(r"\d+", n)[-1]))
            return "\n\n".join(f"[Diapositiva {i + 1}]\n" + _xml_text(z.read(n), ("t",)) for i, n in enumerate(slides))
    if ext == ".xlsx":
        with zipfile.ZipFile(path) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                shared = [s for s in re.findall(r"<t[^>]*>(.*?)</t>", z.read("xl/sharedStrings.xml").decode("utf-8", "ignore"), flags=re.S)]
            out = []
            for n in sorted(x for x in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", x)):
                num = re.findall(r"\d+", n)[-1]
                out.append(f"[Hoja {num}]")
                xml = z.read(n).decode("utf-8", "ignore")
                for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, flags=re.S):
                    cells = []
                    for c in re.findall(r"<c([^>]*?)(?:/>|>(.*?)</c>)", row, flags=re.S):
                        attrs, inner = c
                        v = re.search(r"<v>(.*?)</v>", inner or "", flags=re.S)
                        if not v:
                            continue
                        cells.append(shared[int(v.group(1))] if 't="s"' in attrs and v.group(1).isdigit() and int(v.group(1)) < len(shared) else v.group(1))
                    if cells:
                        out.append(" | ".join(cells))
            return "\n".join(out)
    if ext == ".pdf":
        if not shutil.which("pdftotext"):
            raise RuntimeError("falta pdftotext en el servidor (apt install poppler-utils)")
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("pdftotext no pudo leer el PDF: " + (r.stderr or "")[:150])
        return r.stdout.strip()
    raise RuntimeError(f"no sé leer archivos {ext or 'sin extensión'}; se guardó pero sin texto")


# ------------------------------------------------------------------ transcripción
def openai_key() -> str:
    k = os.environ.get("OPENAI_API_KEY", "")
    if k:
        return k
    for f in (HOME_OC / "credentials" / "openai.env", HOME_OC / "openclaw.env", HOME_OC / ".env"):
        try:
            for line in f.read_text().splitlines():
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _multipart(fields: dict, file_field: str, filename: str, data: bytes) -> tuple:
    b = "----donna" + secrets.token_hex(8)
    body = bytearray()
    for k, v in fields.items():
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
             f'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f"\r\n--{b}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={b}"


def _openai_transcribe(seg: Path, key: str) -> str:
    model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
    last = ""
    for mdl in (model, "whisper-1"):
        body, ctype = _multipart({"model": mdl, "response_format": "text"}, "file", seg.name, seg.read_bytes())
        req = urllib.request.Request("https://api.openai.com/v1/audio/transcriptions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read().decode("utf-8", "ignore").strip()
        except urllib.error.HTTPError as e:
            last = f"OpenAI {e.code}: {e.read().decode('utf-8', 'ignore')[:160]}"
            if e.code in (401, 403, 429):
                break
        except Exception as e:
            last = f"OpenAI: {str(e)[:160]}"
    raise RuntimeError(last or "OpenAI no respondió")


def _whisper_local(seg: Path) -> str:
    wb = next((p for p in ("/usr/local/bin/whisper-cli", "/usr/local/bin/whisper-cpp") if os.path.exists(p)), "")
    wd = HOME_OC / "whisper-models"
    models = [wd / f"ggml-{n}.bin" for n in ("medium", "small", "base") if (wd / f"ggml-{n}.bin").exists()]
    if not wb or not models:
        raise RuntimeError("no hay motor de transcripción: define OPENAI_API_KEY (credentials/openai.env) o instala whisper local")
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(seg), "-ar", "16000", "-ac", "1", str(wav)],
                       capture_output=True, timeout=600, check=True)
        of = Path(td) / "t"
        r = subprocess.run([wb, "-m", str(models[0]), "-f", str(wav), "-l", "auto", "-t", str(max(4, os.cpu_count() or 4)),
                            "-otxt", "-of", str(of), "--no-prints"], capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("whisper local falló: " + (r.stderr or "")[:150])
        return (of.with_suffix(".txt").read_text() if of.with_suffix(".txt").exists() else r.stdout).strip()


def transcribe(path: Path, progreso=None) -> tuple:
    """(texto con marcas de tiempo, motor). Lanza RuntimeError con un motivo claro."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("falta ffmpeg en el servidor (sudo apt install ffmpeg)")
    key = openai_key()
    motor = "openai" if key else "whisper-local"
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                            "-f", "segment", "-segment_time", str(SEG_SECONDS), "-reset_timestamps", "1",
                            str(Path(td) / "seg_%04d.mp3")], capture_output=True, text=True)
        segs = sorted(Path(td).glob("seg_*.mp3"))
        if r.returncode != 0 or not segs:
            raise RuntimeError("no pude leer el audio (¿archivo dañado o sin pista de audio?): " + (r.stderr or "")[-150:])
        out = []
        for i, s in enumerate(segs):
            t = _openai_transcribe(s, key) if key else _whisper_local(s)
            t = re.sub(r"\s+", " ", t or "").strip()
            if t:
                sec = i * SEG_SECONDS
                out.append(f"[{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}] {t}" if len(segs) > 1 else t)
            if progreso:
                progreso(i + 1, len(segs))
        return "\n\n".join(out), motor


# ------------------------------------------------------------------ visión y resumen
def describe_image(path: Path) -> str:
    import base64
    import louis_core as core
    ext = path.suffix.lower()
    media = IMAGE_MIME.get(ext)
    if not media:
        raise RuntimeError(f"formato de imagen {ext} sin lectura (usa JPG/PNG/WebP; la app convierte las fotos solas)")
    data = path.read_bytes()
    if len(data) > 4_500_000:
        raise RuntimeError("imagen demasiado grande para la lectura con visión (>4.5 MB); la app reduce las fotos antes de subirlas")
    key = core.load_anthropic_key()
    if not key:
        raise RuntimeError("falta ANTHROPIC_API_KEY para leer imágenes")
    sys_p = ("Eres los ojos de un asistente ejecutivo. Describe la imagen en español con precisión: qué es, qué se ve, y "
             "TRANSCRIBE literalmente todo el texto visible (carteles, pizarrones, documentos, capturas, tickets). "
             "Si es un documento o captura, extrae sus datos de forma estructurada. No inventes nada que no se vea; "
             "si algo es ilegible, dilo.")
    out = core.call_claude_with_image(key, sys_p, base64.standard_b64encode(data).decode("ascii"), media,
                                      "Describe esta imagen y transcribe su texto.")
    if not out or str(out).lstrip().startswith(("ERROR", "(error", "Error")):
        raise RuntimeError("la visión de Claude no respondió: " + str(out)[:140])
    return str(out).strip()


_SUMMARY_SYS = (
    "Eres un asistente ejecutivo. Recibes la transcripción de una reunión o nota de voz de Polo (CEO de Kawiil/Yoltik). "
    "Devuelve en español, en Markdown breve: **Resumen** (3-6 líneas), **Acuerdos y decisiones** (viñetas), "
    "**Pendientes** (viñetas «Quién — qué — para cuándo»; escribe «por definir» si no se dijo) y **Temas para dar seguimiento**. "
    "Usa SOLO lo que dice la transcripción: no inventes nombres, fechas ni cifras. Si es muy corta o no tiene contenido, dilo."
)


def summarize(text: str) -> str:
    import louis_core as core
    if len(text) < 200:
        return ""
    muestra = text if len(text) <= 60_000 else (text[:30_000] + "\n[…]\n" + text[-30_000:])
    try:
        r = core.call_deepseek(_SUMMARY_SYS, [], muestra)
    except Exception:
        r = None
    return (r or "").strip()


# ------------------------------------------------------------------ procesamiento
def process(mid: str) -> None:
    m = _meta(mid)
    if not m or m.get("estado") not in ("en cola", "procesando"):
        return
    d = _dir(mid)
    path = d / "data" / m["nombre"]
    _update(mid, estado="procesando", error="")
    try:
        tipo = m["tipo"]
        motor, resumen = "", ""
        if tipo == "imagen":
            texto, motor = describe_image(path), "claude-vision"
        elif tipo in ("audio", "video"):
            def prog(i, n):
                _update(mid, progreso=f"{i}/{n}")
            texto, motor = transcribe(path, prog)
            if not texto.strip():
                raise RuntimeError("la grabación no tiene voz que transcribir (silencio o ruido)")
            resumen = summarize(texto)
        else:
            texto, motor = extract_document(path), "texto"
            if not texto.strip():
                raise RuntimeError("el archivo no tiene texto extraíble (¿es un escaneo o una imagen? súbelo como foto)")
            resumen = summarize(texto) if len(texto) > 6000 else ""
        texto = texto[:TEXT_CAP]
        tf = d / "text.txt"
        fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(texto)
        _update(mid, estado="listo", texto_chars=len(texto), resumen=resumen, motor=motor, progreso="")
        if tipo in ("audio", "video"):
            try:
                import louis_push
                louis_push.notify_async(f"Transcripción lista: {m['nombre'][:40]}",
                                        (resumen or texto)[:180].replace("*", ""), tab="chat", tag="media-" + mid)
            except Exception:
                pass
    except Exception as e:
        _update(mid, estado="error", error=str(e)[:300], progreso="")


def register_text(nombre: str, texto: str, tipo: str = "documento", resumen: str = "", origen: str = "") -> dict:
    """Guarda un texto ya listo (p. ej. una conversación traducida) como adjunto buscable por Donna."""
    mid = "m" + secrets.token_hex(5)
    d = _dir(mid)
    d.mkdir(parents=True, mode=0o700)
    os.chmod(d, 0o700)
    texto = (texto or "")[:TEXT_CAP]
    fd = os.open(d / "text.txt", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(texto)
    rec = {"id": mid, "nombre": safe_name(nombre), "mime": "text/plain", "tipo": tipo, "tamano": len(texto.encode()),
           "estado": "listo", "creado": int(time.time()), "actualizado": int(time.time()), "usuario": "", "origen": origen[:30],
           "partes": 0, "texto_chars": len(texto), "resumen": resumen or "", "error": "", "motor": "texto"}
    with _LOCK:
        _write_json(d / "meta.json", rec)
    return public(rec)


# ------------------------------------------------------------------ consulta
def public(m: dict) -> dict:
    return {k: m.get(k) for k in ("id", "nombre", "tipo", "tamano", "estado", "creado", "texto_chars", "error", "progreso",
                                 "motor", "origen")} | {"resumen": (m.get("resumen") or "")[:1500]}


def get(mid: str):
    m = _meta(mid)
    return public(m) if m else None


def listar(limit: int = 50, tipo: str = "") -> list:
    items = []
    try:
        for d in MEDIA_DIR.iterdir():
            m = _meta(d.name)
            if m and (not tipo or m.get("tipo") == tipo):
                items.append(m)
    except OSError:
        return []
    items.sort(key=lambda x: -x.get("creado", 0))
    return [public(m) for m in items[:max(1, min(limit, 500))]]


def leer_texto(mid: str, desde: int = 0, limite: int = 8000) -> str:
    m = _meta(mid)
    if not m:
        return "❌ no existe ese adjunto"
    if m["estado"] != "listo":
        return f"⏳ el adjunto «{m['nombre']}» está en estado «{m['estado']}»" + (f" ({m['error']})" if m.get("error") else "") \
               + (f" progreso {m['progreso']}" if m.get("progreso") else "")
    try:
        t = (_dir(mid) / "text.txt").read_text()
    except OSError:
        return "❌ el adjunto no tiene texto"
    desde, limite = max(0, int(desde or 0)), max(500, min(int(limite or 8000), 30_000))
    trozo = t[desde:desde + limite]
    resto = max(0, len(t) - desde - limite)
    pie = f"\n[… quedan {resto} caracteres: pídelos con desde={desde + limite}]" if resto else ""
    return f"«{m['nombre']}» ({m['tipo']}, {len(t)} caracteres, motor {m.get('motor')}):\n{trozo}{pie}"


def buscar(consulta: str, limit: int = 8) -> str:
    q = (consulta or "").strip().lower()
    if len(q) < 2:
        return "❌ escribe qué buscar (mínimo 2 letras)"
    palabras = [w for w in re.split(r"\s+", q) if len(w) > 1]
    res = []
    try:
        dirs = list(MEDIA_DIR.iterdir())
    except OSError:
        dirs = []
    for d in dirs:
        m = _meta(d.name)
        if not m or m.get("estado") != "listo":
            continue
        try:
            t = (d / "text.txt").read_text()
        except OSError:
            continue
        low = (m["nombre"] + " " + t).lower()
        score = sum(low.count(w) for w in palabras)
        if not score or not all(w in low for w in palabras):
            continue
        i = low.find(palabras[0], len(m["nombre"]) + 1) - len(m["nombre"]) - 1
        i = max(0, i)
        res.append((score, m, t[max(0, i - 120):i + 280].replace("\n", " ")))
    res.sort(key=lambda x: -x[0])
    if not res:
        return "(sin coincidencias en los adjuntos procesados)"
    return "\n\n".join(f"- [[adjunto:{m['id']}]] «{m['nombre']}» ({m['tipo']}, {time.strftime('%d/%m/%Y', time.localtime(m['creado']))})\n  …{ex}…"
                       for _, m, ex in res[:limit])


def file_path(mid: str):
    m = _meta(mid)
    if not m:
        return None, None
    p = _dir(mid) / "data" / m["nombre"]
    return (p, m) if p.exists() else (None, None)


def contexto_para_chat(ids: list, max_chars: int = 3500) -> str:
    """Bloque que se antepone al mensaje de Polo con lo que contienen sus adjuntos (compacto: lo demás, con adjuntos_leer)."""
    partes = []
    for mid in (ids or [])[:10]:
        if not isinstance(mid, str) or not ID_RE.match(mid):
            continue
        m = _meta(mid)
        if not m:
            continue
        cab = f"[ADJUNTO {mid} «{m['nombre']}» ({m['tipo']})"
        if m["estado"] == "listo":
            t = (_dir(mid) / "text.txt").read_text() if (_dir(mid) / "text.txt").exists() else ""
            if len(t) <= max_chars:
                partes.append(f"{cab}]\n{t}")
            else:
                base = (m.get("resumen") or "")[:max_chars // 2]
                partes.append(f"{cab} · {len(t)} caracteres en total]\n{base}\n--- inicio ---\n{t[:max_chars // 2]}\n"
                              f"[…el resto se lee con la herramienta adjuntos_leer(id=\"{mid}\", desde=N)]")
        elif m["estado"] == "error":
            partes.append(f"{cab}] ⚠️ no se pudo procesar: {m.get('error')}")
        else:
            partes.append(f"{cab}] ⏳ todavía se está procesando ({m['estado']}); dile a Polo que en un momento queda lista.")
    return "\n\n".join(partes)
