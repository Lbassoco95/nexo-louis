#!/usr/bin/env bash
# ollama-keepalive.sh — Evita cold start del modelo chat (cron cada 5 min en VPS).
# Uso: */5 * * * * polo /opt/openclaw/scripts/ollama-keepalive.sh >> /opt/openclaw/logs/ollama-keepalive.log 2>&1

set -euo pipefail
MODEL="${OLLAMA_FAST_MODEL:-llama3.1:8b}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
curl -fsS --max-time 30 "${HOST}/api/generate" -d "{\"model\":\"${MODEL}\",\"prompt\":\"ping\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null 2>&1 || true
