#!/usr/bin/env python3
"""patio_yoltik.py — Donna ↔ Patio Yoltik (Supabase propio de Yoltik). Solo stdlib + psycopg2.

DOS credenciales separadas (archivo 0600 credentials/patio-yoltik.env, que sobrevive a los deploys):
  PATIO_YOLTIK_DATABASE_URL        rol patio_lectura   → lee TODO (solo SELECT)
  PATIO_YOLTIK_DATABASE_URL_WRITE  rol patio_escritura → solo UPDATE de tareas e INSERT de comentarios (sin DELETE)
Así, aunque el modelo se equivoque (o alguien le inyecte texto), la base misma impide borrar o tocar otras tablas.
Cada escritura queda en logs/patio-yoltik-ops.log. Todas las funciones devuelven texto listo para el chat y
empiezan con ❌ cuando fallan (el freno anti-invención de louis_core lo detecta).
"""
import os
import re
import json
from pathlib import Path
from datetime import datetime, timezone

HOME_OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))
ENV_FILE = HOME_OC / "credentials" / "patio-yoltik.env"
AUDIT_LOG = HOME_OC / "logs" / "patio-yoltik-ops.log"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
EDITABLES = {"estatus", "prioridad", "fecha_vencimiento", "asignado_a", "titulo", "descripcion"}
PRIORIDADES = {"baja", "media", "alta", "urgente"}


def _env() -> dict:
    out = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in ("PATIO_YOLTIK_DATABASE_URL", "PATIO_YOLTIK_DATABASE_URL_WRITE"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def _conn(write: bool = False):
    key = "PATIO_YOLTIK_DATABASE_URL_WRITE" if write else "PATIO_YOLTIK_DATABASE_URL"
    url = _env().get(key, "")
    if not url:
        return None, (f"❌ Falta {key}: corre `sudo bash /opt/louis/scripts/setup-patio-yoltik-creds.sh` en el servidor."
                      + (" (sin la credencial de escritura Donna solo puede LEER Patio Yoltik)" if write else ""))
    try:
        import psycopg2  # type: ignore
    except ImportError:
        return None, "❌ psycopg2 no instalado (pip3 install --break-system-packages psycopg2-binary)"
    try:
        return psycopg2.connect(url, connect_timeout=10), None
    except Exception as e:
        return None, f"❌ Patio Yoltik conectividad ({'escritura' if write else 'lectura'}): {str(e)[:200]}"


def _audit(razon: str, detalle: str) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a") as f:
            f.write(f"[{datetime.now().isoformat()}] {razon}\n  {detalle[:600]}\n")
    except Exception:
        pass


def _tabla(rows, cols) -> str:
    out = [" | ".join(cols), " | ".join("-" * max(3, len(c)) for c in cols)]
    for r in rows:
        out.append(" | ".join(("" if v is None else str(v).replace("\n", " "))[:70] for v in r))
    return "```\n" + "\n".join(out) + "\n```"


def _select(sql: str, params=()):
    conn, err = _conn()
    if err:
        return None, None, err
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(100)
        conn.close()
        return cols, rows, None
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return None, None, f"❌ Error SQL (Patio Yoltik): {str(e)[:300]}"


# ------------------------------------------------------------------ lectura
def estado() -> str:
    env = _env()
    parts = ["🌱 *Patio Yoltik* (patio-yoltik.mx)",
             f"  Lectura (patio_lectura): {'✓' if env.get('PATIO_YOLTIK_DATABASE_URL') else '✗ falta PATIO_YOLTIK_DATABASE_URL'}",
             f"  Escritura (patio_escritura): {'✓' if env.get('PATIO_YOLTIK_DATABASE_URL_WRITE') else '· no configurada (solo lectura)'}"]
    cols, rows, err = _select("select count(*) from information_schema.tables where table_schema='public'")
    if err:
        return "\n".join(parts + ["  Postgres: " + err])
    parts.append(f"  Tablas legibles: {rows[0][0]}")
    for t in ("projects", "tasks", "deals", "prospects", "leads", "clients"):
        c, r, e = _select(f'select count(*) from public."{t}"')
        parts.append(f"  • {t}: {r[0][0]}" if not e else f"  • {t}: ✗ {e[2:60]}")
    return "\n".join(parts)


def resumen() -> str:
    """Panorama de Yoltik en una sola llamada. Cada bloque es independiente: si uno falla se avisa y los demás salen."""
    bloques = [
        ("Proyectos por estatus", "select estatus, count(*) as n from public.projects group by 1 order by 2 desc", None),
        ("Proyectos activos (más urgentes primero)",
         "select nombre, prioridad, progress_pct as avance_pct, proximo_hito_titulo as proximo_hito, proximo_hito_fecha as hito_fecha, "
         "coalesce(fecha_fin_estimada, fecha_fin) as fin_estimado from public.projects where estatus = 'activo' "
         "order by proximo_hito_fecha nulls last, nombre limit 20", None),
        ("Tareas por estatus", "select estatus, count(*) as n from public.tasks group by 1 order by 2 desc", None),
        ("Tareas VENCIDAS (abiertas con fecha pasada)",
         "select t.titulo, t.estatus, t.prioridad, t.fecha_vencimiento as vencia, p.nombre as proyecto from public.tasks t "
         "left join public.projects p on p.id = t.project_id where t.fecha_vencimiento < current_date "
         "and t.estatus::text not in ('completada','cancelada') order by t.fecha_vencimiento limit 15", None),
        ("Tareas que vencen en 7 días",
         "select t.titulo, t.estatus, t.prioridad, t.fecha_vencimiento as vence, p.nombre as proyecto from public.tasks t "
         "left join public.projects p on p.id = t.project_id where t.fecha_vencimiento between current_date and current_date + 7 "
         "and t.estatus::text not in ('completada','cancelada') order by t.fecha_vencimiento limit 15", None),
        ("Pipeline de deals (etapa · cuántos · monto)",
         "select etapa, count(*) as n, coalesce(sum(monto),0) as monto from public.deals group by 1 order by 2 desc", None),
        ("Prospectos por estado", "select estado, count(*) as n from public.prospects group by 1 order by 2 desc", None),
        ("Leads por estado", "select estado, count(*) as n from public.leads group by 1 order by 2 desc", None),
        ("Leads recientes (14 días)",
         "select created_at::date as fecha, form_type, nombre, empresa from public.leads where created_at > now() - interval '14 days' "
         "order by created_at desc limit 10", None),
    ]
    out, fallos = ["🌱 *Yoltik — panorama (Patio Yoltik, datos reales)*"], 0
    for titulo, sql, _ in bloques:
        cols, rows, err = _select(sql)
        if err:
            if "Falta PATIO" in err or "conectividad" in err:
                return err                                           # sin conexión no tiene caso seguir
            fallos += 1
            out.append(f"\n**{titulo}**: ✗ {err[:140]}")
            continue
        out.append(f"\n**{titulo}**\n" + (_tabla(rows, cols) if rows else "(ninguno)"))
    if fallos:
        out.append(f"\n⚠️ {fallos} bloque(s) no se pudieron leer: no completes esos datos de memoria.")
    return "\n".join(out)


def tablas() -> str:
    cols, rows, err = _select("select table_name from information_schema.tables where table_schema='public' order by 1")
    return err or f"{len(rows)} tablas: " + ", ".join(r[0] for r in rows)


def describir(tabla: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tabla or ""):
        return "❌ nombre de tabla inválido"
    cols, rows, err = _select("select column_name, data_type, is_nullable from information_schema.columns "
                              "where table_schema='public' and table_name=%s order by ordinal_position", (tabla,))
    if err:
        return err
    return _tabla(rows, cols) if rows else f"❌ no existe la tabla `{tabla}`"


_SELECT_OK = re.compile(r"^\s*(select|with)\b", re.I)
_SELECT_BAD = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|call|do)\b|;\s*\S", re.I)


def query(sql: str, razon: str = "") -> str:
    """SELECT libre (máx 100 filas). El rol de lectura ya es de solo lectura; esto es un segundo candado."""
    if not _SELECT_OK.match(sql or "") or _SELECT_BAD.search(sql or ""):
        return "🔒 Solo se permiten consultas SELECT/WITH de una sola sentencia. Para cambiar tareas usa patio_actualizar_tarea."
    cols, rows, err = _select(sql)
    if err:
        return err
    return f"✅ {len(rows)} filas (máx 100):\n" + _tabla(rows, cols)


def tareas(estatus: str = "", proyecto: str = "", limit: int = 30) -> str:
    where, params = [], []
    if estatus:
        where.append("t.estatus::text = %s"); params.append(estatus)
    if proyecto:
        where.append("p.nombre ilike %s"); params.append(f"%{proyecto}%")
    sql = ("select t.id, t.titulo, t.estatus, t.prioridad, t.fecha_vencimiento, p.nombre as proyecto "
           "from public.tasks t left join public.projects p on p.id = t.project_id "
           + ("where " + " and ".join(where) if where else "")
           + " order by t.fecha_vencimiento nulls last, t.created_at desc limit %s")
    cols, rows, err = _select(sql, params + [max(1, min(int(limit or 30), 100))])
    if err:
        return err
    return f"✅ {len(rows)} tareas:\n" + _tabla(rows, cols) if rows else "(sin tareas con ese filtro)"


def proyectos(limit: int = 30) -> str:
    cols, rows, err = _select("select * from public.projects order by created_at desc limit %s", (max(1, min(int(limit or 30), 100)),))
    if err:
        return err
    keep = [i for i, c in enumerate(cols) if c in ("id", "nombre", "estatus", "status", "cliente", "client_id", "fecha_inicio", "fecha_fin", "created_at")] or list(range(min(6, len(cols))))
    return f"✅ {len(rows)} proyectos:\n" + _tabla([[r[i] for i in keep] for r in rows], [cols[i] for i in keep])


def tarea_detalle(tarea_id: str) -> str:
    if not UUID_RE.match(tarea_id or ""):
        return "❌ El id debe ser el UUID completo de la tarea (búscalo con patio_tareas)."
    cols, rows, err = _select("select * from public.tasks where id = %s", (tarea_id,))
    if err:
        return err
    if not rows:
        return f"❌ No existe la tarea `{tarea_id}`."
    out = "\n".join(f"{c}: {v}" for c, v in zip(cols, rows[0]) if v not in (None, "", [], {}))
    c2, r2, e2 = _select("select created_at, body from public.task_comments where task_id = %s order by created_at desc limit 8", (tarea_id,))
    return out + ("\n\nÚltimos comentarios:\n" + _tabla(r2, c2) if r2 else "")


# ------------------------------------------------------------------ escritura (rol patio_escritura)
def actualizar_tarea(tarea_id: str, cambios: dict) -> str:
    if not UUID_RE.match(tarea_id or ""):
        return "❌ El id debe ser el UUID completo de la tarea (búscalo con patio_tareas)."
    cambios = {k: v for k, v in (cambios or {}).items() if k in EDITABLES}
    if not cambios:
        return f"❌ Nada que cambiar. Campos permitidos: {sorted(EDITABLES)}. Para cerrar: {{'estatus': 'completada'}}."
    conn, err = _conn(write=True)
    if err:
        return err
    try:
        cur = conn.cursor()
        if "estatus" in cambios:
            cur.execute("select enumlabel from pg_enum e join pg_type t on t.oid=e.enumtypid where t.typname='task_status'")
            validos = {r[0] for r in cur.fetchall()}
            if cambios["estatus"] not in validos:
                conn.close()
                return f"❌ estatus inválido «{cambios['estatus']}». Válidos: {sorted(validos)}"
        if "prioridad" in cambios and cambios["prioridad"] not in PRIORIDADES:
            conn.close()
            return f"❌ prioridad inválida. Válidas: {sorted(PRIORIDADES)}"
        sets, vals = [], []
        for k, v in cambios.items():
            if k == "estatus":
                sets.append("estatus = %s::task_status")
            elif k == "prioridad":
                sets.append("prioridad = %s::task_priority")
            elif k in ("fecha_vencimiento",):
                sets.append("fecha_vencimiento = %s::date")
            elif k == "asignado_a":
                sets.append("asignado_a = %s::uuid")
            else:
                sets.append(f"{k} = %s")
            vals.append(v if v != "" else None)
        if "estatus" in cambios:
            sets.append("completada_en = " + ("now()" if cambios["estatus"] == "completada" else "null"))
        sets.append("updated_at = now()")
        sql = f"update public.tasks set {', '.join(sets)} where id = %s returning titulo, estatus"
        conn.autocommit = True
        cur.execute(sql, vals + [tarea_id])
        row = cur.fetchone()
        conn.close()
        _audit(f"actualizar_tarea {tarea_id}", json.dumps(cambios, default=str, ensure_ascii=False))
        if not row:
            return f"⚠️ No se actualizó nada: ¿existe la tarea `{tarea_id}`?"
        return f"✅ Tarea «{row[0]}» ahora está en `{row[1]}`. Cambios aplicados: {cambios}"
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return f"❌ No se pudo actualizar (¿permiso del rol de escritura?): {str(e)[:300]}"


def avance(tarea_id: str, texto: str) -> str:
    if not UUID_RE.match(tarea_id or ""):
        return "❌ El id debe ser el UUID completo de la tarea."
    texto = (texto or "").strip()
    if not texto:
        return "❌ El avance está vacío."
    conn, err = _conn(write=True)
    if err:
        return err
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("insert into public.task_comments (task_id, workspace_id, body) "
                    "select id, workspace_id, %s from public.tasks where id = %s returning id", (texto[:4000], tarea_id))
        row = cur.fetchone()
        conn.close()
        _audit(f"avance {tarea_id}", texto)
        return f"✅ Avance registrado en la tarea `{tarea_id}`." if row else f"⚠️ No existe la tarea `{tarea_id}`."
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return f"❌ No se pudo registrar el avance: {str(e)[:300]}"
