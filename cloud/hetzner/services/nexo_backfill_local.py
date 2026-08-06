#!/usr/bin/env python3
"""
nexo_backfill_local.py — Nexo · Capa 1 · Bloque 5 (backfill a la KB-Sensible LOCAL)

Puebla la KB local (nexo_kb_local, SQLite en el VPS) con lo que NO debe vivir en la nube:
  (A) los chunks SENSIBLES de los .md de NEGOCIO (los que _es_conocimiento_sensible marca)
  (B) TODO el contenido de los .md SENSIBLES (SALUD, FAMILIA, FINANZAS, personal, legal…)

Reusa el chunking real de nexo_backfill (chunk_file / _slug / listas de archivos) y el
clasificador canónico de donna_core (_es_conocimiento_sensible). source_ref con prefijo
'memory-sensible/'. Cero dependencias nuevas. Nada sale del VPS.

Uso (en /opt/openclaw/scripts/):
  python3 nexo_backfill_local.py --dry-run     # cuenta + previews, no escribe
  python3 nexo_backfill_local.py --reset       # borra 'memory-sensible/*' del KB local y recarga
  python3 nexo_backfill_local.py               # inserta (sin borrar)
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nexo_kb_local
from nexo_backfill import chunk_file, _slug, BUSINESS_FILES, SKIP_SENSIBLE, DEFAULT_DIR

try:
    from donna_core import _es_conocimiento_sensible
except Exception as e:  # pragma: no cover
    print(f"ERROR: no pude importar _es_conocimiento_sensible de donna_core: {e}", file=sys.stderr)
    sys.exit(1)

PREFIX = "memory-sensible/"
# source_type por archivo sensible (default 'doc')
SENSIBLE_TYPES = {"FAMILIA.md": "person"}


def _rows_from_business(directory):
    """(A) Solo los chunks SENSIBLES de los .md de negocio → local."""
    rows = []
    for fname, stype in BUSINESS_FILES:
        path = os.path.join(directory, fname)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        for i, (title, body) in enumerate(chunk_file(text, fname)):
            content = f"{title}\n\n{body}"
            if not _es_conocimiento_sensible(content):
                continue
            rows.append({
                "source_type": stype,
                "source_ref": f"{PREFIX}{fname}#{i:03d}-{_slug(title)}",
                "title": title,
                "content": content,
                "metadata": {"backfill": True, "file": fname, "origen": "negocio-sensible"},
            })
    return rows


def _rows_from_sensible(directory):
    """(B) TODO el contenido de los .md sensibles → local."""
    rows = []
    for fname in SKIP_SENSIBLE:
        path = os.path.join(directory, fname)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        stype = SENSIBLE_TYPES.get(fname, "doc")
        for i, (title, body) in enumerate(chunk_file(text, fname)):
            content = f"{title}\n\n{body}"
            if not content.strip():
                continue
            rows.append({
                "source_type": stype,
                "source_ref": f"{PREFIX}{fname}#{i:03d}-{_slug(title)}",
                "title": title,
                "content": content,
                "metadata": {"backfill": True, "file": fname, "origen": "sensible"},
            })
    return rows


def main():
    ap = argparse.ArgumentParser(description="Backfill a la KB-Sensible local.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="borra 'memory-sensible/*' del KB local antes de recargar")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    args = ap.parse_args()

    biz = _rows_from_business(args.dir)
    sen = _rows_from_sensible(args.dir)
    rows = biz + sen

    print("== Nexo · Bloque 5 · backfill a KB-Sensible LOCAL ==")
    print(f"dir={args.dir}   db={nexo_kb_local.DB_PATH}")
    print(f"(A) sensibles de .md de negocio: {len(biz)}")
    print(f"(B) todo de .md sensibles:       {len(sen)}")
    print(f"TOTAL a insertar en local: {len(rows)}\n")

    if not rows:
        print("Nada que insertar.")
        return

    if args.dry_run:
        print("--- previews (dry-run, sin escribir) ---")
        for r in rows[:4]:
            print(f"[{r['source_ref']}] {r['title']}")
            print(f"   {r['content'][:140].strip()}{'…' if len(r['content']) > 140 else ''}")
        print("\nDRY-RUN — no se escribió nada.")
        return

    if args.reset:
        borr = nexo_kb_local.delete_by_prefix(PREFIX)
        print(f"Reset: borrados {borr} chunks '{PREFIX}*' del KB local.")

    # Insertar por lotes (embed en CPU ~1-2s por chunk)
    BATCH = 25
    total = len(rows)
    ins = 0
    for i in range(0, total, BATCH):
        ins += nexo_kb_local.insert_chunks(rows[i:i + BATCH])
        print(f"  … {min(i + BATCH, total)}/{total} (insertados {ins})", flush=True)

    print(f"\n✅ OK: {ins} chunks insertados en la KB local ({nexo_kb_local.count()} total en la BD).")


if __name__ == "__main__":
    main()
