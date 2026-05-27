#!/usr/bin/env bash
# update-kawiil-db-url.sh — Actualiza el DATABASE_URL de kawiil-central sin tener
# que correr el setup interactivo completo. Útil cuando solo el password Postgres
# era incorrecto.
#
# Uso (en Hetzner, como root o sudo):
#   sudo bash update-kawiil-db-url.sh 'postgresql://postgres.xxxx:PASSWORD@aws-X-region.pooler.supabase.com:6543/postgres'
#
# El argumento debe venir entre comillas SIMPLES para que no expanda variables
# del shell ($ % &).

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

DB_URL="${1:-}"
if [[ -z "$DB_URL" ]]; then
  fail "Falta argumento — uso: sudo bash $0 'postgresql://...'"
fi
if [[ "$DB_URL" == *"[YOUR-PASSWORD]"* ]]; then
  fail "La cadena todavía tiene [YOUR-PASSWORD] sin reemplazar. Pon el password real."
fi
if [[ "$DB_URL" != postgresql://* && "$DB_URL" != postgres://* ]]; then
  fail "Cadena no parece postgres URL (debe empezar con postgresql:// o postgres://)"
fi

KC_ENV="/opt/openclaw/credentials/kawiil-central.env"
OC_ENV="/opt/openclaw/openclaw.env"

# Backup
cp "$KC_ENV" "$KC_ENV.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
cp "$OC_ENV" "$OC_ENV.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true

# Actualiza credentials file (con comillas)
sed -i '/^KAWIIL_CENTRAL_DATABASE_URL=/d' "$KC_ENV" 2>/dev/null || true
echo "KAWIIL_CENTRAL_DATABASE_URL=\"$DB_URL\"" >> "$KC_ENV"
chmod 0600 "$KC_ENV"

# Actualiza openclaw.env (sin comillas — systemd EnvironmentFile no las parsea bien)
sed -i '/^KAWIIL_CENTRAL_DATABASE_URL=/d' "$OC_ENV" 2>/dev/null || true
echo "KAWIIL_CENTRAL_DATABASE_URL=$DB_URL" >> "$OC_ENV"

ok "DATABASE_URL actualizado en ambos archivos"

# Test conectividad
echo ""
echo "Test conectividad Postgres…"
python3 - <<PYEOF
import psycopg2, sys
url = """$DB_URL"""
try:
    conn = psycopg2.connect(url, connect_timeout=8)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    n = cur.fetchone()[0]
    print(f"  ✓ Postgres OK — {n} tablas en public")
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name LIMIT 20")
    tables = [r[0] for r in cur.fetchall()]
    if tables:
        print(f"  Tablas: {', '.join(tables)}")
    conn.close()
except Exception as e:
    print(f"  ✗ FALLÓ: {e}")
    sys.exit(1)
PYEOF

if [[ $? -ne 0 ]]; then
  warn "Test falló — revisa la cadena. No reinicio servicios para no romper lo que estaba."
  exit 1
fi

# Reinicia servicios para que tomen el nuevo env
echo ""
echo "Reiniciando servicios para tomar nuevo env…"
for svc in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  systemctl restart "$svc" 2>/dev/null && ok "$svc reiniciado" || warn "$svc no se pudo reiniciar"
done

echo ""
ok "Listo. Prueba en Telegram: 'qué tablas tiene kawiil-central'"
