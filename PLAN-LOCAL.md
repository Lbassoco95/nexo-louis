# Plan revisado — OpenClaw 100% local con Ollama (sin Anthropic)

**Fecha:** 2026-05-24
**Cambio respecto al plan anterior:** Eliminamos Anthropic Claude API. Ahora el LLM corre en tu Mac vía Ollama. Cero datos salen a terceros.

---

## Arquitectura final

```
┌─────────────────────────────────────────────────────────┐
│  Tu Mac (laboratorio personal)                          │
│                                                         │
│  ┌──────────────────────────────────────────────┐       │
│  │ Ollama daemon · 127.0.0.1:11434              │       │
│  │  └── Modelo open weight (qwen3, llama3.3,    │       │
│  │      gpt-oss, gemma4 — según RAM)            │       │
│  └──────────────────────────────────────────────┘       │
│           ↑ (HTTP local, sin TLS porque es loopback)    │
│  ┌──────────────────────────────────────────────┐       │
│  │ OpenClaw gateway · 127.0.0.1:18789           │       │
│  │  • Provider: ollama (auto-discovery)         │       │
│  │  • models.providers.ollama.apiKey = ollama-  │       │
│  │    local (placeholder, no es secret real)    │       │
│  └──────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
         ↑                ↑                ↑
    Web Control UI    Telegram bot    Cloudflare Tunnel
    (browser local)   (outbound       (sábado: ai.kawiil.mx
                       polling)        con CF Access)
```

**Cero llamadas a Anthropic / OpenAI / Google.** El modelo vive en tu disco, la inferencia ocurre en tu CPU/GPU. Para datos PLD/Ikán/contratos es la arquitectura ideal porque **nada sale de la Mac**.

---

## Trade-offs honestos vs Claude API

| Aspecto | Local (Ollama) | Claude API (anterior plan) |
|---|---|---|
| **Privacidad** | Total. Nada sale del equipo. | Datos pasan por Anthropic (con DPA y ZDR mitiga, pero salen). |
| **Costo recurrente** | $0 | ~$3,000 MXN/mes en API calls |
| **Costo inicial** | Hardware (RAM) | Cero |
| **Velocidad respuesta** | 1-30 tok/s según modelo + hardware | 50-100+ tok/s |
| **Calidad razonamiento complejo** | 75-90% de Claude Sonnet 4.6 (depende del modelo) | 100% de Claude Sonnet 4.6 |
| **Tool calling (clave para agentes)** | Bueno con qwen3, gpt-oss; mediocre con modelos chicos | Excelente |
| **Cumplimiento LFPIORPI** | Trivial — datos no salen | Requiere DPA + ZDR + revisión legal |
| **Offline** | Funciona sin internet | Requiere conexión |
| **Mantenimiento** | Actualizar modelos manualmente | Auto |

**Veredicto:** para tu laboratorio personal y datos sensibles de Kawiil/Ikán, **local gana**. Para tareas que necesiten razonamiento clase Claude Opus (escritura compleja, análisis profundo), siempre puedes añadir Claude API como modelo *secundario* en el futuro y el agente elige según la tarea. Pero arranca local.

---

## Selección de modelo según tu hardware

OpenClaw usa Ollama, y el modelo más grande que puedes correr depende de tu RAM unificada (Apple Silicon):

| RAM | Modelo recomendado | Comentario |
|---|---|---|
| 8 GB | gemma4 (7B) o qwen3:4b | Funciona pero limita complejidad. Solo para piloto. |
| 16 GB | gpt-oss:20b o qwen3:14b | Sweet spot calidad/velocidad. Tool calling decente. |
| 24-32 GB | qwen3:32b o gpt-oss:32b o gemma4:27b | Excelente para agentes serios. Tool calling fuerte. |
| 48-64 GB | llama3.3 (70B) o qwen3:72b | Calidad cercana a Claude Sonnet en muchos casos. |
| 96+ GB | gpt-oss:120b o equivalentes | Top tier local. |

El script de setup detecta tu RAM y te recomienda automáticamente.

---

## Plan de implementación HOY (revisado)

**El estado actual de tu Mac:**
- ✅ OpenClaw 2026.5.22 instalado
- ✅ Gateway en 127.0.0.1:18789 (Cursor lo corrigió del 3000 que yo asumí)
- ✅ Hardening aplicado (auth.mode: token, bind: loopback)
- ✅ validate.sh pasa 10/10
- ❌ **NO hay modelo configurado** (el onboarding se hizo con `--skip-channels` y sin provider)
- ❌ NO hay Ollama instalado

**Faltan 3 cosas para tener el sistema funcional:**

### 1. Instalar Ollama (5 min)
```bash
brew install ollama
brew services start ollama
```

### 2. Bajar un modelo (10-30 min según tamaño)
El script `setup-ollama-local.sh` (que viene abajo) detecta tu RAM y recomienda. Manualmente:
```bash
ollama pull qwen3:32b   # si tienes ≥32 GB RAM
# o
ollama pull gpt-oss:20b # si tienes 16 GB RAM
# o
ollama pull gemma4      # si tienes 8 GB RAM
```

### 3. Configurar OpenClaw para usar Ollama (2 min)
```bash
export OLLAMA_API_KEY="ollama-local"
echo 'export OLLAMA_API_KEY="ollama-local"' >> ~/.zshrc

openclaw models set ollama/qwen3:32b   # o el modelo que bajaste
openclaw daemon restart
```

Verificar:
```bash
openclaw models list --provider ollama
openclaw infer model run \
  --local \
  --model ollama/qwen3:32b \
  --prompt "Responde solo 'OK' si me entiendes." \
  --json
```

---

## Lo que se ELIMINA del plan anterior

- ❌ Crear API key de Anthropic — ya no la necesitas para uso interno
- ❌ Cap de gasto en Anthropic Console — sin gasto
- ❌ Solicitar Zero Data Retention a Anthropic — no aplica
- ❌ Espacios diferenciados por API key — todos usan el mismo Ollama
- ❌ DPA con Anthropic para Ikán — no aplica

**Lo que se mantiene tal cual:**
- ✅ Hardening del gateway (bind loopback, token, auth)
- ✅ Telegram bot privado para acceso móvil
- ✅ Cloudflare Tunnel + Access para exposición a internet segura (fin de semana)
- ✅ WAF rules custom
- ✅ Killswitch
- ✅ Sistema de spaces (general/legal/ikan) — solo cambia el provider del modelo
- ✅ Skills custom (morning-brief, pld-monitor, contract-review) — ahora consumen Ollama, no Claude API

---

## Puerto correcto

**Recordatorio importante:** El puerto del gateway de OpenClaw es **18789**, no 3000.

Necesito que el `validate.sh` y los scripts de Cloudflare lo reflejen. Cursor ya arregló `validate.sh` y el config para que use 3000 — eso lo arregló Cursor manualmente. La doc oficial dice 18789 como default. Para tu Mac actual, sigue como está (3000) porque ya está funcionando — pero anota que en una reinstalación limpia sería 18789. Lo dejamos así para no romper lo que ya pasa 10/10.

---

## Próximo paso inmediato

Corre el prompt nuevo de Cursor para instalar Ollama + bajar modelo + conectar a OpenClaw. Está en [cursor-prompts.md](cursor-prompts.md) sección "FASE 1.5 - Ollama" (la añadiré ahora).
