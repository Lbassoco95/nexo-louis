# LEARNINGS_extracted — Patrones, preferencias y reglas de trabajo de Polo

> Inferidos del corpus business mar-may 2026. Louis debe internalizar estos patrones para anticipar respuestas de Polo y evitar correcciones repetidas.

---

## Estilo de comunicación

- **[2026-03-26] Mensajería Slack/WhatsApp**: Polo pide mensajes cortos, directos, con secciones etiquetadas con em dashes, mínima prosa. Corrige repetidamente cuando un borrador es demasiado largo. "Concisa y directa" es la norma; si la primera respuesta no lo es, hay que recortar.
  Evidencia: `a3fb6277` Apollo KYB (refinó 3 veces más corto); `42a4960c` Plan Maestro one-pager.

- **[2026-03-26] Dos variantes por correo**: cuando redacta comunicaciones internas/clientes, prefiere recibir 2 versiones (una más formal/detallada y una breve/directa) para escoger.
  Evidencia: `e9152aea`, `a002f315`.

- **[2026-03-26] Tono con clientes cálido cuando hay relación cercana**: con Ana de Dazon usa lenguaje amistoso, emojis, "tú", sin presión. Con Cristhian de ABEJORRO78 corrigió de "usted" a "tú".
  Evidencia: `ed135648`, `4ddf3dc6`.

- **[2026-03-11] Español mexicano**: rechaza palabras en inglés cuando existe equivalente. Pidió "adendum" en español, "lo más mexicano posible".
  Evidencia: `45d13d50`.

- **[2026-04-09] Comunicaciones internas firmadas "Leopoldo Bassoco"** (no Polo) para tono formal.
  Evidencia: `06e2ea4e`.

- **[2026-05-22] Tono de marca Yoltik**: cultura de calle mexicana mezclada con raíces indígenas y claridad técnica. Nunca traducido del inglés ni con voz Silicon Valley. Rechaza "Manifiesto" (cargado político) → usa "Doctrina".
  Evidencia: `8c9e7f1f`.

## Estilo documental

- **[2026-03-12] Codificación estructurada**: todos los documentos llevan folio tipo `DAZ-RH-NOT-001`, `DAZ-COM-POL-03`, `VIZ-PLD-BC-01`, `PW-CE-001-2026`. Mantener la convención al generar nuevos.
  Evidencia: `3b7d2aa0`, `b1ccf8f3`, multiplicas Dazon docs.

- **[2026-03-12] CNBV file naming**: separadores con guiones, sin puntos ni paréntesis. Requerimiento explícito CNBV.
  Evidencia: `e0e226a8`.

- **[2026-03-08] Word .docx es el formato de entrega**. PDF cuando es para firma física o cliente externo. HTML solo para visuales internos. Evita PDF para entregables en iOS (problemas de rendering en Claude app).
  Evidencia: `0186e602`, `afae6c4d`.

- **[2026-03-08] Versiones, no addenda**: cuando se modifica un manual, prefiere regenerar la versión completa v2.0 con Control de Cambios al final, no addenda separadas. Living document.
  Evidencia: `06e2ea4e`.

- **[2026-03-17] Documentos para empleados ≠ documentos regulatorios**: en capacitaciones, NO mezclar hallazgos de auditoría / incidentes regulatorios sensibles. Las capacitaciones son formativas, prácticas, accionables.
  Evidencia: `8e8c0904`.

- **[2026-03-12] Lenguaje neutral, sin caracterización subjetiva**: en actas administrativas/disciplinarias, solo hechos objetivos (horas exactas, fechas). Nunca calificar actitud, tono de voz o conducta del trabajador. Solo LFT como fundamento.
  Evidencia: `3b7d2aa0`.

- **[2026-03-31] Pedidos quirúrgicos**: si dice "solo elimina la sección X, no modifiques nada más", aplicarlo exactamente; revisará y corregirá si se sale del scope.
  Evidencia: `44a0bb7a`, `4b7031f5`.

- **[2026-04-27] Documentos sin marcadores de IA**: NO incluir frases como "Recomendamos consultar un abogado" ni avisos de revisión por IA en contratos finales.
  Evidencia: `093f4043`.

## Reglas de cumplimiento PLD/FT y regulatorio

- **[2026-03-13] Manual Vizum, sin "Fintech"**: TODAS las variantes de "Fintech/fintech/FinTech" se sustituyen por "plataforma tecnológica de Vizum", "empresa cliente", "entidades usuarias". EXCEPCIÓN: "Ley Fintech" (nombre oficial) se preserva.
  Evidencia: `64040270`.

- **[2026-03-13] Disposición 4ª frac IV inciso C/E**: marco regulatorio Vizum para personas morales y propietario real. Disposición 4ª Bis para IDNP. Disposiciones 20ª–21ª para beneficiarios controladores.
  Evidencia: `e660b78d`, `b2b57a42`.

- **[2026-03-25] Carta declaratoria Vizum no actividad vulnerable**: el argumento canónico es que como entidad financiera supervisada por CNBV bajo Art. 81-A Bis LGOAAC, Vizum está fuera del catálogo Art. 17 LFPIORPI.
  Evidencia: `0e51e1a9`.

- **[2026-03-30] EBR canónico (5 elementos)**: productos/servicios, clientes, geografía, transacciones/canales, [quinto elemento por confirmar]. Tres fases: Diseño, Implementación, Valoración. Bloques A/B/C de clasificación.
  Evidencia: `69fd73ab`, `f10e5d99`.

- **[2026-03-30] Ikán "Config over Code"**: el módulo AV no es un módulo único; es contenedor configurable donde cada fracción del Art. 17 LFPIORPI activa contexto independiente (campos, umbrales, alertas, formato aviso). Fracción XVI (cripto): Anexo 16, umbrales 645/3210 UMA. Fracción V (inmobiliarias): 8025 UMA.
  Evidencia: `86cef464`.

- **[2026-03-30] SPPLD deadlines**: aviso día 17 del mes siguiente (NO 30 días); SPPLD se consulta días 15 y último de cada mes.
  Evidencia: `86cef464`.

- **[2026-03-15] EBR teórico**: para auditoría rigurosa, las fuentes CNBV son insuficientes; necesitan complementarse con disciplinas científicas (teoría del riesgo, data science, criminología financiera).
  Evidencia: `69fd73ab`.

- **[2026-04-22] Tesis doctoral**: arquitecturas diferenciadas PLD/FT entre sujetos obligados CNBV vs actividades vulnerables SAT. Yoltik es la base empírica de la pista SAT. MIIR (API gubernamental nacional) es la hipótesis política mayor.
  Evidencia: `2428c504`.

- **[2026-04-30] Aviso GIIN (239/CFF)**: Vizum debe darse de alta como transmisor de dinero. Trámite obligatorio para instituciones financieras no sujetas a reportar bajo FATCA.

- **[2026-04-15] PLD K'awiil como sujeto obligado**: K'awiil mismo es sujeto obligado LFPIORPI por su práctica de representación; se inscribe vía SAT-PLD portal, no directamente UIF.
  Evidencia: `cb9eccae`.

## Reglas legales / fiscales recurrentes

- **[2026-03-24] Aplicación rígida de LFT**: Polo cita siempre Art. 47 LFT (rescisión), Art. 134 (obligaciones), Art. 93 LISR (exenciones), Art. 162 (prima antigüedad). Usa "más de tres faltas en 30 días" (literal Art. 47-X), no "tres faltas".
  Evidencia: `b6f80446`.

- **[2026-04-07] SMG (no UMA) para exención liquidación/prima antigüedad**: Art 93 fr XIII LISR usa "salario mínimo", no UMA. Desindexación 2016 no aplica aquí.
  Evidencia: `65ee912c`.

- **[2026-04-09] Divisor LFT 30 (no 30.4)**: para SDI y prestaciones laborales. Error común que Polo identifica rápido.
  Evidencia: `6857692e`.

- **[2026-03-09] Reglamento Interior Trabajo**: 30 min como threshold de retardo vs falta injustificada; escala disciplinaria de 4 niveles (verbal → escrita → suspensión → terminación). Para Dazon: 1-2 retardos sin consecuencia, 3 verbal, 5 acta administrativa.
  Evidencia: `7db71a2a`.

- **[2026-04-09] Persona física + persona moral**: vehículo PF → PM exento de IVA (Art 9 fr IV LIVA), retención 20% ISR por PM (Art 126 LISR). Para PF asalariada comprando coche: CFDI uso S01 (sin efectos fiscales).
  Evidencia: `534c18f7`, `ecdab860`.

- **[2026-03-31] Tributación internacional**: para Dimex MX → cliente PE, CDT Art 7 business profits, NO asistencia técnica. Factura 0% IVA (export servicios), 0% retención SUNAT con Certificado Residencia Fiscal SAT.
  Evidencia: `dce7f4c9`.

- **[2026-04-07] Pedimento NO se entrega a clientes**: Dazon importa CKD (kits), no vehículos terminados → Art 146 Ley Aduanera no aplica. Defensa: Art 12, 33, 89 LFPC + Art 69 CFF + Ley Propiedad Industrial Arts 82+ (secreto comercial).
  Evidencia: `5978f3e8`.

## Stack de trabajo

- **[2026-03-08] Stack web Polo**: Lovable (React + TS + Vite + Tailwind + shadcn), Supabase backend, Cursor IDE. Cuando hay pregunta técnica, prefiere prompt completo pegable para Lovable.
  Evidencia: `b0261a26`, `68690b76`.

- **[2026-03-20] Excel + CONTPAQi**: stack contable Kawiil. Cuando se entrega Excel, NO mover ni renombrar cuentas, solo construir con datos existentes. Preservar celdas verdes intactas. Formato dinámico con fórmulas.
  Evidencia: `363227bc`, `6ccb53af`.

- **[2026-04-21] LLM consumption planning**: estimaciones razonables para 90 días: $1500–2000 USD (~100M tokens) con dual-pipeline Haiku 4.5 drafts + Sonnet 4 reviews.
  Evidencia: `16346d02`.

- **[2026-05-07] Yoltik dashboard ≠ Kawiil OS**: aunque comparten IP (Yoltik dueño), Yoltik dashboard es standalone para operaciones comerciales. Yoltik usa los mismos 5 agentes IA en Supabase Edge Functions (no Azure).
  Evidencia: `2c10cfa5`, `e501f6ac`.

- **[2026-05-07] Principio universal agentes**: "agents propose, humans approve". NO publicación autónoma; humano siempre setea draft = approved antes de outbound.
  Evidencia: `76806268`.

- **[2026-04-16] Memoria institucional via Kawiil OS**: circulación cross-cliente excluye nombres, emails, datos bancarios, financieros, RFCs, domicilios físicos. Formalizar contractualmente.
  Evidencia: `8a80bd66`.

- **[2026-05-18] Open source agents alternative**: Aider + Open Interpreter en Docker + CrewAI/LangGraph. Polo evalúa Linux Mint XFCE 22 en hardware viejo HP A6-7310 para correr agentes Yoltik/Kawiil.
  Evidencia: `f547a15f`.

- **[2026-04-24] Obsidian**: complementario a Kawiil OS — Obsidian para exploración humana, Kawiil OS para memoria institucional con agentes. Plugin Copilot con Anthropic API key.
  Evidencia: `534b7e5e`.

## Reglas de hire y cultura

- **[2026-03-30] Modelo Teal/Holocracy aspiracional**: célula horizontal, autogestión, propósito evolutivo. G1 → G4. G4 ahora se llama "Transformador" (antes "Guía de Célula").
  Evidencia: `f2d1d083`, `201928de`.

- **[2026-03-30] Recruiter: LinkedIn primero, OCC segundo**. Indeed deprioridado para legal/cumplimiento por baja señal/ruido.
  Evidencia: `9438b3da`.

- **[2026-05-15] Polo NO entrevista directamente** los reclutamientos contables; la directora del área conduce el proceso, Polo entra en 1 o ninguna.
  Evidencia: `dadbb200`.

- **[2026-03-30] Kawiiler en formación**: ruta para recién egresado o ≤1 año experiencia. Onboarding 3 meses, evaluación estructurada → aumento. Email aplicaciones: kawiil@kawiil.mx.

- **[2026-04-20] No crecer comercial sin bases**: Polo conscientemente decide NO contratar más comerciales hasta tener procesos firmes.
  Evidencia: `83b8a81f`.

- **[2026-05-04] Regla universal G1 → G2**: todo nuevo ingreso 3 meses a salario G1 antes de subir a G2 si aprueba evaluación.
  Evidencia: `18bb5060`.

## Identidad visual

- **[2026-04-18] Paleta Kawiil (cuatro sólidos + cinco degradados)**: KAWIIL Blue `#0075EF`, KAWIIL Green `#00FFA1`, Deep Blue `#0000A1`, KAWIIL Black `#000000`. Tipografía: Rajdhani (display), Space Grotesk (body), JetBrains Mono (labels). Estética clean/fintech.

- **[2026-04-26] Paleta Yoltik v2.1**: Navy `#0C2340`, Jade `#00917C`, Ámbar `#F0A500`, Mint `#1DDBA8`, Verde Hielo `#F0F5F4`, Gris Técnico `#6B8C87`. Tipografía Sora. Logo "Órbita Nodal" para Yoltik, "Hexágono Vivo" para Ikán. NUNCA usar burgundy/wine ni gold.
  Evidencia: `9b63ab5c` corrección palette.

- **[2026-04-30] Mascota Kawiilito**: kawaii mexicana, gender-neutral en piezas educativas. Para celebraciones y campañas (Día Niño, Madre, Maestro). Genera imágenes en Gamma; texto luego en Canva (Gamma frecuentemente equivoca fechas).
  Evidencia: `d046c96f`.

- **[2026-03-26] Dazon palette**: dark blue `#1F3864`, medium blue `#2E75B6`, light blue. Fuente Arial.

- **[2026-04-26] Iconografía Yoltik**: shield retirado del logo parent → reasignado solo a productos (hexágono = contenedor producto, escudo = identidad).

## Tooling / preferencias técnicas operativas

- **[2026-03-03] Microsoft 365** es el suite de Polo (Teams, Outlook, anti-spam Defender, transport rules SCL -1).
- **[2026-03-23] Minuta Círculo Kawiil**: formato markdown table simple, sin dashboards visuales ni HTML. Pure text minute-taking.
- **[2026-04-11] Reddit y RRSS personales**: Polo está creando perfil con identidad visual mesoamericana + AI/educación.
- **[2026-04-14] Speaker Congreso America Digital**: foro C-Level IA Digital Banking Fintech, tema "Transformación regulatoria digital MX", perfil "Fundador K'awiil".
- **[2026-05-06] Doctorado ACAMS beca**: Polo aplica beca; carrera compliance inicia 2019 PROFECO.
- **[2026-05-23] Maestría Derecho Aduanero**: primera clase 23-may-2026. Apuntes en Mac (Obsidian recomendado).
- **[2026-04-11] Star Citizen**: hobby personal. Cloud VMs no viables por Easy Anti-Cheat.

## Contenido / marketing / RRSS

- **[2026-04-02] Calendario Q2 Kawiil IG**: martes PLD, miércoles Innovation/Design Thinking, jueves contable/legal, sábados rotación brand/tips/reflexión. Gamma genera imagen por slide individual (no carruseles).
- **[2026-05-15] Día del Maestro**: Kawiilito con tono inclusivo "maestros recién jubilados y los que apenas comienzan".
- **[2026-04-21] LinkedIn share Maestría**: tono profesional/sobrio o reflexivo personal, NO promocional.

## Reglas legales blandas / éticas

- **[2026-04-18] Privacidad Kawiil OS**: jamás circular cross-cliente nombres, RFCs, emails, bancarios, financieros, domicilios físicos sin contrato formal.
- **[2026-03-17] Caso LVGS — door open**: Polo mantiene canal abierto con LVGS NO para comprometerse, sino para observar al nuevo despacho legal y lo que LVGS planea. Estrategia de inteligencia, no de colaboración.
- **[2026-03-17] Carta beneficiario Vizum**: lean, cliente-facing, no duplicar info ya en expediente.

