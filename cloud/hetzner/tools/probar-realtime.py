#!/usr/bin/env python3
"""probar-realtime.py — averigua QUÉ voz en tiempo real tiene tu cuenta de OpenAI (modelos y rutas), sin mostrar llaves.
Uso (en el servidor):  python3 ~/donna-deploy/cloud/hetzner/tools/probar-realtime.py
Solo consulta la lista de modelos y pide claves temporales de prueba (no se usan ni se guardan)."""
import os, sys, json, urllib.request, urllib.error
from pathlib import Path

OC = Path(os.environ.get("OPENCLAW_HOME", "/opt/openclaw"))


def key():
    k = os.environ.get("OPENAI_API_KEY", "")
    for f in (OC / "credentials" / "openai.env", OC / "openclaw.env"):
        if k:
            break
        try:
            for l in f.read_text().splitlines():
                if l.startswith("OPENAI_API_KEY="):
                    k = l.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return k


def call(method, url, k, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return 0, str(e)


def redact(txt: str) -> str:
    try:
        j = json.loads(txt)
    except ValueError:
        return txt[:220].replace("\n", " ")
    if isinstance(j, dict) and ("value" in j or "client_secret" in j):
        v = j.get("value") or (j.get("client_secret") or {}).get("value", "")
        return f"OK — clave temporal recibida ({len(str(v))} caracteres, no se muestra)"
    return json.dumps(j.get("error", j), ensure_ascii=False)[:260]


def main():
    k = key()
    if not k:
        sys.exit("✗ No encuentro OPENAI_API_KEY (credentials/openai.env)")
    st, txt = call("GET", "https://api.openai.com/v1/models", k)
    if st != 200:
        sys.exit(f"✗ OpenAI respondió {st}: {redact(txt)}")
    ids = sorted(m["id"] for m in json.loads(txt)["data"])
    rel = [i for i in ids if any(w in i for w in ("realtime", "transcribe", "tts", "translate", "audio"))]
    print("Modelos de voz/audio de tu cuenta:\n  " + "\n  ".join(rel or ["(ninguno)"]))
    print("\nClaves temporales (para voz en tiempo real desde el navegador):")
    pruebas = [
        ("POST", "/v1/realtime/client_secrets", {"session": {"type": "realtime", "model": "gpt-realtime"}}),
        ("POST", "/v1/realtime/client_secrets", {"session": {"type": "realtime", "model": "gpt-realtime-translate"}}),
        ("POST", "/v1/realtime/translations/client_secrets", {"session": {"model": "gpt-realtime-translate"}}),
        ("POST", "/v1/realtime/translations/client_secrets", {"session": {"model": "gpt-realtime-translate", "audio": {"output": {"language": "en"}}}}),
    ]
    for m, path, body in pruebas:
        s, t = call(m, "https://api.openai.com" + path, k, body)
        print(f"  {path}  modelo={body['session'].get('model')}  → HTTP {s}: {redact(t)}")


if __name__ == "__main__":
    main()
