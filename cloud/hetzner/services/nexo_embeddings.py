#!/usr/bin/env python3
"""
nexo_embeddings.py — Nexo · Capa 1 · Bloque 1
Puente de embeddings locales: convierte texto -> vector(768) usando Ollama
(modelo nomic-embed-text) para poblar/consultar kb_chunks (Bloque 0).

Diseño: stdlib puro (urllib), sin dependencias externas — igual que kawiil_agents.py.
El vector que devuelve embed() es exactamente lo que espera la columna
kb_chunks.embedding vector(768) y la función match_kb_chunks del Bloque 0.

Uso CLI:
  python3 nexo_embeddings.py embed "texto a vectorizar"     # imprime dim + preview
  python3 nexo_embeddings.py check                           # CHECKPOINT Bloque 1
  python3 nexo_embeddings.py sql "texto"                     # imprime el literal '[..]'::vector

Config (env u ~/.openclaw/credentials/kawiil-agents.env):
  OLLAMA_URL        default http://localhost:11434
                    (desde DENTRO del contenedor kawiil-agents usar
                     http://host.docker.internal:11434 con extra_hosts, o http://172.17.0.1:11434)
  NEXO_EMBED_MODEL  default nomic-embed-text
  NEXO_EMBED_DIM    default 768

Requisito previo en el VPS (una vez):
  ollama pull nomic-embed-text
"""

import os
import sys
import json
import math
import urllib.request
import urllib.error
from pathlib import Path

CREDS = Path.home() / ".openclaw" / "credentials" / "kawiil-agents.env"


def load_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# env de archivo, pero os.environ gana (para override en Docker/compose)
_FILE_ENV = load_env(CREDS)


def _cfg(key: str, default: str) -> str:
    return os.environ.get(key) or _FILE_ENV.get(key) or default


OLLAMA_URL = _cfg("OLLAMA_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = _cfg("NEXO_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(_cfg("NEXO_EMBED_DIM", "768"))


def embed(text: str, *, model: str = EMBED_MODEL, timeout: int = 60) -> list:
    """
    Devuelve el embedding de `text` como list[float] de longitud EMBED_DIM.
    Lanza RuntimeError si Ollama falla o si la dimensión no coincide.
    Compatible con /api/embeddings (clásico) y /api/embed (nuevo).
    """
    if not text or not text.strip():
        raise RuntimeError("embed(): texto vacío")

    # Endpoint clásico: {"model","prompt"} -> {"embedding":[...]}
    body = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embeddings", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"No se pudo conectar a Ollama en {OLLAMA_URL} ({e.reason}). "
            f"¿Está corriendo el servicio y se hizo 'ollama pull {model}'?"
        )

    # Compat: 'embedding' (clásico) o 'embeddings'[0] (nuevo)
    vec = data.get("embedding")
    if vec is None and isinstance(data.get("embeddings"), list) and data["embeddings"]:
        vec = data["embeddings"][0]
    if not isinstance(vec, list) or not vec:
        raise RuntimeError(f"Respuesta de Ollama sin embedding: {data}")

    if len(vec) != EMBED_DIM:
        raise RuntimeError(
            f"Dimensión inesperada: {len(vec)} != {EMBED_DIM}. "
            f"¿El modelo '{model}' coincide con el schema vector({EMBED_DIM})?"
        )
    return [float(x) for x in vec]


def to_pgvector(vec: list) -> str:
    """Serializa un vector a literal Postgres: '[0.1,0.2,...]' (casteable a ::vector)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# === CLI ===

def cmd_embed(args):
    if not args:
        print('Uso: nexo_embeddings.py embed "texto"', file=sys.stderr)
        sys.exit(1)
    vec = embed(" ".join(args))
    preview = ", ".join(f"{x:.4f}" for x in vec[:5])
    print(f"modelo:    {EMBED_MODEL}")
    print(f"dimensión: {len(vec)}")
    print(f"preview:   [{preview}, ...]")


def cmd_sql(args):
    if not args:
        print('Uso: nexo_embeddings.py sql "texto"', file=sys.stderr)
        sys.exit(1)
    print(f"'{to_pgvector(embed(' '.join(args)))}'::vector({EMBED_DIM})")


def cmd_check(args):
    """CHECKPOINT Bloque 1: dimensión, estabilidad y discriminación."""
    print(f"== Nexo · Bloque 1 · checkpoint embeddings ==")
    print(f"OLLAMA_URL={OLLAMA_URL}  modelo={EMBED_MODEL}  dim_esperada={EMBED_DIM}\n")

    t1 = "Kailash es la plataforma de pagos B2B de Yoltik."
    t2 = "Kailash es la plataforma de pagos B2B de Yoltik."   # idéntico -> estabilidad
    t3 = "El clima en la playa estuvo soleado todo el fin de semana."  # distinto -> discriminación

    v1 = embed(t1)
    v2 = embed(t2)
    v3 = embed(t3)

    ok_dim = (len(v1) == EMBED_DIM)
    sim_stable = cosine(v1, v2)      # esperado ~1.0 (determinista)
    sim_diff = cosine(v1, v3)        # esperado claramente menor

    ok_stable = sim_stable > 0.9999
    ok_discrim = sim_diff < sim_stable - 0.05

    print(f"[{'OK' if ok_dim else 'XX'}] dimensión = {len(v1)} (esperada {EMBED_DIM})")
    print(f"[{'OK' if ok_stable else 'XX'}] estabilidad (mismo texto): cosine = {sim_stable:.6f}  (esperado > 0.9999)")
    print(f"[{'OK' if ok_discrim else 'XX'}] discriminación (texto distinto): cosine = {sim_diff:.4f}  (debe ser < estabilidad)")

    green = ok_dim and ok_stable and ok_discrim
    print()
    if green:
        print("BLOQUE 1 VERDE [OK] — embeddings locales listos para poblar/consultar kb_chunks.")
        sys.exit(0)
    else:
        print("BLOQUE 1 EN ROJO [XX] — revisa el modelo, la dimension o la conexion a Ollama.", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    handlers = {"embed": cmd_embed, "sql": cmd_sql, "check": cmd_check}
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
