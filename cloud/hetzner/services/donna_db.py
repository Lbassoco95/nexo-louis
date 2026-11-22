#!/usr/bin/env python3
"""donna_db.py — base de datos de Donna (SQLite, WAL) para notificaciones, resúmenes y seguimientos.

Por qué base de datos y no archivos de texto: los datos quedan ordenados, con historial que solo se agrega (no se reescribe), se pueden consultar,
contar y procesar después (estadísticas, memoria, indexado), y el gateway y el scheduler la comparten sin pisarse. El esquema usa tipos simples
(texto, enteros, JSON en texto) para poder pasarlo a Postgres tal cual cuando se decida. Archivo: <STATE_DIR>/donna.db (modo 0600).

Tablas:
  notificaciones   — cada aviso (corto) con su estado: nueva · resuelta · pospuesta · descartada, y las acciones que ofrece.
  notif_eventos    — historial append-only de lo que se hizo con cada aviso.
  resumenes        — resúmenes (cierre del día, mediodía, semanal) como datos estructurados + texto; la notificación solo apunta a ellos.
  seguimientos     — lo que Donna sigue por Polo (tareas, su día, compras…): estado, próxima revisión, notas.
  seg_eventos      — historial append-only de cada seguimiento.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
_INIT_LOCK = threading.Lock()
_INIT_DONE = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notificaciones (
  id TEXT PRIMARY KEY, usuario TEXT NOT NULL DEFAULT 'admin', ts INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT '',
  titulo TEXT NOT NULL, cuerpo TEXT NOT NULL DEFAULT '', tab TEXT NOT NULL DEFAULT 'chat', link TEXT NOT NULL DEFAULT '',
  leida INTEGER NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'nueva', snooze_hasta INTEGER NOT NULL DEFAULT 0,
  acciones TEXT NOT NULL DEFAULT '[]', hechas TEXT NOT NULL DEFAULT '{}', resuelta_ts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_notif_ts ON notificaciones(ts DESC);
CREATE INDEX IF NOT EXISTS ix_notif_estado ON notificaciones(estado, snooze_hasta);
CREATE TABLE IF NOT EXISTS notif_eventos (
  n INTEGER PRIMARY KEY AUTOINCREMENT, notif_id TEXT NOT NULL, ts INTEGER NOT NULL, evento TEXT NOT NULL, detalle TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS resumenes (
  id TEXT PRIMARY KEY, usuario TEXT NOT NULL DEFAULT 'admin', ts INTEGER NOT NULL, fecha TEXT NOT NULL, tipo TEXT NOT NULL,
  titulo TEXT NOT NULL, datos TEXT NOT NULL DEFAULT '{}', texto TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_res_ts ON resumenes(ts DESC);
CREATE TABLE IF NOT EXISTS seguimientos (
  id TEXT PRIMARY KEY, usuario TEXT NOT NULL DEFAULT 'admin', creado INTEGER NOT NULL, actualizado INTEGER NOT NULL,
  titulo TEXT NOT NULL, origen TEXT NOT NULL DEFAULT 'manual', ref TEXT NOT NULL DEFAULT '', estado TEXT NOT NULL DEFAULT 'activo',
  proxima_revision INTEGER NOT NULL DEFAULT 0, cadencia_horas INTEGER NOT NULL DEFAULT 24, ultimo_aviso INTEGER NOT NULL DEFAULT 0,
  nota TEXT NOT NULL DEFAULT '', prioridad INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_seg_estado ON seguimientos(estado, proxima_revision);
CREATE TABLE IF NOT EXISTS seg_eventos (
  n INTEGER PRIMARY KEY AUTOINCREMENT, seg_id TEXT NOT NULL, ts INTEGER NOT NULL, evento TEXT NOT NULL, detalle TEXT NOT NULL DEFAULT '');
"""


def _path() -> Path:
    return STATE_DIR / "donna.db"


def conn() -> sqlite3.Connection:
    """Conexión nueva por uso (segura entre hilos y procesos). La primera vez crea el esquema y migra la bandeja vieja (notifs.json)."""
    p = _path()
    key = str(p)
    if key not in _INIT_DONE:
        with _INIT_LOCK:
            if key not in _INIT_DONE:
                p.parent.mkdir(parents=True, exist_ok=True)
                fresh = not p.exists()
                c = sqlite3.connect(p, timeout=15)
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(SCHEMA)
                c.commit()
                c.close()
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
                _INIT_DONE.add(key)
                _migrar_bandeja_json()
    c = sqlite3.connect(p, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    return c


def _migrar_bandeja_json():
    """Una sola vez: pasa las notificaciones guardadas en notifs.json a la base y deja el archivo como notifs.json.migrado."""
    old = STATE_DIR / "notifs.json"
    if not old.exists():
        return
    try:
        items = json.loads(old.read_text())
    except Exception:
        return
    c = sqlite3.connect(_path(), timeout=15)
    try:
        for r in items if isinstance(items, list) else []:
            c.execute("INSERT OR IGNORE INTO notificaciones(id,ts,kind,titulo,cuerpo,tab,link,leida,estado,acciones,hechas) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (str(r.get("id") or uuid.uuid4().hex[:12]), int(r.get("ts") or time.time()), r.get("kind", ""), r.get("title", ""), r.get("body", ""),
                       r.get("tab", "chat"), r.get("link", ""), 1 if r.get("read") else 0, "resuelta" if r.get("done") else "nueva",
                       json.dumps(r.get("actions") or [], ensure_ascii=False), json.dumps(r.get("done") or {}, ensure_ascii=False)))
        c.commit()
        old.rename(old.with_suffix(".json.migrado"))
    except Exception:
        pass
    finally:
        c.close()


def _id() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------ notificaciones
def _n_dict(r) -> dict:
    return {"id": r["id"], "ts": r["ts"], "title": r["titulo"], "body": r["cuerpo"], "tab": r["tab"], "kind": r["kind"], "link": r["link"],
            "read": bool(r["leida"]), "actions": json.loads(r["acciones"] or "[]"), "done": json.loads(r["hechas"] or "{}"),
            "estado": r["estado"], "snooze_hasta": r["snooze_hasta"]}


def notif_add(titulo: str, cuerpo: str = "", tab: str = "chat", acciones: list = None, kind: str = "", link: str = "", usuario: str = "admin") -> dict:
    nid = _id()
    ts = int(time.time())
    c = conn()
    try:
        c.execute("INSERT INTO notificaciones(id,usuario,ts,kind,titulo,cuerpo,tab,link,acciones) VALUES(?,?,?,?,?,?,?,?,?)",
                  (nid, usuario, ts, kind[:30], (titulo or "Donna")[:120], (cuerpo or "")[:4000], tab, (link or "")[:80], json.dumps(acciones or [], ensure_ascii=False)))
        c.execute("INSERT INTO notif_eventos(notif_id,ts,evento) VALUES(?,?,?)", (nid, ts, "creada"))
        c.commit()
    finally:
        c.close()
    return notif_get(nid)


def notif_get(nid: str):
    c = conn()
    try:
        r = c.execute("SELECT * FROM notificaciones WHERE id=?", (nid,)).fetchone()
        return _n_dict(r) if r else None
    finally:
        c.close()


def notif_list(limit: int = 50, usuario: str = None) -> list:
    """Las más nuevas primero. Las pospuestas y descartadas no salen hasta que les toque."""
    now = int(time.time())
    c = conn()
    try:
        q = "SELECT * FROM notificaciones WHERE estado!='descartada' AND snooze_hasta<=?" + (" AND usuario=?" if usuario else "") + " ORDER BY ts DESC, rowid DESC LIMIT ?"
        args = [now] + ([usuario] if usuario else []) + [max(1, min(int(limit), 200))]
        return [_n_dict(r) for r in c.execute(q, args)]
    finally:
        c.close()


def notif_sin_leer() -> int:
    c = conn()
    try:
        return c.execute("SELECT COUNT(*) FROM notificaciones WHERE leida=0 AND estado IN ('nueva','vista') AND snooze_hasta<=?", (int(time.time()),)).fetchone()[0]
    finally:
        c.close()


def notif_pendientes_accion() -> int:
    """Avisos con un botón de confirmación aún sin usar."""
    c = conn()
    try:
        n = 0
        for r in c.execute("SELECT acciones, hechas FROM notificaciones WHERE estado IN ('nueva','vista') AND acciones!='[]' AND snooze_hasta<=?", (int(time.time()),)):
            hechas = json.loads(r["hechas"] or "{}")
            if any(a.get("id") not in hechas for a in json.loads(r["acciones"] or "[]")):
                n += 1
        return n
    finally:
        c.close()


def notif_marcar_leidas(ids=None) -> int:
    c = conn()
    try:
        if ids is None:
            cur = c.execute("UPDATE notificaciones SET leida=1 WHERE leida=0")
        else:
            ids = list(ids)
            if not ids:
                return 0
            cur = c.execute("UPDATE notificaciones SET leida=1 WHERE leida=0 AND id IN (%s)" % ",".join("?" * len(ids)), ids)
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def notif_set_done(nid: str, accion: str, resultado: str) -> bool:
    """Registra que se usó una acción (botón de confirmación)."""
    c = conn()
    try:
        r = c.execute("SELECT hechas FROM notificaciones WHERE id=?", (nid,)).fetchone()
        if not r:
            return False
        h = json.loads(r["hechas"] or "{}")
        h[accion] = {"ts": int(time.time()), "result": (resultado or "")[:1500]}
        c.execute("UPDATE notificaciones SET hechas=?, leida=1 WHERE id=?", (json.dumps(h, ensure_ascii=False), nid))
        c.execute("INSERT INTO notif_eventos(notif_id,ts,evento,detalle) VALUES(?,?,?,?)", (nid, int(time.time()), "accion:" + accion, (resultado or "")[:300]))
        c.commit()
        return True
    finally:
        c.close()


def notif_resolver(nid: str, como: str = "hecho", detalle: str = "") -> bool:
    ts = int(time.time())
    c = conn()
    try:
        cur = c.execute("UPDATE notificaciones SET estado='resuelta', leida=1, resuelta_ts=? WHERE id=?", (ts, nid))
        c.execute("INSERT INTO notif_eventos(notif_id,ts,evento,detalle) VALUES(?,?,?,?)", (nid, ts, como, detalle[:300]))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def notif_descartar(nid: str) -> bool:
    ts = int(time.time())
    c = conn()
    try:
        cur = c.execute("UPDATE notificaciones SET estado='descartada', leida=1 WHERE id=?", (nid,))
        c.execute("INSERT INTO notif_eventos(notif_id,ts,evento) VALUES(?,?,?)", (nid, ts, "descartada"))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def notif_posponer(nid: str, hasta_ts: int) -> bool:
    ts = int(time.time())
    c = conn()
    try:
        cur = c.execute("UPDATE notificaciones SET estado='pospuesta', snooze_hasta=?, leida=1 WHERE id=?", (int(hasta_ts), nid))
        c.execute("INSERT INTO notif_eventos(notif_id,ts,evento,detalle) VALUES(?,?,?,?)", (nid, ts, "pospuesta", str(int(hasta_ts))))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def notif_pospuestas_vencidas() -> list:
    """Las pospuestas a las que ya les tocó volver: pasan a 'nueva' (sin leer) y se devuelven para volver a avisar."""
    now = int(time.time())
    c = conn()
    try:
        rows = c.execute("SELECT * FROM notificaciones WHERE estado='pospuesta' AND snooze_hasta<=?", (now,)).fetchall()
        out = []
        for r in rows:
            c.execute("UPDATE notificaciones SET estado='nueva', leida=0, snooze_hasta=0 WHERE id=?", (r["id"],))
            c.execute("INSERT INTO notif_eventos(notif_id,ts,evento) VALUES(?,?,?)", (r["id"], now, "volvio"))
            d = _n_dict(r)
            d["estado"], d["read"], d["snooze_hasta"] = "nueva", False, 0
            out.append(d)
        c.commit()
        return out
    finally:
        c.close()


def notif_eventos(nid: str) -> list:
    c = conn()
    try:
        return [dict(r) for r in c.execute("SELECT ts,evento,detalle FROM notif_eventos WHERE notif_id=? ORDER BY n", (nid,))]
    finally:
        c.close()


# ------------------------------------------------------------------ resúmenes
def resumen_add(tipo: str, fecha: str, titulo: str, datos: dict, texto: str = "", usuario: str = "admin") -> str:
    rid = _id()
    c = conn()
    try:
        c.execute("INSERT INTO resumenes(id,usuario,ts,fecha,tipo,titulo,datos,texto) VALUES(?,?,?,?,?,?,?,?)",
                  (rid, usuario, int(time.time()), fecha, tipo, titulo[:120], json.dumps(datos or {}, ensure_ascii=False), texto or ""))
        c.commit()
    finally:
        c.close()
    return rid


def _r_dict(r, con_datos=True) -> dict:
    d = {"id": r["id"], "ts": r["ts"], "fecha": r["fecha"], "tipo": r["tipo"], "titulo": r["titulo"]}
    if con_datos:
        d["datos"] = json.loads(r["datos"] or "{}")
        d["texto"] = r["texto"]
    return d


def resumen_get(rid: str):
    c = conn()
    try:
        r = c.execute("SELECT * FROM resumenes WHERE id=?", (rid,)).fetchone()
        return _r_dict(r) if r else None
    finally:
        c.close()


def resumen_list(limit: int = 20, tipo: str = None) -> list:
    c = conn()
    try:
        q = "SELECT * FROM resumenes" + (" WHERE tipo=?" if tipo else "") + " ORDER BY ts DESC, rowid DESC LIMIT ?"
        return [_r_dict(r, False) for r in c.execute(q, ([tipo] if tipo else []) + [max(1, min(int(limit), 100))])]
    finally:
        c.close()


# ------------------------------------------------------------------ seguimientos
def _s_dict(r) -> dict:
    return {k: r[k] for k in r.keys()}


def seg_crear(titulo: str, proxima_revision: int, nota: str = "", origen: str = "manual", ref: str = "", cadencia_horas: int = 24,
              prioridad: int = 0, usuario: str = "admin") -> dict:
    titulo = " ".join((titulo or "").split())[:200]
    if len(titulo) < 3:
        raise ValueError("Falta qué seguir.")
    sid = _id()
    now = int(time.time())
    c = conn()
    try:
        c.execute("INSERT INTO seguimientos(id,usuario,creado,actualizado,titulo,origen,ref,proxima_revision,cadencia_horas,nota,prioridad) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, usuario, now, now, titulo, origen[:20], ref[:80], int(proxima_revision), max(1, int(cadencia_horas)), nota[:1000], int(prioridad)))
        c.execute("INSERT INTO seg_eventos(seg_id,ts,evento,detalle) VALUES(?,?,?,?)", (sid, now, "creado", nota[:300]))
        c.commit()
    finally:
        c.close()
    return seg_get(sid)


def seg_get(sid: str):
    c = conn()
    try:
        r = c.execute("SELECT * FROM seguimientos WHERE id=?", (sid,)).fetchone()
        return _s_dict(r) if r else None
    finally:
        c.close()


def seg_list(estados=("activo", "pausado"), limite_hechos: int = 10, usuario: str = None) -> list:
    c = conn()
    try:
        out = []
        for r in c.execute("SELECT * FROM seguimientos WHERE estado IN (%s)%s ORDER BY prioridad DESC, proxima_revision" % (",".join("?" * len(estados)), " AND usuario=?" if usuario else ""),
                           list(estados) + ([usuario] if usuario else [])):
            out.append(_s_dict(r))
        if "hecho" not in estados and limite_hechos:
            for r in c.execute("SELECT * FROM seguimientos WHERE estado='hecho'" + (" AND usuario=?" if usuario else "") + " ORDER BY actualizado DESC LIMIT ?",
                               ([usuario] if usuario else []) + [limite_hechos]):
                out.append(_s_dict(r))
        return out
    finally:
        c.close()


def seg_actualizar(sid: str, estado: str = None, proxima_revision: int = None, nota: str = None, evento: str = "", detalle: str = "") -> dict:
    now = int(time.time())
    sets, args = ["actualizado=?"], [now]
    if estado:
        if estado not in ("activo", "pausado", "hecho", "cancelado"):
            raise ValueError("estado no válido")
        sets.append("estado=?"); args.append(estado)
    if proxima_revision is not None:
        sets.append("proxima_revision=?"); args.append(int(proxima_revision))
    if nota is not None:
        sets.append("nota=?"); args.append(nota[:1000])
    c = conn()
    try:
        cur = c.execute("UPDATE seguimientos SET %s WHERE id=?" % ",".join(sets), args + [sid])
        if cur.rowcount == 0:
            raise ValueError("seguimiento no encontrado")
        c.execute("INSERT INTO seg_eventos(seg_id,ts,evento,detalle) VALUES(?,?,?,?)", (sid, now, evento or (estado or "actualizado"), detalle[:300]))
        c.commit()
    finally:
        c.close()
    return seg_get(sid)


def seg_a_revisar(ahora: int = None) -> list:
    """Activos a los que ya les toca revisión."""
    c = conn()
    try:
        return [_s_dict(r) for r in c.execute("SELECT * FROM seguimientos WHERE estado='activo' AND proxima_revision>0 AND proxima_revision<=? ORDER BY prioridad DESC, proxima_revision",
                                              (int(ahora or time.time()),))]
    finally:
        c.close()


def seg_marcar_aviso(sid: str, proxima: int):
    now = int(time.time())
    c = conn()
    try:
        c.execute("UPDATE seguimientos SET ultimo_aviso=?, proxima_revision=?, actualizado=? WHERE id=?", (now, int(proxima), now, sid))
        c.execute("INSERT INTO seg_eventos(seg_id,ts,evento) VALUES(?,?,?)", (sid, now, "aviso"))
        c.commit()
    finally:
        c.close()


def seg_eventos(sid: str) -> list:
    c = conn()
    try:
        return [dict(r) for r in c.execute("SELECT ts,evento,detalle FROM seg_eventos WHERE seg_id=? ORDER BY n", (sid,))]
    finally:
        c.close()
