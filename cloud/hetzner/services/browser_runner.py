#!/usr/bin/env python3
"""
browser_runner.py — Headless Chromium runner controlado por subprocess.

Diseño: un proceso separado por comando — recibe JSON por stdin con el comando
a ejecutar y devuelve JSON por stdout con el resultado. Persiste cookies/
localStorage entre llamadas usando `storage_state` de Playwright en disco.

Esto evita meter Playwright como dependencia en louis_core (donde sería pesado
y solo necesario a veces). Louis lo invoca con `python3 browser_runner.py` y
le pipea el JSON del comando.

Comandos soportados:
  {"cmd": "navigate", "url": "https://..."}
  {"cmd": "read"}                            # Devuelve texto visible + URL actual
  {"cmd": "screenshot"}                      # PNG base64
  {"cmd": "click", "selector": "...", "by": "css|text|role"}
  {"cmd": "fill", "selector": "...", "value": "..."}
  {"cmd": "press", "key": "Enter|Tab|..."}
  {"cmd": "wait_for", "selector": "...", "timeout_ms": 5000}
  {"cmd": "eval", "script": "..."}           # Devuelve resultado JSON-serializable
  {"cmd": "history"}                         # Últimas N páginas visitadas
  {"cmd": "reset_storage"}                   # Borra cookies/localStorage del perfil

Storage state vive en /opt/openclaw/state/browser-context.json (mode 0600).
Screenshots/downloads en /opt/openclaw/state/browser-cache/ (mode 0700).
"""

import sys
import json
import os
import base64
import traceback
from pathlib import Path

STATE_DIR = Path("/opt/openclaw/state")
STATE_FILE = STATE_DIR / "browser-context.json"
CACHE_DIR = STATE_DIR / "browser-cache"
HISTORY_FILE = STATE_DIR / "browser-history.jsonl"
LOG_FILE = Path("/opt/openclaw/logs/browser-runner.log")

DEFAULT_TIMEOUT_MS = 15_000
NAV_TIMEOUT_MS = 30_000
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.114 Safari/537.36"


def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_FILE.open("a") as f:
            from datetime import datetime
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def out(payload: dict):
    print(json.dumps(payload, ensure_ascii=False))


def err_out(msg: str, code: str = "error", extra: dict | None = None):
    p = {"ok": False, "code": code, "error": msg}
    if extra:
        p.update(extra)
    out(p)
    sys.exit(1)


def _record_history(url: str, title: str):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    try:
        with HISTORY_FILE.open("a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "url": url, "title": title}) + "\n")
    except Exception:
        pass


def main():
    raw = sys.stdin.read()
    try:
        cmd = json.loads(raw)
    except Exception as e:
        err_out(f"JSON inválido: {e}")
    action = cmd.get("cmd")
    if not action:
        err_out("Falta 'cmd' en JSON")

    # History command no necesita Playwright
    if action == "history":
        n = int(cmd.get("n", 20))
        if not HISTORY_FILE.exists():
            out({"ok": True, "items": []})
            return
        items = []
        for line in HISTORY_FILE.read_text().splitlines()[-n:]:
            try:
                items.append(json.loads(line))
            except Exception:
                pass
        out({"ok": True, "items": items})
        return

    if action == "reset_storage":
        for p in (STATE_FILE, HISTORY_FILE):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        out({"ok": True, "msg": "Storage borrado — próxima sesión empieza limpia."})
        return

    # Importa Playwright sólo cuando se necesita
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # type: ignore
    except ImportError:
        err_out("playwright no instalado. En Hetzner: `pip3 install --break-system-packages playwright && playwright install-deps chromium && playwright install chromium`",
                code="dep_missing")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    storage_state = str(STATE_FILE) if STATE_FILE.exists() else None

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                # --disable-blink-features=AutomationControlled evita que sitios como
                # dof.gob.mx detecten el navegador como automatizado y devuelvan página
                # en blanco. El resto son flags estándar para correr en VPS sin GPU.
                args=[
                    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--lang=es-MX",
                ],
            )
        except Exception as e:
            err_out(f"No pude lanzar Chromium: {e}", code="launch_failed")

        try:
            ctx = browser.new_context(
                storage_state=storage_state,
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
                accept_downloads=True,
                locale="es-MX",
                timezone_id="America/Mexico_City",
                # Headers realistas que esperan los portales gubernamentales MX.
                extra_http_headers={
                    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                },
            )
        except Exception as e:
            browser.close()
            err_out(f"new_context falló (storage_state corrupto?): {e}", code="context_failed")

        # Quita las señales de automatización que revisan los anti-bot (navigator.webdriver,
        # window.chrome, plugins vacíos). Sin esto, dof.gob.mx y similares sirven 0 bytes.
        try:
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
                "Object.defineProperty(navigator,'languages',{get:()=>['es-MX','es','en']});"
                "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            )
        except Exception:
            pass

        page = ctx.new_page()
        result = {}

        try:
            if action == "navigate":
                url = cmd.get("url") or ""
                if not url.startswith(("http://", "https://")):
                    raise ValueError("URL debe empezar con http(s)://")
                # SPAs (React/Vue/Angular) necesitan que JS termine antes de ser legibles.
                # wait_until="networkidle" espera a que no haya tráfico de red por 500ms.
                # Si el caller pasa wait_until="domcontentloaded", respetamos (más rápido).
                wait_until = cmd.get("wait_until", "networkidle")
                if wait_until not in ("domcontentloaded", "load", "networkidle", "commit"):
                    wait_until = "networkidle"
                try:
                    page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)
                except PWTimeout:
                    # Fallback: si networkidle nunca llega (sitios con polling constante),
                    # cae a domcontentloaded para no fallar.
                    log(f"navigate timeout con wait_until={wait_until}, fallback a domcontentloaded")
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                # Espera extra opcional para SPAs muy lentas
                extra_ms = int(cmd.get("wait_extra_ms", 0))
                if extra_ms > 0:
                    page.wait_for_timeout(min(extra_ms, 15000))
                title = page.title()
                _record_history(page.url, title)
                result = {"ok": True, "url": page.url, "title": title, "wait_until": wait_until}

            elif action == "read":
                # Si Polo no navegó antes, abrimos about:blank y devolvemos vacío
                title = page.title()
                body_text = page.evaluate("document.body ? document.body.innerText : ''")
                # Truncar texto enorme
                if len(body_text) > 8000:
                    body_text = body_text[:8000] + f"\n... (truncado, total {len(body_text)} chars)"
                result = {"ok": True, "url": page.url, "title": title, "text": body_text}

            elif action == "screenshot":
                png = page.screenshot(full_page=False, type="png")
                b64 = base64.b64encode(png).decode("ascii")
                # Guarda copia local para debug
                from datetime import datetime
                fn = CACHE_DIR / f"screenshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
                fn.write_bytes(png)
                result = {"ok": True, "url": page.url, "image_base64": b64, "saved_to": str(fn), "media_type": "image/png"}

            elif action == "click":
                sel = cmd.get("selector") or ""
                by = cmd.get("by", "css")
                timeout = int(cmd.get("timeout_ms", DEFAULT_TIMEOUT_MS))
                if by == "text":
                    page.get_by_text(sel, exact=False).first.click(timeout=timeout)
                elif by == "role":
                    role_name = sel
                    page.get_by_role(role_name).first.click(timeout=timeout)
                else:
                    page.locator(sel).first.click(timeout=timeout)
                result = {"ok": True, "url": page.url, "msg": f"click on {by}={sel}"}

            elif action == "fill":
                sel = cmd.get("selector") or ""
                val = cmd.get("value", "")
                timeout = int(cmd.get("timeout_ms", DEFAULT_TIMEOUT_MS))
                page.locator(sel).first.fill(val, timeout=timeout)
                result = {"ok": True, "msg": f"fill {sel} ({len(val)} chars)"}

            elif action == "press":
                key = cmd.get("key", "Enter")
                page.keyboard.press(key)
                result = {"ok": True, "msg": f"pressed {key}"}

            elif action == "wait_for":
                sel = cmd.get("selector") or ""
                timeout = int(cmd.get("timeout_ms", DEFAULT_TIMEOUT_MS))
                page.locator(sel).first.wait_for(timeout=timeout)
                result = {"ok": True, "msg": f"wait_for {sel} OK"}

            elif action == "eval":
                script = cmd.get("script", "")
                value = page.evaluate(script)
                result = {"ok": True, "value": value}

            else:
                browser.close()
                err_out(f"Comando desconocido: {action}", code="unknown_cmd")

            # Persiste storage_state después de cada comando exitoso
            try:
                ctx.storage_state(path=str(STATE_FILE))
                os.chmod(STATE_FILE, 0o600)
            except Exception as e:
                log(f"persist storage_state falló: {e}")

        except PWTimeout as e:
            result = {"ok": False, "code": "timeout", "error": f"Timeout: {e}", "url": page.url}
        except Exception as e:
            result = {"ok": False, "code": "exception", "error": str(e), "trace": traceback.format_exc()[-800:], "url": page.url}
        finally:
            try:
                browser.close()
            except Exception:
                pass

        log(f"{action} → ok={result.get('ok')} url={result.get('url', '')[:80]}")
        out(result)


if __name__ == "__main__":
    main()
