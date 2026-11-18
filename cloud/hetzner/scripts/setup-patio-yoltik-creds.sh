#!/usr/bin/env bash
# setup-patio-yoltik-creds.sh — guarda en el servidor las cadenas de conexión de Patio Yoltik (sin mostrarlas).
# Uso:  sudo bash setup-patio-yoltik-creds.sh
# Pide: (1) cadena del rol patio_lectura  (2) cadena del rol patio_escritura (opcional, Enter para omitir).
set -uo pipefail
SYSTEM_USER="${SYSTEM_USER:-polo}"; DIR=/opt/openclaw/credentials; F="$DIR/patio-yoltik.env"
mkdir -p "$DIR"; [[ -f "$F" ]] && set -a && source "$F" && set +a
chk() { case "$1" in postgres://*|postgresql://*) return 0;; *) return 1;; esac; }
read -rsp "Cadena de LECTURA (postgresql://patio_lectura....) [Enter = conservar]: " R; echo
[[ -n "$R" ]] && PATIO_YOLTIK_DATABASE_URL="$R"
read -rsp "Cadena de ESCRITURA (postgresql://patio_escritura....) [Enter = conservar/omitir]: " W; echo
[[ -n "$W" ]] && PATIO_YOLTIK_DATABASE_URL_WRITE="$W"
chk "${PATIO_YOLTIK_DATABASE_URL:-}" || { echo "✗ La cadena de lectura debe empezar con postgresql://"; exit 1; }
[[ -z "${PATIO_YOLTIK_DATABASE_URL_WRITE:-}" ]] || chk "$PATIO_YOLTIK_DATABASE_URL_WRITE" || { echo "✗ La de escritura debe empezar con postgresql://"; exit 1; }
umask 077
{ echo "# Generado por setup-patio-yoltik-creds.sh — $(date -Iseconds)"
  echo "PATIO_YOLTIK_DATABASE_URL='${PATIO_YOLTIK_DATABASE_URL}'"
  echo "PATIO_YOLTIK_DATABASE_URL_WRITE='${PATIO_YOLTIK_DATABASE_URL_WRITE:-}'"; } > "$F"
chmod 0600 "$F"; chown "$SYSTEM_USER":"$SYSTEM_USER" "$F"
echo "✓ Guardado en $F (0600)"
python3 -c "import psycopg2" 2>/dev/null || pip3 install --quiet --break-system-packages psycopg2-binary
systemctl restart openclaw-gateway scheduler slack-bridge && echo "✓ Servicios reiniciados"
