#!/usr/bin/env python3
"""
nexo_retrieve.py — Nexo · Capa 1 · Bloque 2a
Recuperación end-to-end (standalone, sin OpenClaw todavía):
  texto de consulta --embed(Bloque 1)--> vector(768)
                    --RPC match_kb_chunks (Supabase REST)--> top-k chunks

Cierra el circuito del Bloque 0 (schema/kb_chunks) con el Bloque 1 (embeddings).
stdlib puro (urllib), igual que kawiil_agents.py / nexo_embeddings.py.

Uso CLI:
  python3 nexo_retrieve.py query "¿qué producto usamos para PLD?" [--org UUID] [--space general] [-k 8]
  python3 nexo_retrieve.py seed                 # inserta chunks demo (org de prueba) con embeddings reales
  python3 nexo_retrieve.py check                # CHECKPOINT 2a: seed -> query NL -> asserts -> cleanup

Config (env u ~/.openclaw/credentials/kawiil-agents.env):
  SUPABASE_URL           https://qppfampapbxdgednkofc.supabase.co
  SUPABASE_SERVICE_KEY   (service_role; bypassa RLS — solo servidor)
  + OLLAMA_URL / NEXO_EMBED_MODEL (los usa nexo_embeddings)
"""

import os
import sys
import json
import re
import urllib.parse
import urllib.request
import urllib.error

# Reutilizamos el Bloque 1 (mismo directorio)
from nexo_embeddings import embed, to_pgvector, cosine, _cfg, EMBED_DIM

SUPABASE_URL = _cfg("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = _cfg("SUPABASE_SERVICE_KEY", "")

TEST_ORG = "00000000-0000-0000-0000-000000000001"


# === Capa HTTP a Supabase REST (PostgREST) ===

def _sb_request(method: str, path: str, body=None, extra_headers=None, timeout: int = 60):
    if not SUPABASE_URL or not SERVICE_KEY:
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY (env o "
            "~/.openclaw/credentials/kawiil-agents.env)."
        )
    url = f"{SUPABASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("Authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("Content-Type", "application/json")
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase HTTP {e.code} {method} {path}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"No se pudo conectar a Supabase ({SUPABASE_URL}): {e.reason}")


# === Recuperación ===

def _vector_search(query_text: str, org_id: str, space_id: str = "general", k: int = 8) -> list:
    """Embebe la consulta y devuelve los top-k chunks vía match_kb_chunks."""
    vec = embed(query_text)
    # PostgREST + pgvector: la forma array (list[float]) funciona como en los
    # ejemplos oficiales de Supabase; dejamos fallback a string por robustez.
    for payload in (vec, to_pgvector(vec)):
        body = {
            "query_embedding": payload,
            "p_org_id": org_id,
            "p_space_id": space_id,
            "match_count": k,
        }
        try:
            res = _sb_request("POST", "/rest/v1/rpc/match_kb_chunks", body)
            return res or []
        except RuntimeError as e:
            # Si el casteo del vector falló (400), probamos la otra forma.
            if "HTTP 400" in str(e) and payload is vec:
                continue
            raise
    return []


# === Recuperación híbrida (vector + keyword) ===
# El vector recupera por significado; el keyword rescata match exacto de nombres
# propios (Kailash, Ixim, personas/clientes) que el embedding a veces no rankea alto.

_STOP = {"que","qué","de","en","el","la","los","las","un","una","es","son","del","al",
         "por","para","con","sobre","quien","quién","cual","cuál","como","cómo","y","o",
         "mi","tu","su","lo","le","se","va","hay","tiene","dime","busca","conocimiento",
         "base","proyecto","cliente","persona"}

def _extract_terms(query, max_terms=5):
    toks = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ][\wÁÉÍÓÚÑáéíóúñ]{2,}", query or "")
    out, seen = [], set()
    for t in toks:
        if t.lower() in _STOP:
            continue
        if (t[0].isupper() or len(t) >= 5) and t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out[:max_terms]

def _keyword_search(terms, org_id, space_id, limit=6):
    if not terms:
        return []
    ors = ",".join(f"content.ilike.*{urllib.parse.quote(t)}*" for t in terms)
    path = (f"/rest/v1/kb_chunks?org_id=eq.{org_id}&space_id=eq.{space_id}"
            f"&or=({ors})&limit={limit}"
            f"&select=id,source_type,source_ref,title,content,metadata")
    rows = _sb_request("GET", path) or []
    for r in rows:
        r["match"] = "keyword"
    return rows

def retrieve(query_text, org_id, space_id="general", k=8):
    vec = _vector_search(query_text, org_id, space_id, k)
    for r in vec:
        r.setdefault("match", "vector")
    kw = _keyword_search(_extract_terms(query_text), org_id, space_id, limit=6)
    seen, merged = set(), []
    for r in kw + vec:                 # keyword primero: match exacto de nombre gana
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid); merged.append(r)
    return merged[: max(k, len(kw))]


# === Inserción (para seed/checkpoint) ===

def insert_chunks(rows: list) -> list:
    """Inserta filas en kb_chunks. Cada row: dict con content/source_ref/... y 'text' a embeber."""
    payload = []
    for r in rows:
        payload.append({
            "org_id": r["org_id"],
            "space_id": r.get("space_id", "general"),
            "source_type": r.get("source_type", "doc"),
            "source_ref": r.get("source_ref"),
            "title": r.get("title"),
            "content": r["content"],
            "embedding": embed(r["content"]),         # embedding real (Bloque 1)
            "metadata": r.get("metadata", {}),
        })
    return _sb_request(
        "POST", "/rest/v1/kb_chunks", payload,
        extra_headers={"Prefer": "return=representation"},
    ) or []


def delete_org(org_id: str) -> None:
    _sb_request("DELETE", f"/rest/v1/kb_chunks?org_id=eq.{org_id}")


# Chunks demo para seed/checkpoint (contenido real de los proyectos)
DEMO_CHUNKS = [
    {"source_ref": "demo/kailash", "title": "Kailash",
     "content": "Kailash es la plataforma de pagos B2B de Yoltik para dispersión y cobros entre empresas."},
    {"source_ref": "demo/ikan", "title": "Ikán",
     "content": "Ikán es el producto de cumplimiento que verifica prevención de lavado de dinero (PLD) bajo la LFPIORPI."},
    {"source_ref": "demo/mati", "title": "Mati",
     "content": "Mati integra FacturAPI para emitir facturas (CFDI) en un esquema multi-tenant."},
    {"source_ref": "demo/ixim", "title": "Ixim Pay",
     "content": "Ixim Pay es el backend white-label de pagos para Finexia en Bolivia."},
]


# === CLI ===

def cmd_query(args):
    # parseo simple de flags
    org, space, k, terms = TEST_ORG, "general", 8, []
    i = 0
    while i < len(args):
        if args[i] == "--org" and i + 1 < len(args):
            org = args[i + 1]; i += 2
        elif args[i] == "--space" and i + 1 < len(args):
            space = args[i + 1]; i += 2
        elif args[i] in ("-k", "--k") and i + 1 < len(args):
            k = int(args[i + 1]); i += 2
        else:
            terms.append(args[i]); i += 1
    if not terms:
        print('Uso: nexo_retrieve.py query "texto" [--org UUID] [--space general] [-k 8]', file=sys.stderr)
        sys.exit(1)
    rows = retrieve(" ".join(terms), org, space, k)
    if not rows:
        print("(sin resultados)")
        return
    print(f"{'SIMIL':7} {'SOURCE_REF':22} {'TÍTULO':16} CONTENIDO")
    print("-" * 100)
    for r in rows:
        sim = r.get("similarity")
        sim_s = f"{sim:.4f}" if isinstance(sim, (int, float)) else str(sim)
        print(f"{sim_s:7} {(r.get('source_ref') or '')[:22]:22} {(r.get('title') or '')[:16]:16} {(r.get('content') or '')[:52]}")


def cmd_seed(args):
    delete_org(TEST_ORG)
    rows = [dict(c, org_id=TEST_ORG) for c in DEMO_CHUNKS]
    inserted = insert_chunks(rows)
    print(f"Insertados {len(inserted)} chunks demo en org de prueba {TEST_ORG}.")


def cmd_check(args):
    print("== Nexo · Bloque 2a · checkpoint recuperación end-to-end ==")
    print(f"SUPABASE_URL={SUPABASE_URL}\n")

    print("[1/4] Limpiando org de prueba…")
    delete_org(TEST_ORG)

    print("[2/4] Sembrando chunks demo con embeddings reales…")
    rows = [dict(c, org_id=TEST_ORG) for c in DEMO_CHUNKS]
    inserted = insert_chunks(rows)
    print(f"       -> {len(inserted)} chunks insertados")

    res = []
    try:
        q = "¿Qué producto usamos para prevención de lavado de dinero?"
        print(f"[3/4] Consulta NL: {q!r}")
        res = retrieve(q, TEST_ORG, "general", 4)
        for r in res:
            # Las filas solo-keyword no traen 'similarity' (no hay score de coseno).
            sim = r.get("similarity")
            sim_s = f"{sim:.4f}" if isinstance(sim, (int, float)) else "  kw  "
            print(f"       {sim_s}  {r.get('source_ref')}  — {r.get('title')}  [{r.get('match', '?')}]")
    finally:
        print("[4/4] Limpiando…")
        delete_org(TEST_ORG)

    top = res[0] if res else {}
    ok_hit = (top.get("source_ref") == "demo/ikan")   # el chunk PLD debe rankear #1 (vector o keyword)
    # El orden por similitud aplica al carril VECTORIAL; keyword no trae score.
    sims = [r["similarity"] for r in res if isinstance(r.get("similarity"), (int, float))]
    ok_order = sims == sorted(sims, reverse=True)

    print()
    print(f"[{'OK' if ok_hit else 'XX'}] top-1 = {top.get('source_ref')} (esperado demo/ikan)")
    print(f"[{'OK' if ok_order else 'XX'}] carril vectorial ordenado descendente ({len(sims)} con score)")
    if ok_hit and ok_order:
        print("\nBLOQUE 2a VERDE [OK] — recuperación híbrida end-to-end funciona.")
        sys.exit(0)
    print("\nBLOQUE 2a EN ROJO [XX] — revisa embeddings, RPC o datos sembrados.", file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    handlers = {"query": cmd_query, "seed": cmd_seed, "check": cmd_check}
    cmd = sys.argv[1]
    if cmd not in handlers:
        print(f"Comando desconocido: {cmd}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    try:
        handlers[cmd](sys.argv[2:])
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
