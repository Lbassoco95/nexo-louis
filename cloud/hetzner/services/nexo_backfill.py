#!/usr/bin/env python3
"""
nexo_backfill.py — Nexo · Capa 1 · Bloque 4 (backfill del histórico)

Carga el conocimiento ya existente en los archivos de memoria (.md) a la KB-Negocio
(Supabase pgvector), chunkeado + embebido, para que Nexo arranque "sabiendo".

Respeta la invariante de sensibilidad: SOLO backfillea archivos de negocio; NUNCA
toca los sensibles (legal, salud, familia, personal) — esos esperan la KB-Sensible
local del Bloque 5.

Reusa la API real: nexo_embeddings.embed + nexo_retrieve.insert_chunks (no inventa nada).

Uso (en el VPS, desde /opt/openclaw/scripts/):
  python3 nexo_backfill.py --dry-run          # muestra cuántos chunks saldrían, sin escribir
  python3 nexo_backfill.py --reset            # borra backfill previo (source_ref 'memory/*') y recarga
  python3 nexo_backfill.py                     # inserta (sin borrar; puede duplicar si ya corrió)
  python3 nexo_backfill.py --org <uuid>        # override del org (default: NEXO_DEFAULT_ORG_ID de openclaw.env)

Idempotencia: usa --reset para recargar limpio (borra solo los chunks 'memory/*' de este org,
no toca capturas de conversación ni datos de prueba).
"""

import os
import re
import sys
import argparse

# nexo_retrieve / nexo_embeddings viven junto a este archivo en /opt/openclaw/scripts/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nexo_retrieve
import nexo_embeddings

# --- Archivos de NEGOCIO → KB nube. (source_type ∈ journal|project|person|doc|agent_learning) ---
BUSINESS_FILES = [
    ("PROJECTS.md",     "project"),
    ("PEOPLE.md",       "person"),
    ("CLIENTES.md",     "person"),
    ("IMPORTANT.md",    "doc"),
    ("LEARNINGS.md",    "doc"),
    ("JOURNAL.md",      "journal"),
    ("SEGUIMIENTOS.md", "project"),
]

# --- Sensibles / config: NUNCA a la nube (referencia; el script simplemente no los toca) ---
SKIP_SENSIBLE = ["OLLAMA_LEGAL_MEMORY.md", "FAMILIA.md", "ALIMENTACION.md",
                 "COACH.md", "KB_SENSIBLE.md", "SALUD.md", "FINANZAS.md", "PERSONAL.md"]

DEFAULT_DIR = "/opt/openclaw/spaces/general"
OPENCLAW_ENV = "/opt/openclaw/openclaw.env"

MAX_CHARS = 1800      # ~500 tokens aprox
OVERLAP = 200


def get_default_org():
    try:
        for line in open(OPENCLAW_ENV):
            if line.startswith("NEXO_DEFAULT_ORG_ID="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return os.environ.get("NEXO_DEFAULT_ORG_ID", "")


def _slug(s, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return s[:n] or "seccion"


def chunk_file(text, filename):
    """Chunkea por secciones markdown (#/##/###); ventana las secciones grandes."""
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
    """Devuelve (rows, resumen_por_archivo). rows listos para insert_chunks."""
    rows, resumen = [], []
    for fname, stype in BUSINESS_FILES:
        path = os.path.join(directory, fname)
        if not os.path.exists(path):
            resumen.append((fname, stype, 0, "(no existe)"))
            continue
        text = open(path, encoding="utf-8").read()
        chunks = chunk_file(text, fname)
        for i, (title, body) in enumerate(chunks):
            rows.append({
                "org_id": org_id,
                "space_id": space_id,
                "source_type": stype,
                "source_ref": f"memory/{fname}#{i:03d}-{_slug(title)}",
                "title": title,
                "content": f"{title}\n\n{body}",
                "metadata": {"backfill": True, "file": fname, "section": title},
            })
        preview = (chunks[0][1][:60] + "…") if chunks else ""
        resumen.append((fname, stype, len(chunks), preview))
    return rows, resumen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="borra backfill previo (source_ref memory/*) de este org")
    ap.add_argument("--org", default=None)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--space", default="general")
    args = ap.parse_args()

    org_id = args.org or get_default_org()
    if not org_id:
        print("ABORTADO: no encontré NEXO_DEFAULT_ORG_ID (pásalo con --org).")
        sys.exit(1)
    if org_id == "00000000-0000-0000-0000-000000000001":
        print("ABORTADO: ese es el org de PRUEBA (lo borra el smoke test). Usa el org real de Kawiil.")
        sys.exit(1)

    print(f"== Nexo · Bloque 4 · backfill ==")
    print(f"org_id: {org_id}  |  space: {args.space}  |  dir: {args.dir}")
    print(f"embeddings: {nexo_embeddings.EMBED_MODEL} ({nexo_embeddings.EMBED_DIM}d)\n")

    rows, resumen = build_rows(args.dir, org_id, args.space)
    print(f"{'ARCHIVO':22} {'TIPO':9} {'CHUNKS':>6}  PREVIEW")
    print("-" * 90)
    for fname, stype, n, preview in resumen:
        print(f"{fname:22} {stype:9} {n:>6}  {preview}")
    print("-" * 90)
    print(f"TOTAL chunks a insertar: {len(rows)}")
    print(f"(Ignorados por sensibles/config: {', '.join(SKIP_SENSIBLE)})\n")

    if args.dry_run:
        print("DRY-RUN — no se escribió nada. Corre sin --dry-run para insertar.")
        return

    if args.reset:
        print("Reset: borrando backfill previo (source_ref memory/*) de este org…")
        nexo_retrieve._sb_request(
            "DELETE",
            f"/rest/v1/kb_chunks?org_id=eq.{org_id}&source_ref=like.memory/*",
        )

    # Insertar por archivo (para ver progreso; el embedding en CPU tarda ~1-2s por chunk)
    inserted_total = 0
    by_file = {}
    for r in rows:
        by_file.setdefault(r["metadata"]["file"], []).append(r)
    for fname, frows in by_file.items():
        print(f"  → {fname}: embebiendo e insertando {len(frows)} chunks…", flush=True)
        res = nexo_retrieve.insert_chunks(frows)
        inserted_total += len(res or [])
    print(f"\nOK: {inserted_total} chunks insertados en el org {org_id}.")
    print("Verifica con: buscar_conocimiento por Telegram, o el checkpoint de abajo.")


if __name__ == "__main__":
    main()
