# SOLUCIÓN: Contexto Persistente en Telegram + Router de Agentes

**Fecha:** 2026-05-27  
**Problema:** Louis siempre responde lo mismo sin entender contexto  
**Causa raíz:** OpenClaw guarda historial pero NO lo pasa a Ollama/kawiil-agents  
**Solución:** Router inteligente que lee historial, elige agente, mantiene sesión

---

## Estado Actual

✅ **Funciona:**
- OpenClaw gateway activo (puerto 3000)
- Telegram channel conectado
- Historial guardado en `~/.openclaw/spaces/general/telegram-history.jsonl`
- kawiil-agents con 19 agentes especializados listos
- gpt-oss:20b en Ollama

❌ **NO funciona:**
- kawiil-agents backend dando HTTP 500
- OpenClaw NO pasa historial a los agentes
- NO hay routing: todas preguntas van a claude-sonnet-4-6 genérico
- NO hay sesiones entre mensajes

---

## Arquitectura NUEVA

```
┌─────────────────────────────────────┐
│ Telegram (mensaje de Polo)          │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│ OpenClaw Gateway (puerto 3000)      │
│ ├─ Lee mensaje                      │
│ └─ Envía a Router                   │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│ telegram-router.py (NUEVO)          │
│                                     │
│ 1. Lee telegram-history.jsonl       │
│ 2. Clasifica pregunta:              │
│    • "¿Qué tengo?" → ollin          │
│    • "¿Deuda?" → teocuitl           │
│    • "Es legal?" → amatl/nelli      │
│    • General → ollama local         │
│ 3. Crea/reutiliza session_id        │
│ 4. Pasa contexto al agente          │
└────────────┬────────────────────────┘
             │
             ├─→ kawiil-agents/ollin
             ├─→ kawiil-agents/teocuitl
             ├─→ kawiil-agents/amatl
             ├─→ Ollama (gpt-oss:20b)
             └─→ Claude (análisis profundo)
             │
             ↓
┌─────────────────────────────────────┐
│ Agent responde (con contexto)       │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│ OpenClaw devuelve a Telegram        │
│ + Guarda en telegram-history.jsonl  │
└────────────┬────────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│ Telegram entrega a celular          │
└─────────────────────────────────────┘
```

---

## Paso 1: Diagnosticar kawiil-agents backend

```bash
# ¿Está corriendo el server?
lsof -iTCP:8000 -sTCP:LISTEN

# ¿Qué logs hay?
ps aux | grep kawiil

# ¿Ollama está bien?
curl http://localhost:11434/api/tags
```

**Si dices que da HTTP 500**, significa que el server está caído o tiene un problema. Necesitamos:

1. Encontrar dónde está el server (probablemente `kawiil-agents/server/main.py` o similar)
2. Iniciarlo en la terminal para ver errores
3. Verificar que conecta a Ollama y Claude API

---

## Paso 2: Crear el Router de Contexto (nuevo archivo)

**Archivo:** `slack/telegram-context-router.py`

```python
#!/usr/bin/env python3
"""
telegram-context-router.py

Router inteligente que:
1. Lee historial de conversación anterior
2. Clasifica la pregunta
3. Elige el agente correcto
4. Mantiene sesión persistente
5. Pasa contexto al agente
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

HOME = Path.home()
HISTORY_FILE = HOME / ".openclaw" / "spaces" / "general" / "telegram-history.jsonl"
SESSIONS_FILE = HOME / ".openclaw" / "telegram_sessions.json"

def load_sessions() -> dict:
    """Carga mapping de chat_id → session_id"""
    if SESSIONS_FILE.exists():
        return json.loads(SESSIONS_FILE.read_text())
    return {}

def save_sessions(sessions: dict):
    """Guarda mapping de chat_id → session_id"""
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))

def get_chat_id_from_context() -> str:
    """
    Obtiene el chat_id del contexto de OpenClaw.
    (En una integración real, OpenClaw pasa metadata)
    """
    # Por ahora usamos un ID fijo para testing.
    # Cuando OpenClaw lo pase, reemplazar.
    return "polo-telegram-main"

def get_session_id(chat_id: str) -> str:
    """Obtiene o crea session_id para este chat_id"""
    sessions = load_sessions()
    if chat_id not in sessions:
        sessions[chat_id] = f"session-{chat_id}-{datetime.now().timestamp()}"
        save_sessions(sessions)
    return sessions[chat_id]

def read_last_n_messages(n: int = 5) -> list:
    """Lee últimos N mensajes del historial"""
    if not HISTORY_FILE.exists():
        return []
    
    messages = []
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            msg = json.loads(line)
            messages.append(msg)
        except:
            pass
    
    return messages[-n:]

def classify_message(message: str, history: list) -> dict:
    """
    Clasifica la pregunta y decide qué agente usar.
    
    Returns:
        {
            "type": "agenda" | "finance" | "legal" | "pld" | "general",
            "agent": "ollin" | "teocuitl" | "amatl" | "nelli" | "general",
            "reason": "string"
        }
    """
    msg_lower = message.lower()
    
    # Patrones para cada tipo
    patterns = {
        "agenda": (
            ["¿qué tengo", "pendiente", "reunión", "cita", "calendario", 
             "agenda", "hoy", "mañana", "próxima"],
            "ollin"
        ),
        "finance": (
            ["deuda", "cliente", "dinero", "pago", "factura", "ingreso", 
             "gasto", "balance", "estado de cuenta", "flujo"],
            "teocuitl"
        ),
        "legal": (
            ["contrato", "legal", "ley", "jurídico", "nda", "cláusula", 
             "derecho", "acuerdo", "términos"],
            "amatl"
        ),
        "pld": (
            ["pld", "lavado", "cumplimiento", "kyc", "cliente", "riesgo", 
             "ikán", "reporta", "lfpiorpi"],
            "nelli"
        ),
    }
    
    for msg_type, (keywords, agent) in patterns.items():
        if any(kw in msg_lower for kw in keywords):
            return {
                "type": msg_type,
                "agent": agent,
                "reason": f"Detectado patrón de {msg_type}"
            }
    
    return {
        "type": "general",
        "agent": "general",
        "reason": "No coincide con patrones específicos"
    }

def format_context(history: list) -> str:
    """Formatea el historial para inyectarlo en el prompt"""
    if len(history) <= 1:
        return ""
    
    context = "=== CONTEXTO DE CONVERSACIÓN ANTERIOR ===\n\n"
    for msg in history[:-1]:  # Todos excepto el último (que es la pregunta actual)
        role = "Polo" if msg["role"] == "user" else "Louis"
        ts = msg.get("ts", "")[:16]  # Solo fecha/hora
        content = msg.get("content", "")[:100]  # Primeros 100 chars
        context += f"[{ts}] {role}: {content}\n"
    
    return context

def call_agent(agent_name: str, message: str, session_id: str, context: str) -> str:
    """
    Llama al agente apropiado.
    Si agent_name == "general", usa Ollama. Si no, usa kawiil-agents.
    """
    
    if agent_name == "general":
        # Usar Ollama directamente
        system_prompt = f"""Eres Louis, asistente de Polo en Kawiil.

{context}

Contexto actual: El usuario acaba de escribir:
"{message}"

Instrucciones:
- Responde ESPECÍFICAMENTE la pregunta actual.
- NO repitas respuestas anteriores si las recuerdas.
- Sé conciso (máximo 2 párrafos).
- Si necesitas más contexto, pide.
"""
        # Aquí iría llamada a Ollama
        # Por ahora placeholder
        return f"[LOCAL] Respuesta a: {message[:50]}"
    
    else:
        # Usar kawiil-agents
        try:
            result = subprocess.run([
                "python3",
                "/Users/leopoldobassoco/.openclaw/scripts/kawiil_agents.py",
                "chat",
                agent_name,
                message,
                "--session", session_id
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                return f"Error con agente {agent_name}: {result.stderr}"
            
            return result.stdout
        
        except Exception as e:
            return f"Error llamando agente: {str(e)}"

def main(user_message: str):
    """
    Flujo principal:
    1. Obtener chat_id y session_id
    2. Leer historial
    3. Clasificar pregunta
    4. Llamar agente con contexto
    5. Devolver respuesta
    """
    
    chat_id = get_chat_id_from_context()
    session_id = get_session_id(chat_id)
    
    history = read_last_n_messages(n=5)
    
    classification = classify_message(user_message, history)
    
    context = format_context(history)
    
    print(f"[Router] chat_id={chat_id}")
    print(f"[Router] session_id={session_id}")
    print(f"[Router] agent={classification['agent']} ({classification['reason']})")
    print(f"[Router] contexto={'SÍ' if context else 'NO'}")
    print()
    
    response = call_agent(
        classification["agent"],
        user_message,
        session_id,
        context
    )
    
    return response

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 telegram-context-router.py \"<mensaje>\"")
        sys.exit(1)
    
    message = sys.argv[1]
    result = main(message)
    print(result)
```

---

## Paso 3: Integrar Router en OpenClaw

En `~/.openclaw/openclaw.json`, modificar el agente `general`:

```json
{
  "agents": {
    "list": [
      {
        "id": "general",
        "default": true,
        "name": "Nexo General",
        "workspace": "~/.openclaw/spaces/general",
        "model": {
          "primary": "custom-router"
        },
        "router": {
          "type": "python",
          "script": "~/.openclaw/scripts/telegram-context-router.py",
          "channels": ["telegram"]
        }
      }
    ]
  }
}
```

---

## Paso 4: Test End-to-End

```bash
# 1. Copiar router a scripts
cp ~/nexo-louis/slack/telegram-context-router.py ~/.openclaw/scripts/

# 2. Hacer test de contexto
python3 ~/.openclaw/scripts/telegram-context-router.py "Hola, soy Louis"
python3 ~/.openclaw/scripts/telegram-context-router.py "¿Qué acabas de decir?"

# 3. Test con clasificación
python3 ~/.openclaw/scripts/telegram-context-router.py "¿Qué tengo pendiente hoy?"
python3 ~/.openclaw/scripts/telegram-context-router.py "¿Cuánta deuda tiene cliente X?"
```

---

## Próximos Pasos

1. **Diagnosticar kawiil-agents backend** (HTTP 500)
2. **Crear `telegram-context-router.py`** y probarlo
3. **Integrar en OpenClaw config**
4. **Test en Telegram real**

---

## Checklist

- [ ] kawiil-agents server corriendo y sano
- [ ] Router creado y testeado
- [ ] OpenClaw config actualizada
- [ ] Primer mensaje en Telegram funciona
- [ ] Segundo mensaje recuerda el contexto
- [ ] Tercera pregunta diferente va al agente correcto
