#!/usr/bin/env bash
# mac-deploy-all.sh — UN solo comando para deployar Donna en Hetzner desde la Mac.
#
# Hace todo en orden:
#   1) Genera /tmp/louis.env desde ~/.openclaw/credentials/
#   2) Empaqueta ~/.openclaw → /tmp/louis-seed.tar.gz (+ extras)
#   3) Crea /opt/louis en el VPS y sube el paquete (rsync)
#   4) Sube .env (scp)
#   5) Sube seed tarballs (scp)
#   6) Corre deploy.sh remoto
#   7) Aplica seed (descomprime memorias + scripts + tokens M365)
#   8) Arranca telegram-bridge
#   9) verify.sh
#
# Uso:
#   ./mac-deploy-all.sh HETZNER_IP
#   ./mac-deploy-all.sh 204.168.131.21

set -euo pipefail

HETZNER_IP="${1:-}"
if [[ -z "$HETZNER_IP" ]]; then
  echo "Uso: $0 HETZNER_IP"
  echo "Ej:   $0 204.168.131.21"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"   # .../yoltik-ai-setup/cloud/hetzner/

ok()   { printf "\n\033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
fail() { printf "\n\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# ── 0) Pre-flight ──────────────────────────────────────────
log "[0/9] Pre-flight"
[[ -d "$PKG_DIR" ]] || fail "No encuentro paquete en $PKG_DIR"
[[ -f "$PKG_DIR/deploy.sh" ]] || fail "Falta $PKG_DIR/deploy.sh"

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SCP_OPTS="-o StrictHostKeyChecking=accept-new"

# Auto-detect: ¿root o polo? Después del primer bootstrap, root queda locked.
REMOTE=""
REMOTE_USER=""
SUDO=""
for u in root polo; do
  if ssh $SSH_OPTS "${u}@${HETZNER_IP}" 'echo ok' >/dev/null 2>&1; then
    REMOTE_USER="$u"
    REMOTE="${u}@${HETZNER_IP}"
    [[ "$u" == "polo" ]] && SUDO="sudo "
    break
  fi
done
[[ -n "$REMOTE" ]] || fail "No puedo SSH ni como root ni como polo a ${HETZNER_IP}. Verifica que tu ~/.ssh/id_ed25519 esté en authorized_keys."
ok "SSH a $REMOTE funciona (sudo='${SUDO:-NO}')"

# ── 1) Generar .env ────────────────────────────────────────
log "[1/9] Generando /tmp/louis.env desde ~/.openclaw/credentials/"
# Muestra el resumen para que veas qué tokens encontró (sin valores)
bash "$SCRIPT_DIR/mac-prep.sh" "$HETZNER_IP" 2>&1 | grep -E "^(==>|✓|⚠|M365_|TELEGRAM|SLACK|ANTHROPIC|SUPABASE|KAWIIL_|LOUIS_|AGENTS_|ACME_|SYSTEM_|OPENCLAW_|MAC_)" || true
[[ -f /tmp/louis.env ]] || fail "mac-prep.sh no generó /tmp/louis.env"
ok ".env generado en /tmp/louis.env"

# ── 2) Empaquetar ~/.openclaw ──────────────────────────────
log "[2/9] Empaquetando ~/.openclaw → /tmp/louis-seed.tar.gz"
bash "$SCRIPT_DIR/seed-from-mac.sh" "$HETZNER_IP" >/dev/null 2>&1 || true
[[ -f /tmp/louis-seed.tar.gz ]] || fail "seed-from-mac.sh no generó el tarball"
ok "Seed empaquetado ($(du -h /tmp/louis-seed.tar.gz | cut -f1))"

# ── 3) Subir paquete ───────────────────────────────────────
log "[3/9] Subiendo paquete hetzner/ → /opt/louis en VPS"
# Si conectamos como polo, /opt/louis necesita pre-existir con permisos polo:polo.
# El deploy anterior probablemente ya lo creó. Si no, polo tendría que crearlo con sudo.
ssh $SSH_OPTS "$REMOTE" "${SUDO}mkdir -p /opt/louis && ${SUDO}chown ${REMOTE_USER}:${REMOTE_USER} /opt/louis 2>/dev/null || true"
rsync -az --delete \
  --exclude='.env' --exclude='*.bak' --exclude='*.log' \
  -e "ssh $SSH_OPTS" \
  --rsync-path="${SUDO}rsync" \
  "$PKG_DIR/" "$REMOTE":/opt/louis/
ssh $SSH_OPTS "$REMOTE" "${SUDO}chmod +x /opt/louis/deploy.sh /opt/louis/verify.sh /opt/louis/bootstrap/*.sh /opt/louis/openclaw/*.sh /opt/louis/sync/*.sh"
ok "Paquete en /opt/louis"

# ── 4) Subir .env ──────────────────────────────────────────
log "[4/9] Subiendo /tmp/louis.env → /opt/louis/.env"
# scp directo a /opt/louis necesita permisos; primero subimos a /tmp y luego movemos con sudo
scp $SCP_OPTS -q /tmp/louis.env "$REMOTE":/tmp/louis.env
ssh $SSH_OPTS "$REMOTE" "${SUDO}mv /tmp/louis.env /opt/louis/.env && ${SUDO}chmod 600 /opt/louis/.env && ${SUDO}chown ${REMOTE_USER}:${REMOTE_USER} /opt/louis/.env"
ok ".env cargado"

# ── 5) Subir seed tarballs ─────────────────────────────────
log "[5/9] Subiendo seed tarballs → /tmp del VPS"
# Limpia restos de deploys anteriores (pueden tener owner=root y bloquear el scp como polo)
ssh $SSH_OPTS "$REMOTE" "${SUDO}rm -f /tmp/louis-seed.tar.gz /tmp/louis-extras.tar.gz /tmp/louis.env 2>/dev/null || true"
scp $SCP_OPTS -q /tmp/louis-seed.tar.gz "$REMOTE":/tmp/louis-seed.tar.gz
[[ -f /tmp/louis-extras.tar.gz ]] && scp $SCP_OPTS -q /tmp/louis-extras.tar.gz "$REMOTE":/tmp/louis-extras.tar.gz || true
ok "Tarballs subidos"

# ── 6) Correr deploy.sh ────────────────────────────────────
log "[6/9] Ejecutando deploy.sh en el VPS (~3-5 min — bootstrap es idempotente)"
ssh $SSH_OPTS -t "$REMOTE" "cd /opt/louis && ${SUDO}./deploy.sh"
ok "deploy.sh terminó"

# ── 7) Aplicar seed ────────────────────────────────────────
log "[7/9] Aplicando seed (memorias, prompts, M365 tokens)"
ssh $SSH_OPTS "$REMOTE" "cd /opt/louis && ${SUDO}bash sync/hetzner-seed-apply.sh /tmp/louis-seed.tar.gz /tmp/louis-extras.tar.gz"
ok "Seed aplicado"

# ── 8) Arrancar Telegram bridge ────────────────────────────
log "[8/9] Encendiendo telegram-bridge.service"
ssh $SSH_OPTS "$REMOTE" "${SUDO}systemctl enable --now telegram-bridge 2>&1 || true; ${SUDO}systemctl restart openclaw 2>&1 || true"
sleep 3
ssh $SSH_OPTS "$REMOTE" "systemctl is-active openclaw telegram-bridge 2>&1 | tr '\n' ' '; echo"
ok "Servicios arriba"

# ── 9) verify.sh ───────────────────────────────────────────
log "[9/9] Health check"
ssh $SSH_OPTS "$REMOTE" "cd /opt/louis && ${SUDO}./verify.sh" || true

# ── Limpieza local ─────────────────────────────────────────
log "Limpiando temporales locales (tienen secretos)"
shred -u /tmp/louis.env /tmp/louis-seed.tar.gz 2>/dev/null || rm -f /tmp/louis.env /tmp/louis-seed.tar.gz
[[ -f /tmp/louis-extras.tar.gz ]] && { shred -u /tmp/louis-extras.tar.gz 2>/dev/null || rm -f /tmp/louis-extras.tar.gz; }
ok "Temporales borrados"

cat <<EOF

══════════════════════════════════════════════════════════
  Donna está vivo en https://donna.kawiil.mx
══════════════════════════════════════════════════════════

Pruébalo:
  - Manda un mensaje a tu bot de Telegram desde el iPhone
  - O abre https://donna.kawiil.mx en el navegador

Si algo falla:
  - Logs:  ssh $REMOTE 'journalctl -u openclaw -u telegram-bridge -f'
  - Re-deploy idempotente:  ./mac-deploy-all.sh $HETZNER_IP

Próximo paso opcional: instalar sync continuo Mac→Hetzner cada 5 min
  cd $SCRIPT_DIR && ./mac-install.sh donna.kawiil.mx polo

EOF
