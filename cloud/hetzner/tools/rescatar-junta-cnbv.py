#!/usr/bin/env python3
"""rescatar-junta-cnbv.py — deja guardada, UNA SOLA VEZ, la junta con la CNBV del 23-nov-2026 que se perdió, como junta guardada de Donna.

La grabación completa no existe: esto se reconstruyó del chat de Polo con Donna y Polo confirmó los 3 puntos. NO es una transcripción literal y así queda marcada
(título «rescatada» + nota de origen en la minuta). Aparece en Escuchar → «Juntas guardadas», en Entregables y en Personas; Donna propone las tareas de Sylon.

Uso (en el servidor, como polo):  sudo python3 ~/donna-deploy/cloud/hetzner/tools/rescatar-junta-cnbv.py
Se niega a correr dos veces (deja una marca en /opt/openclaw/state/rescate-cnbv-20261123.hecho).
"""
import json
import os
import sys
import urllib.request

OC = os.environ.get("OC", "/opt/openclaw")
URL = os.environ.get("DONNA_URL", "http://127.0.0.1:3000")
MARCA = os.path.join(OC, "state", "rescate-cnbv-20261123.hecho")
LIVE = "rescate-cnbv-20261123"

PUNTOS = [
    ("Onboarding de la app (CNBV)", "La CNBV pidió que en el onboarding no se use «cuenta corriente» ni «cuenta de ahorro»: debe decir «cuenta» o «cuenta de fondo de pagos electrónicos»."),
    ("Página web de Sylon: «Ley Fintech»", "En la página web, donde dice «Ley Fintech» hay que poner el nombre correcto de la ley. El nombre exacto NO quedó capturado en la junta: falta confirmarlo."),
    ("Página web de Sylon: sección «Envía y recibe dinero»", "Ajustar el texto de la sección «Envía y recibe dinero desde y hacia cualquier institución del país a través de SPEI. La aplicación del saldo está sujeta a la operación del sistema de pagos y a validaciones aplicables», porque Sylon es responsable ante el cliente de que funcione correctamente. La redacción final está por definir."),
]


def token() -> str:
    for ln in open(os.path.join(OC, "openclaw.env")):
        if ln.startswith("OPENCLAW_GATEWAY_TOKEN="):
            return ln.split("=", 1)[1].strip()
    sys.exit("No encontré OPENCLAW_GATEWAY_TOKEN en %s/openclaw.env" % OC)


def post(path: str, body: dict, tok: str) -> dict:
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def main():
    if os.path.exists(MARCA):
        sys.exit("Ya se rescató esta junta antes (%s). No la duplico." % MARCA)
    tok = token()
    for t, x in PUNTOS:
        post("/v1/app/escucha/nota", {"live_id": LIVE, "texto": "%s — %s" % (t, x), "hora": ""}, tok)
    lineas = [{"t": "00:00", "voz": 0, "es": "(Rescatado del chat de Polo con Donna; no es transcripción literal) %s — %s" % (t, x), "orig": "", "idioma": "es"} for t, x in PUNTOS]
    r = post("/v1/app/escucha/guardar", {
        "duracion": 54 * 60, "live_id": LIVE, "voces": [], "lineas": lineas,
        "titulo": "Sesión con la CNBV (Sylon) 2026-11-23 · rescatada",
        "origen_nota": "La grabación de esta junta se perdió (la pestaña se recargó y el servicio se reinició). Estos puntos se reconstruyeron del chat de Polo con Donna "
                       "durante la junta y Polo los confirmó. No es una transcripción literal; todo lo dicho que no pasó por el chat no está aquí."}, tok)
    os.makedirs(os.path.dirname(MARCA), exist_ok=True)
    open(MARCA, "w").write(r.get("sesion_id", "ok"))
    print("Listo:", r.get("nombre"), "· sesión", r.get("sesion_id"), "· entregable", r.get("archivo"))
    print("Ábrela en la app: Escuchar → Juntas guardadas, o Entregables.")


if __name__ == "__main__":
    main()
