#!/usr/bin/env python3
"""
biblioteca.py — Biblioteca Legal Kawiil: buscar, revisar y ver qué aprenden los agentes.

App web de SOLO LECTURA sobre las bases de SJF y DOF que ya mantiene Louis en el servidor:
  • Buscar     texto completo (FTS5) en tesis/jurisprudencias y en notas del DOF.
  • Ficha      de cada tesis o nota, con su PDF incrustado y los agentes que ya la revisaron.
  • Agentes    por agente kawiil-*: cuánto han indexado, de cuánto es su universo y qué aprendieron.
  • Estado     cobertura y pendientes de ambas bases.

Seguridad (ver docs/BIBLIOTECA-ACCESO.md):
  • Escucha SOLO en 127.0.0.1. Llega gente a través de Caddy + oauth2-proxy (Microsoft 365) o,
    mientras eso no esté activo, por túnel SSH:  ssh -L 4050:127.0.0.1:4050 polo@louis.kawiil.mx
  • Bases abiertas con mode=ro + query_only; consultas parametrizadas; tope de tiempo por consulta.
  • Sin JavaScript (CSP default-src 'none'); todo texto dinámico se escapa. No incrusta tokens.
  • Identidad: si la petición llegó por Caddy (X-Forwarded-Proto) EXIGE un correo verificado en
    X-Auth-Request-Email (falla cerrado); por loopback directo (túnel) entra como "local".
  • PDFs: solo por número de registro, solo rutas bajo las carpetas de PDFs permitidas.
  • Registro de acceso (quién consultó qué) y límite de peticiones por persona.

Uso:  python3 biblioteca.py            # sirve en 127.0.0.1:4050
      python3 biblioteca.py --estado   # imprime el estado de agentes e indexación y sale
"""
from __future__ import annotations

import ast
import collections
import datetime as dt
import html
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── Configuración ────────────────────────────────────────────────────────────
HOST = os.environ.get("BIBLIOTECA_HOST", "127.0.0.1")
PORT = int(os.environ.get("BIBLIOTECA_PORT", "4050"))
SJF_DB = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
DOF_DB = os.environ.get("DOF_DB_PATH", "/opt/openclaw/legal/dof/biblioteca_dof.db")
SJF_PDF_ROOT = Path(os.environ.get("SJF_PDF_DIR", "/opt/openclaw/legal/sjf/pdfs"))
DOF_PDF_ROOT = Path(os.environ.get("DOF_PDF_DIR", "/opt/openclaw/legal/dof/pdfs"))
KNOWLEDGE = Path(os.environ.get("LEGAL_KNOWLEDGE_BASE", "/opt/openclaw/spaces/general/agents/knowledge"))
LOUIS_CORE = Path(os.environ.get("LOUIS_CORE_PATH", "/opt/openclaw/scripts/louis_core.py"))
OLLAMA_MEMORY = Path(os.environ.get("OLLAMA_LEGAL_MEMORY", "/opt/openclaw/spaces/general/OLLAMA_LEGAL_MEMORY.md"))
AUDIT_LOG = Path(os.environ.get("BIBLIOTECA_AUDIT_LOG", "/opt/openclaw/logs/biblioteca-access.log"))
EMAIL_DOMAIN = os.environ.get("BIBLIOTECA_EMAIL_DOMAIN", "kawiil.mx").lower()
REQUIRE_AUTH = os.environ.get("BIBLIOTECA_REQUIRE_AUTH", "0") == "1"
RATE_PER_MIN = int(os.environ.get("BIBLIOTECA_RATE", "120"))
QUERY_TIMEOUT_S = float(os.environ.get("BIBLIOTECA_QUERY_TIMEOUT_S", "8"))
PAGE = 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("biblioteca")

esc = html.escape


# ── Acceso a bases (solo lectura, con tope de tiempo) ────────────────────────
class Pesada(Exception):
    """La consulta superó el tope de tiempo."""


def abrir(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    t0 = time.time()
    c.set_progress_handler(lambda: 1 if time.time() - t0 > QUERY_TIMEOUT_S else 0, 100000)
    return c


def consulta(path: str, sql: str, args=()) -> list:
    if not Path(path).exists():
        return []
    c = abrir(path)
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "interrupt" in str(e).lower():
            raise Pesada() from e
        raise
    finally:
        c.close()


def uno(path: str, sql: str, args=(), defecto=None):
    try:
        r = consulta(path, sql, args)
    except Exception:
        return defecto
    return r[0][0] if r and r[0][0] is not None else defecto


# ── Texto de búsqueda seguro para FTS5 ───────────────────────────────────────
def fts_query(q: str) -> str:
    """Convierte lo que escribe la persona en una consulta FTS5 segura: frases entre comillas y
    palabras sueltas, todas entre comillas (nunca operadores), unidas con AND."""
    q = (q or "").strip()[:200]
    frases = [f.strip() for f in re.findall(r'"([^"]{1,80})"', q) if f.strip()][:3]
    resto = re.sub(r'"[^"]*"', " ", q)
    toks = [t for t in re.findall(r"\w+", resto) if len(t) >= 2][:8]
    return " ".join([f'"{f}"' for f in frases] + [f'"{t}"' for t in toks])


def resaltado(snippet: str) -> str:
    """snippet() marca coincidencias con \\x01 y \\x02; se escapa todo y se vuelve <mark>."""
    return esc(snippet or "").replace("\x01", "<mark>").replace("\x02", "</mark>")


# ── Búsqueda ─────────────────────────────────────────────────────────────────
def buscar_sjf(q: str, tipo: str, epoca: str, pag: int):
    m = fts_query(q)
    if not m:
        return [], 0
    where, args = ["tesis_fts MATCH ?"], [m]
    if tipo == "jurisprudencia":
        where.append("(t.ta_tj = 1 OR t.tipo_tesis LIKE '%urisprud%')")
    elif tipo == "aislada":
        where.append("(COALESCE(t.ta_tj,0) != 1 AND COALESCE(t.tipo_tesis,'') NOT LIKE '%urisprud%')")
    if epoca:
        where.append("t.epoca = ?")
        args.append(epoca)
    w = " AND ".join(where)
    base = "FROM tesis_fts JOIN tesis t ON t.registro_digital = tesis_fts.rowid WHERE " + w
    total = uno(SJF_DB, f"SELECT COUNT(*) FROM (SELECT 1 {base} LIMIT 1001)", args, 0)
    rows = consulta(
        SJF_DB,
        "SELECT t.registro_digital AS id, t.rubro, t.epoca, t.instancia, t.materias, t.fecha_publicacion AS fecha, "
        "t.tipo_tesis, t.ta_tj, snippet(tesis_fts, 1, char(1), char(2), '…', 28) AS ext "
        f"{base} ORDER BY rank LIMIT ? OFFSET ?", args + [PAGE, pag * PAGE])
    return rows, total


def buscar_dof(q: str, tipo: str, orden: str, pag: int):
    m = fts_query(q)
    if not m:
        return [], 0
    where, args = ["notas_fts MATCH ?", "n.incluido = 1"], [m]
    if tipo:
        where.append("n.tipo_documento = ?")
        args.append(tipo)
    w = " AND ".join(where)
    base = "FROM notas_fts JOIN notas n ON n.cod_nota = notas_fts.rowid WHERE " + w
    total = uno(DOF_DB, f"SELECT COUNT(*) FROM (SELECT 1 {base} LIMIT 1001)", args, 0)
    order = "n.fecha DESC, rank" if orden == "fecha" else "rank"
    rows = consulta(
        DOF_DB,
        "SELECT n.cod_nota AS id, n.titulo, n.fecha, n.tipo_documento, n.nombre_cod_orga_uno AS orga, "
        "snippet(notas_fts, 1, char(1), char(2), '…', 28) AS ext "
        f"{base} ORDER BY {order} LIMIT ? OFFSET ?", args + [PAGE, pag * PAGE])
    return rows, total


# ── Conocimiento de los agentes ──────────────────────────────────────────────
# El indexador arma el nombre con la fecha tal cual viene de la base ("2026-08-28", "2026-08-28 00:00:00",
# "nd"...). Se acepta cualquier cosa entre la fuente y el id numérico final.
DOC_RE = re.compile(r"^(dof|sjf)-(.+)-(\d+)\.md$")
AGENT_RE = re.compile(r"^kawiil-[a-z0-9-]{1,40}$")
_cache: dict = {}
_cache_lock = threading.Lock()


def cacheado(clave: str, ttl: float, fn):
    with _cache_lock:
        v = _cache.get(clave)
        if v and time.time() - v[0] < ttl:
            return v[1]
    val = fn()
    with _cache_lock:
        _cache[clave] = (time.time(), val)
    return val


def mapa_agentes() -> dict:
    """KAWIIL_KNOWLEDGE_MAP leído con ast (sin ejecutar louis_core)."""
    def leer():
        try:
            tree = ast.parse(LOUIS_CORE.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                tgt = getattr(n, "targets", None) or [getattr(n, "target", None)]
                if any(getattr(t, "id", "") == "KAWIIL_KNOWLEDGE_MAP" for t in tgt if t) and n.value is not None:
                    return ast.literal_eval(n.value)
        except Exception as e:
            log.warning("no pude leer KAWIIL_KNOWLEDGE_MAP: %s", e)
        return {}
    return cacheado("mapa", 600, leer)


def escanear_conocimiento() -> dict:
    """{agente: {docs:[(fuente, fecha, id, archivo)], index:{...}}} y doc_id→[agentes]."""
    def leer():
        agentes, por_doc = {}, collections.defaultdict(list)
        if KNOWLEDGE.exists():
            for d in sorted(KNOWLEDGE.glob("kawiil-*")):
                if not d.is_dir() or not AGENT_RE.match(d.name):
                    continue
                docs = []
                dd = d / "docs"
                if dd.exists():
                    for f in dd.glob("*.md"):
                        m = DOC_RE.match(f.name)
                        if m:
                            fe = m.group(2)[:10] if re.match(r"\d{4}-\d{2}-\d{2}", m.group(2)) else m.group(2)
                            docs.append((m.group(1), fe, int(m.group(3)), f.name))
                            por_doc[(m.group(1), int(m.group(3)))].append(d.name)
                try:
                    idx = json.loads((d / "index.json").read_text())
                except Exception:
                    idx = {}
                agentes[d.name] = {"docs": docs, "index": idx}
        return {"agentes": agentes, "por_doc": dict(por_doc)}
    return cacheado("conocimiento", 60, leer)


def consulta_or(palabras: list[str]) -> str:
    ps = []
    for p in palabras:
        p = re.sub(r'["]', " ", p).strip()
        if p:
            ps.append(f'"{p}"')
    return " OR ".join(ps)


def universo_agente(nombre: str) -> dict:
    """Documentos de cada base que coinciden con las palabras clave del agente (aprox., vía FTS)."""
    cfg = mapa_agentes().get(nombre) or {}

    def calc():
        out = {"dof": None, "sjf": None}
        q = consulta_or(cfg.get("dof") or [])
        if q:
            out["dof"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas_fts f JOIN notas n ON n.cod_nota=f.rowid "
                                      "WHERE notas_fts MATCH ? AND n.incluido=1", (q,))
        q = consulta_or(cfg.get("sjf") or [])
        if q:
            out["sjf"] = uno(SJF_DB, "SELECT COUNT(*) FROM tesis_fts WHERE tesis_fts MATCH ?", (q,))
        return out
    return cacheado(f"universo-{nombre}", 1800, calc)


def lineas_aprendizaje(agente: str, n: int = 25) -> list[str]:
    try:
        ls = [l for l in OLLAMA_MEMORY.read_text(encoding="utf-8").splitlines()
              if l.startswith("- [") and f"[{agente}]" in l]
        return ls[-n:][::-1]
    except Exception:
        return []


def resumen_agentes() -> list[dict]:
    con = escanear_conocimiento()["agentes"]
    out = []
    for nombre in sorted(set(con) | set(mapa_agentes())):
        info = con.get(nombre, {"docs": [], "index": {}})
        docs = info["docs"]
        n_dof = sum(1 for d in docs if d[0] == "dof")
        n_sjf = sum(1 for d in docs if d[0] == "sjf")
        fechas = sorted(d[1] for d in docs if re.match(r"\d{4}-\d{2}-\d{2}", d[1]))
        out.append({
            "nombre": nombre, "label": (mapa_agentes().get(nombre) or {}).get("label") or info["index"].get("label", ""),
            "dof": n_dof, "sjf": n_sjf, "total": len(docs),
            "desde": fechas[0] if fechas else "", "hasta": fechas[-1] if fechas else "",
            "ultimo": (info["index"].get("last_indexed") or "")[:16].replace("T", " "),
        })
    return out


# ── Estado de las bases ──────────────────────────────────────────────────────
def estado() -> dict:
    def calc():
        e = {}
        e["sjf_total"] = uno(SJF_DB, "SELECT COUNT(*) FROM tesis", (), 0)
        e["sjf_jur"] = uno(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE ta_tj=1 OR tipo_tesis LIKE '%urisprud%'", (), 0)
        e["sjf_ultima"] = uno(SJF_DB, "SELECT substr(MAX(fecha_publicacion),1,10) FROM tesis WHERE fecha_publicacion!=''", (), "—")
        e["sjf_texto"] = uno(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE texto IS NOT NULL AND texto!=''", (), 0)
        e["sjf_pdf"] = uno(SJF_DB, "SELECT COUNT(*) FROM tesis WHERE pdf_path LIKE ? || '%'", (str(SJF_PDF_ROOT),), 0)
        e["dof_total"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas", (), 0)
        e["dof_validas"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas WHERE fecha BETWEEN '1900-01-01' AND date('now')", (), 0)
        e["dof_ultima"] = uno(DOF_DB, "SELECT MAX(fecha) FROM notas WHERE fecha BETWEEN '1900-01-01' AND date('now')", (), "—")
        e["dof_html"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas WHERE incluido=1 AND existe_html=1", (), 0)
        e["dof_texto"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas WHERE texto_plano IS NOT NULL AND texto_plano!=''", (), 0)
        e["dof_pdf"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas WHERE pdf_path IS NOT NULL AND pdf_path!=''", (), 0)
        e["dof_pdf_pend"] = uno(DOF_DB, "SELECT COUNT(*) FROM notas WHERE incluido=1 AND content_downloaded_at IS NOT NULL "
                                        "AND (pdf_path IS NULL OR pdf_path='')", (), 0)
        e["generado"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        return e
    return cacheado("estado", 300, calc)


def n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else esc(str(x if x is not None else "—"))


def pct(a, b) -> str:
    return f"{a / b * 100:.0f}%" if isinstance(a, int) and isinstance(b, int) and b else "—"


# ── Presentación (HTML sin JavaScript) ───────────────────────────────────────
CSS = """
:root{--blue:#1a6ef5;--navy:#0a1a8c;--ink:#1f2430;--grey:#667085;--line:#e3e8f2;--bg:#f6f8fd;--card:#fff;--mark:#fff1a8}
@media(prefers-color-scheme:dark){:root{--ink:#e8ecf5;--grey:#98a2b3;--line:#2a3350;--bg:#0f1424;--card:#171e33;--mark:#6b5b00}}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--navy);color:#fff;border-bottom:4px solid var(--blue)}
header .in{max-width:1080px;margin:0 auto;padding:12px 16px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header b{font-size:17px}header nav{display:flex;gap:14px;flex:1;flex-wrap:wrap}header nav a{color:#cfe0ff;padding:4px 2px}
header nav a.on{color:#fff;border-bottom:2px solid #fff}header .quien{font-size:12px;color:#aac4ff}
main{max-width:1080px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:22px;margin:8px 0 14px}h2{font-size:15px;margin:22px 0 8px;color:var(--blue);text-transform:uppercase;letter-spacing:.04em}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.kpi b{display:block;font-size:24px}.kpi span{color:var(--grey);font-size:12px}
form.s{display:flex;gap:8px;flex-wrap:wrap}input[type=text],select{padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit}
input[type=text]{flex:1;min-width:220px}button{padding:9px 16px;border:0;border-radius:8px;background:var(--blue);color:#fff;font:inherit;cursor:pointer}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}th{color:var(--grey);font-size:12px;text-transform:uppercase}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.res{border-bottom:1px solid var(--line);padding:11px 2px}.res .t{font-weight:600}.res .m{color:var(--grey);font-size:12.5px;margin:2px 0}.res .x{font-size:13.5px}
mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--grey);margin-right:4px}
.tag.j{border-color:var(--blue);color:var(--blue)}.tag.d{border-color:#2e7d32;color:#2e7d32}
dl.f{display:grid;grid-template-columns:150px 1fr;gap:4px 14px;margin:0}dl.f dt{color:var(--grey);font-size:13px}dl.f dd{margin:0}
.txt{white-space:pre-wrap;font-family:Georgia,'Times New Roman',serif;line-height:1.6}
iframe.pdf{width:100%;height:78vh;border:1px solid var(--line);border-radius:8px;background:#fff}
.pg{display:flex;gap:12px;justify-content:space-between;margin:16px 0}.aviso{color:var(--grey);font-size:13px}
.bar{height:8px;background:var(--line);border-radius:99px;overflow:hidden}.bar i{display:block;height:100%;background:var(--blue)}
"""

NAV = (("/", "Inicio"), ("/buscar", "Buscar"), ("/agentes", "Agentes"), ("/estado", "Estado"))


def pagina(titulo: str, cuerpo: str, activo: str = "/", quien: str = "") -> bytes:
    nav = "".join(f'<a href="{u}"{" class=on" if u == activo else ""}>{esc(t)}</a>' for u, t in NAV)
    return (f"<!doctype html><html lang=es><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{esc(titulo)} · Biblioteca Legal Kawiil</title>"
            f"<style>{CSS}</style></head><body><header><div class=in><b>Biblioteca Legal Kawiil</b><nav>{nav}</nav>"
            f"<span class=quien>{esc(quien)}</span></div></header><main>{cuerpo}</main></body></html>").encode("utf-8")


def caja_busqueda(q="", fuente="sjf", tipo="", epoca="", orden="relevancia") -> str:
    def op(v, t, sel):
        return f'<option value="{esc(v)}"{" selected" if v == sel else ""}>{esc(t)}</option>'
    return (f'<form class=s action="/buscar" method=get><input type=text name=q value="{esc(q)}" '
            f'placeholder="Busca en tesis y en el DOF (usa comillas para frases exactas)" autofocus>'
            f'<select name=fuente>{op("sjf", "Tesis y jurisprudencia", fuente)}{op("dof", "Diario Oficial", fuente)}</select>'
            f'<select name=tipo>{op("", "Cualquier tipo", tipo)}{op("jurisprudencia", "Solo jurisprudencia", tipo)}{op("aislada", "Solo tesis aislada", tipo)}</select>'
            f'<select name=orden>{op("relevancia", "Por relevancia", orden)}{op("fecha", "Más recientes (DOF)", orden)}</select>'
            f'<button>Buscar</button></form>')


# ── Páginas ──────────────────────────────────────────────────────────────────
def p_inicio(quien):
    e = estado()
    ag = resumen_agentes()
    kp = [("Tesis y jurisprudencias", n(e["sjf_total"]), f"{n(e['sjf_jur'])} jurisprudencias"),
          ("Última tesis publicada", esc(str(e["sjf_ultima"])), "SJF"),
          ("Notas del DOF", n(e["dof_validas"]), f"hasta {esc(str(e['dof_ultima']))}"),
          ("Documentos revisados por agentes", n(sum(a["total"] for a in ag)), f"{len(ag)} agentes")]
    k = "".join(f'<div class=kpi><b>{a}</b>{esc(t)}<br><span>{b}</span></div>' for t, a, b in kp)
    cuerpo = (f"<h1>Biblioteca Legal</h1>{caja_busqueda()}<h2>Panorama</h2><div class=kpis>{k}</div>"
              f"<p class=aviso>Datos al {esc(e['generado'])}. Esta biblioteca es de solo lectura.</p>")
    return pagina("Inicio", cuerpo, "/", quien)


def p_buscar(qs, quien):
    q = (qs.get("q", [""])[0] or "").strip()
    fuente = qs.get("fuente", ["sjf"])[0]
    fuente = fuente if fuente in ("sjf", "dof") else "sjf"
    tipo = qs.get("tipo", [""])[0]
    orden = qs.get("orden", ["relevancia"])[0]
    epoca = (qs.get("epoca", [""])[0] or "")[:40]
    try:
        pag = max(0, min(int(qs.get("p", ["0"])[0]), 500))
    except ValueError:
        pag = 0
    cuerpo = f"<h1>Buscar</h1>{caja_busqueda(q, fuente, tipo, epoca, orden)}"
    if not q:
        return pagina("Buscar", cuerpo + "<p class=aviso>Escribe palabras o una frase entre comillas.</p>", "/buscar", quien)
    if not fts_query(q):
        return pagina("Buscar", cuerpo + "<p class=aviso>Escribe al menos una palabra de 2 letras o más.</p>", "/buscar", quien)
    try:
        rows, total = (buscar_sjf(q, tipo, epoca, pag) if fuente == "sjf" else buscar_dof(q, "" if tipo in ("jurisprudencia", "aislada") else tipo, orden, pag))
    except Pesada:
        return pagina("Buscar", cuerpo + "<div class=card>Esa búsqueda es demasiado amplia. Agrega más palabras o una frase entre comillas.</div>", "/buscar", quien)
    except sqlite3.Error as e:
        log.warning("búsqueda falló: %s", e)
        return pagina("Buscar", cuerpo + "<div class=card>No pude ejecutar la búsqueda ahora. Intenta de nuevo.</div>", "/buscar", quien)
    tope = "más de 1,000" if total > 1000 else n(total)
    cuerpo += f"<p class=aviso>{tope} resultado(s) para «{esc(q)}»</p>"
    for r in rows:
        if fuente == "sjf":
            jur = (r["ta_tj"] == 1) or "urisprud" in (r["tipo_tesis"] or "")
            tag = f'<span class="tag j">{"Jurisprudencia" if jur else "Tesis aislada"}</span>'
            meta = " · ".join(x for x in (r["epoca"], r["instancia"], (r["fecha"] or "")[:10], r["materias"]) if x)
            cuerpo += (f'<div class=res><div class=t>{tag}<a href="/tesis/{r["id"]}">{esc(r["rubro"] or "(sin rubro)")}</a></div>'
                       f'<div class=m>{esc(meta)} · reg. {r["id"]}</div><div class=x>{resaltado(r["ext"])}</div></div>')
        else:
            tag = f'<span class="tag d">{esc(r["tipo_documento"] or "DOF")}</span>'
            meta = " · ".join(x for x in (r["fecha"], r["orga"]) if x)
            cuerpo += (f'<div class=res><div class=t>{tag}<a href="/nota/{r["id"]}">{esc(r["titulo"] or "(sin título)")}</a></div>'
                       f'<div class=m>{esc(meta)} · cod. {r["id"]}</div><div class=x>{resaltado(r["ext"])}</div></div>')
    if not rows:
        cuerpo += "<div class=card>Sin resultados.</div>"
    base = "/buscar?" + urllib.parse.urlencode({"q": q, "fuente": fuente, "tipo": tipo, "orden": orden, "epoca": epoca})
    nav = ""
    if pag > 0:
        nav += f'<a href="{esc(base)}&p={pag - 1}">← Anterior</a>'
    nav += "<span></span>"
    if (pag + 1) * PAGE < min(total, 1000):
        nav += f'<a href="{esc(base)}&p={pag + 1}">Siguiente →</a>'
    return pagina("Buscar", cuerpo + f'<div class=pg>{nav}</div>', "/buscar", quien)


def agentes_de(fuente: str, ident: int) -> str:
    ags = escanear_conocimiento()["por_doc"].get((fuente, ident), [])
    if not ags:
        return "<p class=aviso>Ningún agente ha revisado todavía este documento.</p>"
    items = []
    for a in ags:
        docs = escanear_conocimiento()["agentes"][a]["docs"]
        f = next((d[3] for d in docs if d[0] == fuente and d[2] == ident), "")
        items.append(f'<li><a href="/agentes/{esc(a)}/{esc(urllib.parse.quote(f, safe=""))}">{esc(a)}</a></li>')
    return f"<ul>{''.join(items)}</ul>"


def pdf_link(fuente: str, ident: int, ruta: str | None) -> str:
    if ruta and ruta_permitida(fuente, ruta):
        return (f'<h2>PDF</h2><p><a href="/pdf/{fuente}/{ident}">Abrir en otra pestaña</a></p>'
                f'<iframe class=pdf src="/pdf/{fuente}/{ident}" title="PDF"></iframe>')
    return '<h2>PDF</h2><p class=aviso>Este documento todavía no tiene PDF en el servidor.</p>'


def p_tesis(reg: int, quien):
    r = consulta(SJF_DB, "SELECT * FROM tesis WHERE registro_digital=?", (reg,))
    if not r:
        return None
    t = r[0]
    jur = (t["ta_tj"] == 1) or "urisprud" in (t["tipo_tesis"] or "")
    campos = [("Tipo", "Jurisprudencia" if jur else "Tesis aislada"), ("Clave", t["clave_tesis"]), ("Época", t["epoca"]),
              ("Instancia", t["instancia"]), ("Sala / órgano", t["sala"]), ("Materias", t["materias"]),
              ("Publicación", (t["fecha_publicacion"] or "")[:10]), ("Fuente", t["fuente"]),
              ("Localización", t["localizacion"]), ("Registro digital", t["registro_digital"])]
    dl = "".join(f"<dt>{esc(k)}</dt><dd>{esc(str(v))}</dd>" for k, v in campos if v)
    sec = ""
    for tit, col in (("Texto", "texto"), ("Precedentes", "precedentes"), ("Notas", "notas_genericas"), ("Publicación", "texto_publicacion")):
        if t[col]:
            sec += f"<h2>{tit}</h2><div class='card txt'>{esc(str(t[col]))}</div>"
    cuerpo = (f'<p><a href="/buscar">← Buscar</a></p><h1>{esc(t["rubro"] or "(sin rubro)")}</h1>'
              f'<div class=card><dl class=f>{dl}</dl></div>{sec}'
              f'<h2>Agentes que la revisaron</h2><div class=card>{agentes_de("sjf", reg)}</div>'
              f'{pdf_link("sjf", reg, t["pdf_path"])}'
              f'<p class=aviso>Fuente oficial: <a href="https://sjf2.scjn.gob.mx/detalle/tesis/{reg}" rel="noopener noreferrer" target=_blank>sjf2.scjn.gob.mx</a></p>')
    return pagina(f"Tesis {reg}", cuerpo, "/buscar", quien)


def p_nota(cod: int, quien):
    r = consulta(DOF_DB, "SELECT * FROM notas WHERE cod_nota=?", (cod,))
    if not r:
        return None
    t = r[0]
    campos = [("Fecha", t["fecha"]), ("Edición", t["edicion"]), ("Sección", t["seccion"]), ("Tipo", t["tipo_documento"]),
              ("Dependencia", t["nombre_cod_orga_uno"]), ("Código", t["cod_nota"])]
    dl = "".join(f"<dt>{esc(k)}</dt><dd>{esc(str(v))}</dd>" for k, v in campos if v)
    cuerpo_txt = (f"<h2>Texto</h2><div class='card txt'>{esc(str(t['texto_plano']))}</div>" if t["texto_plano"]
                  else "<h2>Texto</h2><p class=aviso>Esta nota no tiene texto disponible (PDF escaneado o aún sin descargar).</p>")
    cuerpo = (f'<p><a href="/buscar">← Buscar</a></p><h1>{esc(t["titulo"] or "(sin título)")}</h1>'
              f'<div class=card><dl class=f>{dl}</dl></div>{cuerpo_txt}'
              f'<h2>Agentes que la revisaron</h2><div class=card>{agentes_de("dof", cod)}</div>'
              f'{pdf_link("dof", cod, t["pdf_path"])}'
              f'<p class=aviso>Fuente oficial: <a href="https://www.dof.gob.mx/nota_detalle.php?codigo={cod}" rel="noopener noreferrer" target=_blank>dof.gob.mx</a></p>')
    return pagina(f"DOF {cod}", cuerpo, "/buscar", quien)


def p_agentes(quien):
    filas = ""
    for a in resumen_agentes():
        u = universo_agente(a["nombre"])
        rel_d, rel_s = u.get("dof"), u.get("sjf")
        cob_d = f'{pct(a["dof"], rel_d)}' if rel_d else "—"
        cob_s = f'{pct(a["sjf"], rel_s)}' if rel_s else "—"
        rel_d = rel_d if rel_d is not None else "?"
        rel_s = rel_s if rel_s is not None else "?"
        filas += (f'<tr><td><a href="/agentes/{esc(a["nombre"])}">{esc(a["nombre"])}</a><br><span class=aviso>{esc(a["label"])}</span></td>'
                  f'<td class=num>{n(a["dof"])}<br><span class=aviso>de ~{n(rel_d)} · {cob_d}</span></td>'
                  f'<td class=num>{n(a["sjf"])}<br><span class=aviso>de ~{n(rel_s)} · {cob_s}</span></td>'
                  f'<td>{esc(a["desde"])}<br>{esc(a["hasta"])}</td><td>{esc(a["ultimo"] or "—")}</td></tr>')
    cuerpo = ("<h1>Agentes</h1><p class=aviso>«Revisados» son los documentos que el agente ya resumió y de los que sacó un aprendizaje. "
              "«De ~N» es cuántos documentos de cada base coinciden con las palabras clave de su área (cálculo aproximado por búsqueda "
              "de texto); la diferencia es lo que falta por revisar.</p>"
              "<table><tr><th>Agente</th><th class=num>DOF revisados</th><th class=num>SJF revisados</th><th>Fechas revisadas</th><th>Última pasada</th></tr>"
              f"{filas or '<tr><td colspan=5>Sin agentes con conocimiento todavía.</td></tr>'}</table>")
    return pagina("Agentes", cuerpo, "/agentes", quien)


def md_lite(s: str) -> str:
    """Markdown mínimo y seguro: se escapa todo y solo se interpretan encabezados y negritas."""
    out = []
    for ln in s.splitlines():
        e = esc(ln)
        e = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e)
        m = re.match(r"^(#{1,3})\s+(.*)$", e)
        out.append(f"<h2>{m.group(2)}</h2>" if m else (f"<p>{e}</p>" if e.strip() else ""))
    return "\n".join(out)


def p_agente(nombre, quien):
    info = escanear_conocimiento()["agentes"].get(nombre)
    if info is None:
        return None
    docs = sorted(info["docs"], key=lambda d: (d[1], d[2]), reverse=True)
    lis = "".join(f'<tr><td><span class="tag {"d" if d[0] == "dof" else "j"}">{d[0].upper()}</span></td><td>{esc(d[1])}</td>'
                  f'<td><a href="/agentes/{esc(nombre)}/{esc(urllib.parse.quote(d[3], safe=""))}">{esc(d[3])}</a></td></tr>' for d in docs[:150])
    apr = "".join(f"<li>{esc(l[2:])}</li>" for l in lineas_aprendizaje(nombre)) or "<li class=aviso>Sin aprendizajes registrados.</li>"
    cfg = mapa_agentes().get(nombre) or {}
    cuerpo = (f'<p><a href="/agentes">← Agentes</a></p><h1>{esc(nombre)}</h1><p class=aviso>{esc(cfg.get("label", ""))}</p>'
              f'<h2>Lo que está aprendiendo (más reciente primero)</h2><div class=card><ul>{apr}</ul></div>'
              f'<h2>Documentos revisados ({len(docs):,}; se muestran los 150 más recientes)</h2>'
              f'<table><tr><th>Fuente</th><th>Fecha</th><th>Documento</th></tr>{lis}</table>')
    return pagina(nombre, cuerpo, "/agentes", quien)


def p_agente_doc(nombre, archivo, quien):
    archivo = urllib.parse.unquote(archivo)
    if (not AGENT_RE.match(nombre) or not DOC_RE.match(archivo) or Path(archivo).name != archivo
            or "\x00" in archivo or archivo.startswith(".")):
        return None
    carpeta = (KNOWLEDGE / nombre / "docs").resolve()
    f = (carpeta / archivo).resolve()
    if f.parent != carpeta or not f.is_file():      # debe ser un archivo directo de la carpeta del agente
        return None
    m = DOC_RE.match(archivo)
    enlace = f'/{"tesis" if m.group(1) == "sjf" else "nota"}/{int(m.group(3))}'
    cuerpo = (f'<p><a href="/agentes/{esc(nombre)}">← {esc(nombre)}</a> · <a href="{enlace}">Ver el documento original</a></p>'
              f'<div class=card>{md_lite(f.read_text(encoding="utf-8", errors="replace")[:60000])}</div>')
    return pagina(archivo, cuerpo, "/agentes", quien)


def barra(a, b) -> str:
    w = min(100, a / b * 100) if isinstance(a, int) and isinstance(b, int) and b else 0
    return f'<div class=bar><i style="width:{w:.0f}%"></i></div>'


def p_estado(quien):
    e = estado()
    cuerpo = (f"<h1>Estado de las bases</h1><p class=aviso>Al {esc(e['generado'])} (se actualiza cada 5 minutos).</p>"
              f"<h2>SJF — Semanario Judicial</h2><div class=card><dl class=f>"
              f"<dt>Tesis</dt><dd>{n(e['sjf_total'])} ({n(e['sjf_jur'])} jurisprudencias)</dd>"
              f"<dt>Última publicación</dt><dd>{esc(str(e['sjf_ultima']))}</dd>"
              f"<dt>Con texto</dt><dd>{n(e['sjf_texto'])} ({pct(e['sjf_texto'], e['sjf_total'])})</dd>"
              f"<dt>Con PDF en el servidor</dt><dd>{n(e['sjf_pdf'])} ({pct(e['sjf_pdf'], e['sjf_total'])}){barra(e['sjf_pdf'], e['sjf_total'])}</dd></dl></div>"
              f"<h2>DOF — Diario Oficial</h2><div class=card><dl class=f>"
              f"<dt>Notas</dt><dd>{n(e['dof_validas'])} válidas de {n(e['dof_total'])}</dd>"
              f"<dt>Última edición</dt><dd>{esc(str(e['dof_ultima']))}</dd>"
              f"<dt>Con HTML descargable</dt><dd>{n(e['dof_html'])} ({pct(e['dof_html'], e['dof_validas'])} del total)</dd>"
              f"<dt>Con texto</dt><dd>{n(e['dof_texto'])}</dd>"
              f"<dt>PDF generados</dt><dd>{n(e['dof_pdf'])} · pendientes: {n(e['dof_pdf_pend'])}{barra(e['dof_pdf'], e['dof_pdf'] + (e['dof_pdf_pend'] or 0) if isinstance(e['dof_pdf'], int) else 0)}</dd></dl></div>")
    return pagina("Estado", cuerpo, "/estado", quien)


# ── PDFs ─────────────────────────────────────────────────────────────────────
def ruta_permitida(fuente: str, ruta: str) -> Path | None:
    raiz = SJF_PDF_ROOT if fuente == "sjf" else DOF_PDF_ROOT
    try:
        p = Path(ruta).resolve()
        r = raiz.resolve()
    except Exception:
        return None
    return p if (r in p.parents and p.suffix.lower() == ".pdf" and p.is_file()) else None


def pdf_bytes(fuente: str, ident: int):
    if fuente == "sjf":
        r = consulta(SJF_DB, "SELECT pdf_path FROM tesis WHERE registro_digital=?", (ident,))
    else:
        r = consulta(DOF_DB, "SELECT pdf_path FROM notas WHERE cod_nota=?", (ident,))
    if not r or not r[0][0]:
        return None
    p = ruta_permitida(fuente, r[0][0])
    return p.read_bytes() if p else None


# ── Identidad, límite y registro ─────────────────────────────────────────────
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_visitas: dict = collections.defaultdict(collections.deque)
_audit_lock = threading.Lock()


def identidad(headers) -> tuple[str | None, str]:
    """(correo, motivo). correo=None → se rechaza."""
    via_proxy = "X-Forwarded-Proto" in headers
    em = (headers.get("X-Auth-Request-Email") or "").strip()
    if EMAIL_RE.match(em) and "{" not in em:
        if EMAIL_DOMAIN and not em.lower().endswith("@" + EMAIL_DOMAIN):
            return None, "dominio no permitido"
        return em.lower(), "ok"
    if via_proxy or REQUIRE_AUTH:
        return None, "sin identidad verificada"
    return "local", "ok"      # loopback directo (túnel SSH)


def limite_ok(quien: str) -> bool:
    ahora = time.time()
    q = _visitas[quien]
    while q and ahora - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        return False
    q.append(ahora)
    return True


def auditar(quien: str, ip: str, metodo: str, ruta: str, estado_http: int):
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _audit_lock:
            if AUDIT_LOG.exists() and AUDIT_LOG.stat().st_size > 5_000_000:
                AUDIT_LOG.replace(AUDIT_LOG.with_suffix(".log.1"))
            with AUDIT_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now().isoformat(timespec='seconds')}\t{quien}\t{ip}\t{metodo}\t{ruta[:300]}\t{estado_http}\n")
    except Exception:
        pass


# ── Servidor HTTP ────────────────────────────────────────────────────────────
SEG = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
       "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                                  "frame-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'"}


class H(BaseHTTPRequestHandler):
    server_version = "Biblioteca"
    sys_version = ""

    def log_message(self, *a):
        pass

    def _enviar(self, codigo: int, cuerpo: bytes, tipo="text/html; charset=utf-8", extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for k, v in {**SEG, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _simple(self, codigo: int, texto: str, quien=""):
        self._enviar(codigo, pagina(texto, f"<h1>{esc(texto)}</h1>", "", quien))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        ruta, qs = u.path, urllib.parse.parse_qs(u.query, keep_blank_values=True, max_num_fields=20)
        ip = self.headers.get("X-Real-IP") or self.client_address[0]
        if ruta == "/healthz" and "X-Forwarded-Proto" not in self.headers:
            return self._enviar(200, b"ok", "text/plain")
        quien, motivo = identidad(self.headers)
        if quien is None:
            auditar("-", ip, self.command, ruta, 401)
            return self._simple(401, "Acceso no autorizado")
        if not limite_ok(quien):
            auditar(quien, ip, self.command, ruta, 429)
            return self._enviar(429, b"Demasiadas peticiones", "text/plain", {"Retry-After": "30"})
        codigo = 200
        try:
            m = re.match(r"^/(tesis|nota)/(\d{1,12})$", ruta)
            m2 = re.match(r"^/pdf/(sjf|dof)/(\d{1,12})$", ruta)
            m3 = re.match(r"^/agentes/([a-z0-9-]{1,48})$", ruta)
            m4 = re.match(r"^/agentes/([a-z0-9-]{1,48})/([^/]{1,300})$", ruta)
            if ruta == "/":
                r = p_inicio(quien)
            elif ruta == "/buscar":
                r = p_buscar(qs, quien)
            elif ruta == "/agentes":
                r = p_agentes(quien)
            elif ruta == "/estado":
                r = p_estado(quien)
            elif m:
                r = p_tesis(int(m.group(2)), quien) if m.group(1) == "tesis" else p_nota(int(m.group(2)), quien)
            elif m2:
                b = pdf_bytes(m2.group(1), int(m2.group(2)))
                if b is None:
                    codigo = 404
                    self._simple(404, "PDF no disponible", quien)
                else:
                    self._enviar(200, b, "application/pdf", {"Content-Disposition": "inline",
                                                             "Content-Security-Policy": "frame-ancestors 'self'"})
                auditar(quien, ip, self.command, ruta, codigo)
                return
            elif m3:
                r = p_agente(m3.group(1), quien)
            elif m4:
                r = p_agente_doc(m4.group(1), m4.group(2), quien)
            else:
                r = None
            if r is None:
                codigo = 404
                self._simple(404, "No encontrado", quien)
            else:
                self._enviar(200, r)
        except Exception as e:  # nunca filtrar detalles internos
            codigo = 500
            log.exception("error en %s: %s", ruta, e)
            self._simple(500, "Error interno", quien)
        auditar(quien, ip, self.command, self.path, codigo)

    def do_POST(self):
        self._enviar(405, b"Solo lectura", "text/plain", {"Allow": "GET, HEAD"})

    do_PUT = do_DELETE = do_PATCH = do_POST


def verificar_bases():
    """Avisa en el log si una base no se puede abrir (p. ej. WAL sin permiso para el archivo -shm)."""
    for nombre, ruta in (("SJF", SJF_DB), ("DOF", DOF_DB)):
        if not Path(ruta).exists():
            log.warning("%s: no existe %s", nombre, ruta)
            continue
        try:
            c = abrir(ruta)
            c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
            modo = c.execute("PRAGMA journal_mode").fetchone()[0]
            c.close()
            log.info("%s: base abierta en solo lectura (journal_mode=%s)", nombre, modo)
        except sqlite3.Error as e:
            log.error("%s: NO se puede abrir %s en solo lectura: %s. Si el modo es WAL, agrega el "
                      "directorio de la base a ReadWritePaths de la unidad (ver biblioteca.service).", nombre, ruta, e)


def calentar():
    """Precalcula en segundo plano lo que tarda (universo de cada agente, estado)."""
    try:
        estado()
        for nombre in mapa_agentes():
            universo_agente(nombre)
        log.info("precálculo listo")
    except Exception as e:
        log.warning("precálculo falló: %s", e)


def cli_estado() -> int:
    e = estado()
    print(f"SJF: {n(e['sjf_total'])} tesis ({n(e['sjf_jur'])} jurisprudencias) · última {e['sjf_ultima']} · PDF servidor {n(e['sjf_pdf'])}")
    print(f"DOF: {n(e['dof_validas'])} notas · última {e['dof_ultima']} · PDF {n(e['dof_pdf'])} (pendientes {n(e['dof_pdf_pend'])})")
    print(f"\n{'agente':22} {'DOF rev.':>9} {'de ~':>9} {'SJF rev.':>9} {'de ~':>9}  fechas revisadas            última pasada")
    for a in resumen_agentes():
        u = universo_agente(a["nombre"])
        print(f"{a['nombre']:22} {a['dof']:>9,} {u.get('dof') or 0:>9,} {a['sjf']:>9,} {u.get('sjf') or 0:>9,}  "
              f"{a['desde'] or '—':10} → {a['hasta'] or '—':10}  {a['ultimo'] or '—'}")
    return 0


def main() -> int:
    if "--estado" in sys.argv:
        return cli_estado()
    verificar_bases()
    threading.Thread(target=calentar, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    log.info("Biblioteca Legal en http://%s:%d (solo lectura)", HOST, PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
