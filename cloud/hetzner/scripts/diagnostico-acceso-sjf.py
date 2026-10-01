#!/usr/bin/env python3
"""
diagnostico-acceso-sjf.py — ¿por qué dejamos de obtener tesis, y por dónde sí se puede?

Contexto. Desde el 2026-08-28 el acervo dejó de crecer. El log del harvester dice
`403 (bloqueo WAF)` en cadena. Hay DOS hipótesis que no se pueden distinguir desde el
log, y la diferencia cambia por completo qué hay que hacer:

  A) Nos bloquearon por ritmo (nuestra IP, temporal)   → se resuelve bajando el ritmo.
  B) El endpoint que raspábamos está detrás de un reto  → no se resuelve con paciencia;
     de bot (Imperva/Incapsula) para CUALQUIER cliente     hay que usar la vía oficial.
     automatizado

Y hay una tercera cosa que nadie había comprobado: la SCJN publica un API DE DATOS
ABIERTOS en el Repositorio del Bicentenario, con el catálogo completo de ids. Si ése
responde, el problema deja de ser "cómo raspamos" y pasa a ser "cómo ingerimos".

Este script NO escribe nada. Solo mide y reporta.

Uso:
    python3 diagnostico-acceso-sjf.py              # diagnóstico completo
    python3 diagnostico-acceso-sjf.py --json       # salida para máquina
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 25

# El endpoint que veníamos raspando (el que da 403).
RASPADO = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis"
RASPADO_REFERER = "https://sjf2.scjn.gob.mx/"

# El API de datos abiertos del Repositorio del Bicentenario. Es la vía que la SCJN
# publica para esto; no es un endpoint interno ni uno que haya que descubrir.
OFICIAL = "https://bicentenario.scjn.gob.mx/repositorio-scjn/api/v1"

UA_NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
UA_HONESTO = os.environ.get(
    "SJF_USER_AGENT", "KawiilLegalBot/1.0 (+https://kawiil.mx) investigacion juridica")

VERDE, ROJO, AMA, AZUL, GRIS, FIN = (
    "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[1;36m", "\033[0;90m", "\033[0m")


def _pedir(url: str, ua: str, accept: str = "application/json",
           referer: str = "") -> dict:
    """Una petición. Devuelve SIEMPRE un dict describiendo qué pasó, sin lanzar."""
    cab = {"User-Agent": ua, "Accept": accept}
    if referer:
        cab["Referer"] = referer
    r = {"url": url, "ua": ua[:34], "status": None, "tipo": "", "bytes": 0,
         "incapsula": False, "error": "", "ms": 0, "cuerpo": ""}
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=cab),
                                      timeout=TIMEOUT)
        cuerpo = resp.read(60000)
        r.update(status=resp.status, tipo=resp.headers.get("Content-Type", ""),
                 bytes=len(cuerpo), cuerpo=cuerpo[:400].decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        cuerpo = b""
        try:
            cuerpo = e.read(60000)
        except Exception:
            pass
        r.update(status=e.code, tipo=e.headers.get("Content-Type", "") if e.headers else "",
                 bytes=len(cuerpo), cuerpo=cuerpo[:400].decode("utf-8", "replace"))
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        r["error"] = f"{type(e).__name__}: {e}"
    r["ms"] = int((time.time() - t0) * 1000)
    # La huella de Imperva/Incapsula: HTML donde debía ir JSON, con su marcador.
    marca = (r["cuerpo"] or "").lower()
    r["incapsula"] = ("incapsula" in marca or "_incap_" in marca
                      or "imperva" in marca or "request unsuccessful" in marca)
    return r


def _linea(etiqueta: str, r: dict) -> None:
    if r["error"]:
        print(f"  {ROJO}✗{FIN} {etiqueta:38} {GRIS}{r['error'][:52]}{FIN}")
        return
    st = r["status"]
    color = VERDE if st == 200 else (AMA if st in (404, 429) else ROJO)
    es_json = "json" in (r["tipo"] or "").lower()
    nota = ""
    if r["incapsula"]:
        nota = f"  {ROJO}← reto de Imperva/Incapsula{FIN}"
    elif st == 200 and not es_json:
        nota = f"  {AMA}← 200 pero NO es JSON{FIN}"
    print(f"  {color}{st}{FIN} {etiqueta:38} {GRIS}{(r['tipo'] or '?')[:24]:24} "
          f"{r['bytes']:>6}B {r['ms']:>5}ms{FIN}{nota}")


def main() -> int:
    salida_json = "--json" in sys.argv
    res: dict = {}

    print(f"\n{AZUL}═══ 1. El endpoint que raspábamos (sjf2) ═══{FIN}")
    print(f"{GRIS}  Si esto da 403 con HTML de Incapsula, el problema NO es el ritmo.{FIN}")
    res["raspado_navegador"] = _pedir(f"{RASPADO}/2032577", UA_NAVEGADOR,
                                      referer=RASPADO_REFERER)
    _linea("tesis/2032577 · UA de navegador", res["raspado_navegador"])
    res["raspado_honesto"] = _pedir(f"{RASPADO}/2032577", UA_HONESTO,
                                    referer=RASPADO_REFERER)
    _linea("tesis/2032577 · UA identificado", res["raspado_honesto"])
    res["portada"] = _pedir(RASPADO_REFERER, UA_NAVEGADOR, accept="text/html")
    _linea("portada del SJF (sesión/cookie)", res["portada"])

    print(f"\n{AZUL}═══ 2. El API OFICIAL de datos abiertos (Bicentenario) ═══{FIN}")
    print(f"{GRIS}  Es la vía que la SCJN publica para obtener el acervo completo.{FIN}")
    res["count"] = _pedir(f"{OFICIAL}/tesis/count", UA_HONESTO)
    _linea("tesis/count", res["count"])
    res["ids"] = _pedir(f"{OFICIAL}/tesis/ids?page=0&size=1000", UA_HONESTO)
    _linea("tesis/ids?page=0&size=1000", res["ids"])

    # Si el catálogo respondió, probamos traer UNA tesis real de esa lista.
    id_muestra = None
    try:
        datos = json.loads(res["ids"]["cuerpo"]) if res["ids"]["status"] == 200 else None
        if isinstance(datos, list) and datos:
            id_muestra = datos[0] if not isinstance(datos[0], dict) else \
                next(iter(datos[0].values()))
    except Exception:
        pass
    if id_muestra:
        res["detalle"] = _pedir(f"{OFICIAL}/tesis/{id_muestra}", UA_HONESTO)
        _linea(f"tesis/{id_muestra} (del catálogo)", res["detalle"])

    # ── Lectura ────────────────────────────────────────────────────────────
    print(f"\n{AZUL}═══ 3. Qué significa ═══{FIN}")
    raspado_ok = res["raspado_navegador"]["status"] == 200
    raspado_waf = (res["raspado_navegador"]["incapsula"]
                   or res["raspado_navegador"]["status"] in (403, 503))
    oficial_ok = res["count"]["status"] == 200 and res["ids"]["status"] == 200
    oficial_waf = res["count"]["incapsula"] or res["ids"]["incapsula"]

    if raspado_ok:
        print(f"  {VERDE}•{FIN} El endpoint viejo YA RESPONDE: el bloqueo era por ritmo y "
              f"se levantó.\n    Con el freno nuevo puesto, el harvester puede reanudar.")
    elif raspado_waf:
        print(f"  {ROJO}•{FIN} El endpoint viejo sigue bloqueado por el WAF. Esto no se "
              f"arregla\n    esperando ni cambiando cabeceras: es un control de seguridad, "
              f"no un\n    límite de tasa.")

    if oficial_ok:
        try:
            total = int(json.loads(res["count"]["cuerpo"]))
        except Exception:
            total = None
        print(f"  {VERDE}•{FIN} El API OFICIAL responde{f' — {total:,} tesis en el catálogo' if total else ''}.")
        print(f"    {VERDE}Ésta es la vía correcta:{FIN} da el catálogo completo de ids, así "
              f"que se\n    acaba el caminar a ciegas por 2 millones de números.")
    elif oficial_waf:
        print(f"  {ROJO}•{FIN} El API oficial también está detrás del reto de Imperva.")
        print(f"    {AMA}Lo que toca es pedirle acceso a la SCJN{FIN} (Unidad General de "
              f"Transparencia),\n    no intentar pasar el reto: evadir un control de "
              f"seguridad de un\n    órgano jurisdiccional no es terreno donde Kawiil quiera "
              f"estar.")
    else:
        print(f"  {AMA}•{FIN} El API oficial no respondió como se esperaba. Revisa el cuerpo:")
        print(f"{GRIS}    {(res['count']['cuerpo'] or res['count']['error'])[:200]}{FIN}")

    if salida_json:
        print("\n--- JSON ---")
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
