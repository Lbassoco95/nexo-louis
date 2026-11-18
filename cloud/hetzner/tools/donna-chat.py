#!/usr/bin/env python3
"""donna-chat — habla con Donna (Nexo) desde la terminal. Solo stdlib, sin instalar nada.

Uso:
  donna-chat.py                    # conversación interactiva
  donna-chat.py "¿qué tengo hoy?"  # una sola pregunta y sale

Configuración (opcional):
  DONNA_URL     dirección del servidor        (por defecto https://donna.kawiil.mx;
                                               en el propio servidor: http://127.0.0.1:3000)
  DONNA_TOKEN   token de acceso               (si no está, lo pide la primera vez y lo guarda en
                                               ~/.config/donna/token con permisos 0600)
Comandos dentro de la conversación:  /nuevo  (olvida el contexto)   /salir  (o Ctrl+D)
"""
import os
import sys
import json
import time
import uuid
import getpass
import threading
import urllib.request
import urllib.error
from pathlib import Path

URL = os.environ.get("DONNA_URL", "https://donna.kawiil.mx").rstrip("/")
TOKEN_FILE = Path.home() / ".config" / "donna" / "token"
MAX_HIST = 16


def load_token() -> str:
    t = os.environ.get("DONNA_TOKEN", "").strip()
    if t:
        return t
    try:
        t = TOKEN_FILE.read_text().strip()
        if t:
            return t
    except OSError:
        pass
    t = getpass.getpass("Token de acceso (no se muestra; se guarda solo en este equipo): ").strip()
    if t:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t)
    return t


def ask(token: str, history: list, text: str) -> str:
    """Envía el mensaje y va imprimiendo la respuesta conforme llega (SSE). Devuelve el texto completo."""
    body = json.dumps({"rid": uuid.uuid4().hex, "messages": history + [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(URL + "/v1/app/chat/stream", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + token})
    t0, started = time.time(), threading.Event()

    def spinner():  # "Pensando… N s" mientras no llega texto
        while not started.is_set():
            sys.stderr.write(f"\r\033[2mPensando… {int(time.time() - t0)} s\033[0m")
            sys.stderr.flush()
            time.sleep(0.5)
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    th = threading.Thread(target=spinner, daemon=True)
    th.start()
    out, ev, err, model = [], None, None, ""
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip() or "{}")
                    except ValueError:
                        continue
                    if ev == "delta":
                        if not started.is_set():  # detener y borrar el "Pensando…" ANTES de imprimir
                            started.set()
                            th.join(timeout=2)
                        out.append(d.get("d", ""))
                        sys.stdout.write(d.get("d", ""))
                        sys.stdout.flush()
                    elif ev == "fail":
                        err = d.get("error", "error")
                    elif ev == "done":
                        model = d.get("model", "")
    except urllib.error.HTTPError as e:
        started.set()
        if e.code == 401:
            sys.exit("\nToken inválido. Bórralo con:  rm ~/.config/donna/token  y vuelve a intentar.")
        try:
            msg = json.loads(e.read().decode()).get("error", "")
        except Exception:
            msg = ""
        sys.exit(f"\nError {e.code}: {msg}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        started.set()
        sys.exit(f"\nNo pude conectar con {URL}: {getattr(e, 'reason', e)}")
    finally:
        started.set()
        th.join(timeout=1)
    if err:
        sys.stdout.write(f"\n⚠️  {err}")
    sys.stdout.write("\n")
    if model:  # qué ruta respondió (deepseek, haiku-fallback, deepseek-fallback, chat-error…)
        sys.stderr.write(f"\033[2m(modelo: {model})\033[0m\n")
    return "".join(out)


def main():
    token = load_token()
    if not token:
        sys.exit("Sin token.")
    if len(sys.argv) > 1:  # una sola pregunta
        ask(token, [], " ".join(sys.argv[1:]))
        return
    print(f"Donna · {URL}   (/nuevo = olvidar contexto · /salir o Ctrl+D = salir)\n")
    history = []
    while True:
        try:
            text = input("\033[1mTú ›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/salir", "/exit", "/q"):
            break
        if text == "/nuevo":
            history = []
            print("(contexto borrado)\n")
            continue
        sys.stdout.write("\033[1mDonna ›\033[0m ")
        sys.stdout.flush()
        answer = ask(token, history, text)
        print()
        if answer:
            history += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
            history = history[-MAX_HIST:]


if __name__ == "__main__":
    main()
