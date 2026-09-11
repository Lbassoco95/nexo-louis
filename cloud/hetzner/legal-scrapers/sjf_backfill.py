#!/usr/bin/env python3
"""
sjf_backfill.py — relleno histórico del acervo SJF (corre en segundo plano).

Mientras el harvester diario (sjf_harvest.py) trae lo NUEVO hacia adelante, este
job rellena lo ANTERIOR: camina hacia atrás desde el registro más alto, saltando
los que ya están en la BD y los ya marcados 404, descargando los que faltan. Así
llena huecos dentro del rango y va extendiendo el acervo hacia épocas anteriores.

Diseñado para correr cada par de horas vía systemd timer:
  - Lote acotado por corrida (BACKFILL_BATCH descargas reales) → gentil, no satura.
  - Commit POR REGISTRO → si se corta, no se pierde nada.
  - Guarda el cursor en la tabla `progress` → la próxima corrida continúa donde quedó.
  - Reusa fetch (cookie + isSemanal/consolidado) y normalize/upsert del scraper.

Uso:
    python3 sjf_backfill.py                 # un lote (default 800)
    BACKFILL_BATCH=2000 python3 sjf_backfill.py
    SJF_BACKFILL_FLOOR=1900000 python3 sjf_backfill.py   # no bajar de ese registro

Variables de entorno:
    SJF_DB_PATH          BD (default /opt/openclaw/legal/sjf/biblioteca.db)
    BACKFILL_BATCH       descargas reales por corrida (default 800)
    SJF_BACKFILL_FLOOR   registro mínimo a intentar (default 0 = sin piso)
    SJF_SCRAPER          ruta al sjf_biblioteca.py (normalize/upsert/mark_404)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

# Reutilizamos el fetch del harvester (cookie de sesión + isSemanal + fallback).
HARVEST_PATH = os.environ.get(
    "SJF_HARVEST", "/opt/openclaw/legal/sjf/sjf_harvest.py")
SCRAPER_PATH = os.environ.get("SJF_SCRAPER") or next(
    (p for p in (
        "/opt/openclaw/legal/sjf/sjf_biblioteca.py",
        "/opt/openclaw/mac/Projects/Kawiil Legal/sjf_biblioteca_install/sjf_biblioteca.py",
    ) if Path(p).exists()),
    "/opt/openclaw/legal/sjf/sjf_biblioteca.py",
)
DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
BATCH = int(os.environ.get("BACKFILL_BATCH", "800"))
FLOOR = int(os.environ.get("SJF_BACKFILL_FLOOR", "0"))
THROTTLE_MS = int(os.environ.get("BACKFILL_THROTTLE_MS", "150"))
CONSECUTIVE_404_THRESHOLD = int(os.environ.get("BACKFILL_404_THRESHOLD", "50"))
CONSECUTIVE_404_JUMP = int(os.environ.get("BACKFILL_404_JUMP", "50000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sjf_backfill")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No pude cargar {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Saltos de "zona muerta": registrarlos, muestrearlos, no perderlos ───────
# El defecto que esto arregla, medido en producción: tras 50 IDs 404 seguidos el
# walker saltaba 50,000 de golpe, sin registrar el bloque en ninguna tabla y sin
# volver nunca (solo quedaba una línea en el log). En un espacio de IDs disperso,
# 50 huecos seguidos no dicen NADA del bloque completo.
#
# Consecuencia real: 425 saltos, 499,144 IDs intentados de ~2.03 millones, y el
# acervo sin los años 1997-2011 de la Novena Época. Las últimas cuatro líneas del
# log lo resumen: el 24-jul-2026, entre 08:44 y 08:46, el cursor fue de 198,643 a
# 0 en cuatro brincos — 198 mil IDs descartados con 200 sondeos. Los
# registro_digital NO son cronológicos (a mayor ID, época más vieja), y la Novena
# vive justo debajo de 198,693, que era el mínimo de la BD.
#
# Ahora: antes de saltar se MUESTREA el bloque, y salte o no, el bloque queda
# anotado para poder volver.
# Dos densidades: el muestreo en línea es un triage rápido para decidir si un
# bloque merece caminarse; el de la fase de recuperación es denso, porque ahí ya se
# está decidiendo si un bloque se descarta de verdad.
MUESTRA = int(os.environ.get("BACKFILL_MUESTRA", "24"))
MUESTRA_DENSA = int(os.environ.get("BACKFILL_MUESTRA_DENSA", "120"))


def _ahora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _tabla_saltos(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS backfill_saltos (
        desde        INTEGER NOT NULL,   -- extremo BAJO del bloque (inclusive)
        hasta        INTEGER NOT NULL,   -- extremo ALTO del bloque (inclusive)
        detectado_at TEXT    NOT NULL,
        estado       TEXT    NOT NULL,   -- pendiente | vacio-muestreado | recorrido
        vivos        INTEGER,            -- cuántos respondieron 200 en el muestreo
        probados     INTEGER,
        revisado_at  TEXT,
        PRIMARY KEY (desde, hasta))""")
    conn.commit()


def _anotar_salto(conn, desde, hasta, estado, vivos=None, probados=None):
    conn.execute(
        "INSERT INTO backfill_saltos(desde,hasta,detectado_at,estado,vivos,probados,revisado_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(desde,hasta) DO UPDATE SET "
        "  estado=excluded.estado, vivos=excluded.vivos, probados=excluded.probados,"
        "  revisado_at=excluded.revisado_at",
        (desde, hasta, _ahora(), estado, vivos, probados,
         None if estado == "pendiente" else _ahora()))
    conn.commit()


def _unir_intervalos(filas) -> list:
    """Fusiona intervalos [(desde,hasta)] solapados o contiguos."""
    out: list = []
    for lo, hi in sorted((min(a, b), max(a, b)) for a, b in filas):
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _pct(parte: int, total: int) -> float:
    """Porcentaje que NUNCA puede pasar de 100. Si pasara, es un bug de conteo y
    hay que verlo, no esconderlo: se avisa."""
    if total <= 0:
        return 0.0
    v = 100.0 * parte / total
    if v > 100.001:
        log.warning("Cobertura calculada en %.1f%% (>100): hay doble conteo. "
                    "Reportando 100%%.", v)
        return 100.0
    return v


def _cobertura(conn) -> tuple:
    """(tesis, cuatrocientocuatros, span, sin_tocar, descartados_por_muestreo).

    Se reporta en CADA corrida. Sin esto, el backfill llevaba 46 días disparándose
    cada hora sin hacer nada —cursor en el piso— y nadie se enteró: el total de
    178,554 tesis se leía como un acervo completo.

    El universo de IDs (`span`) abarca las TRES fuentes: tesis, 404 registrados y
    bloques saltados. Sacándolo solo de `tesis` la cobertura salía >100%, porque los
    bloques saltados y los 404 caen por debajo del mínimo de `tesis` y quedaban
    fuera del denominador mientras se contaban en el numerador.
    """
    n = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
    try:
        n404 = conn.execute("SELECT COUNT(*) FROM registros_404").fetchone()[0]
    except sqlite3.OperationalError:
        n404 = 0
    try:
        bloques = [(r[0], r[1]) for r in conn.execute(
            "SELECT desde, hasta FROM backfill_saltos "
            "WHERE estado='vacio-muestreado' AND COALESCE(probados,0) >= ?",
            (MUESTRA_DENSA,))]
        extremos = tuple(conn.execute(
            "SELECT MIN(desde), MAX(hasta) FROM backfill_saltos").fetchone())
    except sqlite3.OperationalError:
        bloques, extremos = [], (None, None)

    # tuple() explícito: la conexión usa row_factory=sqlite3.Row y dos Row no se
    # pueden concatenar con +.
    candidatos = [x for x in (
        tuple(conn.execute("SELECT MIN(registro_digital), MAX(registro_digital) "
                           "FROM tesis").fetchone())
        + tuple(conn.execute("SELECT MIN(registro_digital), MAX(registro_digital) "
                             "FROM registros_404").fetchone())
        + tuple(extremos)) if x is not None]
    if not candidatos:
        return n, n404, 0, 0, 0
    lo, hi = min(candidatos), max(candidatos)
    span = hi - lo + 1

    # Los bloques se solapan entre sí; se unen y se recortan al span. Y de cada uno
    # se descuenta lo que ya está contado en tesis/404, para no sumarlo dos veces.
    muerto = 0
    recortados = [(max(lo, a), min(hi, b)) for a, b in bloques if min(hi, b) >= max(lo, a)]
    for a, b in _unir_intervalos(recortados):
        dentro = (conn.execute("SELECT COUNT(*) FROM tesis WHERE registro_digital "
                               "BETWEEN ? AND ?", (a, b)).fetchone()[0]
                  + conn.execute("SELECT COUNT(*) FROM registros_404 WHERE "
                                 "registro_digital BETWEEN ? AND ?", (a, b)).fetchone()[0])
        muerto += max(0, (b - a + 1) - dentro)
    return n, n404, span, max(0, span - n - n404 - muerto), muerto


def _muestrear(harvest, sjf, conn, present, known404, desde, hasta, n=None):
    """Prueba n IDs repartidos por [desde, hasta]. Devuelve (vivos, probados).

    Es la diferencia entre saltar por EVIDENCIA y saltar por corazonada. Lo que el
    muestreo encuentre se GUARDA: tirar una tesis real ya descargada sería pagar
    la llamada dos veces.
    """
    n = n or MUESTRA
    if hasta < desde:
        return [], 0
    paso = max(1, (hasta - desde + 1) // max(1, n))
    vivos = []
    probados = 0
    for reg in range(hasta, desde - 1, -paso):
        if probados >= n:
            break
        probados += 1
        if reg in present:
            vivos.append(reg)
            continue
        if reg in known404:
            continue
        try:
            st, raw = harvest.fetch_tesis(reg)
        except Exception as e:
            log.warning("muestreo: %d falló (%s)", reg, e)
            continue
        if st == 200 and raw:
            t = sjf.normalize_tesis(raw, reg)
            sjf.upsert_tesis(conn, t)
            conn.commit()
            present.add(reg)
            vivos.append(reg)
        time.sleep(THROTTLE_MS / 1000)
    return vivos, probados


def _recorrer(harvest, sjf, conn, present, known404, desde, hasta,
              presupuesto, permitir_salto=True):
    """Camina IDs hacia abajo desde `desde` hasta `hasta` (exclusive), gastando
    como máximo `presupuesto` intentos. Devuelve (ok, miss, cursor, gastados)."""
    ok = miss = gastados = rechazos = 0
    seguidos_404 = 0
    cursor = desde
    while gastados < presupuesto and cursor > hasta:
        cursor -= 1
        if cursor in present or cursor in known404:
            continue
        gastados += 1
        st, raw = harvest.fetch_tesis(cursor)
        if st == 200 and raw:
            t = sjf.normalize_tesis(raw, cursor)
            sjf.upsert_tesis(conn, t)
            present.add(cursor)
            ok += 1
            seguidos_404 = 0
            conn.commit()  # commit por registro → crash-safe
            log.info("[+%d] %d %s | %s", ok, cursor, raw.get("fechaPublicacion", ""),
                     (t.get("rubro") or "")[:55])
        elif st in (404, 410):
            if hasattr(sjf, "mark_404"):
                sjf.mark_404(conn, cursor)
            else:
                conn.execute("INSERT OR IGNORE INTO registros_404(registro_digital, first_seen_at) "
                             "VALUES(?,?)", (cursor, _ahora()))
            known404.add(cursor)
            miss += 1
            seguidos_404 += 1
            if permitir_salto and seguidos_404 >= CONSECUTIVE_404_THRESHOLD:
                bloque_hasta = cursor - 1
                bloque_desde = max(hasta, cursor - CONSECUTIVE_404_JUMP)
                # Muestrear ANTES de descartar. 50 huecos seguidos son evidencia
                # de 50 huecos, no de 50,000.
                vivos, probados = _muestrear(harvest, sjf, conn, present, known404,
                                             bloque_desde, bloque_hasta)
                seguidos_404 = 0
                if vivos:
                    # Hay vida en el bloque. Se salta hasta el ID vivo MÁS ALTO —el
                    # desierto de en medio se sondeó— y lo que quedó por encima se
                    # anota PENDIENTE, no vacío: un muestreo disperso (1 sondeo por
                    # cada ~2,500 IDs) no alcanza para descartar nada. Aplazar con
                    # registro es honesto; descartar con esa evidencia es el bug que
                    # costó los años 1997-2011.
                    alto = max(vivos)
                    log.info("Bloque %d–%d: %d/%d vivos → aterrizo en %d; %d–%d queda "
                             "PENDIENTE de un muestreo denso",
                             bloque_desde, bloque_hasta, len(vivos), probados,
                             alto, alto + 1, bloque_hasta)
                    if bloque_hasta > alto:
                        _anotar_salto(conn, alto + 1, bloque_hasta, "pendiente")
                    cursor = alto + 1
                else:
                    cursor = bloque_desde
                    log.info("Bloque %d–%d: 0 vivos en %d sondeos → saltado (anotado)",
                             bloque_desde, bloque_hasta, probados)
                    _anotar_salto(conn, bloque_desde, bloque_hasta,
                                  "vacio-muestreado", 0, probados)
            if miss % 50 == 0:
                conn.commit()
        else:
            # 403 y demás: NO se marcan (se reintentan en otra corrida), pero sí se
            # cuentan. Sin esto, una corrida entera bloqueada por el SJF reportaba
            # "+0 nuevas, 0 404" — idéntico a "no había nada que hacer". El 403 del
            # priming de sesión ya salía en el log y se leía como ruido.
            rechazos += 1
            if rechazos in (1, 25) or rechazos % 200 == 0:
                log.warning("El SJF respondió %s en %d de %d intentos de esta corrida",
                            st, rechazos, gastados)
        time.sleep(THROTTLE_MS / 1000)
    conn.commit()
    return ok, miss, cursor, gastados, rechazos


def importar_saltos_del_log(conn, ruta: str) -> int:
    """Reconstruye `backfill_saltos` desde el histórico del log.

    Los 425 saltos que ya ocurrieron solo existen como líneas de log ('cursor → N'),
    porque el código viejo no los guardaba en ninguna parte. Cada línea implica el
    bloque [N+1, N+CONSECUTIVE_404_JUMP]. Recuperarlos es la única forma de saber
    QUÉ hay que volver a recorrer sin re-caminar los 2 millones de IDs.
    """
    import re
    pat = re.compile(r"saltando\s+(\d+)\s+IDs,\s*cursor\s*→\s*(\d+)")
    _tabla_saltos(conn)
    n = 0
    with open(ruta, "r", errors="replace") as fh:
        for linea in fh:
            m = pat.search(linea)
            if not m:
                continue
            salto, destino = int(m.group(1)), int(m.group(2))
            _anotar_salto(conn, destino + 1, destino + salto, "pendiente")
            n += 1
    log.info("Importados %d saltos del log → backfill_saltos", n)
    return n


def main() -> int:
    if "--importar-log" in sys.argv:
        i = sys.argv.index("--importar-log")
        ruta = sys.argv[i + 1] if len(sys.argv) > i + 1 else \
            "/opt/openclaw/logs/sjf-backfill.log"
        if not Path(ruta).exists():
            log.error("No existe el log %s", ruta)
            return 1
        conn = sqlite3.connect(DB_PATH)
        try:
            n = importar_saltos_del_log(conn, ruta)
            pend = conn.execute("SELECT COUNT(*), MIN(desde), MAX(hasta) FROM backfill_saltos "
                                "WHERE estado='pendiente'").fetchone()
            print(f"{n} líneas de salto leídas. Bloques pendientes: {pend[0]:,} "
                  f"(IDs {pend[1]}–{pend[2]})" if pend[0] else f"{n} líneas leídas.")
        finally:
            conn.close()
        return 0

    if not Path(HARVEST_PATH).exists():
        log.error("No existe el harvester en %s", HARVEST_PATH)
        return 1
    harvest = _load(HARVEST_PATH, "sjf_harvest")   # fetch_tesis + _prime

    # El mismo interruptor que el harvester: es el MISMO servidor y el MISMO WAF.
    # Sin esto, el backfill (cada hora) seguiría tocando la puerta mientras el
    # harvester se aparta, y el bloqueo no se levantaría nunca.
    if hasattr(harvest, "waf_bloqueado"):
        _c = sqlite3.connect(DB_PATH)
        try:
            espera = harvest.waf_bloqueado(_c)
        finally:
            _c.close()
        if espera:
            log.warning("%s", espera)
            return 0
    sjf = _load(SCRAPER_PATH, "sjf_biblioteca")    # normalize_tesis + upsert_tesis (+ mark_404)
    harvest._prime()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if hasattr(sjf, "SCHEMA"):
        conn.executescript(sjf.SCHEMA)
    conn.execute("CREATE TABLE IF NOT EXISTS progress (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS registros_404 "
                 "(registro_digital INTEGER PRIMARY KEY, first_seen_at TEXT)")
    _tabla_saltos(conn)
    conn.commit()

    max_reg = conn.execute("SELECT COALESCE(MAX(registro_digital),0) FROM tesis").fetchone()[0]
    if not max_reg:
        log.error("BD vacía — corre primero el harvester")
        return 1

    row = conn.execute("SELECT value FROM progress WHERE key='backfill_cursor'").fetchone()
    cursor = int(row[0]) if row and row[0] else max_reg
    cursor = min(cursor, max_reg)

    present = {r[0] for r in conn.execute("SELECT registro_digital FROM tesis")}
    known404 = {r[0] for r in conn.execute("SELECT registro_digital FROM registros_404")}

    hallados, n404, span, nunca, muerto = _cobertura(conn)
    log.info("Cobertura: %d tesis + %d 404 confirmados + %d descartados por muestreo "
             "denso = %.1f%% de %d IDs. SIN TOCAR: %d",
             hallados, n404, muerto, _pct(hallados + n404 + muerto, span), span, nunca)
    log.info("Cursor en %d (piso %d), lote %d", cursor, FLOOR, BATCH)

    ok = miss = rechazos = 0
    presupuesto = BATCH

    # ── Fase 1: seguir bajando, si queda camino ─────────────────────────────
    if cursor > FLOOR:
        o, m, cursor, gastados, rech = _recorrer(harvest, sjf, conn, present, known404,
                                                 cursor, FLOOR, presupuesto)
        ok += o; miss += m; presupuesto -= gastados; rechazos += rech
        conn.execute(
            "INSERT INTO progress(key,value,updated_at) VALUES('backfill_cursor',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (str(cursor), _ahora()))
        conn.commit()

    # ── Fase 2: recuperar los bloques que se saltaron ───────────────────────
    # Antes, con el cursor en el piso el job no hacía NADA y salía en silencio:
    # 46 días de corridas vacías cada hora. Ahora el piso es el comienzo del
    # trabajo de recuperación, no el final.
    while presupuesto > 0:
        # Prioridad: primero los pendientes (hay vida confirmada cerca), y solo
        # cuando no queda ninguno, los que se marcaron vacíos con el muestreo RALO
        # del triage. Un bloque solo se da por muerto tras un muestreo denso; así el
        # trabajo converge sin descartar nada con 24 sondeos sobre 60,000 IDs.
        blq = conn.execute(
            "SELECT desde, hasta FROM backfill_saltos WHERE estado='pendiente' "
            "ORDER BY hasta DESC LIMIT 1").fetchone()
        if not blq:
            blq = conn.execute(
                "SELECT desde, hasta FROM backfill_saltos "
                "WHERE estado='vacio-muestreado' AND COALESCE(probados,0) < ? "
                "ORDER BY hasta DESC LIMIT 1", (MUESTRA_DENSA,)).fetchone()
            if blq:
                log.info("Sin pendientes: reviso con muestreo denso el bloque %d–%d, "
                         "que se descartó con pocos sondeos", blq["desde"], blq["hasta"])
        if not blq:
            break
        desde, hasta = blq["desde"], blq["hasta"]
        vivos, probados = _muestrear(harvest, sjf, conn, present, known404,
                                     desde, hasta, n=MUESTRA_DENSA)
        if not vivos:
            log.info("Bloque pendiente %d–%d: vacío en %d sondeos → descartado",
                     desde, hasta, probados)
            _anotar_salto(conn, desde, hasta, "vacio-muestreado", 0, probados)
            presupuesto -= probados
            continue
        log.info("Bloque pendiente %d–%d: %d/%d vivos → recorriendo completo",
                 desde, hasta, len(vivos), probados)
        o, m, parado, gastados, rech = _recorrer(harvest, sjf, conn, present, known404,
                                                 hasta + 1, desde - 1, presupuesto,
                                                 permitir_salto=False)
        ok += o; miss += m; presupuesto -= gastados + probados; rechazos += rech
        if parado <= desde:
            _anotar_salto(conn, desde, hasta, "recorrido", len(vivos), probados)
        else:
            # No alcanzó el presupuesto. Se parte de VERDAD: la fila original se va y
            # quedan dos que no se solapan — lo caminado y lo que falta. Dejar la fila
            # grande como "recorrido" mentiría sobre lo que ya se cubrió.
            conn.execute("DELETE FROM backfill_saltos WHERE desde=? AND hasta=?",
                         (desde, hasta))
            _anotar_salto(conn, parado, hasta, "recorrido", len(vivos), probados)
            _anotar_salto(conn, desde, parado - 1, "pendiente")
            log.info("Bloque partido: %d–%d recorrido, queda pendiente %d–%d",
                     parado, hasta, desde, parado - 1)
            break

    hallados, n404, span, nunca, muerto = _cobertura(conn)
    pend = conn.execute("SELECT COUNT(*) FROM backfill_saltos WHERE estado='pendiente'").fetchone()[0]
    conn.close()
    log.info("== backfill: +%d nuevas, %d 404, %d rechazados. Acervo: %d tesis. "
             "Cobertura %.1f%% (%d IDs sin tocar, %d bloques pendientes)",
             ok, miss, rechazos, hallados, _pct(hallados + n404 + muerto, span),
             nunca, pend)
    if rechazos and rechazos >= max(1, ok + miss):
        if hasattr(harvest, "waf_marcar"):
            _c = sqlite3.connect(DB_PATH)
            try:
                harvest.waf_marcar(_c)
            finally:
                _c.close()
        log.error("El SJF rechazó %d de %d intentos: esta corrida NO avanzó por "
                  "BLOQUEO, no por falta de trabajo. Revisa si el server está vetado "
                  "(403) antes de suponer que el acervo está completo.",
                  rechazos, rechazos + ok + miss)
    if not ok and not miss and not rechazos and not pend and nunca:
        log.warning("Esta corrida no intentó NADA y quedan %d IDs SIN TOCAR sin ningún "
                    "bloque pendiente que los cubra. Recupera el histórico de saltos "
                    "con: sjf_backfill.py --importar-log", nunca)
    return 0


if __name__ == "__main__":
    sys.exit(main())
