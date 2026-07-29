#!/usr/bin/env python3
"""
nexo_backfill_kc.py — Nexo · Capa 1 · Bloque 4 (2ª pasada)
Backfill de Kawiil Central (proyectos + tareas) a la KB-Negocio (kb_chunks).

Contenido de alto valor que NO cabe en el prompt: ~224 proyectos y ~834 tareas.
Con la recuperación híbrida (vector + keyword), buscar proyectos/tareas por nombre
funciona muy bien.

Reglas (para NO inventar):
  - Conexión: reusa louis_core._kawiil_central_pg() (psycopg2 contra
    KAWIIL_CENTRAL_DATABASE_URL). NO hardcodea credenciales.
  - Schema: descubre tabla real con _kawiil_central_find_table y columnas vía
    information_schema — NO asume nombres de columna (igual que _kawiil_central_*).
  - 1 chunk por proyecto y por tarea:
      proyecto → source_type='project', source_ref='kc:project:<id>'
      tarea    → source_type='project', source_ref='kc:task:<id>'
    metadata = {"source":"kawiil-central","kind":"project|task","id":<id>}
  - Embed+insert vía nexo_retrieve.insert_chunks (embebe row['content']).
  - Invariante de sensibilidad: cada chunk pasa por _es_conocimiento_sensible
    (Bloque 3). Lo PLD/personal/legal NO sube a la nube — se salta.
  - org = NEXO_DEFAULT_ORG_ID (org real de Kawiil).

Idempotencia:
  --reset   borra SOLO los chunks de esta fuente (source_ref like 'kc:%') del org,
            sin tocar memory/* ni las capturas.

Uso:
  python3 nexo_backfill_kc.py --dry-run            # cuenta y muestra previews, no escribe
  python3 nexo_backfill_kc.py --limit 20           # prueba con pocos
  python3 nexo_backfill_kc.py --reset              # borra kc:* y recarga todo
  python3 nexo_backfill_kc.py                      # carga incremental (sin borrar)

~1000 embeds en CPU tardan ~15-30 min. Para correr completo sin colgar la sesión:
  nohup python3 nexo_backfill_kc.py --reset > /tmp/nexo_kc_backfill.log 2>&1 &
  tail -f /tmp/nexo_kc_backfill.log
"""

import os
import sys
import argparse

# --- bootstrap de imports (mismo patrón que _buscar_conocimiento) ---
for _cand in filter(None, [
    os.environ.get("NEXO_LIB_DIR"),
    os.path.dirname(os.path.abspath(__file__)),
    os.path.expanduser("~"),
]):
    if _cand not in sys.path:
        sys.path.insert(0, _cand)

try:
    import nexo_retrieve
except Exception as e:  # pragma: no cover
    print(f"ERROR: no pude importar nexo_retrieve: {e}", file=sys.stderr)
    sys.exit(1)

try:
    from louis_core import (
        _kawiil_central_pg,
        _kawiil_central_find_table,
        _es_conocimiento_sensible,
    )
except Exception as e:  # pragma: no cover
    print(f"ERROR: no pude importar helpers de louis_core: {e}", file=sys.stderr)
    sys.exit(1)


def _load_env_files():
    """Puebla os.environ desde los .env del runtime cuando faltan claves. Necesario en
    corrida MANUAL: systemd inyecta el entorno al servicio, pero un `python3 ...` directo
    no. Usa setdefault → NUNCA pisa un valor ya presente (contexto systemd intacto)."""
    for path in ("/opt/openclaw/openclaw.env",   # NEXO_DEFAULT_ORG_ID vive aquí
                 "/opt/openclaw/.env",            # KAWIIL_CENTRAL_DATABASE_URL, etc.
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

ORG = (os.environ.get("NEXO_DEFAULT_ORG_ID")
       or os.environ.get("KAWIIL_KAWIIL_ORG_ID")
       or os.environ.get("KAWIIL_ORG_ID"))

SPACE = "general"
BATCH = 25  # chunks por llamada a insert_chunks (para progreso y requests manejables)


# ===== Helpers de schema (defensivos, sin asumir columnas) =====

def _colset(conn, tabla):
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s", (tabla,))
    return {r[0] for r in cur.fetchall()}


def _pick(cols, *cands):
    for c in cands:
        if c in cols:
            return c
    return None


def _fetch_all(conn, tabla, limit=None):
    cur = conn.cursor()
    sql = f"SELECT * FROM public.{tabla}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql)
    colnames = [d[0] for d in cur.description]
    return colnames, [dict(zip(colnames, r)) for r in cur.fetchall()]


def _s(v):
    """Normaliza un valor a texto limpio ('' si None)."""
    if v is None:
        return ""
    return str(v).strip()


def _name_map(conn, table_cands, name_cands=("name", "nombre", "title", "razon_social")):
    """id(str) -> nombre, para enriquecer contenido (clientes, proyectos)."""
    tabla = _kawiil_central_find_table(conn, table_cands)
    if not tabla:
        return {}
    cols = _colset(conn, tabla)
    if "id" not in cols:
        return {}
    namecol = _pick(cols, *name_cands)
    if not namecol:
        return {}
    cur = conn.cursor()
    cur.execute(f"SELECT id, {namecol} FROM public.{tabla}")
    return {str(i): _s(n) for i, n in cur.fetchall()}


# ===== Construcción de contenido (1 hecho legible por fila) =====

def _project_row(row, cols, client_map):
    pid = _s(row.get("id"))
    name = _s(row.get(_pick(cols, "name", "nombre", "title")))
    status = _s(row.get(_pick(cols, "status", "state", "estado")))
    area = _s(row.get("area")) if "area" in cols else ""
    dcol = _pick(cols, "description", "descripcion", "notes", "detalle", "resumen", "summary")
    desc = _s(row.get(dcol)) if dcol else ""
    client = ""
    if "client_id" in cols and row.get("client_id") is not None:
        client = client_map.get(_s(row.get("client_id")), "")
    created = _s(row.get("created_at")) if "created_at" in cols else ""

    parts = [f"Proyecto: {name}." if name else "Proyecto (sin nombre)."]
    if client:  parts.append(f"Cliente: {client}.")
    if area:    parts.append(f"Área: {area}.")
    if status:  parts.append(f"Estado: {status}.")
    if desc:    parts.append(desc if desc.endswith(".") else desc + ".")
    if created: parts.append(f"Creado: {created}.")
    content = " ".join(parts)
    return pid, (name or f"Proyecto {pid}"), content


def _task_row(row, cols, proj_map):
    tid = _s(row.get("id"))
    title = _s(row.get(_pick(cols, "title", "titulo", "name")))
    status = _s(row.get(_pick(cols, "status", "state", "estado")))
    pcol = _pick(cols, "priority", "prioridad")
    prio = _s(row.get(pcol)) if pcol else ""
    ducol = _pick(cols, "due_date", "deadline", "fecha_limite", "vence", "fecha_vencimiento")
    due = _s(row.get(ducol)) if ducol else ""
    dcol = _pick(cols, "description", "descripcion", "notes", "detalle")
    desc = _s(row.get(dcol)) if dcol else ""
    proj = ""
    pjcol = _pick(cols, "project_id", "proyecto_id")
    if pjcol and row.get(pjcol) is not None:
        proj = proj_map.get(_s(row.get(pjcol)), "")

    parts = [f"Tarea: {title}." if title else "Tarea (sin título)."]
    if proj:   parts.append(f"Proyecto: {proj}.")
    if status: parts.append(f"Estado: {status}.")
    if prio:   parts.append(f"Prioridad: {prio}.")
    if due:    parts.append(f"Vence: {due}.")
    if desc:   parts.append(desc if desc.endswith(".") else desc + ".")
    content = " ".join(parts)
    return tid, (title or f"Tarea {tid}"), content


# ===== Idempotencia =====

def _reset_kc():
    """Borra SOLO los chunks de esta fuente (source_ref like 'kc:%') del org."""
    nexo_retrieve._sb_request(
        "DELETE",
        f"/rest/v1/kb_chunks?org_id=eq.{ORG}&source_ref=like.kc:*",
    )


# ===== Pipeline =====

def _build_rows(conn, limit=None):
    """Devuelve (rows, stats). rows = lista lista para insert_chunks (ya filtrada por sensibilidad)."""
    stats = {"projects": 0, "tasks": 0, "skipped_sensible": 0, "no_table": []}

    client_map = _name_map(conn, ("clients", "client", "clientes"))

    rows = []

    # --- Proyectos ---
    proj_map = {}
    ptabla = _kawiil_central_find_table(conn, ("projects", "project", "proyectos"))
    if not ptabla:
        stats["no_table"].append("proyectos")
    else:
        pcols = _colset(conn, ptabla)
        _, prows = _fetch_all(conn, ptabla, limit)
        for r in prows:
            pid, name, content = _project_row(r, pcols, client_map)
            if not pid:
                continue
            proj_map[pid] = name
            if _es_conocimiento_sensible(f"{name} {content}"):
                stats["skipped_sensible"] += 1
                continue
            rows.append({
                "org_id": ORG, "space_id": SPACE,
                "source_type": "project", "source_ref": f"kc:project:{pid}",
                "title": name[:200] or None, "content": content,
                "metadata": {"source": "kawiil-central", "kind": "project", "id": pid},
            })
            stats["projects"] += 1

    # --- Tareas ---
    ttabla = _kawiil_central_find_table(conn, ("tasks", "task", "todos", "issues", "tareas"))
    if not ttabla:
        stats["no_table"].append("tareas")
    else:
        tcols = _colset(conn, ttabla)
        _, trows = _fetch_all(conn, ttabla, limit)
        for r in trows:
            tid, title, content = _task_row(r, tcols, proj_map)
            if not tid:
                continue
            if _es_conocimiento_sensible(f"{title} {content}"):
                stats["skipped_sensible"] += 1
                continue
            rows.append({
                "org_id": ORG, "space_id": SPACE,
                "source_type": "project", "source_ref": f"kc:task:{tid}",
                "title": title[:200] or None, "content": content,
                "metadata": {"source": "kawiil-central", "kind": "task", "id": tid},
            })
            stats["tasks"] += 1

    return rows, stats


def main():
    ap = argparse.ArgumentParser(description="Backfill de Kawiil Central a la KB-Negocio.")
    ap.add_argument("--dry-run", action="store_true", help="Cuenta y muestra previews, no escribe.")
    ap.add_argument("--reset", action="store_true", help="Borra los chunks kc:* del org antes de cargar.")
    ap.add_argument("--limit", type=int, default=None, help="Máximo de filas por fuente (para pruebas).")
    args = ap.parse_args()

    if not ORG:
        print("ERROR: falta NEXO_DEFAULT_ORG_ID (org real de Kawiil). Debería vivir en "
              "/opt/openclaw/openclaw.env; verifica esa línea o expórtala antes de correr: "
              "`export NEXO_DEFAULT_ORG_ID=$(grep -E '^NEXO_DEFAULT_ORG_ID' /opt/openclaw/openclaw.env | cut -d= -f2-)`",
              file=sys.stderr)
        sys.exit(1)

    print("== Nexo · Bloque 4 (2ª pasada) · backfill Kawiil Central ==")
    print(f"org={ORG}  space={SPACE}  dry_run={args.dry_run}  reset={args.reset}  limit={args.limit}\n")

    conn, err = _kawiil_central_pg()
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)
    try:
        rows, stats = _build_rows(conn, args.limit)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if stats["no_table"]:
        print(f"⚠️  No encontré tabla(s) para: {', '.join(stats['no_table'])}")

    print(f"Candidatos a KB: {len(rows)}  "
          f"(proyectos={stats['projects']}, tareas={stats['tasks']}, "
          f"saltados por sensibilidad={stats['skipped_sensible']})")

    if not rows:
        print("Nada que cargar.")
        sys.exit(0)

    if args.dry_run:
        print("\n--- previews (dry-run, sin escribir) ---")
        for r in rows[:3]:
            print(f"[{r['source_ref']}] {r['title']}")
            print(f"   {r['content'][:180]}{'…' if len(r['content']) > 180 else ''}")
        sys.exit(0)

    if args.reset:
        print("Reset: borrando chunks kc:* del org…")
        _reset_kc()

    total = len(rows)
    inserted = 0
    for i in range(0, total, BATCH):
        batch = rows[i:i + BATCH]
        got = nexo_retrieve.insert_chunks(batch)
        inserted += len(got) if isinstance(got, list) else 0
        print(f"  … {min(i + BATCH, total)}/{total} procesados (insertados acumulados: {inserted})")

    print(f"\n✅ Backfill Kawiil Central completo: {inserted}/{total} chunks insertados en org {ORG}.")
    if inserted != total:
        print(f"⚠️  {total - inserted} no confirmados — revisa Supabase/embeddings.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
