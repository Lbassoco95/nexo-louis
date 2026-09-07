#!/usr/bin/env python3
"""gamma_gen.py — genera diseños con la API de Gamma (v1.0) desde el servidor.

Por qué: Donna intentaba usar el SITIO gamma.app (SPA con JS) desde el servidor
y Gamma lo bloquea. La forma correcta es la API REST v1.0, que sí funciona
server-side. Esto genera presentaciones / documentos / posts sociales y
devuelve la URL de Gamma + el export (png/pdf/pptx).

Uso:
  gamma_gen.py "texto o tema" [--format social|presentation|document|webpage]
               [--export png|pdf|pptx] [--cards N] [--dim 1x1|4x5|9x16]
               [--instr "instrucciones de marca/estilo"] [--telegram]

La API key se lee de GAMMA_API_KEY o de /opt/openclaw/credentials/gamma.env.
Nunca se imprime la key.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

API_BASE = "https://public-api.gamma.app/v1.0/generations"
CREDS_TG = os.environ.get("TELEGRAM_CREDS", "/opt/openclaw/credentials/telegram.env")
CREDS_GAMMA = os.environ.get("GAMMA_CREDS", "/opt/openclaw/credentials/gamma.env")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _read_env(path, key):
    p = Path(path)
    if p.exists():
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return None


def api_key():
    return os.environ.get("GAMMA_API_KEY") or _read_env(CREDS_GAMMA, "GAMMA_API_KEY")


def _req(method, url, key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json",
        # Cloudflare (error 1010) veta el UA de urllib; usamos uno de navegador.
        "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", "replace")[:500]
        return e.code, {"error": f"HTTP {e.code}", "detalle": detalle}
    except Exception as e:
        return 0, {"error": str(e)}


def listar_themes():
    key = api_key()
    if not key:
        return {"ok": False, "msg": f"Falta GAMMA_API_KEY (env o {CREDS_GAMMA})"}
    st, r = _req("GET", "https://public-api.gamma.app/v1.0/themes", key)
    return {"ok": st == 200, "status": st, "raw": r}


def generar(texto, fmt, export, cards, dim, instr, theme="", img_style=""):
    key = api_key()
    if not key:
        return {"ok": False, "msg": f"Falta GAMMA_API_KEY (env o {CREDS_GAMMA})"}
    body = {
        "inputText": texto,
        "format": fmt,
        "textMode": "generate",
        "numCards": cards,
        "exportAs": export,
    }
    if instr:
        body["additionalInstructions"] = instr
    if theme:
        body["themeName"] = theme          # nombre o ID del theme de marca (logo+colores)
    if img_style:
        body["imageOptions"] = {"source": "aiGenerated", "style": img_style}
    if fmt == "social" and dim:
        body["cardOptions"] = {"dimensions": dim}
    st, resp = _req("POST", API_BASE, key, body)
    if st not in (200, 201) or "generationId" not in resp:
        return {"ok": False, "msg": f"Error al crear (status {st}): {json.dumps(resp, ensure_ascii=False)[:400]}"}
    gid = resp["generationId"]
    print(f"generationId={gid} — generando…", file=sys.stderr)
    # poll hasta ~4 min
    for _ in range(48):
        time.sleep(5)
        st, r = _req("GET", f"{API_BASE}/{gid}", key)
        status = (r.get("status") or "").lower()
        if status in ("completed", "succeeded", "done"):
            return {"ok": True, "gammaUrl": r.get("gammaUrl"), "exportUrl": r.get("exportUrl"),
                    "gammaId": r.get("gammaId") or gid, "raw": r}
        if status in ("failed", "error"):
            return {"ok": False, "msg": f"Generación falló: {json.dumps(r, ensure_ascii=False)[:400]}"}
    return {"ok": False, "msg": "Timeout esperando la generación (>4 min)"}


def _tg_creds():
    tok = _read_env(CREDS_TG, "TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = _read_env(CREDS_TG, "TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    return tok, chat


def telegram(texto):
    tok, chat = _tg_creds()
    if not tok or not chat:
        return False
    body = json.dumps({"chat_id": chat, "text": texto[:3900], "parse_mode": "HTML",
                       "disable_web_page_preview": False}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read(); return True
    except Exception:
        return False


def _descargar(url):
    """Descarga el export (URL firmada que expira) AHORA que está fresca."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def telegram_doc(url, filename, caption):
    """Descarga el archivo y lo manda como DOCUMENTO a Telegram (no link que caduca)."""
    tok, chat = _tg_creds()
    if not tok or not chat:
        return False
    try:
        content = _descargar(url)
    except Exception as e:
        return telegram(caption + f"\n⚠️ No pude bajar el archivo ({e}); link (puede caducar): {url}")
    import uuid as _u
    b = "----L" + _u.uuid4().hex
    mime = "image/png" if filename.lower().endswith(".png") else "application/octet-stream"
    parts = []
    for n, v in (("chat_id", str(chat)), ("caption", caption), ("parse_mode", "HTML")):
        parts += [f"--{b}".encode(), f'Content-Disposition: form-data; name="{n}"'.encode(),
                  b"", v.encode("utf-8")]
    parts += [f"--{b}".encode(),
              f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode(),
              f"Content-Type: {mime}".encode(), b"", content, f"--{b}--".encode(), b""]
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendDocument",
                                 data=b"\r\n".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        urllib.request.urlopen(req, timeout=60).read(); return True
    except Exception as e:
        return telegram(caption + f"\n⚠️ No pude adjuntar el archivo ({e}); link: {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("texto", nargs="?", default="")
    ap.add_argument("--format", default="social", choices=["social", "presentation", "document", "webpage"])
    ap.add_argument("--export", default="png", choices=["png", "pdf", "pptx"])
    ap.add_argument("--cards", type=int, default=1)
    ap.add_argument("--dim", default="1x1", choices=["1x1", "4x5", "9x16"])
    ap.add_argument("--instr", default="")
    ap.add_argument("--theme", default="", help="nombre o ID del theme de marca (logo+colores)")
    ap.add_argument("--img-style", dest="img_style", default="", help="estilo de las imágenes IA")
    ap.add_argument("--list-themes", action="store_true", help="lista los themes del workspace y sale")
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    if a.list_themes:
        r = listar_themes()
        print(json.dumps(r.get("raw", {}), ensure_ascii=False, indent=2)[:2000])
        return 0
    res = generar(a.texto, a.format, a.export, a.cards, a.dim, a.instr, a.theme, a.img_style)
    if not res["ok"]:
        print("❌ " + res["msg"])
        return 1
    gamma_url = res.get("gammaUrl")
    export_url = res.get("exportUrl")
    print("✅ OK")
    print("gammaUrl:", gamma_url)
    print("exportUrl:", export_url)
    if a.telegram:
        cap = f"🎨 <b>Gamma listo</b>\nVer/editar: {gamma_url}"
        if export_url:
            # descarga el archivo AHORA (el link firmado caduca) y lo adjunta
            fn = f"gamma_{res.get('gammaId','post')}.{a.export}"
            telegram_doc(export_url, fn, cap)
        else:
            telegram(cap)
        print("Enviado a Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
