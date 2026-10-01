#!/usr/bin/env python3
"""
diagnostico-infra.py — lo aburrido primero: ¿la máquina estuvo viva y sana?

Por qué existe. Cuando el acervo del SJF dejó de crecer, el diagnóstico saltó
directo a analizar el WAF de la SCJN —Imperva, retos de bot, cabeceras— sin haber
comprobado antes lo barato: si el servidor estuvo encendido, si quedaba disco, si
había red, si el reloj estaba en hora. El servidor es de pago mensual; una
suspensión por falta de pago explica «dejó de bajar información» sin necesidad de
ningún análisis sofisticado, y lo habríamos visto en treinta segundos.

Regla que este script implanta: **antes de explicar algo complicado, descartar lo
simple.** Las comprobaciones van de la más barata y más probable a la más cara.

No escribe nada ni toca servicios. Solo mide.

Uso:
    python3 diagnostico-infra.py
    python3 diagnostico-infra.py --dias 45     # ventana del análisis de huecos
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME_OC = Path("/opt/openclaw") if Path("/opt/openclaw").exists() else Path.home() / ".openclaw"

V, R, A, C, G, F = ("\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[1;36m",
                    "\033[0;90m", "\033[0m")

_hallazgos: list[tuple[str, str]] = []   # (severidad, texto)


def nota(sev: str, txt: str) -> None:
    _hallazgos.append((sev, txt))


_SIN_SYSTEMD = ("has not been booted with systemd", "failed to connect to bus",
                "can't operate", "no such file or directory")


def no_medible(salida: str) -> bool:
    """True si el comando no pudo correr (no si el sistema está mal).

    Un diagnóstico que reporta «unidades en failed» cuando lo que pasa es que no
    hay systemd es ruido, y el ruido se ignora — que es como se pierde la señal
    buena. Peor todavía es lo contrario: decir «✓ todos los timers han corrido»
    cuando no se pudo leer ninguno.
    """
    b = (salida or "").lower()
    return not salida or any(x in b for x in _SIN_SYSTEMD) or b.startswith("(no pude")


def sh(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"(no pude ejecutar {cmd[0]}: {e})"


def titulo(n: str, t: str) -> None:
    print(f"\n{C}═══ {n}. {t} ═══{F}")


def ok(t: str) -> None:
    print(f"  {V}✓{F} {t}")


def mal(t: str) -> None:
    print(f"  {R}✗{F} {t}")


def dud(t: str) -> None:
    print(f"  {A}?{F} {t}")


# ── 1. ¿Estuvo viva la máquina? ─────────────────────────────────────────────
def maquina() -> None:
    titulo(1, "La máquina")
    up = sh(["uptime", "-p"])
    desde = sh(["uptime", "-s"])
    print(f"  encendida {up or '?'}  {G}(desde {desde or '?'}){F}")

    # Un servidor de pago mensual que se suspende y vuelve, REINICIA. El historial
    # de arranques es la huella más directa de una interrupción administrativa.
    boots = sh(["journalctl", "--list-boots", "--no-pager"])
    lineas = [l for l in boots.splitlines() if re.search(r"-?\d+\s+[0-9a-f]{8}", l)]
    if lineas:
        print(f"  {G}arranques registrados: {len(lineas)} (los 4 más recientes){F}")
        for l in lineas[-4:]:
            print(f"    {G}{l.strip()[:96]}{F}")
        if len(lineas) > 1:
            nota("info", f"{len(lineas)} arranques en el journal — revisa si alguno "
                         f"coincide con el día que dejó de bajar información.")
    else:
        dud("no pude leer el historial de arranques (¿journal volátil?)")


# ── 2. Recursos: lo que rompe en silencio ───────────────────────────────────
def recursos() -> None:
    titulo(2, "Disco, inodos y memoria")
    # Un disco lleno no truena ruidosamente: SQLite deja de escribir, los logs se
    # quedan mudos y todo parece «no hubo nada nuevo».
    for punto in ("/", str(HOME_OC)):
        try:
            u = shutil.disk_usage(punto)
            pct = 100 * u.used / u.total
            libre_gb = u.free / 1024**3
            txt = f"{punto}: {pct:.0f}% usado · {libre_gb:,.1f} GB libres"
            if pct >= 95 or libre_gb < 2:
                mal(txt + "  ← sin espacio, SQLite deja de escribir en silencio")
                nota("alto", f"Disco {punto} al {pct:.0f}%: libera espacio antes de "
                             f"buscar causas en el código.")
            elif pct >= 85:
                dud(txt)
            else:
                ok(txt)
        except Exception as e:
            dud(f"{punto}: no pude medir ({e})")

    inodos = sh(["df", "-i", "/"])
    for l in inodos.splitlines()[1:]:
        campos = l.split()
        if len(campos) >= 5 and campos[4].endswith("%"):
            p = int(campos[4].rstrip("%"))
            (mal if p >= 90 else ok)(f"inodos en /: {p}% usados")
            if p >= 90:
                nota("alto", "Inodos agotados: no se pueden crear archivos nuevos "
                             "aunque sobre espacio.")
            break

    mem = sh(["free", "-h"])
    for l in mem.splitlines():
        if l.lower().startswith(("mem", "swap")):
            print(f"  {G}{l}{F}")


# ── 3. Red, DNS y reloj ─────────────────────────────────────────────────────
def red() -> None:
    titulo(3, "Red, DNS y reloj")
    # Destino NEUTRAL a propósito: separa «no tengo internet» de «la SCJN me
    # bloquea». Sin esta distinción, una caída de red se lee como un WAF.
    ip = ""
    for destino in ("https://api.ipify.org", "https://ifconfig.me/ip",
                    "https://checkip.amazonaws.com"):
        cand = sh(["curl", "-sS", "--max-time", "10", destino]).strip()
        if re.fullmatch(r"[0-9.]{7,15}", cand):
            ip = cand
            break
    if re.fullmatch(r"[0-9.]{7,15}", ip or ""):
        ok(f"salida a internet OK · IP pública {ip}")
        if ip != "204.168.131.21":
            nota("alto", f"La IP pública es {ip}, no la 204.168.131.21 de siempre. "
                         f"Si el proveedor reasignó la IP, cualquier lista blanca o "
                         f"reputación previa se perdió.")
            mal(f"la IP cambió: antes 204.168.131.21, ahora {ip}")
    else:
        mal(f"sin salida a internet — {(ip or 'sin respuesta')[:60]}")
        nota("alto", "No hay salida a internet. Esto explica por sí solo que no se "
                     "descargue nada; no busques más lejos hasta resolverlo.")

    dns = sh(["getent", "hosts", "scjn.gob.mx"])
    (ok if dns and "." in dns else mal)(f"DNS scjn.gob.mx → {(dns or 'no resuelve')[:60]}")

    # Un reloj desfasado rompe TLS y las cookies de sesión, y el síntoma que da es
    # un 403 — idéntico a un bloqueo.
    t = sh(["timedatectl", "show", "--property=NTPSynchronized",
            "--property=Timezone", "--value"])
    partes = t.split("\n") if t else []
    sinc = partes[0].strip().lower() if partes else ""
    if no_medible(t):
        dud("no pude consultar el reloj con timedatectl — SIN medir")
        sinc = "__nomedible__"
    if sinc == "yes":
        ok(f"reloj sincronizado por NTP · zona {partes[1] if len(partes) > 1 else '?'}")
    elif sinc != "__nomedible__":
        mal(f"reloj NO sincronizado (NTP={sinc or '?'}) — un desfase rompe TLS y da 403")
        nota("alto", "Reloj sin sincronizar: puede producir 403 que parecen bloqueo.")


# ── 4. Servicios y timers ───────────────────────────────────────────────────
def servicios() -> None:
    titulo(4, "Servicios y timers")
    fallidos = sh(["systemctl", "--failed", "--no-legend", "--no-pager"])
    if no_medible(fallidos):
        dud("no pude consultar systemd aquí — esta comprobación queda SIN medir")
        nota("medio", "No se pudo consultar systemd: el estado de los servicios y "
                      "timers quedó sin verificar.")
        return
    if fallidos and "0 loaded" not in fallidos:
        for l in [x for x in fallidos.splitlines() if x.strip()][:10]:
            mal(l.strip()[:92])
        nota("alto", "Hay unidades en estado failed.")
    else:
        ok("ninguna unidad en estado failed")

    ahora = dt.datetime.now()
    salida = sh(["systemctl", "list-timers", "--all", "--no-pager", "--no-legend"])
    if no_medible(salida):
        dud("no pude listar los timers — SIN medir")
        return
    atrasados = []
    leidos = 0
    for l in salida.splitlines():
        m = re.search(r"((?:\w{3}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})|n/a)"
                      r"\s+\S+\s+(\S+\.timer)", l)
        if not m:
            continue
        leidos += 1
        último, nombre = m.group(1), m.group(2)
        if último == "n/a":
            atrasados.append((nombre, "nunca ha corrido"))
            continue
        try:
            f = dt.datetime.strptime(último.split(None, 1)[1], "%Y-%m-%d %H:%M:%S")
            dias = (ahora - f).days
            if dias >= 2:
                atrasados.append((nombre, f"hace {dias} días"))
        except Exception:
            pass
    if atrasados:
        for n, c in atrasados[:12]:
            dud(f"{n:30} última ejecución: {c}")
        nota("medio", f"{len(atrasados)} timer(s) sin correr recientemente.")
    elif leidos:
        ok(f"los {leidos} timers han corrido en los últimos 2 días")
    else:
        dud("no se reconoció ningún timer en la salida — SIN medir")


# ── 5. ¿Cuándo dejó de escribir cada cosa? ──────────────────────────────────
def ultimos_datos() -> None:
    titulo(5, "Última escritura real de cada subsistema")
    print(f"  {G}Esto fecha el problema. Si TODO se detuvo el mismo día, fue la "
          f"máquina;\n  si solo se detuvo el SJF, fue el SJF.{F}")
    ahora = dt.datetime.now()
    objetivos = [
        ("SJF · biblioteca", HOME_OC / "legal/sjf/biblioteca.db"),
        ("DOF · biblioteca", HOME_OC / "legal/dof/biblioteca_dof.db"),
        ("log scheduler", HOME_OC / "logs/scheduler.log"),
        ("log telegram", HOME_OC / "logs/telegram-bridge.log"),
        ("log sjf-update", HOME_OC / "logs/sjf-update.log"),
        ("log sjf-backfill", HOME_OC / "logs/sjf-backfill.log"),
        ("cola de avisos", HOME_OC / "reminders/sent.jsonl"),
    ]
    fechas = []
    for etiqueta, p in objetivos:
        if not p.exists():
            dud(f"{etiqueta:22} (no existe {p})")
            continue
        m = dt.datetime.fromtimestamp(p.stat().st_mtime)
        dias = (ahora - m).days
        fechas.append((etiqueta, m, dias))
        linea = f"{etiqueta:22} {m:%Y-%m-%d %H:%M}  ({dias} días)"
        (ok if dias <= 1 else (dud if dias <= 3 else mal))(linea)

    # La señal clave: ¿se detuvieron todos juntos?
    if len(fechas) >= 3:
        viejos = [f for f in fechas if f[2] >= 2]
        if len(viejos) == len(fechas):
            dias_cal = {f[1].date() for f in fechas}
            if len(dias_cal) <= 2:
                nota("alto", f"TODOS los subsistemas se detuvieron el mismo día "
                             f"({sorted(dias_cal)[-1]}). Eso no es un bloqueo del SJF: "
                             f"es la máquina. Revisa pago/suspensión del servidor.")
                mal(f"todo se detuvo junto el {sorted(dias_cal)[-1]} → causa común, "
                    f"no causa del SJF")
        elif viejos:
            nota("medio", f"Solo {len(viejos)} de {len(fechas)} subsistemas están "
                          f"detenidos: la causa es específica de esos, no general.")


# ── 6. Huecos en el log: tiempo sin servicio ────────────────────────────────
def huecos(dias: int) -> None:
    titulo(6, f"Huecos de actividad (últimos {dias} días)")
    print(f"  {G}El scheduler escribe cada pocos minutos. Un hueco largo en su log "
          f"es\n  tiempo en que la máquina no estuvo corriendo.{F}")
    log = HOME_OC / "logs/scheduler.log"
    if not log.exists():
        dud(f"no existe {log}")
        return
    limite = dt.datetime.now() - dt.timedelta(days=dias)
    marcas = []
    try:
        with log.open(errors="replace") as fh:
            for linea in fh:
                m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", linea)
                if not m:
                    continue
                try:
                    t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if t >= limite:
                    marcas.append(t)
    except Exception as e:
        dud(f"no pude leer el log ({e})")
        return
    if len(marcas) < 2:
        dud("muy pocas marcas de tiempo para analizar")
        return
    marcas.sort()
    brechas = [(a, b, (b - a).total_seconds() / 3600)
               for a, b in zip(marcas, marcas[1:]) if (b - a).total_seconds() > 3 * 3600]
    if not brechas:
        ok(f"sin huecos mayores a 3 h entre {marcas[0]:%Y-%m-%d} y {marcas[-1]:%Y-%m-%d}")
        return
    brechas.sort(key=lambda x: -x[2])
    for a, b, h in brechas[:6]:
        mal(f"{h:6.1f} h sin actividad · de {a:%d-%b %H:%M} a {b:%d-%b %H:%M}")
    total = sum(h for _, _, h in brechas)
    nota("alto", f"{len(brechas)} hueco(s) sumando {total:.0f} h sin actividad. "
                 f"Un servidor apagado explica la falta de datos sin más teoría.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=45)
    a = ap.parse_args()

    print(f"\n{C}Diagnóstico de infraestructura — lo barato antes que lo complicado{F}")
    print(f"{G}{dt.datetime.now():%Y-%m-%d %H:%M} · {os.uname().nodename}{F}")

    for paso in (maquina, recursos, red, servicios, ultimos_datos):
        try:
            paso()
        except Exception as e:
            dud(f"la comprobación '{paso.__name__}' falló: {e}")
    try:
        huecos(a.dias)
    except Exception as e:
        dud(f"la comprobación de huecos falló: {e}")

    titulo(7, "Lectura")
    altos = [t for s, t in _hallazgos if s == "alto"]
    medios = [t for s, t in _hallazgos if s == "medio"]
    if altos:
        print(f"  {R}Atiende esto antes de buscar causas en el código:{F}")
        for t in altos:
            print(f"    {R}•{F} {t}")
    if medios:
        print(f"\n  {A}Para revisar:{F}")
        for t in medios:
            print(f"    {A}•{F} {t}")
    if not altos and not medios:
        print(f"  {V}La infraestructura está sana.{F} Si falta información, la causa "
              f"es de la fuente\n  o del código, no de la máquina. Ahora sí corre "
              f"diagnostico-acceso-sjf.py.")
    else:
        print(f"\n  {G}Resuelve lo de arriba y vuelve a correr este diagnóstico antes "
              f"de\n  analizar el WAF o el código.{F}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
