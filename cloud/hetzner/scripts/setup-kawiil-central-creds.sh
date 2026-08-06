#!/usr/bin/env bash
# setup-kawiil-central-creds.sh — Configura las credenciales que Donna usa
# para operar contra Kawiil Central EN PRODUCCIÓN (Vercel + Supabase).
#
# NO clona el repo. NO baja código. Solo guarda credenciales en /opt/openclaw/
# y se las inyecta en openclaw.env para que los tools de Donna las vean.
#
# kawiil-central vive en:
#   - Frontend: https://www.kawiil-central.mx  (Vercel)
#   - DB:       Supabase (proyecto privado de Polo)
#
# Donna se conecta directo a Supabase para:
#   - Listar y filtrar proyectos
#   - Crear, actualizar, mover tareas
#   - Registrar avances/comentarios en tareas
#   - Consultar estado general
#
# Uso (en Hetzner como root o sudo):
#   sudo bash setup-kawiil-central-creds.sh

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "  \033[1;36m· %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

SYSTEM_USER="${SYSTEM_USER:-polo}"
CREDS_DIR="/opt/openclaw/credentials"
KC_ENV="$CREDS_DIR/kawiil-central.env"
OPENCLAW_ENV="/opt/openclaw/openclaw.env"

mkdir -p "$CREDS_DIR"

# ─── Defaults conocidos ──────────────────────────────────
DEFAULT_VERCEL_URL="https://www.kawiil-central.mx"

# ─── Lee creds existentes si las hay ─────────────────────
if [[ -f "$KC_ENV" ]]; then
  set -a
  source "$KC_ENV"
  set +a
  log "Credenciales previas leídas de $KC_ENV"
fi

# ─── Pide lo que falte ───────────────────────────────────
echo ""
echo "Credenciales Kawiil Central (producción Vercel + Supabase)"
echo "──────────────────────────────────────────────────────────"

if [[ -z "${KAWIIL_CENTRAL_VERCEL_URL:-}" ]]; then
  if [[ -t 0 ]]; then
    read -rp "Vercel URL [$DEFAULT_VERCEL_URL]: " KAWIIL_CENTRAL_VERCEL_URL
    KAWIIL_CENTRAL_VERCEL_URL="${KAWIIL_CENTRAL_VERCEL_URL:-$DEFAULT_VERCEL_URL}"
  else
    KAWIIL_CENTRAL_VERCEL_URL="$DEFAULT_VERCEL_URL"
  fi
fi

if [[ -z "${KAWIIL_CENTRAL_SUPABASE_URL:-}" ]]; then
  if [[ -t 0 ]]; then
    read -rp "Supabase URL del proyecto kawiil-central (https://xxxxx.supabase.co): " KAWIIL_CENTRAL_SUPABASE_URL
  else
    fail "KAWIIL_CENTRAL_SUPABASE_URL no definido y no estoy en TTY"
  fi
fi
[[ -n "$KAWIIL_CENTRAL_SUPABASE_URL" ]] || fail "Supabase URL vacío"

if [[ -z "${KAWIIL_CENTRAL_SUPABASE_KEY:-}" ]]; then
  if [[ -t 0 ]]; then
    echo "  Necesito la service_role key (no anon) para que Donna pueda INSERT/UPDATE tareas."
    echo "  En Supabase Studio → Project Settings → API → service_role secret."
    read -rsp "Supabase service_role key (sb_secret_... o eyJ...): " KAWIIL_CENTRAL_SUPABASE_KEY
    echo ""
  else
    fail "KAWIIL_CENTRAL_SUPABASE_KEY no definido"
  fi
fi
[[ -n "$KAWIIL_CENTRAL_SUPABASE_KEY" ]] || fail "Supabase key vacío"

if [[ -z "${KAWIIL_CENTRAL_DATABASE_URL:-}" ]]; then
  if [[ -t 0 ]]; then
    echo ""
    echo "  Para SQL directo necesito la connection string Postgres."
    echo "  En Supabase Studio → Project Settings → Database → Connection string → URI."
    echo "  Formato: postgresql://postgres.[ref]:[pwd]@aws-0-[region].pooler.supabase.com:6543/postgres"
    echo "  (Deja vacío si solo quieres usar la REST API por ahora.)"
    read -rsp "Database URL (postgres://..., opcional): " KAWIIL_CENTRAL_DATABASE_URL
    echo ""
  fi
fi

# ─── Guarda env ──────────────────────────────────────────
cat > "$KC_ENV" <<EOF
# Generado por setup-kawiil-central-creds.sh — $(date -Iseconds)
# Operación de Donna contra kawiil-central EN PRODUCCIÓN (no es clon local)
KAWIIL_CENTRAL_VERCEL_URL="$KAWIIL_CENTRAL_VERCEL_URL"
KAWIIL_CENTRAL_SUPABASE_URL="$KAWIIL_CENTRAL_SUPABASE_URL"
KAWIIL_CENTRAL_SUPABASE_KEY="$KAWIIL_CENTRAL_SUPABASE_KEY"
KAWIIL_CENTRAL_DATABASE_URL="${KAWIIL_CENTRAL_DATABASE_URL:-}"
EOF
chmod 0600 "$KC_ENV"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$KC_ENV"
ok "Credenciales guardadas en $KC_ENV (mode 0600)"

# ─── Inyecta en openclaw.env ─────────────────────────────
if [[ -f "$OPENCLAW_ENV" ]]; then
  # Limpia entradas previas (también las antiguas KAWIIL_OS_*)
  sed -i.bak '/^KAWIIL_CENTRAL_/d;/^KAWIIL_OS_/d' "$OPENCLAW_ENV"
  {
    echo "KAWIIL_CENTRAL_VERCEL_URL=$KAWIIL_CENTRAL_VERCEL_URL"
    echo "KAWIIL_CENTRAL_SUPABASE_URL=$KAWIIL_CENTRAL_SUPABASE_URL"
    echo "KAWIIL_CENTRAL_SUPABASE_KEY=$KAWIIL_CENTRAL_SUPABASE_KEY"
    echo "KAWIIL_CENTRAL_DATABASE_URL=${KAWIIL_CENTRAL_DATABASE_URL:-}"
  } >> "$OPENCLAW_ENV"
  ok "openclaw.env actualizado (purgado KAWIIL_OS_* legacy)"
fi

# ─── psycopg2 si hay DATABASE_URL ────────────────────────
if [[ -n "${KAWIIL_CENTRAL_DATABASE_URL:-}" ]]; then
  if ! python3 -c "import psycopg2" 2>/dev/null; then
    log "Instalando psycopg2-binary para queries directos a Postgres"
    pip3 install --quiet --break-system-packages psycopg2-binary
    ok "psycopg2-binary instalado"
  fi
fi

# ─── Reinicia servicios para que tomen el env ────────────
for svc in telegram-bridge slack-bridge scheduler openclaw-gateway; do
  if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
    systemctl restart "$svc"
    sleep 1
    if systemctl is-active --quiet "$svc"; then
      ok "$svc reiniciado"
    else
      warn "$svc no quedó active"
    fi
  fi
done

# ─── Test de conectividad ────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Test de conectividad"
echo "═══════════════════════════════════════"

# Vercel
if curl -fsI --max-time 5 "$KAWIIL_CENTRAL_VERCEL_URL" >/dev/null 2>&1; then
  ok "Vercel responde: $KAWIIL_CENTRAL_VERCEL_URL"
else
  warn "Vercel no respondió en 5s (puede ser temporal)"
fi

# Supabase REST
if curl -fsS --max-time 5 \
    -H "apikey: $KAWIIL_CENTRAL_SUPABASE_KEY" \
    -H "Authorization: Bearer $KAWIIL_CENTRAL_SUPABASE_KEY" \
    "$KAWIIL_CENTRAL_SUPABASE_URL/rest/v1/" >/dev/null 2>&1; then
  ok "Supabase REST responde"
else
  warn "Supabase REST no respondió o creds inválidas"
fi

# Postgres directo
if [[ -n "${KAWIIL_CENTRAL_DATABASE_URL:-}" ]]; then
  if python3 -c "
import psycopg2
conn = psycopg2.connect('$KAWIIL_CENTRAL_DATABASE_URL', connect_timeout=5)
cur = conn.cursor()
cur.execute(\"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\")
n = cur.fetchone()[0]
print(f'  ✓ Postgres OK — {n} tablas en schema public')
conn.close()
" 2>&1; then
    :
  else
    warn "Postgres directo no conectó — verifica DATABASE_URL"
  fi
fi

echo ""
ok "Setup completo. Prueba desde Telegram:"
echo ""
echo "  'qué tablas tiene kawiil-central'"
echo "  'qué proyectos están activos en kawiil-central'"
echo "  'lista las tareas pendientes de [proyecto]'"
echo "  'crea una tarea en kawiil-central: [descripción]'"
