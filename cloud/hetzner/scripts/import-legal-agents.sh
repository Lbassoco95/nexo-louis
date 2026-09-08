#!/usr/bin/env bash
# import-legal-agents.sh — Clona anthropics/claude-for-legal y registra cada
# SKILL.md como sub-agente Donna en /opt/openclaw/spaces/general/agents/.
#
# Cada sub-agente queda como `legal-<plugin>-<skill>.md` con frontmatter:
#   nombre, especialidad (de description), modelo (claude-sonnet-4-6), origen
#
# Idempotente: si el agente ya existe, lo sobreescribe (para que actualizaciones
# del repo upstream se propaguen).
#
# Uso (en Hetzner como polo o root):
#   sudo bash /opt/louis/scripts/import-legal-agents.sh

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/anthropics/claude-for-legal.git}"
BRANCH="${BRANCH:-main}"
SYSTEM_USER="${SYSTEM_USER:-polo}"

CACHE_DIR="/opt/openclaw/cache/claude-for-legal"
AGENTS_DIR="/opt/openclaw/spaces/general/agents"
LOG="==>"

log() { printf "\033[1;36m%s\033[0m %s\n" "$LOG" "$*"; }
ok()  { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail(){ printf "\033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

command -v git >/dev/null || fail "git no instalado"
mkdir -p "$AGENTS_DIR"
mkdir -p "$(dirname "$CACHE_DIR")"

# Plugins relevantes para Polo/Kawiil. Ajustables vía PLUGINS env.
# Defaults: commercial, corporate, privacy, regulatory, employment, ip, product, ai-governance
PLUGINS_DEFAULT="commercial-legal corporate-legal privacy-legal regulatory-legal employment-legal ip-legal product-legal ai-governance-legal"
PLUGINS="${PLUGINS:-$PLUGINS_DEFAULT}"
# ollama = sub-call local sin Anthropic; claude-sonnet-4-6 si hay créditos
LEGAL_AGENT_MODEL="${LEGAL_AGENT_MODEL:-ollama}"

# ── 1) Clone or pull ──────────────────────────────────────────
if [[ -d "$CACHE_DIR/.git" ]]; then
  log "Pull en $CACHE_DIR"
  git -C "$CACHE_DIR" fetch --quiet origin
  git -C "$CACHE_DIR" reset --hard "origin/$BRANCH" >/dev/null
else
  log "Clone $REPO_URL → $CACHE_DIR"
  git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$CACHE_DIR" >/dev/null 2>&1 || fail "Clone falló"
fi
ok "Repo listo ($(git -C "$CACHE_DIR" rev-parse --short HEAD))"

# ── 2) Recorre plugins y registra skills ──────────────────────
count_total=0
count_new=0
count_upd=0

for plugin in $PLUGINS; do
  plugin_dir="$CACHE_DIR/$plugin"
  if [[ ! -d "$plugin_dir/skills" ]]; then
    warn "Plugin '$plugin' sin carpeta skills/ — skip"
    continue
  fi
  log "Plugin: $plugin"
  while IFS= read -r -d '' skill_md; do
    # Extraer slug del path: .../<plugin>/skills/<slug>/SKILL.md
    skill_slug=$(basename "$(dirname "$skill_md")")
    out_name="legal-${plugin%-legal}-${skill_slug}.md"
    # Limpia dobles guiones
    out_name=$(echo "$out_name" | tr -s '-')
    out_path="$AGENTS_DIR/$out_name"

    # Parsear frontmatter del SKILL.md (campo 'description')
    desc=$(awk '/^---$/{n++; next} n==1 && /^description:/ {sub(/^description: */,""); print; exit}' "$skill_md")
    name=$(awk '/^---$/{n++; next} n==1 && /^name:/ {sub(/^name: */,""); print; exit}' "$skill_md")
    [[ -z "$desc" ]] && desc="(sin descripción)"
    [[ -z "$name" ]] && name="$skill_slug"
    # Quita comillas
    desc=$(echo "$desc" | sed -e 's/^"//;s/"$//;s/^'\''//;s/'\''$//')
    name=$(echo "$name" | sed -e 's/^"//;s/"$//')

    # Cuerpo: todo después del segundo ---
    body=$(awk 'BEGIN{n=0} /^---$/{n++; next} n>=2 {print}' "$skill_md")

    was_new=true
    [[ -f "$out_path" ]] && was_new=false

    # Escribir el agente Donna
    {
      echo "---"
      echo "nombre: legal-${plugin%-legal}-${skill_slug}"
      echo "especialidad: [$plugin] $desc"
      echo "modelo: $LEGAL_AGENT_MODEL"
      echo "origen: anthropics/claude-for-legal $plugin/skills/$skill_slug"
      echo "actualizado: $(date -Iseconds)"
      echo "---"
      echo ""
      echo "# $name"
      echo ""
      echo "$body"
    } > "$out_path"

    count_total=$((count_total + 1))
    if $was_new; then
      count_new=$((count_new + 1))
    else
      count_upd=$((count_upd + 1))
    fi
  done < <(find "$plugin_dir/skills" -name "SKILL.md" -print0 2>/dev/null)
done

# ── 3) Ownership y resumen ────────────────────────────────────
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$AGENTS_DIR" 2>/dev/null || true

echo ""
ok "Import completo"
echo "    Plugins procesados:  $(echo $PLUGINS | wc -w)"
echo "    Skills registrados:  $count_total ($count_new nuevos, $count_upd actualizados)"
echo "    Carpeta:             $AGENTS_DIR"
echo ""
echo "Ver agentes:"
echo "  ls $AGENTS_DIR/legal-*.md | head -20"
echo ""
echo "Probar desde Telegram:"
echo "  'lista mis agentes' → debe listar los legales"
echo "  'invoca legal-commercial-vendor-agreement-reviewer para revisar...'"
