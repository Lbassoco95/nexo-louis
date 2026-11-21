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
  {"cmd": "steps", "url": "https://...", "steps": [{"do": "click|fill|press|wait|read|screenshot|navigate", ...}]}
                                             # VARIOS pasos en la MISMA página y sesión (formularios, selección de función, etc.)
Cada comando suelto es un proceso nuevo: recuerda la última página (browser-last.json) y la vuelve a abrir antes de leer/hacer clic.
Los candados de browser_guard.py impiden pagar o teclear datos de tarjeta (el pago lo hace Polo).
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_guard as guard

STATE_DIR = Path(os.environ.get("BROWSER_STATE_DIR", "/opt/openclaw/state"))
STATE_FILE = STATE_DIR / "browser-context.json"
CACHE_DIR = STATE_DIR / "browser-cache"
HISTORY_FILE = STATE_DIR / "browser-history.jsonl"
LOG_FILE = Path(os.environ.get("BROWSER_LOG_FILE", "/opt/openclaw/logs/browser-runner.log"))
LAST_FILE = STATE_DIR / "browser-last.json"          # última página abierta: cada comando es un proceso nuevo y la restaura

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
        for p in (STATE_FILE, HISTORY_FILE, LAST_FILE):
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

        class Bloqueado(Exception):
            pass

        def texto_pagina(limite: int) -> str:
            try:
                t = page.evaluate("document.body ? document.body.innerText : ''")
            except Exception:
                return ""
            return t if len(t) <= limite else t[:limite] + f"\n... (truncado, total {len(t)} chars)"

        def guardar_ultima():
            try:
                if page.url and page.url.startswith("http"):
                    LAST_FILE.write_text(json.dumps({"url": page.url}))
                    os.chmod(LAST_FILE, 0o600)
            except Exception:
                pass

        def restaurar():
            """Cada comando es un proceso nuevo: vuelve a abrir la última página para poder leerla o hacer clic."""
            try:
                u = json.loads(LAST_FILE.read_text()).get("url", "")
            except Exception:
                u = ""
            if u.startswith("http"):
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    page.wait_for_timeout(1200)
                except Exception as e:
                    log(f"restaurar {u[:80]} falló: {e}")

        def navegar(url, wait_until="networkidle", extra_ms=0):
            if not str(url).startswith(("http://", "https://")):
                raise ValueError("URL debe empezar con http(s)://")
            if wait_until not in ("domcontentloaded", "load", "networkidle", "commit"):
                wait_until = "networkidle"
            try:
                page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)
            except PWTimeout:
                log(f"navigate timeout con wait_until={wait_until}, fallback a domcontentloaded")
                page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if int(extra_ms or 0) > 0:
                page.wait_for_timeout(min(int(extra_ms), 15000))
            title = page.title()
            _record_history(page.url, title)
            guardar_ultima()
            return {"ok": True, "url": page.url, "title": title, "wait_until": wait_until, "text": texto_pagina(2500)}

        def localizar(sel, by):
            if by == "text":
                return page.get_by_text(sel, exact=False).first
            if by == "role":
                return page.get_by_role(sel).first
            return page.locator(sel).first

        def hacer_clic(sel, by="css", timeout=DEFAULT_TIMEOUT_MS):
            loc = localizar(sel, by)
            try:
                info = loc.evaluate("e => [e.innerText||'', e.getAttribute('aria-label')||'', e.value||'', e.getAttribute('title')||'', e.getAttribute('href')||''].join(' | ')", timeout=timeout)
            except Exception:
                info = ""
            if guard.bloquea_clic(sel if by == "text" else "", info):
                raise Bloqueado(guard.MENSAJE)
            loc.click(timeout=timeout)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=4000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            guardar_ultima()
            return {"ok": True, "url": page.url, "msg": f"click on {by}={sel}", "text": texto_pagina(1500)}

        def llenar(sel, val, timeout=DEFAULT_TIMEOUT_MS):
            loc = page.locator(sel).first
            try:
                at = loc.evaluate("""e => ({name: e.name||'', id: e.id||'', autocomplete: e.getAttribute('autocomplete')||'', placeholder: e.placeholder||'',
                    aria_label: e.getAttribute('aria-label')||'', type: e.type||'', label: ((e.labels&&e.labels[0]&&e.labels[0].innerText)||(e.closest('label')&&e.closest('label').innerText)||'')})""", timeout=timeout)
            except Exception:
                at = {}
            if guard.bloquea_campo(selector=sel, **at):
                raise Bloqueado(guard.MENSAJE)
            loc.fill(val, timeout=timeout)
            return {"ok": True, "msg": f"fill {sel} ({len(val)} chars)"}

        def tecla(key):
            page.keyboard.press(key)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(600)
            guardar_ultima()
            return {"ok": True, "msg": f"pressed {key}", "text": texto_pagina(1500)}

        try:
            if action == "navigate":
                result = navegar(cmd.get("url") or "", cmd.get("wait_until", "networkidle"), cmd.get("wait_extra_ms", 0))

            elif action == "steps":
                if cmd.get("url"):
                    navegar(cmd["url"], cmd.get("wait_until", "networkidle"), cmd.get("wait_extra_ms", 0))
                else:
                    restaurar()
                pasos = []
                for st in (cmd.get("steps") or [])[:25]:
                    do = str(st.get("do", ""))
                    try:
                        if do == "navigate":
                            navegar(st.get("url", ""), st.get("wait_until", "networkidle"), st.get("wait_extra_ms", 0))
                        elif do == "click":
                            hacer_clic(st.get("selector", ""), st.get("by", "css"), int(st.get("timeout_ms", DEFAULT_TIMEOUT_MS)))
                        elif do == "fill":
                            llenar(st.get("selector", ""), str(st.get("value", "")), int(st.get("timeout_ms", DEFAULT_TIMEOUT_MS)))
                        elif do == "press":
                            tecla(st.get("key", "Enter"))
                        elif do in ("wait", "wait_for", "esperar"):
                            if st.get("selector"):
                                page.locator(st["selector"]).first.wait_for(timeout=int(st.get("timeout_ms", DEFAULT_TIMEOUT_MS)))
                            else:
                                page.wait_for_timeout(min(int(st.get("ms", 1000)), 10000))
                        elif do in ("read", "screenshot"):
                            pass
                        else:
                            raise ValueError(f"paso desconocido: {do}")
                        pasos.append({"do": do, "ok": True})
                    except Bloqueado as e:
                        pasos.append({"do": do, "ok": False, "bloqueado": True, "error": str(e)})
                        break
                    except Exception as e:
                        pasos.append({"do": do, "ok": False, "error": str(e)[:200]})
                        break
                guardar_ultima()
                bloq = any(x.get("bloqueado") for x in pasos)
                result = {"ok": all(x["ok"] for x in pasos), "pasos": pasos, "url": page.url, "title": page.title(), "text": texto_pagina(6000)}
                if bloq:
                    result["code"] = "bloqueado"
                    result["error"] = guard.MENSAJE
                if any(st.get("do") == "screenshot" for st in (cmd.get("steps") or [])):
                    png = page.screenshot(full_page=False, type="png")
                    from datetime import datetime
                    fn = CACHE_DIR / f"screenshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
                    fn.write_bytes(png)
                    result["saved_to"] = str(fn)

            elif action == "read":
                restaurar()
                title = page.title()
                body_text = texto_pagina(8000)
                result = {"ok": True, "url": page.url, "title": title, "text": body_text}

            elif action == "screenshot":
                restaurar()
                png = page.screenshot(full_page=False, type="png")
                b64 = base64.b64encode(png).decode("ascii")
                # Guarda copia local para debug
                from datetime import datetime
                fn = CACHE_DIR / f"screenshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
                fn.write_bytes(png)
                result = {"ok": True, "url": page.url, "image_base64": b64, "saved_to": str(fn), "media_type": "image/png"}

            elif action == "click":
                restaurar()
                result = hacer_clic(cmd.get("selector") or "", cmd.get("by", "css"), int(cmd.get("timeout_ms", DEFAULT_TIMEOUT_MS)))

            elif action == "fill":
                restaurar()
                result = llenar(cmd.get("selector") or "", cmd.get("value", ""), int(cmd.get("timeout_ms", DEFAULT_TIMEOUT_MS)))

            elif action == "press":
                restaurar()
                result = tecla(cmd.get("key", "Enter"))

            elif action == "wait_for":
                restaurar()
                sel = cmd.get("selector") or ""
                timeout = int(cmd.get("timeout_ms", DEFAULT_TIMEOUT_MS))
                page.locator(sel).first.wait_for(timeout=timeout)
                result = {"ok": True, "msg": f"wait_for {sel} OK"}

            elif action == "eval":
                restaurar()
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

        except Bloqueado as e:
            result = {"ok": False, "code": "bloqueado", "error": str(e), "url": page.url}
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
