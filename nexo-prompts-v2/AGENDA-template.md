# AGENDA — Polo (CEO Kawiil)

Última actualización: 2026-05-24 21:36 CST por Louis (Nexo).

---

## Para HOY (lunes 25 mayo 2026)

Pendientes técnicos del setup de Louis:

- [ ] **Registrar app de Nexo en Entra ID — tenant Kawiil**
  Portal: https://entra.microsoft.com
  Guía: ~/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/m365/01-registrar-app-entra-id.md
  Resultado: Client ID + Tenant ID de Kawiil guardados en ~/.openclaw/credentials/m365-kawiil.env
  Tiempo: 10-15 min

- [ ] **Registrar app de Nexo en Entra ID — tenant Yoltik**
  Repetir lo mismo con cuenta admin de Yoltik
  Resultado: Client ID + Tenant ID de Yoltik guardados en ~/.openclaw/credentials/m365-yoltik.env
  Tiempo: 10-15 min

- [ ] **Rotar gateway token de OpenClaw**
  Comando listo en chat de ayer. El token actual quedó pegado en conversación con Claude y debe considerarse comprometido.
  Tiempo: 2 min

- [ ] **Rotar Anthropic API key**
  Generar nueva en console.anthropic.com → settings → API Keys
  Actualizar en ~/.openclaw/.env y .zshrc
  Revocar la anterior
  Tiempo: 5 min

## Para esta SEMANA

- [ ] Conectar MCP de Outlook a OpenClaw (paso 2 del setup M365) — bloqueado por Entra ID arriba
- [ ] Primer OAuth handshake con cada tenant
- [ ] Probar lectura de correo desde Nexo: "Cuántos correos no leídos tengo en Kawiil hoy"
- [ ] Probar lectura de calendario: "Mi agenda de mañana"

## Pendientes acumulados (backlog)

- [ ] Conectar Slack MCP
- [ ] Conectar Dropbox MCP
- [ ] Conectar Kawiil Central (decisión de read-only vs read-write — diferida a fase 2.5)
- [ ] Skills custom: morning-brief, pld-monitor, meeting-prep, end-of-day
- [ ] Migración a dominio permanente (nexo.kawiil.mx con Cloudflare Tunnel + Access + Entra SSO)
- [ ] Definir backup de credenciales (1Password? secure note?)

## Decisiones pendientes

- Definir qué pasa con iMessage como canal — quedó parqueado, posiblemente segunda Apple ID en Mac dedicada
- Cuándo migrar de Mac de trabajo a Mac Mini dedicada para Nexo

---

## Cómo Louis usa este archivo

- Al iniciar cada conversación nueva, Louis lee este archivo y hace briefing.
- Al final de cada interacción importante, Louis actualiza este archivo con nuevos pendientes o marca completados.
- Polo puede pedir "agenda" en cualquier momento para revisar.
- Polo puede decir "olvida X" para borrar un pendiente, o "agrega Y" para agregar uno.
- Cuando un pendiente se completa, Louis lo mueve a una sección "Completado esta semana" al final del archivo (no la borra inmediatamente, para que Polo vea su progreso).
