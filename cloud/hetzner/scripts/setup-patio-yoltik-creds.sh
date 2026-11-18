#!/usr/bin/env bash
# setup-patio-yoltik-creds.sh — guarda en el servidor las cadenas de conexión de Patio Yoltik (sin mostrarlas).
# Uso:  sudo bash setup-patio-yoltik-creds.sh
# Cuando pida la cadena: PEGA y presiona Enter (no se ve lo que pegas; te dirá cuántos caracteres recibió).
# Si el pegado no entra (campo invisible): sudo VISIBLE=1 bash setup-patio-yoltik-creds.sh  (muestra lo que pegas).
# NO la escribas como comando en la terminal: se guardaría en el historial.
set -uo pipefail
SYSTEM_USER="${SYSTEM_USER:-polo}"; DIR=/opt/openclaw/credentials; F="$DIR/patio-yoltik.env"
mkdir -p "$DIR"; [[ -f "$F" ]] && { set -a; source "$F"; set +a; }
TTY=/dev/tty; [[ -r $TTY ]] || TTY=/dev/stdin

pedir() {  # $1=etiqueta  $2=variable
  local v
  if [[ "${VISIBLE:-0}" == "1" ]]; then read -rp "$1 [Enter = conservar lo actual]: " v < "$TTY"
  else read -rsp "$1 [Enter = conservar lo actual]: " v < "$TTY"; echo >&2; fi
  v="${v//[$'\r\n ']/}"
  if [[ -z "$v" ]]; then echo "  · sin cambios (no recibí nada)" >&2; return; fi
  echo "  · recibí ${#v} caracteres" >&2
  printf -v "$2" '%s' "$v"
}
valida() {  # $1=etiqueta  $2=valor  $3=usuario esperado
  [[ "$2" == postgres://* || "$2" == postgresql://* ]] || { echo "✗ $1: debe empezar con postgresql://"; return 1; }
  [[ "$2" == *REGION* || "$2" == *USUARIO* || "$2" == *CONTRASENA* ]] && { echo "✗ $1: quedó texto de ejemplo (REGION/USUARIO/CONTRASENA). Copia la cadena REAL de Supabase → Connect → Session pooler."; return 1; }
  [[ "$2" == *"//$3."* || "$2" == *"//$3:"* ]] || { echo "✗ $1: el usuario debe ser $3.<ref-del-proyecto>"; return 1; }
  return 0
}
pedir "Cadena de LECTURA  (usuario patio_lectura.…)" PATIO_YOLTIK_DATABASE_URL
pedir "Cadena de ESCRITURA (usuario patio_escritura.…, Enter para omitir)" PATIO_YOLTIK_DATABASE_URL_WRITE
[[ -n "${PATIO_YOLTIK_DATABASE_URL:-}" ]] || { echo "✗ Falta la cadena de lectura."; exit 1; }
valida "lectura" "$PATIO_YOLTIK_DATABASE_URL" patio_lectura || exit 1
[[ -z "${PATIO_YOLTIK_DATABASE_URL_WRITE:-}" ]] || valida "escritura" "$PATIO_YOLTIK_DATABASE_URL_WRITE" patio_escritura || exit 1

python3 -c "import psycopg2" 2>/dev/null || pip3 install --quiet --break-system-packages psycopg2-binary

probar() {  # $1=etiqueta $2=url  → conecta de verdad y muestra solo ✓/✗ (nunca la cadena)
  PU="$2" python3 - "$1" <<'PY'
import os, sys, psycopg2
try:
    c = psycopg2.connect(os.environ["PU"], connect_timeout=10); cur = c.cursor()
    cur.execute("select current_user, (select count(*) from information_schema.tables where table_schema='public')")
    u, n = cur.fetchone(); print(f"  ✓ {sys.argv[1]}: conecta como {u.split('.')[0]} ({n} tablas visibles)")
except Exception as e:
    msg = str(e).strip().splitlines()[0][:160]; print(f"  ✗ {sys.argv[1]}: {msg}"); sys.exit(1)
PY
}
OKALL=0
probar "lectura" "$PATIO_YOLTIK_DATABASE_URL" || OKALL=1
[[ -z "${PATIO_YOLTIK_DATABASE_URL_WRITE:-}" ]] || probar "escritura" "$PATIO_YOLTIK_DATABASE_URL_WRITE" || OKALL=1
if [[ $OKALL -ne 0 ]]; then echo "✗ No guardé nada: corrige la cadena (usuario, contraseña, host) y vuelve a correr el script."; exit 1; fi

umask 077
{ echo "# Generado por setup-patio-yoltik-creds.sh — $(date -Iseconds)"
  echo "PATIO_YOLTIK_DATABASE_URL='${PATIO_YOLTIK_DATABASE_URL}'"
  echo "PATIO_YOLTIK_DATABASE_URL_WRITE='${PATIO_YOLTIK_DATABASE_URL_WRITE:-}'"; } > "$F"
chmod 0600 "$F"; chown "$SYSTEM_USER":"$SYSTEM_USER" "$F"
echo "✓ Guardado en $F (0600)"
systemctl restart openclaw-gateway scheduler slack-bridge && echo "✓ Servicios reiniciados"
