#!/usr/bin/env python3
"""
nexo_kb_local.py — Nexo · Capa 1 · Bloque 5 (KB-Sensible LOCAL)

Base de conocimiento para lo SENSIBLE (PLD/Ikán/legal/salud/datos personales).
Vive SOLO en el disco del VPS — NUNCA sale a la nube (invariante del proyecto).

Diseño (fiel al estilo del código, cero dependencias nuevas):
  - Almacén: SQLite (`sqlite3`, stdlib).
  - Embeddings: los mismos que la KB-nube — `nexo_embeddings.embed` (Ollama, local).
    El vector se guarda como JSON en una columna TEXT.
  - Recuperación: fuerza bruta con `nexo_embeddings.cosine` (para unos miles de
    chunks en CPU es instantáneo; sin sqlite-vec ni extensiones).

API espejo de nexo_retrieve para que la integración sea simétrica:
  insert_chunks(rows) · retrieve(query, k) · delete_by_prefix(prefix) · count()

CLI:
  python3 nexo_kb_local.py init           # crea la BD
  python3 nexo_kb_local.py count          # cuántos chunks hay
  python3 nexo_kb_local.py query "texto"  # prueba de recuperación
"""

import os
import sys
import json
import uuid
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nexo_embeddings

DB_PATH = os.environ.get("NEXO_KB_LOCAL_DB", "/opt/openclaw/kb_sensible.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_sensible (
    id          TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_ref  TEXT,
    title       TEXT,
    content     TEXT NOT NULL,
    embedding   TEXT NOT NULL,   -- JSON: lista de floats (768)
    metadata    TEXT,            -- JSON
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_sensible_ref ON kb_sensible(source_ref);
"""


def _db(path=None):
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    conn.executescript(_SCHEMA)
    return conn


def insert_chunks(rows, path=None):
    """rows: dicts con content (obligatorio), source_type, source_ref, title, metadata.
    Embebe content con Ollama y guarda el vector como JSON. Devuelve n insertados."""
    conn = _db(path)
    n = 0
    try:
        for r in rows:
            content = (r.get("content") or "").strip()
            if not content:
                continue
            vec = nexo_embeddings.embed(content)
            conn.execute(
                "INSERT OR REPLACE INTO kb_sensible "
                "(id, source_type, source_ref, title, content, embedding, metadata, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    r.get("id") or str(uuid.uuid4()),
                    (r.get("source_type") or "doc"),
                    r.get("source_ref"),
                    r.get("title"),
                    content,
                    json.dumps(vec),
                    json.dumps(r.get("metadata") or {}, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def retrieve(query_text, k=8, path=None):
    """Embebe la consulta y devuelve top-k por coseno (fuerza bruta en memoria).
    Cada resultado: {id, source_type, source_ref, title, content, metadata, similarity, match}."""
    if not query_text or not query_text.strip():
        return []
    qvec = nexo_embeddings.embed(query_text)
    conn = _db(path)
    try:
        cur = conn.execute(
            "SELECT id, source_type, source_ref, title, content, embedding, metadata FROM kb_sensible")
        scored = []
        for row in cur:
            emb = json.loads(row[5])
            sim = nexo_embeddings.cosine(qvec, emb)
            scored.append((sim, row))
    finally:
        conn.close()
    scored.sort(key=lambda x: -x[0])
    out = []
    for sim, row in scored[:k]:
        out.append({
            "id": row[0], "source_type": row[1], "source_ref": row[2],
            "title": row[3], "content": row[4],
            "metadata": json.loads(row[6] or "{}"),
            "similarity": sim, "match": "local",
        })
    return out


def delete_by_prefix(prefix, path=None):
    """Borra chunks cuyo source_ref empiece con prefix (para recargas idempotentes)."""
    conn = _db(path)
    try:
        cur = conn.execute("DELETE FROM kb_sensible WHERE source_ref LIKE ?", (prefix + "%",))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def count(path=None):
    conn = _db(path)
    try:
        return conn.execute("SELECT count(*) FROM kb_sensible").fetchone()[0]
    finally:
        conn.close()


# === CLI ===
def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init":
        _db().close(); print(f"BD lista en {DB_PATH}")
    elif cmd == "count":
        print(f"{count()} chunks en {DB_PATH}")
    elif cmd == "query":
        if len(sys.argv) < 3:
            print('Uso: nexo_kb_local.py query "texto"', file=sys.stderr); sys.exit(1)
        res = retrieve(" ".join(sys.argv[2:]), 8)
        if not res:
            print("(sin resultados)"); return
        for r in res:
            print(f"[{r['similarity']:.3f}] {r.get('source_ref')} — {(r['content'][:70]).strip()}")
    else:
        print(f"Comando desconocido: {cmd}", file=sys.stderr); sys.exit(1)


if __name__ == "__main__":
    main()
