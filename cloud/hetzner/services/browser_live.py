#!/usr/bin/env python3
"""browser_live.py — navegador de Donna que Polo VE y maneja desde la app (pestaña «Navegador»).

Para qué: iniciar sesión UNA vez (Cinemex, OpenTable, etc.) escribiendo tú mismo usuario/contraseña/código en una página real, y que esa sesión
quede guardada y Donna la retome después. Donna nunca ve ni guarda tu contraseña: solo se guardan las cookies de la sesión en el mismo archivo
(`browser-context.json`, modo 0600) que usa `browser_runner.py`, así que lo que abres aquí también lo usa Donna al buscar y preparar compras.

Diseño: un solo Chromium persistente dentro del gateway, en un hilo propio (Playwright sync no se comparte entre hilos). La app pide
fotogramas JPEG y envía toques/teclas. Se cierra solo tras 10 min sin uso. Solo https hacia sitios públicos (nada de red interna).
"""
import base64
import ipaddress
import json
import os
import queue
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

STATE_DIR = Path(os.environ.get("BROWSER_STATE_DIR", "/opt/openclaw/state"))
STATE_FILE = STATE_DIR / "browser-context.json"
LAST_FILE = STATE_DIR / "browser-last.json"
IDLE_SECONDS = int(os.environ.get("BROWSER_LIVE_IDLE", "600"))
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.114 Safari/537.36"

# Sitios sugeridos en el selector (se puede escribir cualquier otro https público).
SITIOS = [
    {"id": "cinemex", "nombre": "Cinemex", "url": "https://cinemex.com/"},
    {"id": "cinepolis", "nombre": "Cinépolis", "url": "https://cinepolis.com/"},
    {"id": "cinemark", "nombre": "Cinemark", "url": "https://www.cinemark.com.mx/"},
    {"id": "opentable", "nombre": "OpenTable", "url": "https://www.opentable.com.mx/"},
    {"id": "ticketmaster", "nombre": "Ticketmaster", "url": "https://www.ticketmaster.com.mx/"},
    {"id": "boletia", "nombre": "Boletia", "url": "https://www.boletia.com/"},
]


def url_permitida(url: str) -> str:
    """Devuelve la URL normalizada o lanza ValueError. Solo https (http solo con BROWSER_LIVE_ALLOW_LOCAL=1, para pruebas) y nunca red privada."""
    u = (url or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    p = urlparse(u)
    local_ok = os.environ.get("BROWSER_LIVE_ALLOW_LOCAL") == "1"
    if p.scheme not in (("https", "http") if local_ok else ("https",)) or not p.hostname:
        raise ValueError("Solo se abren páginas https.")
    if local_ok:
        return u
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except OSError:
        raise ValueError("No encuentro ese sitio.")
    for inf in infos:
        ip = ipaddress.ip_address(inf[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Ese destino no está permitido.")
    return u


class _Live:
    def __init__(self):
        self.q = queue.Queue()
        self.t = None
        self.lock = threading.Lock()
        self.last_use = 0.0

    def _ensure_thread(self):
        with self.lock:
            if not (self.t and self.t.is_alive()):
                self.t = threading.Thread(target=self._loop, daemon=True, name="browser-live")
                self.t.start()

    def call(self, op: str, timeout: float = 45.0, **kw) -> dict:
        self._ensure_thread()
        box = {"ev": threading.Event(), "res": None}
        self.q.put((op, kw, box))
        if not box["ev"].wait(timeout):
            return {"ok": False, "error": "El navegador tardó demasiado."}
        return box["res"]

    # ---- hilo dueño de Playwright ----
    def _loop(self):
        pw = browser = ctx = page = None
        vw, vh = 820, 1100

        def guardar():
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                ctx.storage_state(path=str(STATE_FILE))
                os.chmod(STATE_FILE, 0o600)
                if page.url.startswith("http"):
                    LAST_FILE.write_text(json.dumps({"url": page.url}))
                    os.chmod(LAST_FILE, 0o600)
            except Exception:
                pass

        def cerrar(guardando=True):
            nonlocal pw, browser, ctx, page
            try:
                if guardando and ctx and page:
                    guardar()
            except Exception:
                pass
            for o in (browser,):
                try:
                    o and o.close()
                except Exception:
                    pass
            try:
                pw and pw.stop()
            except Exception:
                pass
            pw = browser = ctx = page = None

        def abrir_navegador(w, h):
            nonlocal pw, browser, ctx, page, vw, vh
            from playwright.sync_api import sync_playwright
            vw, vh = w, h
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                                                              "--disable-blink-features=AutomationControlled", "--lang=es-MX"])
            ctx = browser.new_context(storage_state=str(STATE_FILE) if STATE_FILE.exists() else None, user_agent=USER_AGENT,
                                      viewport={"width": vw, "height": vh}, locale="es-MX", timezone_id="America/Mexico_City",
                                      extra_http_headers={"Accept-Language": "es-MX,es;q=0.9,en;q=0.8"})
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome=window.chrome||{runtime:{}};")
            page = ctx.new_page()

        def estado(extra=None):
            d = {"ok": True, "abierto": bool(page), "url": page.url if page else "", "title": "", "w": vw, "h": vh}
            try:
                d["title"] = page.title() if page else ""
            except Exception:
                pass
            if extra:
                d.update(extra)
            return d

        while True:
            try:
                op, kw, box = self.q.get(timeout=5)
            except queue.Empty:
                if page and time.time() - self.last_use > IDLE_SECONDS:
                    cerrar()
                continue
            self.last_use = time.time()
            try:
                if op == "status":
                    res = estado()
                elif op == "close":
                    cerrar()
                    res = {"ok": True, "abierto": False}
                elif op == "sesiones":
                    try:
                        cookies = json.loads(STATE_FILE.read_text()).get("cookies", [])
                    except Exception:
                        cookies = []
                    dom = {}
                    for c in cookies:
                        d = (c.get("domain") or "").lstrip(".")
                        parts = d.split(".")
                        if all(x.isdigit() for x in parts):
                            base = d
                        else:
                            base = ".".join(parts[-3:] if len(parts) > 2 and parts[-2] in ("com", "org", "net", "gob") else parts[-2:])
                        dom[base] = dom.get(base, 0) + 1
                    res = {"ok": True, "sesiones": [{"sitio": k, "cookies": v} for k, v in sorted(dom.items())]}
                elif op == "olvidar":
                    sitio = str(kw.get("sitio", "")).lower().strip().lstrip(".")
                    if not sitio:
                        raise ValueError("Falta el sitio.")
                    cerrar(False)                # sin guardar: si no, la sesión viva volvería a escribir las cookies
                    try:
                        st = json.loads(STATE_FILE.read_text())
                    except Exception:
                        st = {"cookies": [], "origins": []}
                    st["cookies"] = [c for c in st.get("cookies", []) if not ((c.get("domain") or "").lstrip(".").endswith(sitio))]
                    st["origins"] = [o for o in st.get("origins", []) if sitio not in o.get("origin", "")]
                    STATE_FILE.write_text(json.dumps(st))
                    os.chmod(STATE_FILE, 0o600)
                    res = {"ok": True}
                elif op == "open":
                    url = url_permitida(kw.get("url", ""))
                    w = max(360, min(1280, int(kw.get("w") or 820)))
                    h = max(480, min(1600, int(kw.get("h") or 1100)))
                    if page and (abs(w - vw) > 40 or abs(h - vh) > 40):
                        cerrar()
                    if not page:
                        abrir_navegador(w, h)
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as e:
                        if "Timeout" not in str(e):
                            raise
                    page.wait_for_timeout(1200)
                    guardar()
                    res = estado()
                elif not page:
                    res = {"ok": True, "abierto": False}
                elif op == "frame":
                    png = page.screenshot(type="jpeg", quality=55)
                    res = estado({"img": base64.b64encode(png).decode("ascii")})
                elif op == "click":
                    page.mouse.click(float(kw["x"]), float(kw["y"]))
                    page.wait_for_timeout(500)
                    guardar()
                    res = estado()
                elif op == "type":
                    txt = str(kw.get("text", ""))[:500]
                    if txt:
                        page.keyboard.insert_text(txt)
                    res = estado()
                elif op == "key":
                    k = str(kw.get("key", ""))
                    if k in ("Enter", "Backspace", "Tab", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Delete", "Space"):
                        page.keyboard.press("Space" if k == "Space" else k)
                        page.wait_for_timeout(300)
                        if k == "Enter":
                            guardar()
                    res = estado()
                elif op == "scroll":
                    page.mouse.wheel(0, max(-2000, min(2000, float(kw.get("dy", 0)))))
                    page.wait_for_timeout(150)
                    res = estado()
                elif op == "back":
                    page.go_back(wait_until="domcontentloaded", timeout=15000)
                    res = estado()
                elif op == "guardar":
                    guardar()
                    res = estado()
                else:
                    res = {"ok": False, "error": "operación desconocida"}
            except ImportError:
                res = {"ok": False, "error": "Falta Playwright en el servidor (scripts/install-playwright.sh)."}
            except ValueError as e:
                res = {"ok": False, "error": str(e)}
            except Exception as e:
                res = {"ok": False, "error": ("Error del navegador: " + str(e))[:200]}
            box["res"] = res
            box["ev"].set()


LIVE = _Live()


def operar(body: dict) -> dict:
    """Punto único para el gateway. Nunca se registran textos escritos (pueden ser contraseñas)."""
    op = str(body.get("op", "status"))
    if op == "sitios":
        return {"ok": True, "sitios": SITIOS}
    kw = {k: body.get(k) for k in ("url", "w", "h", "x", "y", "text", "key", "dy", "sitio") if k in body}
    return LIVE.call(op, **kw)
