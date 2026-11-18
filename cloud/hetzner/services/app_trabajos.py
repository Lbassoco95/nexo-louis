#!/usr/bin/env python3
"""app_trabajos.py — TRABAJOS de Donna: lo grande o ambicioso se convierte en un PLAN de pasos que ella va resolviendo sola, con avance visible.

Cuando un pedido es demasiado grande para contestarlo en la conversación (revisar cientos de datos, cargar o limpiar muchas cosas, armar un informe…),
Donna lo registra como un trabajo:
  1. PLANEA: lo divide en 3-8 pasos concretos (cada uno marcado «leer», «memoria» o «cambio»).
  2. EJECUTA los pasos uno por uno con su cerebro completo (sus herramientas y sus protecciones contra inventar datos); cada resultado queda guardado.
  3. NO toca sistemas externos sin permiso: los pasos de tipo «cambio» (cerrar tareas, enviar correos, mover eventos…) quedan en «espera de tu confirmación»
     y solo corren cuando Polo los confirma en el módulo Trabajos.
  4. Al terminar resume lo hecho y lo PENDIENTE, avisa por notificación, y se puede reanudar («continuar») para reintentar lo que falló.
Persiste en PUSH_STATE_DIR/trabajos/<id>.json (0600) y se reanuda solo si el servidor se reinicia a medias.
La ejecución real (planear / ejecutar un paso / resumir / avisar) la inyecta el gateway con configurar(); aquí no hay llamadas a modelos."""
import os
import re
import json
import time
import secrets
import threading
from pathlib import Path

STATE_DIR = Path(os.environ.get("PUSH_STATE_DIR", "/opt/openclaw/state"))
TRAB_DIR = STATE_DIR / "trabajos"
ID_RE = re.compile(r"^j[0-9a-f]{10}$")
MAX_PASOS = 8
MAX_ACTIVOS = 3
MAX_TRABAJOS = 100
TIPOS = ("leer", "memoria", "cambio")
_CFG = {"ejecutar": None, "planear": None, "resumir": None, "notificar": None}
_LOCK = threading.RLock()
_HILOS = set()                    # ids con un hilo corriendo AHORA (el estado en disco puede decir «en curso» tras un reinicio)
_TL = threading.local()


def configurar(**kw) -> None:
    _CFG.update({k: v for k, v in kw.items() if k in _CFG})


def en_trabajo() -> bool:
    """True dentro de un paso de un trabajo: ahí Donna NO puede crear trabajos nuevos (evita trabajos dentro de trabajos)."""
    return bool(getattr(_TL, "activo", False))


# ------------------------------------------------------------------ almacenamiento
def _path(jid: str) -> Path:
    if not ID_RE.match(jid or ""):
        raise ValueError("id de trabajo inválido")
    return TRAB_DIR / f"{jid}.json"


def _leer(jid: str):
    try:
        return json.loads(_path(jid).read_text())
    except Exception:
        return None


def _escribir(job: dict) -> None:
    TRAB_DIR.mkdir(parents=True, exist_ok=True)
    job["actualizado"] = int(time.time())
    p = _path(job["id"])
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(job, f, ensure_ascii=False)
    os.replace(tmp, p)


def _mutar(jid: str, fn):
    """Lee-modifica-escribe bajo candado (el hilo del trabajo y las acciones de la app escriben el mismo archivo)."""
    with _LOCK:
        job = _leer(jid)
        if job is None:
            return None
        fn(job)
        _escribir(job)
        return job


def _log(job: dict, texto: str) -> None:
    job.setdefault("bitacora", []).append({"ts": int(time.time()), "t": texto[:300]})
    job["bitacora"] = job["bitacora"][-120:]


def _progreso(job: dict) -> int:
    pasos = job.get("pasos") or []
    if not pasos:
        return 0
    hechos = sum(1 for p in pasos if p["estado"] in ("hecho", "omitido", "falló"))
    return int(100 * hechos / len(pasos))


# ------------------------------------------------------------------ plan
def _normalizar_plan(obj) -> tuple:
    """(titulo, pasos) validado: 1-8 pasos, tipos permitidos, textos acotados. Lanza ValueError si no hay nada usable."""
    if not isinstance(obj, dict):
        raise ValueError("plan inválido")
    pasos = []
    for p in (obj.get("pasos") or [])[:MAX_PASOS]:
        if isinstance(p, str):
            p = {"titulo": p}
        if not isinstance(p, dict) or not str(p.get("titulo", "")).strip():
            continue
        tipo = str(p.get("tipo", "leer")).lower()
        pasos.append({"titulo": str(p["titulo"]).strip()[:140], "detalle": str(p.get("detalle", "")).strip()[:900],
                      "tipo": tipo if tipo in TIPOS else "leer", "estado": "pendiente", "resultado": "", "t0": 0, "t1": 0, "confirmado": False})
    if not pasos:
        raise ValueError("el plan no trae pasos")
    return str(obj.get("titulo", "")).strip()[:110], pasos


def _plan_de_respaldo(pedido: str) -> tuple:
    return pedido[:80], [{"titulo": pedido[:130], "detalle": pedido[:900], "tipo": "leer", "estado": "pendiente", "resultado": "",
                          "t0": 0, "t1": 0, "confirmado": False}]


# ------------------------------------------------------------------ crear / correr
def crear(pedido: str, titulo: str = "", usuario: str = "", esperar_plan: float = 0) -> dict:
    pedido = (pedido or "").strip()
    if len(pedido) < 8:
        raise ValueError("el pedido es muy corto para armar un trabajo")
    with _LOCK:
        activos = [j for j in listar(200) if j["estado"] in ("planeando", "en curso")]
        if len(activos) >= MAX_ACTIVOS:
            raise ValueError("ya hay %d trabajos corriendo; espera a que termine alguno" % len(activos))
        jid = "j" + secrets.token_hex(5)
        job = {"id": jid, "titulo": (titulo or pedido)[:110], "pedido": pedido[:3000], "estado": "planeando", "creado": int(time.time()),
               "actualizado": int(time.time()), "usuario": usuario[:60], "pasos": [], "progreso": 0, "resumen": "", "pendientes": [],
               "bitacora": [], "control": ""}
        _log(job, "Trabajo recibido; armando el plan…")
        _escribir(job)
        _podar()
    _arrancar(jid)
    if esperar_plan:
        fin = time.time() + esperar_plan
        while time.time() < fin:
            j = _leer(jid)
            if j and (j["pasos"] or j["estado"] not in ("planeando",)):
                break
            time.sleep(0.2)
    return obtener(jid)


def _podar() -> None:
    try:
        files = sorted(TRAB_DIR.glob("j*.json"), key=lambda p: p.stat().st_mtime)
        for p in files[:-MAX_TRABAJOS]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _arrancar(jid: str) -> bool:
    with _LOCK:
        if jid in _HILOS:
            return False
        _HILOS.add(jid)
    threading.Thread(target=_correr, args=(jid,), daemon=True, name="trabajo-" + jid).start()
    return True


def reanudar_pendientes() -> int:
    """Al arrancar el servidor: retoma los trabajos que quedaron a medias."""
    n = 0
    try:
        for p in TRAB_DIR.glob("j*.json"):
            j = _leer(p.stem)
            if j and j["estado"] in ("planeando", "en curso") and _arrancar(j["id"]):
                n += 1
    except OSError:
        pass
    return n


def _correr(jid: str) -> None:
    try:
        job = _leer(jid)
        if not job:
            return
        if any(p["estado"] == "en curso" for p in job["pasos"]):          # quedó a medias por un reinicio: se reintenta ese paso
            job = _mutar(jid, lambda j: [p.update(estado="pendiente") for p in j["pasos"] if p["estado"] == "en curso"])
        if not job["pasos"]:
            try:
                if not _CFG["planear"]:
                    raise RuntimeError("sin planeador")
                titulo, pasos = _normalizar_plan(_CFG["planear"](job["pedido"]))
            except Exception as e:
                titulo, pasos = _plan_de_respaldo(job["pedido"])
                _mutar(jid, lambda j: _log(j, "No pude armar un plan detallado (%s); lo hago como un solo paso." % str(e)[:80]))

            def poner(j):
                j["pasos"] = pasos
                j["titulo"] = titulo or j["titulo"]
                _log(j, "Plan listo: %d paso(s)." % len(pasos))
            _mutar(jid, poner)
        _mutar(jid, lambda j: j.update(estado="en curso"))
        while True:
            job = _leer(jid)
            if job["control"] == "cancelar":
                _mutar(jid, lambda j: (j.update(estado="cancelado", control=""), _log(j, "Trabajo cancelado.")))
                return
            if job["control"] == "pausar":
                _mutar(jid, lambda j: (j.update(estado="pausado", control=""), _log(j, "Trabajo en pausa.")))
                return
            idx = next((i for i, p in enumerate(job["pasos"]) if p["estado"] == "pendiente"), None)
            if idx is None:
                break
            paso = job["pasos"][idx]
            if paso["tipo"] == "cambio" and not paso.get("confirmado"):
                def espera(j, i=idx):
                    j["pasos"][i]["estado"] = "espera"
                    _log(j, "Paso %d «%s» cambia sistemas externos: espera tu confirmación." % (i + 1, j["pasos"][i]["titulo"][:60]))
                _mutar(jid, espera)
                continue
            _ejecutar_paso(jid, idx)
        _finalizar(jid)
    except Exception as e:
        _mutar(jid, lambda j: (j.update(estado="error"), _log(j, "Error interno: %s" % str(e)[:150])))
    finally:
        with _LOCK:
            _HILOS.discard(jid)


def _prompt_paso(job: dict, idx: int) -> str:
    previos = []
    for i, p in enumerate(job["pasos"][:idx]):
        if p["estado"] in ("hecho", "falló") and p["resultado"]:
            previos.append(f"Paso {i + 1} ({p['estado']}): {p['titulo']}\n{p['resultado'][:700]}")
    paso = job["pasos"][idx]
    plan = "\n".join(f"{i + 1}. {p['titulo']}" for i, p in enumerate(job["pasos"]))
    permiso = ("Polo YA CONFIRMÓ este paso: puedes ejecutar el cambio." if paso.get("confirmado") else
               "Este paso es de lectura/memoria: consulta, analiza y guarda en tu memoria si hace falta, pero NO cambies sistemas externos (no cierres tareas, no envíes correos ni mensajes, no muevas eventos).")
    return (f"TRABAJO: {job['titulo']}\nPEDIDO ORIGINAL DE POLO: {job['pedido']}\n\nPLAN:\n{plan}\n\n"
            + ("RESULTADOS DE LOS PASOS ANTERIORES:\n" + "\n\n".join(previos) + "\n\n" if previos else "")
            + f"PASO ACTUAL ({idx + 1}/{len(job['pasos'])}): {paso['titulo']}\n{paso['detalle']}\n\n{permiso}\n"
            "Ejecuta SOLO este paso con tus herramientas. No crees trabajos nuevos. Responde con el resultado concreto y VERAZ: cifras reales de las herramientas, "
            "nombres exactos; si algo falló o no se pudo, dilo claramente. No inventes ni rellenes datos.")


def _ejecutar_paso(jid: str, idx: int) -> None:
    def iniciar(j):
        j["pasos"][idx].update(estado="en curso", t0=int(time.time()))
        _log(j, "Paso %d/%d en curso: %s" % (idx + 1, len(j["pasos"]), j["pasos"][idx]["titulo"][:70]))
    job = _mutar(jid, iniciar)
    texto, err = "", ""
    _TL.activo = True
    try:
        if not _CFG["ejecutar"]:
            raise RuntimeError("sin ejecutor")
        texto, err = _CFG["ejecutar"](_prompt_paso(job, idx))
    except Exception as e:
        err = str(e)
    finally:
        _TL.activo = False
    ok = bool((texto or "").strip()) and not err

    def cerrar(j):
        p = j["pasos"][idx]
        p.update(estado="hecho" if ok else "falló", resultado=(texto or "")[:4000] if ok else ("No se pudo: " + (str(err) or "sin respuesta"))[:600], t1=int(time.time()))
        j["progreso"] = _progreso(j)
        _log(j, "Paso %d %s." % (idx + 1, "completado" if ok else "falló: " + str(err)[:80]))
    _mutar(jid, cerrar)


def _finalizar(jid: str) -> None:
    job = _leer(jid)
    pasos = job["pasos"]
    espera = [p for p in pasos if p["estado"] == "espera"]
    falla = [p for p in pasos if p["estado"] == "falló"]
    pend = [f"Confirmar: {p['titulo']}" for p in espera] + [f"Reintentar: {p['titulo']}" for p in falla]
    estado = "espera confirmación" if espera else ("con pendientes" if falla else "terminado")
    resumen = ""
    try:
        if _CFG["resumir"]:
            resumen = str(_CFG["resumir"](job) or "").strip()
    except Exception:
        resumen = ""
    if not resumen:
        hechos = [p["titulo"] for p in pasos if p["estado"] == "hecho"]
        resumen = ("Hecho: " + "; ".join(hechos) if hechos else "No se completó ningún paso.") + ("\nPendiente: " + "; ".join(pend) if pend else "")

    def cerrar(j):
        j.update(estado=estado, pendientes=pend, resumen=resumen[:5000], progreso=_progreso(j))
        _log(j, "Trabajo %s." % estado)
    _mutar(jid, cerrar)
    try:
        if _CFG["notificar"]:
            emoji = {"terminado": "✅", "con pendientes": "⚠️", "espera confirmación": "🕓"}.get(estado, "ℹ️")
            _CFG["notificar"](f"{emoji} Trabajo {estado}: {job['titulo'][:50]}", resumen[:220], "trabajo:" + jid, resumen)
    except Exception:
        pass


# ------------------------------------------------------------------ consulta y acciones
def _publico(j: dict) -> dict:
    return {k: j[k] for k in ("id", "titulo", "pedido", "estado", "creado", "actualizado", "pasos", "progreso", "resumen", "pendientes", "bitacora")} | \
           {"corriendo": j["id"] in _HILOS}


def obtener(jid: str):
    j = _leer(jid) if ID_RE.match(jid or "") else None
    return _publico(j) if j else None


def listar(limit: int = 50) -> list:
    out = []
    try:
        for p in TRAB_DIR.glob("j*.json"):
            j = _leer(p.stem)
            if j:
                out.append({"id": j["id"], "titulo": j["titulo"], "estado": j["estado"], "progreso": j.get("progreso", 0), "creado": j["creado"],
                            "actualizado": j["actualizado"], "pasos": len(j["pasos"]),
                            "espera": sum(1 for x in j["pasos"] if x["estado"] == "espera"), "corriendo": j["id"] in _HILOS})
    except OSError:
        pass
    return sorted(out, key=lambda x: -x["actualizado"])[:max(1, min(limit, 200))]


def accion(jid: str, accion: str, paso=None) -> dict:
    """pausar | reanudar | cancelar | confirmar (paso=n, 1-based). Devuelve el trabajo actualizado."""
    job = _leer(jid) if ID_RE.match(jid or "") else None
    if not job:
        raise ValueError("no existe ese trabajo")
    corriendo = jid in _HILOS
    if accion == "pausar":
        if not corriendo:
            raise ValueError("el trabajo no está corriendo")
        _mutar(jid, lambda j: j.update(control="pausar"))
    elif accion == "cancelar":
        if corriendo:
            _mutar(jid, lambda j: j.update(control="cancelar"))
        else:
            _mutar(jid, lambda j: (j.update(estado="cancelado", control=""), _log(j, "Trabajo cancelado.")))
    elif accion == "reanudar":
        if corriendo:
            raise ValueError("el trabajo ya está corriendo")
        if job["estado"] in ("terminado", "cancelado"):
            raise ValueError("el trabajo ya terminó")

        def reabrir(j):
            j["control"] = ""
            for p in j["pasos"]:
                if p["estado"] in ("falló", "en curso"):
                    p.update(estado="pendiente", resultado="")
            j["estado"] = "en curso"
            _log(j, "Reanudado: se reintenta lo que faltó.")
        _mutar(jid, reabrir)
        _arrancar(jid)
    elif accion == "confirmar":
        try:
            i = int(paso) - 1
        except (TypeError, ValueError):
            raise ValueError("falta el número de paso")
        if not (0 <= i < len(job["pasos"])) or job["pasos"][i]["estado"] != "espera":
            raise ValueError("ese paso no está esperando confirmación")

        def ok(j):
            j["pasos"][i].update(estado="pendiente", confirmado=True)
            j["estado"] = "en curso"
            _log(j, "Polo confirmó el paso %d." % (i + 1))
        _mutar(jid, ok)
        if jid not in _HILOS:
            _arrancar(jid)
    else:
        raise ValueError("acción desconocida")
    return obtener(jid)


def resumen_texto(jid: str = "") -> str:
    """Texto para Donna/voz: un trabajo en detalle, o la lista de los recientes."""
    if jid:
        j = obtener(jid)
        if not j:
            return "❌ no existe ese trabajo"
        ic = {"hecho": "✅", "en curso": "🔄", "pendiente": "⏳", "falló": "❌", "espera": "🕓", "omitido": "➖"}
        lineas = [f"[[trabajo:{j['id']}]] «{j['titulo']}» — {j['estado']} ({j['progreso']}%)"]
        lineas += [f"  {ic.get(p['estado'], '·')} {i + 1}. {p['titulo']} ({p['estado']})" for i, p in enumerate(j["pasos"])]
        if j["pendientes"]:
            lineas.append("  Pendiente: " + "; ".join(j["pendientes"]))
        return "\n".join(lineas)
    items = listar(10)
    return "\n".join(f"- [[trabajo:{i['id']}]] «{i['titulo']}» — {i['estado']} ({i['progreso']}%)" for i in items) or "(no hay trabajos todavía)"
