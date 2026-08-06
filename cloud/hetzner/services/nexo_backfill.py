#!/usr/bin/env python3
"""nexo_backfill.py — Nexo · Capa 1 · Bloque 4 (backfill con guard por chunk)."""
import os
import re
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nexo_retrieve
import nexo_embeddings

BUSINESS_FILES = [
    ("PROJECTS.md",     "project"),
    ("PEOPLE.md",       "person"),
    ("CLIENTES.md",     "person"),
    ("IMPORTANT.md",    "doc"),
    ("LEARNINGS.md",    "doc"),
    ("JOURNAL.md",      "journal"),
    ("SEGUIMIENTOS.md", "project"),
]
SKIP_SENSIBLE = ["OLLAMA_LEGAL_MEMORY.md", "FAMILIA.md", "ALIMENTACION.md",
                 "COACH.md", "KB_SENSIBLE.md", "SALUD.md", "FINANZAS.md", "PERSONAL.md"]
DEFAULT_DIR = "/opt/openclaw/spaces/general"
OPENCLAW_ENV = "/opt/openclaw/openclaw.env"
MAX_CHARS = 1800
OVERLAP = 200


def get_default_org():
    try:
        for line in open(OPENCLAW_ENV):
            if line.startswith("NEXO_DEFAULT_ORG_ID="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return os.environ.get("NEXO_DEFAULT_ORG_ID", "")


def get_sensitivity_checker():
    for cand in filter(None, [os.environ.get("NEXO_LIB_DIR"),
                              os.path.dirname(os.path.abspath(__file__))]):
        if cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import donna_core as donna_core
        fn = donna_core._es_conocimiento_sensible
        assert fn("expediente KYC del cliente con CURP") is True
        return fn
    except Exception as e:
        print(f"ABORTADO: no pude cargar el guard de sensibilidad de donna_core - {e}. "
              "NO backfilleo sin filtro (riesgo de subir PLD).")
        sys.exit(1)


def _slug(s, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return s[:n] or "seccion"


def chunk_file(text, filename):
    lines = text.split("\n")
    sections, cur_title, cur = [], filename, []
    for ln in lines:
        if re.match(r"^#{1,3}\s+", ln):
            if any(l.strip() for l in cur):
                sections.append((cur_title, "\n".join(cur).strip()))
            cur_title = re.sub(r"^#{1,3}\s+", "", ln).strip()
            cur = []
        else:
            cur.append(ln)
    if any(l.strip() for l in cur):
        sections.append((cur_title, "\n".join(cur).strip()))
    chunks = []
    for title, body in sections:
        if not body.strip():
            continue
        if len(body) <= MAX_CHARS:
            chunks.append((title, body))
        else:
            start = 0
            while start < len(body):
                chunks.append((title, body[start:start + MAX_CHARS]))
                start += MAX_CHARS - OVERLAP
    return chunks


def build_rows(directory, org_id, space_id):
    es_sensible = get_sensitivity_checker()
    rows, resumen = [], []
    for fname, stype in BUSINESS_FILES:
        path = os.path.join(directory, fname)
        if not os.path.exists(path):
            resumen.append((fname, stype, 0, 0, "(no existe)"))
            continue
        text = open(path, encoding="utf-8").read()
        chunks = chunk_file(text, fname)
        kept = skipped = 0
        preview = ""
        for i, (title, body) in enumerate(chunks):
            content = f"{title}\n\n{body}"
            if es_sensible(content):
                skipped += 1
                continue
            rows.append({
                "org_id": org_id, "space_id": space_id, "source_type": stype,
                "source_ref": f"memory/{fname}#{i:03d}-{_slug(title)}",
                "title": title, "content": content,
                "metadata": {"backfill": True, "file": fname, "section": title},
            })
            if not preview:
                preview = body[:58].replace("\n", " ") + "..."
            kept += 1
        resumen.append((fname, stype, kept, skipped, preview))
    return rows, resumen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--org", default=None)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--space", default="general")
    args = ap.parse_args()

    org_id = args.org or get_default_org()
    if not org_id:
        print("ABORTADO: no encontre NEXO_DEFAULT_ORG_ID (pasalo con --org).")
        sys.exit(1)
    if org_id == "00000000-0000-0000-0000-000000000001":
        print("ABORTADO: ese es el org de PRUEBA. Usa el org real de Kawiil.")
        sys.exit(1)

    print("== Nexo . Bloque 4 . backfill (con guard por chunk) ==")
    print(f"org_id: {org_id}  |  space: {args.space}  |  dir: {args.dir}")
    print(f"embeddings: {nexo_embeddings.EMBED_MODEL} ({nexo_embeddings.EMBED_DIM}d)\n")

    rows, resumen = build_rows(args.dir, org_id, args.space)
    print(f"{'ARCHIVO':22} {'TIPO':9} {'LIMPIOS':>7} {'SENSIBLES':>9}  PREVIEW")
    print("-" * 95)
    tot_kept = tot_skip = 0
    for fname, stype, kept, skipped, preview in resumen:
        tot_kept += kept; tot_skip += skipped
        print(f"{fname:22} {stype:9} {kept:>7} {skipped:>9}  {preview}")
    print("-" * 95)
    print(f"TOTAL limpios: {tot_kept}   |   saltados por sensibles (a la nube NO): {tot_skip}\n")

    if args.dry_run:
        print("DRY-RUN - no se escribio nada.")
        return
    if args.reset:
        print("Reset: borrando backfill previo (memory/*)...")
        nexo_retrieve._sb_request("DELETE", f"/rest/v1/kb_chunks?org_id=eq.{org_id}&source_ref=like.memory/*")
    by_file = {}
    for r in rows:
        by_file.setdefault(r["metadata"]["file"], []).append(r)
    inserted = 0
    for fname, frows in by_file.items():
        print(f"  -> {fname}: {len(frows)} chunks limpios...", flush=True)
        res = nexo_retrieve.insert_chunks(frows)
        inserted += len(res or [])
    print(f"\nOK: {inserted} chunks LIMPIOS insertados. ({tot_skip} sensibles fuera de la nube.)")


if __name__ == "__main__":
    main()
