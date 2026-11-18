#!/usr/bin/env python3
"""donna-passwd — administra el usuario y la contraseña con los que se entra a la app. Se corre EN EL SERVIDOR.

  python3 donna-passwd.py polo                 crea o cambia la contraseña de «polo» (la pide sin mostrarla)
  python3 donna-passwd.py --listar             usuarios y sesiones abiertas (sin secretos)
  python3 donna-passwd.py --cerrar-sesiones    cierra TODAS las sesiones (p. ej. si pierdes un teléfono)
  python3 donna-passwd.py --cerrar-sesiones polo
  python3 donna-passwd.py --borrar polo        elimina el usuario y sus sesiones

La contraseña se guarda solo como hash (scrypt) en /opt/openclaw/state/app_users.json (0600).
No hace falta reiniciar nada: el gateway lee el archivo en cada intento de entrada.
"""
import os
import sys
import time
import getpass

AQUI = os.path.dirname(os.path.abspath(__file__))
for d in ("/opt/openclaw/scripts", os.path.join(AQUI, "..", "services")):
    if os.path.exists(os.path.join(d, "app_auth.py")):
        sys.path.insert(0, d)
        break
import app_auth as A  # noqa: E402


def _fecha(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--listar":
        us = A.list_users()
        print("Usuarios:", ", ".join(us) if us else "(ninguno — crea uno con: donna-passwd.py polo)")
        for s in A.list_sessions():
            print(f"  · {s['user']:<10} último uso {_fecha(s['last'])}  vence {_fecha(s['exp'])}  {s['device'][:60] or '?'}")
        return 0
    if argv[0] == "--cerrar-sesiones":
        n = A.revoke_all(argv[1] if len(argv) > 1 else None)
        print(f"Sesiones cerradas: {n}")
        return 0
    if argv[0] == "--borrar":
        if len(argv) < 2:
            print("Falta el usuario.")
            return 2
        print("Usuario eliminado." if A.delete_user(argv[1]) else "Ese usuario no existe.")
        return 0

    user = argv[0]
    print(f"Contraseña para «{user}» (mínimo {A.MIN_PASSWORD_LEN} caracteres; mejor una frase larga). No se mostrará al escribirla.")
    p1 = getpass.getpass("Contraseña: ")
    p2 = getpass.getpass("Repítela:    ")
    if p1 != p2:
        print("✗ No coinciden. No se cambió nada.")
        return 1
    err = A.set_password(user, p1)
    if err:
        print(f"✗ {err} No se cambió nada.")
        return 1
    n = A.revoke_all(user)            # al cambiar la contraseña, las sesiones anteriores dejan de valer
    print(f"✓ Contraseña guardada para «{user}»." + (f" Se cerraron {n} sesión(es) anteriores." if n else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
