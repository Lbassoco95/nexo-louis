#!/usr/bin/env bash
# import-kawiil-agents.sh — Clona el repo privado Lbassoco95/kawiil-agents
# y registra sus prompts como sub-agentes de Donna en
# /opt/openclaw/spaces/general/agents/.
#
# Uso (en Hetzner, sudo):
#   sudo bash import-kawiil-agents.sh ghp_xxxxxxxxxxxxxxxxxxxx
#
# El argumento es el Personal Access Token de GitHub (classic, scope `repo`).
# Después del clone, el token se borra del remote (queda solo HTTPS limpio).
# Si se vuelve a correr, hace `git pull` con el token que se guardó cifrado en
# /opt/openclaw/credentials/github.env mode 0600.

set -uo pipefail

ok()   { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; }
log()  { printf "  \033[1;36m· %s\033[0m\n" "$*"; }
warn() { printf "  \033[1;33m! %s\033[0m\n" "$*"; }
fail() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

PAT="${1:-}"
SYSTEM_USER="${SYSTEM_USER:-polo}"
REPO_URL="https://github.com/Lbassoco95/kawiil-agents.git"
REPO_OWNER_NAME="Lbassoco95/kawiil-agents"
DEST="/opt/openclaw/projects/kawiil-agents"
CREDS_DIR="/opt/openclaw/credentials"
GH_ENV="$CREDS_DIR/github.env"
AGENTS_DEST="/opt/openclaw/spaces/general/agents"

# Lee PAT existente si no se pasó por argumento
if [[ -z "$PAT" && -f "$GH_ENV" ]]; then
  set -a; source "$GH_ENV"; set +a
  PAT="${GITHUB_PAT:-}"
  [[ -n "$PAT" ]] && log "PAT leído de $GH_ENV"
fi

if [[ -z "$PAT" ]]; then
  fail "Falta PAT. Uso: sudo bash $0 ghp_xxxxxxxx (o pásalo en $GH_ENV como GITHUB_PAT=...)"
fi

if [[ "$PAT" == "PEGA_TU_PAT_AQUI" || "$PAT" == *"PLACEHOLDER"* ]]; then
  fail "PAT parece placeholder literal — pon el token REAL"
fi

# Guarda PAT en env file (mode 0600)
mkdir -p "$CREDS_DIR"
cat > "$GH_ENV" <<EOF
# Generado por import-kawiil-agents.sh — $(date -Iseconds)
GITHUB_PAT="$PAT"
EOF
chmod 0600 "$GH_ENV"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$GH_ENV"
ok "PAT guardado en $GH_ENV (mode 0600)"

# Clone o pull
mkdir -p /opt/openclaw/projects
chown "$SYSTEM_USER":"$SYSTEM_USER" /opt/openclaw/projects

AUTH_URL="https://${PAT}@github.com/${REPO_OWNER_NAME}.git"

if [[ -d "$DEST/.git" ]]; then
  log "$DEST ya existe — git pull"
  cd "$DEST"
  sudo -u "$SYSTEM_USER" git -C "$DEST" remote set-url origin "$AUTH_URL"
  if sudo -u "$SYSTEM_USER" git -C "$DEST" pull --rebase --autostash 2>&1 | tail -3; then
    ok "git pull OK"
  else
    warn "git pull tuvo issues — revisa manualmente"
  fi
  sudo -u "$SYSTEM_USER" git -C "$DEST" remote set-url origin "$REPO_URL"
else
  log "Clonando $REPO_OWNER_NAME en $DEST…"
  if sudo -u "$SYSTEM_USER" git clone "$AUTH_URL" "$DEST" 2>&1 | tail -5; then
    ok "Clonado"
    sudo -u "$SYSTEM_USER" git -C "$DEST" remote set-url origin "$REPO_URL"
  else
    fail "git clone falló. Verifica que el PAT tenga scope 'repo' y acceso a $REPO_OWNER_NAME"
  fi
fi

# ── Registrar agentes en /opt/openclaw/spaces/general/agents/ ──
# Buscamos archivos .md en el repo y los convertimos en sub-agentes con prefijo kawiil-.
# Estructura esperada (flexible): el repo puede tener carpetas tipo agents/, prompts/,
# skills/SKILL.md, o .md sueltos. Vamos a recoger cualquier .md y normalizar.

mkdir -p "$AGENTS_DEST"
chown "$SYSTEM_USER":"$SYSTEM_USER" "$AGENTS_DEST"

log "Importando agentes del repo a $AGENTS_DEST…"
COUNT=0
SKIPPED=0

# ── Caso especial: kawiil-agents tiene los prompts en agents/base_agent.py
# dentro de un dict PROMPTS = {"name": "system_prompt", ...}
BASE_AGENT="$DEST/agents/base_agent.py"
if [[ -f "$BASE_AGENT" ]]; then
  log "Detecté base_agent.py con dict PROMPTS — extrayendo prompts del framework"
  python3 - <<PYEOF
import ast, os, sys
src = open("$BASE_AGENT").read()
try:
    tree = ast.parse(src)
except Exception as e:
    print(f"  ! No pude parsear base_agent.py: {e}")
    sys.exit(0)

dest_dir = "$AGENTS_DEST"
imported = 0
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PROMPTS":
                if isinstance(node.value, ast.Dict):
                    for k_node, v_node in zip(node.value.keys, node.value.values):
                        try:
                            k = ast.literal_eval(k_node)
                            v = ast.literal_eval(v_node)
                        except Exception:
                            continue
                        slug = str(k).lower().replace("_", "-")
                        out_path = os.path.join(dest_dir, f"kawiil-{slug}.md")
                        # No sobreescribir si ya existe versión mejor (humana)
                        if os.path.exists(out_path):
                            try:
                                existing = open(out_path).read()
                                # Si el archivo existente es claramente más rico (>500 chars body), respeta
                                if len(existing) > 1000:
                                    print(f"  · kawiil-{slug}: ya existe versión rica ({len(existing)}c), skip")
                                    continue
                            except Exception:
                                pass
                        body = v if isinstance(v, str) else str(v)
                        content = f"""---
name: kawiil-{slug}
description: Agente {slug} (placeholder del framework — system prompt básico, hay que enriquecerlo)
metadata:
  modelo: claude-sonnet-4-6
  source: Lbassoco95/kawiil-agents
  source_path: agents/base_agent.py:PROMPTS["{k}"]
  status: placeholder
---

# Rol
{body}

# Capacidades disponibles
Puedes usar todas las tools de Donna cuando seas invocado:
- consejo_experto_legal(area, pregunta) — consulta a los 92 expertos legales US y obtén síntesis adaptada a México
- kawiil_central_tareas / crear_tarea / actualizar_tarea / avance — opera el sistema de gestión Kawiil
- m365_inbox / m365_responder — correos Kawiil/Yoltik
- browser_navegar / vault_obtener — para acciones en sitios web
- legal_buscar / legal_estado — biblioteca SJF + DOF

# Reglas
- Responde SIEMPRE en español de México (tono profesional, claro, conciso)
- Si el caso requiere expertise legal técnico, consulta consejo_experto_legal antes de dar tu opinión
- Si vas a crear o modificar una tarea/correo, muestra el borrador y espera 'confirmo' del usuario
- NUNCA expongas passwords ni configs de sistema

# TODO (Polo)
Este es el prompt original del framework — está en BÁSICO. Cuando tengas tiempo:
- Define tono distintivo del agente (Amatl/Nelli/Tepantli/Matiox tienen personalidades diferentes?)
- Agrega ejemplos de tareas típicas que resuelve
- Restringe alcance (qué SÍ hace, qué NO)
- Decide en qué proyectos/clientes participa
"""
                        with open(out_path, "w") as f:
                            f.write(content)
                        try:
                            os.chown(out_path, $(id -u "$SYSTEM_USER"), $(id -g "$SYSTEM_USER"))
                        except PermissionError:
                            pass
                        imported += 1
                        print(f"  ✓ kawiil-{slug} importado")
print(f"  → {imported} prompts del framework importados como kawiil-*")
PYEOF
  EXTRA=$(ls "$AGENTS_DEST"/kawiil-*.md 2>/dev/null | wc -l)
  COUNT=$((COUNT + EXTRA))
fi

while IFS= read -r -d '' f; do
  base="$(basename "$f" .md)"
  # Convierte path a slug — mantiene jerarquía: agents/amatl.md → kawiil-amatl
  rel_path="${f#$DEST/}"
  slug="$(echo "$rel_path" | sed 's|/|-|g; s|\.md$||; s|[^a-zA-Z0-9-]|-|g' | tr 'A-Z' 'a-z' | sed 's/--*/-/g; s/^-//; s/-$//')"
  # Slugs comunes que queremos limpiar
  slug="${slug#agents-}"
  slug="${slug#prompts-}"
  slug="${slug#skills-}"
  # Si el archivo es SKILL.md, usa el nombre del directorio padre
  if [[ "$base" == "SKILL" ]]; then
    parent="$(basename "$(dirname "$f")")"
    slug="$parent"
  fi
  # Si el slug está vacío o es muy raro, skip
  if [[ -z "$slug" || "$slug" == "readme" || "$slug" == "license" || "$slug" == "changelog" ]]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  out_name="kawiil-${slug}.md"
  out_path="$AGENTS_DEST/$out_name"

  # Lee contenido y extrae description del frontmatter si existe
  content="$(cat "$f")"
  description=""
  if [[ "${content:0:3}" == "---" ]]; then
    fm_end=$(echo "$content" | awk '/^---$/{c++; if (c==2) {print NR; exit}}')
    if [[ -n "$fm_end" ]]; then
      fm=$(echo "$content" | head -n $((fm_end - 1)) | tail -n +2)
      description=$(echo "$fm" | grep -i '^description:' | head -1 | sed 's/^[Dd]escription:[[:space:]]*//; s/^["\x27]//; s/["\x27]$//' | head -c 200)
      # Body sin frontmatter
      body=$(echo "$content" | tail -n +$((fm_end + 1)))
    else
      body="$content"
    fi
  else
    body="$content"
  fi
  [[ -z "$description" ]] && description="Agente kawiil $slug (importado de $REPO_OWNER_NAME)"

  # Skip si está vacío
  if [[ -z "$body" ]] || [[ ${#body} -lt 50 ]]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  cat > "$out_path" <<AGENT_EOF
---
name: kawiil-${slug}
description: $description
metadata:
  modelo: claude-sonnet-4-6
  source: $REPO_OWNER_NAME
  source_path: $rel_path
---

$body
AGENT_EOF

  chown "$SYSTEM_USER":"$SYSTEM_USER" "$out_path"
  COUNT=$((COUNT + 1))
done < <(find "$DEST" -name "*.md" -type f -not -path "*/.git/*" -not -path "*/node_modules/*" -print0)

ok "Importados: $COUNT agentes kawiil-* ($SKIPPED archivos saltados)"

# ── Resumen ──
echo ""
log "Agentes kawiil-* ahora disponibles:"
ls "$AGENTS_DEST" | grep "^kawiil-" | head -20 | sed 's/^/    /' || true
TOTAL_KAWIIL=$(ls "$AGENTS_DEST" 2>/dev/null | grep -c "^kawiil-" || echo 0)
TOTAL_LEGAL=$(ls "$AGENTS_DEST" 2>/dev/null | grep -c "^legal-" || echo 0)
echo ""
ok "Total agentes en spaces/general/agents/: kawiil-* = $TOTAL_KAWIIL, legal-* = $TOTAL_LEGAL"

# Recarga openclaw-gateway para que /v1/agents refleje los nuevos
systemctl restart openclaw-gateway 2>/dev/null && ok "openclaw-gateway recargado" || warn "no recargué openclaw-gateway"

echo ""
ok "Listo. Verifica en Telegram: 'lista mis agentes kawiil'"
echo ""
log "API: curl http://127.0.0.1:3000/v1/agents | grep kawiil"
