# Checklist maestro — Yoltik AI / OpenClaw

**Objetivo:** Tener una instancia funcional **HOY** que puedas probar desde tu celular, y endurecerla con Cloudflare Zero Trust el fin de semana.

**Filosofía:** Cowork (yo) ya generó todos los scripts y configs. Tú solo ejecutas comandos y pegas tokens cuando se te pida.

---

## 🟢 HOY (~2 horas tuyas)

### A. Pre-requisitos (15 min)

| # | Acción | Cómo | Quién |
|---|---|---|---|
| A1 | Tener una Mac con macOS 13+ y ~5GB libres | Tu MacBook actual sirve para el piloto | **Tú** |
| A2 | API key de Anthropic | Ve a [console.anthropic.com](https://console.anthropic.com) → API keys → Create key. Cópiala a un lugar seguro. | **Tú** |
| A3 | Cap de gasto en Anthropic | En la misma consola → Settings → Limits → pon $200 USD/mes para el piloto | **Tú** |
| A4 | Cuenta de Telegram | Si no tienes, descarga la app y crea cuenta. Cualquier número celular sirve. | **Tú** |

### B. Instalación (30 min · 5 min tuyos + tiempo de descarga)

| # | Acción | Comando | Quién |
|---|---|---|---|
| B1 | Abre Terminal en tu Mac | Cmd+Espacio → "Terminal" | **Tú** |
| B2 | Navega al folder del setup | `cd "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy"` | **Tú** |
| B3 | Haz ejecutable el script | `chmod +x install-yoltik-ai.sh validate.sh killswitch.sh` | **Tú** |
| B4 | Corre el instalador | `bash install-yoltik-ai.sh` | **Tú** + script automatizado por mí |
| B5 | Cuando OpenClaw pida API key | Pega la del paso A2 | **Tú** |
| B6 | Cuando pregunte por daemon | Responde "yes" | **Tú** |
| B7 | Cuando pregunte por canales | Skipea Telegram aquí, lo hacemos por UI después | **Tú** |

✅ **Cowork hace:** Homebrew, Node 22, jq, cloudflared, OpenClaw, hardening del config, creación de spaces, allowlist, audit logs, restart del daemon, verificación de bind.

🙋 **Polo hace:** correr 4 comandos y pegar 1 API key.

### C. Telegram bot (15 min)

| # | Acción | Cómo | Quién |
|---|---|---|---|
| C1 | Abre Telegram en celular o desktop | — | **Tú** |
| C2 | Busca `@BotFather` y mándale `/newbot` | — | **Tú** |
| C3 | Nombre del bot: `Yoltik AI` | Cualquier nombre humano | **Tú** |
| C4 | Username del bot: `yoltik_ai_bot` (o variante si está tomado) | Debe terminar en `_bot` | **Tú** |
| C5 | Copia el token que te da BotFather | Formato `123456789:ABC...` | **Tú** |
| C6 | Abre el UI de OpenClaw | En tu Mac: http://127.0.0.1:3000 | **Tú** |
| C7 | Login con el token del gateway | El token está en `~/.openclaw/gateway-token.txt` | **Tú** |
| C8 | Channels → Add → Telegram → pega el token de C5 | — | **Tú** |
| C9 | Crea grupo privado en Telegram: "Yoltik AI · Polo" | En la app → Nuevo grupo | **Tú** |
| C10 | Agrega el bot al grupo como administrador | Permisos básicos están bien | **Tú** |
| C11 | Manda `/start` en el grupo | El bot debería contestar con un saludo | **Tú** |

### D. Primera prueba (15 min)

| # | Acción | Esperado |
|---|---|---|
| D1 | En el grupo de Telegram, escribe: *"Hola, ¿qué puedes hacer?"* | El bot responde con su lista de capacidades |
| D2 | Pruébalo desde el celular (sale de oficina, datos móviles) | Mismo resultado, sin importar dónde estés |
| D3 | Pídele: *"Resúmeme en 3 puntos qué es la LFPIORPI"* | Respuesta usando Claude Sonnet 4.6 |
| D4 | Pídele: *"Crea un archivo de prueba en ~/Desktop/prueba.txt con tres haikus sobre dragones"* | El archivo aparece en tu Desktop |

### E. Validación de seguridad (5 min)

| # | Acción | Comando |
|---|---|---|
| E1 | Corre el validador | `bash validate.sh` |
| E2 | Si todo está en verde: termina por hoy | — |
| E3 | Si hay warnings: revísalos, suelen ser FileVault o backup | — |
| E4 | Si hay fallos: corre `bash killswitch.sh` y avísame | — |

✅ **Fin del día 1.** Tienes una instancia funcional accesible desde tu celular, hardened a nivel local. No está expuesta a internet, así que solo TÚ la usas. Mañana o el fin de semana añadimos Cloudflare para que los directivos también puedan entrar por web.

---

## 🟡 FIN DE SEMANA (~3 horas tuyas · sábado o domingo)

### F. Dominio en Cloudflare (45 min · principalmente espera de DNS)

| # | Acción | Cómo | Quién |
|---|---|---|---|
| F1 | Verifica que kawiil.mx está en Cloudflare | dash.cloudflare.com → ¿aparece kawiil.mx? | **Tú** |
| F2 | Si NO está: agrégalo | dash.cloudflare.com → Add a Site → kawiil.mx → elige plan Free → cambia nameservers donde compraste el dominio | **Tú** |
| F3 | Espera propagación DNS | 5-60 min. Verifica con `dig NS kawiil.mx` que apunte a Cloudflare | **Tú** |
| F4 | Activa Zero Trust | one.dash.cloudflare.com → te lleva por wizard de activación | **Tú** |

### G. Cloudflare Tunnel (20 min)

| # | Acción | Comando |
|---|---|---|
| G1 | Cd al folder del fin de semana | `cd "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/fin-de-semana"` |
| G2 | Permisos de ejecución | `chmod +x setup-cloudflare-tunnel.sh` |
| G3 | Corre el setup | `bash setup-cloudflare-tunnel.sh` |
| G4 | El script abre el browser para login en CF — autoriza | — |
| G5 | El script crea el túnel, configura DNS, instala servicio | Automatizado por mí |

✅ **Cowork hace:** crear el túnel, generar config.yml con tus valores, configurar DNS record, instalar el daemon como servicio, verificar conexión.

🙋 **Polo hace:** dar permiso de sudo, autorizar en browser.

### H. Cloudflare Access (30 min)

| # | Acción | Dónde |
|---|---|---|
| H1 | Conectar Google Workspace como IdP | one.dash.cloudflare.com → Settings → Authentication → Login methods → Add → Google Workspace |
| H2 | Crear aplicación "Yoltik AI" | Access → Applications → Add → Self-hosted (subdomain: ai, domain: kawiil.mx, session 8h) |
| H3 | Policy "Solo directivos" | Allow + Emails ending in @kawiil.mx + Require MFA |
| H4 | Policy "Block all" | Block + Everyone (al final de la lista) |

### I. WAF rules (15 min)

| # | Acción | Dónde |
|---|---|---|
| I1 | Abrir Security → WAF → Custom rules | Dashboard de kawiil.mx |
| I2 | Crear las 4 reglas listadas en `waf-rules.txt` | Copy-paste de las expresiones del archivo |
| I3 | Crear Rate limiting rule en `Security → Rate limiting rules` | Mismo archivo |

### J. Validación full (30 min)

| # | Acción | Esperado |
|---|---|---|
| J1 | Desde tu celular sin VPN: `https://ai.kawiil.mx` | Redirect a login Google → tras login + MFA → UI de OpenClaw |
| J2 | Desde otra cuenta NO @kawiil.mx | Bloqueo de Access |
| J3 | Desde una IP de Europa (con VPN) | Bloqueo por geo-restriction |
| J4 | `nmap -p 3000 <tu-IP-pública>` | Puerto closed/filtered |
| J5 | Corre `bash validate.sh` otra vez | Todo verde |

✅ **Fin del día 2.** ai.kawiil.mx funciona desde cualquier celular para emails @kawiil.mx con MFA. Listo para invitar directivos.

---

## 🔵 SEMANA SIGUIENTE (~4 horas tuyas, repartidas)

### K. Onboarding directivos (1h por persona)

Para cada directivo:
1. Crear su cuenta en Google Workspace si no la tiene.
2. Añadirlo al grupo `directivos@kawiil.mx` (creas el grupo si no existe).
3. Forzar MFA en su cuenta.
4. Sesión 1:1 de 30 min: mostrarle cómo entrar, qué pedirle, qué NO pedirle.
5. Crear su grupo privado de Telegram con el bot.

### L. Mac Mini dedicada (opcional, recomendado mes 2)

Cuando el piloto funcione y todos los directivos lo usen, migrar a una Mac Mini dedicada. El proceso es:
1. Comprar Mac Mini M4 8GB (~$15k MXN).
2. Backup completo de `~/.openclaw/` desde la Mac actual.
3. Correr `install-yoltik-ai.sh` en la nueva Mac.
4. Restaurar `~/.openclaw/` desde backup.
5. Migrar el túnel (apuntar al nuevo cloudflared).
6. Decommission de la Mac actual.

### M. Espacios `legal` e `ikan` (semana 2-3)

| Espacio | Cuándo activarlo | Bloqueador |
|---|---|---|
| `legal` | Cuando el contrato review skill esté validado por ti | Probar con NDAs simulados primero |
| `ikan` | DESPUÉS de revisión legal interna LFPIORPI | DPA con Anthropic + ZDR activado + revisión de tu asesor PLD |

### N. Skills curados (continuo)

Tres skills iniciales que te recomiendo añadir uno por uno (cada uno: leer su código en GitHub, añadirlo a la allowlist, reiniciar, probar):

1. **email-summary** — digest matutino de Gmail.
2. **calendar-brief** — preparación de juntas del día.
3. **pld-monitor** (custom, lo escribimos juntos) — scrape semanal DOF/CNBV.

---

## 🆘 ANTE CUALQUIER EMERGENCIA

En la Mac, abre Terminal y corre:
```bash
cd "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy"
bash killswitch.sh
```

Esto:
1. Apaga el túnel Cloudflare.
2. Detiene OpenClaw.
3. Toma snapshot del audit log.
4. Te muestra las últimas 30 acciones del agente.

Después, abre un chat conmigo en Cowork y pega:
- Lo que pasó (cómo te enteraste).
- El output del killswitch.
- Cualquier mensaje sospechoso recibido.

---

## 📊 Resumen de la división de trabajo

| Fase | Tiempo Polo | Tiempo Cowork (script) | % Automatizado |
|---|---|---|---|
| Hoy — instalación base | ~5 min activos + 25 min espera | 30 min de instalación | ~85% |
| Hoy — Telegram | ~15 min (UI clicks) | — | 0% (es UI manual) |
| Hoy — prueba | ~15 min | — | — |
| Fin de semana — CF Tunnel | ~5 min activos + 15 min espera DNS | 20 min de setup | ~80% |
| Fin de semana — CF Access | ~30 min (dashboard manual) | — | 0% (es UI de CF) |
| Fin de semana — WAF | ~15 min copy-paste | configs ya generadas | ~50% |
| Fin de semana — validación | ~30 min | script | ~70% |
| **TOTAL** | **~2 horas hoy + 3 horas fin de semana** | **— scripts ya están** | **~70% automatizado** |

---

## 📁 Archivos de este bundle

```
yoltik-ai-setup/
├── CHECKLIST-POLO.md           ← este archivo
├── hoy/
│   ├── install-yoltik-ai.sh    ← instalador principal
│   ├── validate.sh             ← validador de seguridad
│   └── killswitch.sh           ← apagado de emergencia
└── fin-de-semana/
    ├── setup-cloudflare-tunnel.sh  ← túnel CF
    ├── cloudflared-config.yml.template
    └── waf-rules.txt           ← reglas WAF para copy-paste
```

---

## 🎯 Próximo paso INMEDIATO

Abre Terminal en tu Mac y corre:

```bash
cd "/Users/leopoldobassoco/Documents/Claude/Projects/Yoltik Desarrollos/yoltik-ai-setup/hoy"
chmod +x *.sh
bash install-yoltik-ai.sh
```

Cuando termine (o si algo falla), regrésate al chat y dime cómo te fue.
