#!/usr/bin/env python3
"""Pruebas del indexador de agentes (services/legal_indexer.py) con bases sintéticas que usan los
esquemas reales (incluido FTS5). Sin red ni DeepSeek.   Uso:  python3 tests/test_legal_indexer.py"""
import datetime as dt, json, logging, random, re, shutil, sqlite3, sys, tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "services"))
import legal_indexer as LI

def esquema(f):
    return re.search(r'SCHEMA = """(.*?)"""', (AQUI.parent / "legal-scrapers" / f).read_text(), re.S).group(1)

W = Path(tempfile.mkdtemp(prefix="indexer-test-"))
random.seed(7); KW = ["ISR", "SAT", "reforma fiscal"]; hoy = dt.date.today()
dof = sqlite3.connect(W / "dof.db"); dof.executescript(esquema("dof_biblioteca.py"))
cod = 0
for d in range(600):
    for _ in range(3):
        cod += 1; kw = random.choice(KW)
        dof.execute("insert into notas(cod_nota,fecha,titulo,tipo_documento,incluido,existe_html,texto_plano) values(?,?,?,?,1,1,?)",
                    (cod, (hoy - dt.timedelta(days=d)).isoformat(), f"Nota {cod} {kw}" if random.random() < .5 else f"Nota {cod}",
                     "DECRETO", "x" * 250 + f" {kw} "))
for i in range(30):                                   # sin clasificar: no deben indexarse
    cod += 1
    dof.execute("insert into notas(cod_nota,fecha,titulo,incluido,texto_plano) values(?,?,?,NULL,?)", (cod, hoy.isoformat(), f"Sin clasificar {i}", "y" * 300 + " ISR"))
sjf = sqlite3.connect(W / "sjf.db"); sjf.executescript(esquema("sjf_biblioteca.py"))
for i in range(200):
    kw = random.choice(KW)
    sjf.execute("insert into tesis(registro_digital,rubro,texto,tipo_tesis,ta_tj,fecha_publicacion,fetched_at) values(?,?,?,?,?,?,?)",
                (500000 + i, f"TESIS {i} {kw}", "z" * 250 + f" {kw} ", "Tesis Jurisprudenciales" if i % 4 == 0 else "Tesis Aisladas",
                 1 if i % 4 == 0 else 0, (hoy - dt.timedelta(days=i * 3)).isoformat() + (" 00:00:00" if i % 2 else ""), "x"))
dof.commit(); sjf.commit(); dof.close(); sjf.close()

ro = lambda p: (lambda c: (setattr(c, "row_factory", sqlite3.Row), c)[1])(sqlite3.connect(f"file:{p}?mode=ro", uri=True))
llamadas = []
resumir = lambda ag, t, f, fu, tx: (llamadas.append(t), (f"Resumen {t}", f"Aprendizaje {t[:20]}"))[1]
cfg = {"label": "Fiscal", "dof": KW, "sjf": KW}
kdir = W / "kb" / "kawiil-x"; docs = kdir / "docs"

def pasada(limite=20, c=cfg, **kw):
    d, s = ro(W / "dof.db"), ro(W / "sjf.db")
    try:
        return LI.indexar_agente("kawiil-x", c, kdir, docs, d, s, summarize=resumir, append_memory=lambda *a: None,
                                 limite=limite, log=logging.getLogger("t"), **kw)
    finally:
        d.close(); s.close()

# 1) Progreso profundo hasta cubrir TODO el universo (el indexador anterior se estancaba en ~40)
r = pasada(); assert r["nuevos"] == 20 and set(r["por_flujo"]) >= {"sjf_jur_rubro", "dof_titulo"}, r
while pasada(50)["nuevos"]:
    pass
ids = LI.ids_indexados(docs); q = LI.consulta_or(KW)
d, s = ro(W / "dof.db"), ro(W / "sjf.db")
uni = (d.execute("select count(*) from notas_fts join notas n on n.cod_nota=notas_fts.rowid where notas_fts match ? and n.incluido=1 and length(n.texto_plano)>200", (q,)).fetchone()[0]
       + s.execute("select count(*) from tesis_fts join tesis t on t.registro_digital=tesis_fts.rowid where tesis_fts match ? and length(t.texto)>200", (q,)).fetchone()[0])
d.close(); s.close()
assert len(ids) == uni, (len(ids), uni)
assert not any(i.startswith("dof-") and int(i[4:]) > 1800 for i in ids), "indexó notas sin clasificar"
assert len(llamadas) == len(set(llamadas)), "resumió dos veces el mismo documento"
assert all(v["fin"] for k, v in json.loads((kdir / "cursor.json").read_text()).items() if not k.startswith("_"))

# 2) Idempotencia y recuperación: sin cursor.json no se re-indexa nada (la verdad es docs/)
n0, l0 = len(ids), len(llamadas); (kdir / "cursor.json").unlink()
assert pasada()["nuevos"] == 0 and len(llamadas) == l0 and len(LI.ids_indexados(docs)) == n0

# 3) Publicación nueva y publicación TARDÍA (por encima del cursor) se captan
c = sqlite3.connect(W / "dof.db")
for i, (f, t) in enumerate([(hoy.isoformat(), "NUEVA ISR"), ((hoy - dt.timedelta(days=20)).isoformat(), "TARDIA SAT")]):
    c.execute("insert into notas(cod_nota,fecha,titulo,incluido,existe_html,texto_plano,tipo_documento) values(?,?,?,1,1,?, 'X')", (900000 + i, f, t, "w" * 260 + " ISR SAT"))
c.commit(); c.close()
assert pasada()["nuevos"] == 2 and {"dof-900000", "dof-900001"} <= LI.ids_indexados(docs)

# 4) Nombres con hora/espacios/nd se reconocen; sin tope de 500
(docs / "sjf-2026-08-28 00:00:00-999991.md").write_text("x"); (docs / "sjf-nd-999992.md").write_text("x")
assert {"sjf-999991", "sjf-999992"} <= LI.ids_indexados(docs) and len(LI.ids_indexados(docs)) > 500

# 5) Resumidor caído, BD ausente, palabras clave hostiles, agente sin palabras clave
shutil.rmtree(docs, ignore_errors=True); (kdir / "cursor.json").unlink(missing_ok=True)
d, s = ro(W / "dof.db"), ro(W / "sjf.db")
# 5a) DeepSeek caído: NO se guarda un "resumen" de relleno, el cursor retrocede y se reintenta
estado_api = {"ok": False}; mem = []
def resumidor(ag, t, f, fu, tx):
    if not estado_api["ok"]:
        raise RuntimeError("DeepSeek caído")
    return (f"Resumen {t}", f"Aprendizaje {t[:20]}")
def correr(limite=6):
    return LI.indexar_agente("kawiil-x", cfg, kdir, docs, d, s, summarize=resumidor, append_memory=lambda *a: mem.append(a),
                             limite=limite, log=logging.getLogger("t"))
r = correr(); assert r["nuevos"] == 0 and r["fallidos"] == 6 and r["api_caida"] and not list(docs.glob("*.md")), r
assert not mem, "un fallo no debe contaminar la memoria de Ollama"
estado_api["ok"] = True                                # DeepSeek vuelve: los mismos 6 documentos se reintentan
r = correr(); assert r["nuevos"] == 6 and r["fallidos"] == 0 and len(LI.ids_indexados(docs)) == 6, r
assert len(mem) == 6
# 5b) un documento que falla SIEMPRE: tras MAX_FALLOS se guarda el texto original marcado y el avance sigue
shutil.rmtree(docs, ignore_errors=True); (kdir / "cursor.json").unlink(missing_ok=True); mem.clear()
primero = {}
def resumidor_tozudo(ag, t, f, fu, tx):
    primero.setdefault("t", t)
    if t == primero["t"]:
        raise RuntimeError("este documento siempre falla")
    return (f"Resumen {t}", f"Aprendizaje {t[:20]}")
for _ in range(LI.MAX_FALLOS):
    r = LI.indexar_agente("kawiil-x", cfg, kdir, docs, d, s, summarize=resumidor_tozudo, append_memory=lambda *a: mem.append(a), limite=4, log=logging.getLogger("t"))
degradados = [p for p in docs.glob("*.md") if "Resumen automático no disponible" in p.read_text()]
assert len(degradados) == 1 and r["degradados"] == 1, (degradados, r)
assert len(mem) == len(list(docs.glob("*.md"))) - 1, "el documento degradado no debe aportar a la memoria"
assert "_fallos" in json.loads((kdir / "cursor.json").read_text())
shutil.rmtree(docs, ignore_errors=True); (kdir / "cursor.json").unlink(missing_ok=True)
shutil.rmtree(docs, ignore_errors=True); (kdir / "cursor.json").unlink(missing_ok=True)
r = LI.indexar_agente("kawiil-x", cfg, kdir, docs, None, s, summarize=resumir, append_memory=lambda *a: None, limite=10, log=logging.getLogger("t"))
assert r["nuevos"] == 10 and not any(k.startswith("dof") for k in r["por_flujo"])
hostil = {"label": "h", "dof": ['persona "moral"', "a' OR 1=1 --", "(((", "NOT", ""], "sjf": []}
r = pasada(5, hostil); assert not r["errores_flujo"], r
assert pasada(5, {"label": "x", "dof": [], "sjf": []})["nuevos"] == 0
d.close(); s.close()

# 6) Presupuesto diario
b = W / "b.json"; assert LI.presupuesto_restante(b, 100) == 100
LI.gastar(b, 60, 100); assert LI.presupuesto_restante(b, 100) == 40 and json.loads(b.read_text())["tope"] == 100
LI.gastar(b, 60); assert LI.presupuesto_restante(b, 100) == 0
b.write_text(json.dumps({"fecha": "2000-01-01", "docs": 9999})); assert LI.presupuesto_restante(b, 100) == 100

shutil.rmtree(W, ignore_errors=True)
print("OK: todas las pruebas del indexador pasaron")
