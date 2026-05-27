#!/usr/bin/env bash
# install-always-on.sh — Configura la Mac para que Louis siempre esté disponible.
#
# Comportamiento:
# - Conectada a corriente (AC): NUNCA duerme. Disco activo. Pantalla puede apagar a 30 min.
# - Con batería: duerme tras 30 min para no drenar.
# - En cualquier modo: despierta cuando llega tráfico de red (magic packet, conexión nueva).
# - Power Nap activo: tareas de fondo (cron, fetches) ocurren incluso si parece "dormida".
# - TCP keepalive activo: conexiones a Telegram/Microsoft Graph se mantienen vivas.
#
# Pantalla bloqueada NO afecta — daemons siguen corriendo normalmente.

set -euo pipefail

echo "==> Estado ACTUAL de pmset (antes del cambio)"
pmset -g | head -20

echo ""
echo "==> Aplicando configuración always-on (requiere contraseña sudo)"
echo ""

# AC adapter: nunca dormir computadora, disco, mantener TCP keepalive
sudo pmset -c \
  sleep 0 \
  disksleep 0 \
  displaysleep 30 \
  womp 1 \
  powernap 1 \
  tcpkeepalive 1 \
  networkoversleep 1

# Batería: TAMPOCO duerme (Polo quiere que responda aún en batería).
# Wake on network + Power Nap + TCP keepalive activos.
# Importante: drena batería más rápido (~3-5h vs 8-10h default).
# macOS sigue manejando "emergency sleep" si batería <5%, eso no se desactiva.
sudo pmset -b \
  sleep 0 \
  disksleep 0 \
  displaysleep 5 \
  womp 1 \
  powernap 1 \
  tcpkeepalive 1 \
  networkoversleep 1

echo ""
echo "==> Estado FINAL (después del cambio)"
pmset -g | head -20

echo ""
echo "==> Verificación de OpenClaw (debe seguir corriendo)"
launchctl print "gui/$(id -u)/ai.openclaw.gateway" 2>&1 | grep -E "(state|pid|last exit)" | head -5
lsof -iTCP:3000 -sTCP:LISTEN 2>/dev/null | head -3

echo ""
echo "============================================================"
echo "Configuración aplicada. Comportamiento:"
echo ""
echo "  • Conectada a corriente: NO duerme nunca. Louis siempre responde."
echo "  • Con batería: TAMPOCO duerme. Louis sigue respondiendo en batería."
echo "    Tradeoff: batería dura ~3-5h en lugar de 8-10h."
echo "    macOS aún hará 'emergency sleep' si batería <5%."
echo "  • Pantalla bloqueada NO la afecta — siempre puede trabajar."
echo ""
echo "Prueba:"
echo "  1. Bloquea la pantalla (Cmd+Ctrl+Q)."
echo "  2. Manda mensaje a Louis por Telegram desde tu iPhone."
echo "  3. Louis debe responder en pocos segundos sin desbloquear la Mac."
echo ""
echo "Si quieres revertir (volver a defaults de macOS):"
echo "  sudo pmset -c sleep 10 disksleep 10 displaysleep 10 womp 0 powernap 0"
echo "  sudo pmset -b sleep 5 disksleep 5 displaysleep 2 powernap 0"
echo "============================================================"
