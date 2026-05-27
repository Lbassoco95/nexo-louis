#!/usr/bin/env bash
# install-memory-from-cowork.sh
# Reemplaza los 5 archivos de memoria de Louis con los extraídos del corpus
# Cowork de los últimos 90 días. Hace backup de los seed previos.

set -euo pipefail

SRC="$HOME/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/memory-import/extracted"
DEST="$HOME/.openclaw/spaces/general"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ ! -d "$SRC" ]]; then
  echo "Error: no encontré $SRC" >&2
  exit 1
fi

echo "==> Backup de archivos seed actuales"
mkdir -p "$DEST/.backup-$STAMP"
for fname in PROJECTS.md PEOPLE.md LEARNINGS.md JOURNAL.md IMPORTANT.md; do
  if [[ -f "$DEST/$fname" ]]; then
    cp "$DEST/$fname" "$DEST/.backup-$STAMP/$fname"
    echo "    backup: $DEST/.backup-$STAMP/$fname"
  fi
done

echo ""
echo "==> Reemplazando memoria con datos extraídos del corpus Cowork (90 días)"
cp "$SRC/PROJECTS_extracted.md"  "$DEST/PROJECTS.md"
cp "$SRC/PEOPLE_extracted.md"    "$DEST/PEOPLE.md"
cp "$SRC/LEARNINGS_extracted.md" "$DEST/LEARNINGS.md"
cp "$SRC/JOURNAL_extracted.md"   "$DEST/JOURNAL.md"
cp "$SRC/IMPORTANT_extracted.md" "$DEST/IMPORTANT.md"

# Permisos restrictivos (memoria privada)
chmod 600 "$DEST"/{PROJECTS,PEOPLE,LEARNINGS,JOURNAL,IMPORTANT}.md

echo "    Instalado:"
ls -la "$DEST"/{PROJECTS,PEOPLE,LEARNINGS,JOURNAL,IMPORTANT}.md

echo ""
echo "==> Actualizando AGENTS.md para que Louis sepa que la memoria fue actualizada"
PATCH="$DEST/AGENTS.md"

if ! grep -q "MEMORIA IMPORTADA DE COWORK" "$PATCH" 2>/dev/null; then
  cat >> "$PATCH" <<'NEXOEOF'

# MEMORIA IMPORTADA DE COWORK — 2026-05-24

Tu memoria estructurada (PROJECTS.md, PEOPLE.md, LEARNINGS.md, JOURNAL.md, IMPORTANT.md) fue actualizada con datos extraídos de 431 conversaciones business de Polo en Cowork (rango 2026-03-02 a 2026-05-23, 90 días).

Cambios importantes que debes asumir:

1. **El holding paraguas de Polo se llama "Tloque"** (bautizado 22-may-2026, aún no formalizado). Agrupa Kawiil, Yoltik y Tonatiuh.

2. **Polo lidera/participa en más entidades de las que sabíamos:**
   - Kawiil Mx (Socio Director / Director General)
   - Yoltik (Fundador, Presidente del Consejo)
   - Tonatiuh (empresa de seguridad privada, en diseño — director fundador)
   - Vizum Technologies (Oficial de Cumplimiento PLD/FT)
   - Plankton Wallet (Representante Legal — caso LVGS)
   - Kuali A.C. (asociación civil, fundador/Presidente)
   - TEQUIO (co-fundador, proyecto tokenizado)
   - Infinitech Labs (Secretario CA, 1%)

3. **Yoltik tiene DOS productos SaaS, no uno:**
   - **MATI**: operaciones con IA (no estaba en memoria previa)
   - **Ikán**: cumplimiento LFPIORPI (ya conocido)
   - Kailash es un proyecto distinto, no producto Yoltik vivo.

4. **Equipo Kawiil real:** Viri (Viridiana García Turcott — socia, contable), Chucho (Jesús García Turcott — socio, legal), Fernando, Roberto, Xime, Sebastián. Tratamiento "El Tequio" para el equipo interno Yoltik.

5. **Frentes regulatorios activos:** Vizum vs CNBV (2 expedientes: amparo D.A. 17/2026 + Oficio 14556950). Caso LVGS con $850K que requiere Reconocimiento de Deuda.

6. **Polo está cursando Doctorado INAP (Inteligencia/Seguridad) + Maestría Derecho Aduanero** simultáneamente desde 22/23-may-2026.

7. **Kawiil OS** tiene 3 agentes nativos (Archivista, Integrador, Nutritor) + 6 agentes VM (Maya, Sofia, Diego, Alex, Carlos, Luna). Pipeline Haiku 4.5 → Sonnet 4 a tabla `knowledge_insights`. Tú (Nexo/Louis) eres parte de este ecosistema agentic, no aislado.

8. **Identidad visual Yoltik formalizada en Brandbook v2.1**: paleta Navy `#0C2340` / Jade `#00917C` / Ámbar `#F0A500` / Mint `#1DDBA8`, tipografía Sora. Logo Yoltik = "Órbita Nodal", logo Ikán = "Hexágono Vivo".

9. **PII personal de Polo en PEOPLE.md** (tel, BBVA, dirección de padres, etc.): Polo autorizó tenerla en memoria local porque vive en su Mac. No la compartas con servicios externos sin permiso explícito.

Lee siempre los 5 archivos al inicio de cada conversación nueva, no solo USER.md.
NEXOEOF
  cp "$PATCH" "$HOME/.openclaw/agents/general/agent/AGENTS.md"
  echo "    AGENTS.md actualizado"
else
  echo "    AGENTS.md ya tiene la sección de import, no se duplicó"
fi

echo ""
echo "==> Reiniciando gateway"
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
sleep 4
if lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "    OK, gateway escuchando"
else
  echo "    WARN: revisa con: lsof -iTCP:3000 -sTCP:LISTEN"
fi

echo ""
echo "============================================================"
echo "Memoria de Louis actualizada con corpus Cowork de 90 días."
echo ""
echo "Pruébalo: en Telegram o iPad nueva conversación, manda:"
echo "  'Louis, qué proyectos tengo activos y qué frentes regulatorios atendiendo'"
echo ""
echo "Louis debe mencionar Tloque, Vizum vs CNBV, caso LVGS, etc."
echo "============================================================"
