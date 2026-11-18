#!/usr/bin/env bash
# envmerge.sh — funciones para regenerar openclaw.env SIN perder lo que otros scripts inyectaron.
#
# Contexto: install.sh reescribe /opt/openclaw/openclaw.env en cada deploy, pero varios scripts le agregan
# variables a mano (setup-kawiil-central-creds.sh → KAWIIL_CENTRAL_*, install-1password.sh → OP_SERVICE_ACCOUNT_TOKEN,
# install-bitwarden.sh → BITWARDEN_ENV_FILE, install-playwright.sh → PLAYWRIGHT_BROWSERS_PATH…). Antes cada deploy las
# borraba en silencio y las herramientas dejaban de funcionar (p. ej. Kawiil Central sin credenciales).

# envmerge_backup ARCHIVO — copia fechada del archivo actual (si existe); conserva las 5 más recientes.
envmerge_backup() {
  local f="$1" ts
  [[ -f "$f" ]] || return 0
  ts="$(date +%Y%m%d%H%M%S)"
  cp -p "$f" "$f.bak.$ts" 2>/dev/null || return 0
  chmod 600 "$f.bak.$ts" 2>/dev/null || true
  # shellcheck disable=SC2012
  ls -1t "$f".bak.* 2>/dev/null | tail -n +6 | while IFS= read -r old; do rm -f -- "$old"; done
}

# envmerge_keep VIEJO NUEVO — agrega a NUEVO toda línea CLAVE=valor de VIEJO cuya clave NO esté ya en NUEVO.
# Lo que install.sh genera de nuevo manda; lo demás se conserva tal cual. Imprime las claves conservadas.
envmerge_keep() {
  local old="$1" new="$2" line k kept=()
  [[ -f "$old" && -f "$new" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    k="${line%%=*}"
    if ! grep -q "^${k}=" "$new"; then
      printf '%s\n' "$line" >> "$new"
      kept+=("$k")
    fi
  done < "$old"
  ((${#kept[@]})) && printf '%s\n' "${kept[@]}"
  return 0
}
