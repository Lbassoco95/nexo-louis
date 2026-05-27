#!/usr/bin/env python3
"""
OAuth Authorization Code + PKCE para tenant Kawiil.
Levanta servidor local, abre Safari, intercambia code por tokens, guarda en disco.
"""

import os
import sys
import json
import time
import hashlib
import base64
import secrets
import urllib.parse
import urllib.request
import urllib.error
import http.server
import socketserver
import threading
import subprocess
from pathlib import Path

HOME = Path.home()
CREDS_FILE = HOME / ".openclaw" / "credentials" / "m365-kawiil.env"
TOKENS_FILE = HOME / ".openclaw" / "credentials" / "m365-kawiil-tokens.json"
PORT = 8765


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


def gen_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def main():
    env = load_env(CREDS_FILE)
    client_id = env.get("M365_KAWIIL_CLIENT_ID")
    tenant_id = env.get("M365_KAWIIL_TENANT_ID")
    redirect = env.get("M365_KAWIIL_REDIRECT_URI", f"http://localhost:{PORT}/callback")

    if not client_id or not tenant_id:
        print(f"ERROR: faltan credentials en {CREDS_FILE}", file=sys.stderr)
        sys.exit(1)

    verifier, challenge = gen_pkce()
    state = secrets.token_hex(16)

    scopes = "Mail.ReadWrite Mail.Send Calendars.ReadWrite User.Read offline_access"
    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_mode": "query",
        "prompt": "select_account",
    }
    auth_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        + urllib.parse.urlencode(auth_params)
    )

    # ===== Servidor local =====
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            for k, v in qs.items():
                received[k] = v[0] if v else ""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<!DOCTYPE html><html><head><title>Listo</title></head>"
                b"<body style='font-family:sans-serif;padding:40px;'>"
                b"<h2>Autorizacion recibida</h2>"
                b"<p>Ya puedes cerrar esta pestana y volver a la terminal.</p>"
                b"</body></html>"
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    print(f"==> Servidor local en localhost:{PORT}, esperando callback OAuth...")
    print(f"==> Abriendo Safari...")
    print("    Autentica con leo.bassoco@kawiil.mx y aprueba los permisos.\n")

    server = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Abrir Safari (open en macOS abre URL en navegador default)
    subprocess.run(["open", auth_url], check=False)

    print("    Esperando que completes login en Safari...")
    # Esperar hasta 5 min
    deadline = time.time() + 300
    while not received and time.time() < deadline:
        time.sleep(0.5)

    server.shutdown()

    if not received:
        print("ERROR: timeout esperando callback (5 min). ¿Cerraste Safari?", file=sys.stderr)
        sys.exit(1)

    if "error" in received:
        print(f"ERROR de Microsoft: {received.get('error')}: {received.get('error_description', '')}", file=sys.stderr)
        sys.exit(1)

    if received.get("state") != state:
        print(f"ERROR: state no coincide (recibido={received.get('state')!r}, esperado={state!r})", file=sys.stderr)
        sys.exit(1)

    code = received.get("code")
    if not code:
        print("ERROR: no recibí code en callback", file=sys.stderr)
        print(f"Callback contenía: {received}", file=sys.stderr)
        sys.exit(1)

    print(f"==> Code recibido. Intercambiando por tokens...")

    # ===== Intercambiar code por tokens =====
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "scope": scopes,
        "code": code,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()

    req = urllib.request.Request(token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR HTTP {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)

    if "access_token" not in data:
        print(f"ERROR: respuesta sin access_token: {data}", file=sys.stderr)
        sys.exit(1)

    # Guardar con timestamp de emisión
    data["__issued_at"] = int(time.time())
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(data))
    TOKENS_FILE.chmod(0o600)

    expires_in = data.get("expires_in", "?")
    has_refresh = "refresh_token" in data
    print(f"\nOK. Tokens guardados en {TOKENS_FILE}")
    print(f"    access_token: válido por {expires_in}s")
    print(f"    refresh_token: {'sí presente (auto-rotación)' if has_refresh else 'NO PRESENTE — ojo'}")
    print(f"\nYa puedes correr los scripts m365-leer-correos.sh, m365-calendario.sh, m365-mandar-correo.sh")


if __name__ == "__main__":
    main()
