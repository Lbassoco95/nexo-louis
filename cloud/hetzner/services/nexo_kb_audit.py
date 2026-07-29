#!/usr/bin/env python3
"""
nexo_kb_audit.py — Nexo · Capa 1 · auditoría READ-ONLY de la KB-nube

Reporta cuántos chunks ya cargados en Supabase (por defecto los del backfill de .md,
source_ref 'memory/*') se marcarían SENSIBLES con el clasificador endurecido de
Bloque 3 (_es_conocimiento_sensible), desglosado por archivo y por término disparador.

NO borra ni modifica NADA — solo hace GET. Sirve para decidir si hay que purgar/
re-cargar tras endurecer la política (Kawiil Central / legal / datos personales).

Privacidad: por defecto NO imprime títulos ni contenido (podrían traer nombres de
cliente); solo conteos + frecuencia de términos. Usa --show-refs para listar los
source_ref marcados (opt-in; el slug del ref puede incluir nombres).

Uso (en el VPS, desde /opt/openclaw/scripts/):
  python3 nexo_kb_audit.py                 # resumen memory/* por archivo + términos
  python3 nexo_kb_audit.py --show-refs     # además lista los source_ref marcados
  python3 nexo_kb_audit.py --prefix kc:    # auditar otra fuente (default 'memory/')
  python3 nexo_kb_audit.py --org <uuid>    # override del org
"""

import os
import sys
import re
import argparse
import collections

# --- bootstrap de imports + entorno (igual patrón que nexo_backfill_kc) ---
for _cand in filter(None, [
    os.environ.get("NEXO_LIB_DIR"),
    os.path.dirname(os.path.abspath(__file__)),
    os.path.expanduser("~"),
]):
    if _cand not in sys.path:
        sys.path.insert(0, _cand)


def _load_env_files():
    for path in ("/opt/openclaw/openclaw.env",
                 "/opt/openclaw/.env",
                 os.path.expanduser("~/.openclaw/.env"),
                 os.path.expanduser("~/.openclaw/credentials/kawiil-agents.env")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):]
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass


_load_env_files()

try:
    import nexo_retrieve
    from louis_core import _es_conocimiento_sensible, _KB_SENSIBLE_RE
except Exception as e:  # pragma: no cover
    print(f"ERROR importando dependencias: {e}", file=sys.stderr)
    sys.exit(1)

ORG = (os.environ.get("NEXO_DEFAULT_ORG_ID")
       or os.environ.get("KAWIIL_KAWIIL_ORG_ID")
       or os.environ.get("KAWIIL_ORG_ID"))


def _fetch_all(org, prefix):
    """GET paginado de kb_chunks con source_ref que empieza por `prefix` (read-only)."""
    rows, page, off = [], 1000, 0
    while True:
        path = (f"/rest/v1/kb_chunks?org_id=eq.{org}&source_ref=like.{prefix}*"
                f"&select=source_ref,title,content&limit={page}&offset={off}")
        batch = nexo_retrieve._sb_request("GET", path) or []
        rows.extend(batch)
        if len(batch) < page:
            break
        off += page
    return rows


def _file_of(source_ref):
    """'memory/PROJECTS.md#003-...' -> 'PROJECTS.md'; 'kc:project:<id>' -> 'kc:project'."""
    sr = source_ref or "?"
    m = re.match(r"[^/]+/([^#]+)", sr)     # forma memory/<archivo>#...
    if m:
        return m.group(1)
    return sr.rsplit(":", 1)[0] if ":" in sr else sr


def main():
    ap = argparse.ArgumentParser(description="Auditoría read-only de sensibilidad en la KB-nube.")
    ap.add_argument("--prefix", default="memory/", help="Prefijo de source_ref a auditar (default 'memory/').")
    ap.add_argument("--org", default=None)
    ap.add_argument("--show-refs", action="store_true",
                    help="Lista los source_ref marcados (opt-in; puede incluir nombres en el slug).")
    args = ap.parse_args()

    org = args.org or ORG
    if not org:
        print("ERROR: falta NEXO_DEFAULT_ORG_ID (o pásalo con --org).", file=sys.stderr)
        sys.exit(1)

    rows = _fetch_all(org, args.prefix)
    print("== Auditoría READ-ONLY · KB-nube (no borra ni modifica) ==")
    print(f"org={org}  prefijo='{args.prefix}'  chunks encontrados: {len(rows)}\n")
    if not rows:
        print("(sin chunks con ese prefijo — nada que auditar)")
        return

    per_file = collections.defaultdict(lambda: [0, 0])   # archivo -> [total, sensibles]
    term_freq = collections.Counter()
    flagged_refs = []

    for r in rows:
        f = _file_of(r.get("source_ref"))
        per_file[f][0] += 1
        blob = f"{r.get('title') or ''} {r.get('content') or ''}"
        if _es_conocimiento_sensible(blob):
            per_file[f][1] += 1
            flagged_refs.append(r.get("source_ref"))
            m = _KB_SENSIBLE_RE.search(blob)
            if m:
                term_freq[m.group(1).lower()] += 1

    print(f"{'ARCHIVO / FUENTE':26} {'TOTAL':>6} {'SENS':>6} {'%':>5}")
    print("-" * 48)
    tot = sens = 0
    for f in sorted(per_file):
        t, s = per_file[f]
        tot += t
        sens += s
        pct = (100 * s // t) if t else 0
        print(f"{f:26} {t:>6} {s:>6} {pct:>4}%")
    print("-" * 48)
    print(f"{'TOTAL':26} {tot:>6} {sens:>6} {(100 * sens // tot) if tot else 0:>4}%\n")

    if term_freq:
        print("Términos que dispararon el filtro (top 15):")
        for term, c in term_freq.most_common(15):
            print(f"  {c:>4}  {term}")

    if args.show_refs and flagged_refs:
        print("\nsource_ref marcados como sensibles:")
        for ref in flagged_refs:
            print(f"  {ref}")

    print(f"\nResumen: {sens}/{tot} chunks '{args.prefix}*' se marcarían sensibles hoy. "
          "Read-only: no se cambió nada. Decide si purgar/re-cargar.")


if __name__ == "__main__":
    main()
