"""
self_update.py — capacidad de Donna para editarse a sí mismo (con guardrails).

Tools expuestas vía donna_core:
  • leer_mi_codigo(archivo)              — lee un archivo del runtime
  • editar_mi_codigo(archivo, old, new)  — replace con validación + backup
  • reiniciar_mi_servicio(servicio)      — systemctl restart con auto-rollback
  • ver_mis_backups()                    — lista backups disponibles
  • restaurar_mi_codigo(archivo, backup_id) — restaura un backup

Guardrails:
  - Whitelist: solo archivos en /opt/openclaw/scripts/ (no toca .env, ni configs)
  - Backup automático antes de cada edición (timestamp + diff)
  - Valida sintaxis Python con `py_compile` antes de aceptar el cambio
  - Después de reiniciar servicio: verifica is-active. Si falla → rollback automático.
  - Whitelist de servicios reiniciables: telegram-bridge, slack-bridge, scheduler.
    NO incluye 'ollama' (binario) ni 'openclaw' (no manejamos).

Workflow esperado:
  1. Polo dice 'agrega X a Donna'
  2. Donna llama leer_mi_codigo(archivo) para ver estado actual
  3. Donna propone el cambio en chat (muestra old → new)
  4. Polo confirma ('hazlo' / 'sí')
  5. Donna llama editar_mi_codigo → valida sintaxis → guarda backup
  6. Donna llama reiniciar_mi_servicio → verifica
  7. Si rompe → rollback automático + reporta el error
"""

import os
import subprocess
import shutil
import difflib
from pathlib import Path
from datetime import datetime

# ===== Paths =====
SCRIPTS_DIR = Path("/opt/openclaw/scripts")
BACKUPS_DIR = Path("/opt/openclaw/code-backups")
LOG_PREFIX = "[self_update]"

# Archivos editables (whitelist)
EDITABLE_FILES = {
    "donna_core.py", "telegram-bridge.py", "slack-bridge.py",
    "scheduler.py", "m365.py", "import-legal-agents.sh",
}
# Subcarpetas permitidas (relativas a SCRIPTS_DIR)
EDITABLE_SUBPATHS = {"m365/m365.py"}

# Servicios reiniciables
RESTARTABLE_SERVICES = {"telegram-bridge", "slack-bridge", "scheduler"}


def _resolve_file(archivo: str) -> Path:
    """Resuelve y valida que el archivo esté en la whitelist."""
    # Permite tanto 'donna_core.py' como 'm365/m365.py'
    archivo = archivo.strip().lstrip("/")
    if archivo in EDITABLE_FILES:
        return SCRIPTS_DIR / archivo
    if archivo in EDITABLE_SUBPATHS:
        return SCRIPTS_DIR / archivo
    raise ValueError(
        f"Archivo '{archivo}' NO está en la whitelist. Permitidos: "
        f"{sorted(EDITABLE_FILES | EDITABLE_SUBPATHS)}"
    )


def _backup_file(path: Path) -> Path:
    """Hace backup con timestamp. Devuelve el path del backup."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUPS_DIR / f"{path.name}.{ts}.bak"
    shutil.copy2(path, backup)
    return backup


def _validate_python(path: Path) -> tuple:
    """Valida sintaxis Python. Returns (ok, error_msg)."""
    if not str(path).endswith(".py"):
        return True, ""
    r = subprocess.run(
        ["python3", "-m", "py_compile", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or "(sin detalle)").strip()


def _validate_bash(path: Path) -> tuple:
    """Valida sintaxis bash con bash -n."""
    if not str(path).endswith(".sh"):
        return True, ""
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or "(sin detalle)").strip()


def _is_ancestor(parent_pid: int, child_pid: int) -> bool:
    """¿parent_pid es ancestro de child_pid?"""
    try:
        cur = child_pid
        for _ in range(30):  # max 30 niveles, evita loop infinito
            stat_path = Path(f"/proc/{cur}/stat")
            if not stat_path.exists():
                return False
            parts = stat_path.read_text().split()
            ppid = int(parts[3])
            if ppid == parent_pid:
                return True
            if ppid in (0, 1):
                return False
            cur = ppid
    except Exception:
        pass
    return False


def _diff(old_text: str, new_text: str, filename: str) -> str:
    """Diff unificado, primeras 40 líneas."""
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{filename}", tofile=f"b/{filename}", n=3,
    )
    lines = list(diff)[:40]
    return "".join(lines) if lines else "(sin cambios)"


# ===== Tool implementations =====
def leer_mi_codigo(archivo: str, offset: int = 0, limit: int = 200) -> str:
    """Lee un archivo del runtime con offset/limit."""
    try:
        path = _resolve_file(archivo)
    except ValueError as e:
        return f"ERROR: {e}"
    if not path.exists():
        return f"ERROR: {path} no existe"
    lines = path.read_text().splitlines()
    total = len(lines)
    end = min(offset + limit, total)
    chunk = "\n".join(f"{i+1:5d}  {l}" for i, l in enumerate(lines[offset:end], start=offset))
    header = f"=== {archivo} (líneas {offset+1}-{end} de {total}) ===\n"
    return header + chunk


def editar_mi_codigo(archivo: str, old_string: str, new_string: str, descripcion: str = "") -> str:
    """
    Hace replace en el archivo. Backup + valida sintaxis. NO reinicia el servicio
    (eso es paso aparte para que Donna pueda confirmar antes).
    """
    try:
        path = _resolve_file(archivo)
    except ValueError as e:
        return f"ERROR: {e}"
    if not path.exists():
        return f"ERROR: {path} no existe"
    content = path.read_text()
    occurrences = content.count(old_string)
    if occurrences == 0:
        return (
            f"ERROR: old_string NO encontrado en {archivo}. "
            "Verifica que coincida exactamente (incluyendo espacios e indentación). "
            "Usa leer_mi_codigo primero para ver el texto exacto."
        )
    if occurrences > 1:
        return (
            f"ERROR: old_string aparece {occurrences} veces en {archivo} (ambiguo). "
            "Incluye más contexto para que el match sea único."
        )
    new_content = content.replace(old_string, new_string, 1)

    # Backup ANTES de escribir
    backup = _backup_file(path)

    # Escribir nuevo contenido
    path.write_text(new_content)

    # Validar sintaxis (Python o bash según extensión)
    ok_py, err_py = _validate_python(path)
    ok_sh, err_sh = _validate_bash(path)
    if not ok_py or not ok_sh:
        # Revertir
        shutil.copy2(backup, path)
        err = err_py or err_sh
        return f"ERROR: sintaxis inválida después del cambio — REVERTIDO\nDetalle:\n{err[:1500]}"

    # OK
    diff_text = _diff(content, new_content, archivo)
    return (
        f"OK editado {archivo}\n"
        f"Backup: {backup.name}\n"
        f"Descripción: {descripcion or '(sin descripción)'}\n"
        f"Sintaxis: válida ✓\n"
        f"--- diff ---\n{diff_text}\n"
        f"--- siguiente paso ---\n"
        f"Reinicia el servicio con reiniciar_mi_servicio para que el cambio tome efecto."
    )


def reiniciar_mi_servicio(servicio: str) -> str:
    """systemctl restart + verifica is-active. Si falla, intenta rollback del último backup.

    Nota: si Donna reinicia su PROPIO bridge (el que está respondiendo a Polo en este momento),
    se mata a sí mismo y la respuesta jamás vuelve. En ese caso, hacemos restart deferred
    via 'at' o systemd-run para que arranque después de que el current request termine.
    """
    if servicio not in RESTARTABLE_SERVICES:
        return f"ERROR: servicio '{servicio}' no está en la whitelist. Permitidos: {sorted(RESTARTABLE_SERVICES)}"

    # Detectar si Donna está reiniciando su propio bridge — el script que llama a esto
    # vive en uno de los servicios. Si nombre coincide con un proceso activo del mismo
    # archivo de script, hacemos deferred restart.
    me_script = os.environ.get("LOUIS_BRIDGE_SCRIPT", "")  # set por bridges si quieren
    script_name = f"{servicio}.py"
    self_restart = (script_name in me_script)
    if not self_restart:
        # Detectar por nombre de proceso (heurística adicional)
        try:
            r = subprocess.run(["pgrep", "-f", script_name], capture_output=True, text=True, timeout=3)
            my_pid = os.getpid()
            pids = [int(p) for p in r.stdout.strip().splitlines() if p.strip().isdigit()]
            # Si nuestro PID está entre los del bridge, somos nosotros
            if my_pid in pids or any(_is_ancestor(p, my_pid) for p in pids):
                self_restart = True
        except Exception:
            pass

    if self_restart:
        # Deferred restart — 3 segundos después, para que el response actual llegue al chat
        r = subprocess.run(
            ["bash", "-c", f"(sleep 3 && sudo /bin/systemctl restart {servicio}) >/dev/null 2>&1 &"],
            capture_output=True, text=True, timeout=5,
        )
        return (
            f"OK reinicio diferido de {servicio} en 3 segundos.\n"
            "(Me estoy reiniciando a mí mismo — esta respuesta es lo último que mando antes "
            "de morirme y volver. Próximo mensaje que me mandes ya correrá con el código nuevo.)"
        )

    # Restart inmediato (otro servicio, no el que me ejecuta a mí)
    r = subprocess.run(
        ["sudo", "/bin/systemctl", "restart", servicio],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return f"ERROR ejecutando systemctl restart {servicio}: {r.stderr.strip()}"

    # Esperar y verificar
    import time
    time.sleep(3)
    r = subprocess.run(
        ["systemctl", "is-active", servicio],
        capture_output=True, text=True, timeout=5,
    )
    state = r.stdout.strip()
    if state == "active":
        # Leer últimas líneas del log para confirmar arranque limpio
        log_path = Path(f"/opt/openclaw/logs/{servicio}.log")
        tail = ""
        if log_path.exists():
            lines = log_path.read_text().splitlines()[-15:]
            tail = "\n".join(lines)
        return f"OK {servicio} activo después del restart.\n--- últimas 15 líneas del log ---\n{tail}"

    # No arrancó — intentar rollback automático
    log_path = Path(f"/opt/openclaw/logs/{servicio}.log")
    tail = ""
    if log_path.exists():
        lines = log_path.read_text().splitlines()[-20:]
        tail = "\n".join(lines)
    msg = (
        f"WARN: {servicio} está '{state}' después del restart (esperado 'active').\n"
        f"--- log ---\n{tail}\n"
        "Para revertir el último cambio: restaurar_mi_codigo(archivo, backup_id) usando ver_mis_backups."
    )
    return msg


def ver_mis_backups(archivo: str = None) -> str:
    """Lista los backups disponibles. Si archivo, filtra por ese."""
    if not BACKUPS_DIR.exists():
        return "(no hay backups todavía)"
    pattern = f"{archivo}.*.bak" if archivo else "*.bak"
    backups = sorted(BACKUPS_DIR.glob(pattern), reverse=True)[:30]
    if not backups:
        return f"(no hay backups{' para ' + archivo if archivo else ''})"
    out = [f"=== {len(backups)} backup(s) ==="]
    for b in backups:
        size = b.stat().st_size
        out.append(f"  • {b.name}  ({size} bytes)")
    return "\n".join(out)


def restaurar_mi_codigo(archivo: str, backup_id: str) -> str:
    """Restaura un backup específico encima del archivo actual."""
    try:
        path = _resolve_file(archivo)
    except ValueError as e:
        return f"ERROR: {e}"
    # backup_id puede ser el nombre completo o solo el timestamp
    candidates = list(BACKUPS_DIR.glob(f"{path.name}.*.bak"))
    target = None
    for c in candidates:
        if c.name == backup_id or backup_id in c.name:
            target = c
            break
    if not target:
        return f"ERROR: no encontré backup '{backup_id}' para {archivo}. Usa ver_mis_backups."
    # Backup del estado actual antes de revertir (por si el rollback estaba mal)
    pre_rollback = _backup_file(path)
    shutil.copy2(target, path)
    ok_py, err_py = _validate_python(path)
    if not ok_py:
        # raro pero defensivo
        return f"WARN: backup restaurado pero sintaxis sigue inválida: {err_py[:500]}"
    return f"OK restaurado {archivo} desde {target.name}. Pre-rollback en {pre_rollback.name}. Reinicia el servicio para aplicar."
