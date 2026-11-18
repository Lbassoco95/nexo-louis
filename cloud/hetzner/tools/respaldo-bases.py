#!/usr/bin/env python3
"""respaldo-bases.py — foto de las tablas de trabajo ANTES de limpiar datos con Donna (solo lectura).

  python3 respaldo-bases.py            # Patio Yoltik + Kawiil Central
  python3 respaldo-bases.py patio      # solo Patio
  python3 respaldo-bases.py kawiil     # solo Kawiil Central

Guarda JSON en /opt/openclaw/state/backups/AAAA-MM-DD_HHMM/ (carpeta 0700, archivos 0600).
Usa las credenciales que ya tiene Donna; nunca las imprime. Para deshacer un cambio puntual se consulta el JSON.
"""
import os, sys, json, re
from pathlib import Path
from datetime import datetime

OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))
SRC = {
    "patio": {"env": OC / "credentials" / "patio-yoltik.env", "var": "PATIO_YOLTIK_DATABASE_URL",
              "tablas": ["projects", "tasks", "task_comments", "project_comments", "team_members", "deals", "clients", "companies"]},
    "kawiil": {"env": OC / "credentials" / "kawiil-central.env", "var": "KAWIIL_CENTRAL_DATABASE_URL",
               "tablas": ["projects", "tasks", "task_comments", "clients"]},
}


def leer_env(p: Path) -> dict:
    out = {}
    try:
        for l in p.read_text().splitlines():
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def main():
    quien = sys.argv[1:] or list(SRC)
    try:
        import psycopg2
    except ImportError:
        sys.exit("✗ falta psycopg2")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    base = OC / "state" / "backups" / stamp
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    for q in quien:
        if q not in SRC:
            print(f"✗ fuente desconocida: {q}"); continue
        cfg = SRC[q]
        url = leer_env(cfg["env"]).get(cfg["var"]) or os.environ.get(cfg["var"], "")
        if not url:
            print(f"✗ {q}: sin credencial ({cfg['var']})"); continue
        try:
            conn = psycopg2.connect(url, connect_timeout=10)
            conn.set_session(readonly=True, autocommit=True)
        except Exception as e:
            print(f"✗ {q}: no conecta: {str(e).splitlines()[0][:120]}"); continue
        cur = conn.cursor()
        for t in cfg["tablas"]:
            if not re.fullmatch(r"[a-z_]+", t):
                continue
            try:
                cur.execute(f'select * from public."{t}"')
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                f = base / f"{q}_{t}.json"
                f.write_text(json.dumps(rows, default=str, ensure_ascii=False))
                os.chmod(f, 0o600)
                print(f"  ✓ {q}.{t}: {len(rows)} filas")
            except Exception as e:
                conn.rollback() if not conn.autocommit else None
                print(f"  · {q}.{t}: omitida ({str(e).splitlines()[0][:80]})")
        conn.close()
    print(f"\n✓ Respaldo en {base}")


if __name__ == "__main__":
    main()
