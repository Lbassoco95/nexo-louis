#!/usr/bin/env python3
"""app_artifacts.py — artefactos (tableros visuales) que Donna crea y la app muestra. Solo stdlib.

Donna redacta HTML (tableros de proyecto, tablas, seguimientos) y lo guarda aquí; la app lo muestra dentro de un
iframe AISLADO. Como ese HTML puede alimentarse de texto externo (mensajes de Slack, correos…), nunca se confía en él:

  1. SANITIZADOR POR LISTA PERMITIDA: solo etiquetas y atributos de presentación (texto, tablas, SVG sencillo, CSS).
     Se eliminan scripts, iframes, formularios, meta (refresh), enlaces con destino, imágenes remotas, @import, url()
     externos, atributos on*, etc. No hay JavaScript: un tablero no lo necesita.
  2. Se muestra en <iframe sandbox=""> (sin scripts, sin same-origin: no ve tu sesión ni el almacenamiento) con una
     Content-Security-Policy que bloquea cualquier red (default-src 'none'; img-src data:; form-action 'none').
  3. SELLO DE PROCEDENCIA que añade el SERVIDOR (no el modelo): qué herramientas de datos se consultaron al crearlo y
     cuándo, o un aviso «sin consulta de datos: cifras no verificadas». Así un tablero bonito no pasa por verificado.

Archivos: PUSH_STATE_DIR/artefactos/<id>.json (0600), con las últimas 5 versiones. Tope: 300 KB por artefacto, 200 en total.
"""
import os
import re
import json
import time
import fcntl
import secrets
import threading
import html as _html
from html.parser import HTMLParser
from pathlib import Path

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
ART_DIR = STATE_DIR / "artefactos"
LOCK_FILE = STATE_DIR / "artefactos.lock"
MAX_BYTES = 300_000
MAX_ARTIFACTS = 200
MAX_VERSIONS = 5
MAX_TAGS = 20_000
MAX_DEPTH = 48
ID_RE = re.compile(r"^a[0-9a-f]{10}$")
_TLOCK = threading.Lock()

# ------------------------------------------------------------------ sanitizador
_VOID = {"br", "hr", "col", "wbr"}
_TEXT_TAGS = {
    "div", "span", "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "b", "em", "i",
    "u", "s", "small", "sub", "sup", "code", "pre", "blockquote", "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "caption", "colgroup", "col", "details", "summary", "section", "article", "header", "footer", "main", "nav",
    "aside", "figure", "figcaption", "progress", "meter", "dl", "dt", "dd", "mark", "abbr", "time", "kbd", "wbr",
}
_SVG_TAGS = {
    "svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "defs",
    "lineargradient", "radialgradient", "stop", "title", "desc",
}
_STYLE_TAG = "style"
# etiquetas que se eliminan CON su contenido (lo que haya adentro no se muestra)
_DROP_WITH_CONTENT = {
    "script", "iframe", "object", "embed", "applet", "link", "meta", "base", "form", "input", "button", "textarea",
    "select", "option", "audio", "video", "source", "track", "canvas", "noscript", "template", "frame", "frameset",
    "foreignobject", "math", "head", "title_html", "image", "use", "animate", "set", "animatetransform", "a_svg",
}
_ATTRS_COMMON = {"class", "id", "style", "title", "role", "aria-label", "aria-hidden", "lang", "dir"}
_ATTRS_BY_TAG = {
    "td": {"colspan", "rowspan", "scope", "align", "valign"}, "th": {"colspan", "rowspan", "scope", "align", "valign"},
    "col": {"span"}, "colgroup": {"span"}, "ol": {"start", "reversed", "type"}, "li": {"value"},
    "details": {"open"}, "time": {"datetime"}, "abbr": set(),
    "progress": {"value", "max"}, "meter": {"value", "min", "max", "low", "high", "optimum"},
}
_SVG_ATTRS = {
    "viewbox", "width", "height", "x", "y", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2", "d", "points", "fill",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity", "fill-opacity",
    "stroke-opacity", "transform", "text-anchor", "font-size", "font-weight", "font-family", "offset", "stop-color",
    "stop-opacity", "preserveaspectratio", "dominant-baseline", "dx", "dy", "gradientunits", "x-offset", "xmlns",
}
_URL_RE = re.compile(r"url\s*\(\s*(['\"]?)\s*(#[\w\-]+)\s*\1\s*\)", re.I)       # url(#id) local: permitido
_ANY_URL_RE = re.compile(r"url\s*\(", re.I)
_BAD_CSS_RE = re.compile(r"@import|@charset|@namespace|expression\s*\(|behavior\s*:|-moz-binding|javascript:|vbscript:|"
                         r"data:text/html|\\0|&#", re.I)
_BAD_VALUE_RE = re.compile(r"^\s*(javascript|vbscript|data|file):", re.I)


def _clean_css(css: str) -> str:
    """CSS sin canales de red: se descarta TODO url() salvo referencias locales url(#id), y @import/expression/etc."""
    css = re.sub(r"/\*.*?\*/", "", css or "", flags=re.S)
    if _BAD_CSS_RE.search(css):
        css = _BAD_CSS_RE.sub("", css)
    css = _URL_RE.sub(lambda m: f"url({m.group(2)})", css)       # normaliza los permitidos
    # lo que aún tenga url( (no local) se elimina por completo
    css = re.sub(r"url\s*\((?!#)[^)]*\)", "none", css, flags=re.I)
    return css[:60_000]


def _clean_style_attr(v: str) -> str:
    decls = []
    for d in (v or "").split(";"):
        if ":" not in d:
            continue
        k, val = d.split(":", 1)
        k, val = k.strip().lower(), val.strip()
        if not re.fullmatch(r"-?[a-z][a-z0-9-]*", k):
            continue
        val = _clean_css(val)
        if not val or _ANY_URL_RE.search(val) and not _URL_RE.search(val):
            continue
        decls.append(f"{k}:{val}")
    return ";".join(decls)[:2000]


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []          # etiquetas abiertas que SÍ emitimos
        self.skip = 0            # >0: dentro de una etiqueta que se descarta con su contenido
        self.skip_tag = None
        self.in_style = False
        self.style_buf = []
        self.count = 0
        self.dropped = set()     # para informar al modelo qué se quitó

    # -- helpers
    def _attrs(self, tag, attrs):
        ok = []
        svg = tag in _SVG_TAGS
        allowed = _ATTRS_COMMON | _ATTRS_BY_TAG.get(tag, set())
        for k, v in attrs:
            k = (k or "").lower()
            v = v if v is not None else ""
            if k.startswith("on") or k in ("href", "src", "srcset", "action", "formaction", "xlink:href", "srcdoc",
                                           "background", "poster", "data", "ping"):
                self.dropped.add(k)
                continue
            if k == "style":
                s = _clean_style_attr(v)
                if s:
                    ok.append(("style", s))
                continue
            if not (k in allowed or (svg and k in _SVG_ATTRS)):
                continue
            if _BAD_VALUE_RE.match(v):
                self.dropped.add(k)
                continue
            if svg and _ANY_URL_RE.search(v) and not _URL_RE.fullmatch(v.strip()):
                self.dropped.add(k)
                continue
            if k in ("class", "id"):
                v = re.sub(r"[^\w\- ]", "", v)[:200]
            ok.append((k, v[:2000]))
        return ok

    # -- eventos del parser
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.count += 1
        if self.count > MAX_TAGS:
            raise ValueError("el HTML tiene demasiados elementos")
        if self.skip:
            if tag == self.skip_tag and tag not in _VOID:
                self.skip += 1
            return
        if tag in _DROP_WITH_CONTENT:
            self.dropped.add(f"<{tag}>")
            if tag not in _VOID:
                self.skip, self.skip_tag = 1, tag
            return
        if tag == _STYLE_TAG:
            self.in_style, self.style_buf = True, []
            return
        if tag == "a":                      # los enlaces no llevan a ningún lado: se muestran como texto
            self.dropped.add("<a href>")
            tag = "span"
        if tag not in _TEXT_TAGS and tag not in _SVG_TAGS:
            self.dropped.add(f"<{tag}>")    # etiqueta desconocida: se descarta la etiqueta, se conserva el texto
            return
        if len(self.stack) >= MAX_DEPTH:
            raise ValueError("el HTML está demasiado anidado")
        a = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in self._attrs(tag, attrs))
        self.out.append(f"<{tag}{a}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
                if not self.skip:
                    self.skip_tag = None
            return
        if tag == _STYLE_TAG and self.in_style:
            css = _clean_css("".join(self.style_buf))
            if css.strip():
                self.out.append(f"<style>{css}</style>")
            self.in_style = False
            return
        if tag == "a":
            tag = "span"
        if tag in _VOID or tag not in self.stack:
            return
        while self.stack:                    # cierra lo que haya quedado abierto hasta esta etiqueta
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_style:
            self.style_buf.append(data)
            return
        self.out.append(_html.escape(data, quote=False))

    # comentarios, declaraciones, instrucciones de proceso: se ignoran (no se emiten)
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass

    def close_all(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")


def sanitize(fragment: str):
    """(html_limpio, lista_de_lo_eliminado). Lanza ValueError si es demasiado grande o anidado."""
    if len((fragment or "").encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"el artefacto pesa más de {MAX_BYTES // 1000} KB; hazlo más compacto")
    p = _Sanitizer()
    p.feed(fragment or "")
    p.close()
    p.close_all()
    return "".join(p.out), sorted(p.dropped)


# ------------------------------------------------------------------ documento que se muestra en el iframe
KIT_CSS = """
:root{color-scheme:light dark;--bg:#fff;--tx:#111a2e;--mut:#5d6985;--line:#dde3f0;--card:#f4f6fb;--acc:#4f7cff;
--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--tx:#e8edf7;--mut:#8b97b3;--line:#243049;--card:#121a2c;--acc:#7aa2ff;
--ok:#34d399;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--tx);font:15px/1.5 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;padding:14px 14px 22px;
overflow-wrap:anywhere}
h1{font-size:21px;margin:0 0 4px}h2{font-size:17px;margin:20px 0 8px}h3{font-size:15px;margin:14px 0 6px}
p{margin:.4em 0}.muted,small{color:var(--mut)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;text-align:center}
.kpi .v{display:block;font-size:26px;font-weight:700;line-height:1.1}.kpi .l{display:block;font-size:12px;color:var(--mut);margin-top:3px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:10px 0}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
.tag{display:inline-block;font-size:11px;font-weight:600;border-radius:999px;padding:2px 9px;background:var(--card);border:1px solid var(--line)}
.tag.ok{color:var(--ok);border-color:var(--ok)}.tag.warn{color:var(--warn);border-color:var(--warn)}
.tag.bad{color:var(--bad);border-color:var(--bad)}.tag.info{color:var(--acc);border-color:var(--acc)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.bar{height:9px;border-radius:999px;background:var(--line);overflow:hidden;margin:4px 0}.bar>i{display:block;height:100%;background:var(--acc)}
.bar.ok>i{background:var(--ok)}.bar.warn>i{background:var(--warn)}.bar.bad>i{background:var(--bad)}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto;font-size:13.5px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}th{background:var(--card)}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 12px;margin:8px 0}summary{cursor:pointer;font-weight:600}
ul,ol{padding-left:1.3em}svg{max-width:100%;height:auto}
.prov{margin-top:22px;padding-top:10px;border-top:1px solid var(--line);font-size:12px;color:var(--mut)}
.prov.sin{color:var(--warn)}
"""

CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
       "form-action 'none'; base-uri 'none'")


def render_document(titulo: str, body_html: str, procedencia: str = "", verificado: bool = True) -> str:
    """Documento completo para <iframe srcdoc> (la app lo pone dentro de sandbox='' → sin scripts ni red)."""
    prov = ""
    if procedencia:
        prov = f'<footer class="prov{"" if verificado else " sin"}">{_html.escape(procedencia)}</footer>'
    return ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">"
            f"<title>{_html.escape(titulo or 'Tablero')}</title><style>{KIT_CSS}</style></head>"
            f"<body>{body_html}{prov}</body></html>")


# ------------------------------------------------------------------ almacenamiento
class _Lock:
    def __enter__(self):
        _TLOCK.acquire()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._f = open(LOCK_FILE, "a")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._f, fcntl.LOCK_UN)
            self._f.close()
        finally:
            _TLOCK.release()


def _path(aid: str) -> Path:
    if not ID_RE.match(aid or ""):
        raise ValueError("id de artefacto inválido")
    return ART_DIR / f"{aid}.json"


def _write(path: Path, data: dict) -> None:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _read(aid: str):
    try:
        return json.loads(_path(aid).read_text())
    except Exception:
        return None


def _stamp(procedencia: str, verificado: bool) -> dict:
    return {"procedencia": (procedencia or "")[:600], "verificado": bool(verificado)}


def crear(titulo: str, html_src: str, proyecto: str = "", procedencia: str = "", verificado: bool = True) -> dict:
    """Sanitiza y guarda. Devuelve {id, titulo, quitado, version}. Lanza ValueError con un motivo claro."""
    titulo = (titulo or "").strip()[:120]
    if not titulo:
        raise ValueError("falta el título")
    limpio, quitado = sanitize(html_src)
    if len(re.sub(r"<[^>]+>", "", limpio).strip()) < 5:
        raise ValueError("el artefacto quedó vacío (¿era solo scripts o formularios?); escribe contenido en HTML sencillo")
    now = int(time.time())
    aid = "a" + secrets.token_hex(5)
    rec = {"id": aid, "titulo": titulo, "proyecto": (proyecto or "").strip()[:120], "creado": now, "actualizado": now,
           "version": 1, "html": limpio, **_stamp(procedencia, verificado), "versiones": []}
    with _Lock():
        _write(_path(aid), rec)
        _prune()
    return {"id": aid, "titulo": titulo, "quitado": quitado, "version": 1}


def actualizar(aid: str, html_src: str, titulo: str = None, procedencia: str = "", verificado: bool = True,
               nota: str = "") -> dict:
    limpio, quitado = sanitize(html_src)
    if len(re.sub(r"<[^>]+>", "", limpio).strip()) < 5:
        raise ValueError("el artefacto quedó vacío; escribe contenido en HTML sencillo")
    with _Lock():
        rec = _read(aid)
        if not rec:
            raise ValueError("no existe un artefacto con ese id")
        rec["versiones"] = ([{"v": rec["version"], "ts": rec["actualizado"], "html": rec["html"],
                              "nota": (nota or "")[:200]}] + rec.get("versiones", []))[:MAX_VERSIONS]
        rec.update(html=limpio, version=rec["version"] + 1, actualizado=int(time.time()), **_stamp(procedencia, verificado))
        if titulo and titulo.strip():
            rec["titulo"] = titulo.strip()[:120]
        _write(_path(aid), rec)
    return {"id": aid, "titulo": rec["titulo"], "quitado": quitado, "version": rec["version"]}


def obtener(aid: str):
    """Registro completo + documento listo para el iframe, o None."""
    rec = _read(aid)
    if not rec:
        return None
    out = {k: rec[k] for k in ("id", "titulo", "proyecto", "creado", "actualizado", "version")}
    out["verificado"] = rec.get("verificado", True)
    out["procedencia"] = rec.get("procedencia", "")
    out["doc"] = render_document(rec["titulo"], rec["html"], rec.get("procedencia", ""), rec.get("verificado", True))
    return out


def listar(proyecto: str = None, limit: int = 50) -> list:
    items = []
    try:
        files = list(ART_DIR.glob("a*.json"))
    except OSError:
        files = []
    for f in files:
        try:
            r = json.loads(f.read_text())
        except Exception:
            continue
        if proyecto and proyecto.lower() not in (r.get("proyecto", "") + " " + r.get("titulo", "")).lower():
            continue
        items.append({"id": r["id"], "titulo": r["titulo"], "proyecto": r.get("proyecto", ""),
                      "actualizado": r["actualizado"], "version": r["version"], "verificado": r.get("verificado", True)})
    return sorted(items, key=lambda x: -x["actualizado"])[:max(1, min(limit, 200))]


def borrar(aid: str) -> bool:
    with _Lock():
        p = _path(aid)
        if not p.exists():
            return False
        p.unlink()
    return True


def _prune() -> None:
    files = sorted(ART_DIR.glob("a*.json"), key=lambda p: p.stat().st_mtime)
    for p in files[:-MAX_ARTIFACTS]:
        p.unlink(missing_ok=True)
