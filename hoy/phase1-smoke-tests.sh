#!/usr/bin/env bash
# phase1-smoke-tests.sh — Smoke tests Claude + Ollama para Fase 1 Yoltik híbrido
set -euo pipefail

set -a
[[ -f "$HOME/.openclaw/.env" ]] && source "$HOME/.openclaw/.env"
set +a

FAIL=0

echo "=== Smoke Ollama (local) ==="
if openclaw infer model run --local --model "ollama/gpt-oss:20b" \
  --prompt "OK si me entiendes." --json 2>&1 | tee /tmp/yoltik-ollama-smoke.json | grep -q '"ok": true'; then
  echo "✓ Ollama OK"
else
  echo "✗ Ollama FALLÓ"
  FAIL=1
fi

echo ""
echo "=== Smoke Claude (cloud) ==="
if ! grep -q '^ANTHROPIC_API_KEY=' "$HOME/.openclaw/.env" 2>/dev/null; then
  echo "✗ Falta ANTHROPIC_API_KEY en ~/.openclaw/.env"
  echo "  Ejecuta: bash $(dirname "$0")/setup-anthropic-key.sh"
  exit 1
fi

CLAUDE_OUT=$(mktemp)
if perl -e 'alarm 60; exec @ARGV' openclaw infer model run \
  --model "anthropic/claude-sonnet-4-6" \
  --prompt "Eres asistente ejecutivo del CEO de Kawiil. Responde en español mexicano profesional. Solo di OK si me entiendes." \
  --json > "$CLAUDE_OUT" 2>&1; then
  if grep -q '"ok": true' "$CLAUDE_OUT"; then
    echo "✓ Claude OK"
    grep -o '"text": "[^"]*"' "$CLAUDE_OUT" | head -1 || true
  else
    echo "✗ Claude respondió pero sin ok:true"
    cat "$CLAUDE_OUT"
    FAIL=1
  fi
else
  echo "✗ Claude FALLÓ"
  cat "$CLAUDE_OUT"
  FAIL=1
fi
rm -f "$CLAUDE_OUT"

if [[ "$FAIL" -ne 0 ]]; then
  echo ""
  echo "PARAR: corrige auth Claude antes de iMessage."
  exit 1
fi

echo ""
echo "✓ Ambos smoke tests pasaron. Siguiente: bash $(dirname "$0")/setup-imessage.sh"
