#!/usr/bin/env bash
# Prueba de los filtros de mac-push-projects.sh con rsync REAL en local (sin red): qué borraría el push en el servidor.
# Regresión: el filtro original borraba archivos del despliegue de kawiil-agents (docx/templates/default.docx, package.json de Playwright,
# requirements*.txt, *.md) porque el destino del push (/opt/openclaw/projects) contiene ese despliegue y --delete borra lo incluido que no
# esté en la Mac. Uso: bash tests/test_push_projects_filtros.sh
set -uo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$AQUI/../sync/mac-push-projects.sh"
command -v rsync >/dev/null || { echo "SKIP: no hay rsync"; exit 0; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fallos=0
ok()   { echo "  ok   $1"; }
mal()  { echo "  FALLA $1"; fallos=$((fallos+1)); }

# Filtros REALES del script, en su orden.
mapfile -t ACTUALES < <(grep -oE -- "--(include|exclude)='[^']*'" "$SCRIPT" | sed "s/'//g")
[[ ${#ACTUALES[@]} -gt 10 ]] || { echo "FALLA: no pude leer los filtros del script"; exit 1; }
# Filtros ORIGINALES (antes del arreglo), para demostrar la regresión.
ORIGINALES=(--exclude=/_Louis-Generados/ --exclude=.git --exclude=.git/ --exclude=node_modules --exclude=.cursor --exclude=.vscode --exclude=.next
  --exclude=dist --exclude=build --exclude=venv --exclude=.DS_Store --include='*/' --include='*.md' --include='*.markdown' --include='*.txt'
  --include='*.pdf' --include='*.docx' --include='*.doc' --include='*.csv' --include='*.xlsx' --include='*.xls' --include='*.pptx' --include='*.json'
  --include='*.rtf' --exclude='*')

montar() {
  rm -rf "$T/mac" "$T/srv"
  mkdir -p "$T/mac/ClienteA" "$T/srv/ClienteA" "$T/srv/_Louis-Generados" "$T/srv/kawiil-agents-main/server" "$T/srv/kawiil-agents-main/ClienteX/.venv/lib/python3.12/site-packages/docx/templates"
  SP="$T/srv/kawiil-agents-main/.venv/lib/python3.12/site-packages"
  mkdir -p "$SP/docx/templates" "$SP/playwright/driver/package"
  echo acta  > "$T/mac/ClienteA/acta.docx"; echo viejo > "$T/srv/ClienteA/acta.docx"
  echo x > "$SP/docx/templates/default.docx"; echo x > "$SP/playwright/driver/package/package.json"
  echo x > "$T/srv/kawiil-agents-main/server/requirements-core.txt"; echo x > "$T/srv/kawiil-agents-main/ENTREGABLES.md"
  echo x > "$T/srv/_Louis-Generados/informe.pdf"
  echo x > "$T/srv/ClienteA/solo-en-servidor.pdf"
}
borrados() { rsync -rn --delete --prune-empty-dirs -i "$@" "$T/mac/" "$T/srv/" | grep '^\*deleting' | grep -v '/$' | sed 's/^\*deleting *//'; }

echo "[1] el filtro ORIGINAL sí borraba el despliegue (regresión demostrada)"
montar; ORIG="$(borrados "${ORIGINALES[@]}")"
for f in "kawiil-agents-main/.venv/lib/python3.12/site-packages/docx/templates/default.docx" "kawiil-agents-main/.venv/lib/python3.12/site-packages/playwright/driver/package/package.json" "kawiil-agents-main/server/requirements-core.txt" "kawiil-agents-main/ENTREGABLES.md"; do
  grep -qxF "$f" <<<"$ORIG" && ok "el original borraba $f" || mal "el original ya no reproduce el borrado de $f (¿cambió el escenario?)"
done

echo "[2] el filtro ACTUAL (con el arreglo) no toca el despliegue"
montar; ACT="$(borrados "${ACTUALES[@]}")"
if grep -q "^kawiil-agents-main/" <<<"$ACT"; then mal "borraría algo de kawiil-agents-main:"; grep "^kawiil-agents-main/" <<<"$ACT" | sed 's/^/        /'; else ok "no borra nada dentro de kawiil-agents-main/ (ni su .venv, ni requirements, ni .md)"; fi
grep -q "site-packages" <<<"$ACT" && mal "borraría archivos de un site-packages" || ok "no borra archivos de ningún site-packages"
grep -q "_Louis-Generados" <<<"$ACT" && mal "borraría lo generado por Louis" || ok "lo generado por Louis sigue protegido"

echo "[3] lo que el push debe seguir haciendo (no se quita nada del sync)"
montar
SUBE="$(rsync -rn --delete --prune-empty-dirs -i "${ACTUALES[@]}" "$T/mac/" "$T/srv/" | grep -E '^>f' | sed 's/^[^ ]* *//')"
grep -qxF "ClienteA/acta.docx" <<<"$SUBE" && ok "sigue subiendo los documentos de la Mac (acta.docx actualizado)" || mal "ya no sube los documentos de la Mac"
grep -qxF "ClienteA/solo-en-servidor.pdf" <<<"$ACT" && ok "(comportamiento existente, sin cambio) un documento que solo está en el servidor dentro de una carpeta de proyecto se sigue borrando: la Mac es la fuente" || mal "cambió el comportamiento de borrado de documentos de proyectos"

[[ $fallos -eq 0 ]] && { echo "TODO OK"; exit 0; } || { echo "$fallos fallas"; exit 1; }
