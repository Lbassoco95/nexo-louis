# PROJECTS_extracted — Universo de proyectos, empresas y clientes de Polo

> Síntesis a partir de 431 conversaciones business (mar 2 – may 23, 2026). Las entidades con asterisco (*) son recurrentes y dominan el flujo de trabajo cotidiano de Polo.

---

## 1. Tloque (grupo paraguas) *
- **Qué es**: nombre elegido por Polo (de *Tloque Nahuaque*, "señor de lo cercano y lo lejano") para el holding/ecosistema que agrupa Kawiil, Yoltik y Tonatiuh. Sugerencias formales: "Tloque", "Grupo Tloque" o "Tloque MX".
- **Estado**: bautizado el 22-may-2026; aún no formalizado legalmente. Los nodos K'awiil, Yoltik y Tonatiuh siguen operando como entidades independientes.
- **Última mención**: 2026-05-22
- **Refs**: `6f68f064` Networking presentación de Polo y Viri.

## 2. Kawiil Mx (kawiil.mx) *
- **Qué es**: HUB ConTech + LegalTech con +10 años de operación. Estructura horizontal en células (Contabilidad/Fiscal, Legal, Administración) con grados G1–G4 (G4 = "Transformador") y un colectivo Consejo de Socios. Servicios: backoffice integral, cumplimiento PLD/FT, softlanding, constitución, litigio, asesoría regulatoria. Polo se firma como Socio Director / Director General. Razón social paralela: **Bassoco, Vega, Salas, Morales, Servicios Empresariales S.C.**
- **Estado**: en transición de "despacho" a "hub" (toda la documentación interna ya no usa "despacho"). Cuenta con ~14 Kawiilers; modelo aspiracional Teal/Holocracy. Manuales v3.0 (Organizacional), v2.0 (Identidad), v3.0 (Perfiles de Puesto) consolidados. Tooling: ERP propio (Kawiil OS/Central), Slack, Microsoft 365, Dropbox, Savio, CONTPAQi (VM), Worky. Notion eliminado.
- **Bloqueadores / dependencias**: Polo decidió NO crecer la célula Comercial hasta tener bases firmes; pipeline de hire G1 contable iniciado mayo (entrevistas con Viri, no con Polo); proceso de onboarding "Kawiiler en formación".
- **Modelo financiero**: ~60 clientes activos, $330,288 MXN/mes base. Planes basic $2,400 / standard $4,500 / integral $7,500. Polo modela mayo–dic 2026 con plan comercial de 2–3 basics/mes + 1 upsell desde agosto.
- **Última mención**: 2026-05-22
- **Refs**: `fb3a2061` Manual Organizacional v3.0; `fde10d57` Identidad visual Kawiil OS; `f2d1d083` Convocatoria administrativo Kawiil; `18bb5060` Análisis financiero CFO.

## 3. Kawiil OS / Kawiil Central / kawiil-agents *
- **Qué es**: ERP/plataforma interna del hub Kawiil. Stack: Lovable (React+TS+Vite+Tailwind+shadcn), Supabase project `qppfampapbxdgednkofc` (Pro plan, 85+ tablas, RLS por organization_id), TanStack React Query, React Router v6. Capa IA: edge function `ai-chat/index.ts` (~3,200 líneas, tool-calling), 3 agentes nativos (Archivista, Integrador, Nutritor) + 6 agentes VM (Maya, Sofia, Diego, Alex, Carlos, Luna) en Python sobre AWS EC2. Pipeline Haiku 4.5 drafts → Sonnet 4 reviews → tabla `knowledge_insights`.
- **Módulos vivos**: Clientes, Proyectos, Tareas (con semáforo prioridad), Documentos, Admin, Email, Slack, Microsoft 365, obligaciones fiscales, contabilidad, PLD compliance. Integraciones: Dropbox, Slack, Savio, Firecrawl.
- **Estado**: Fase 1 cerrada (bugs corregidos, endpoints validados). Fase 2A/2B en curso (insight pipeline dual-review). Polo decidió "probar Kawiil IA como está con trabajo real antes de seguir construyendo".
- **Estimación consumo LLM**: ~$1,500–2,000 USD / 90 días (~100M tokens combinados).
- **Refs**: `8a80bd66` Agentes Proyecto Inicial; `2c10cfa5` Arquitectura Yoltik dashboard basada en Kawiil Central; `76806268` Paperclip orquestación; `3140102b` plantillas tareas cumplimiento.

## 4. Yoltik (yoltik.mx) *
- **Qué es**: hub mexicano de innovación tecnológica con IA. RegTech. Productos SaaS propios: **MATI** (operaciones con IA, raíz náhuatl) e **Ikán** (cumplimiento LFPIORPI, raíz maya). Servicios: BackOffice Tecnológico y AI Development & Integration. Tagline: "Corazón vivo de tu operación". Polo es fundador y Presidente del Consejo. Equipo interno se llama "El Tequio".
- **Identidad visual**: paleta oficial Navy Profundo `#0C2340`, Jade Turquesa `#00917C`, Ámbar Cálido `#F0A500`, Electric Mint `#1DDBA8`, Verde Hielo `#F0F5F4`, Gris Técnico `#6B8C87`. Tipografía: Sora. Logo Yoltik = "Órbita Nodal"; logo Ikán = "Hexágono Vivo" (hexágono jade con greca azteca).
- **Estado**: rebranding completado abr 26. Brandbook v1.0 y Manual de Identidad Organizacional v1.0 (basados estructuralmente en el de Kawiil). Sprint en curso: app Lovable con KYC/KYB en primera fase. Dashboard v2.0 con CRM/Pipeline + Inbox unificado (email, WhatsApp Business, LinkedIn, Meta Ads, formularios web) + 5 agentes IA (Lead Scout, Qualifier, Nurture, Proposal Drafter, Pipeline Monitor). RRSS planificadas: IG, FB, LinkedIn.
- **Modelo comercial Ikán (Actividades Vulnerables LFPIORPI)**: Inicia $3,990 / Crece $9,990 / Multi $24,990 MXN/mes; trial 20 verificaciones (CURP/RFC/AML, no biometría). Demo arranca con sector XVI cripto (FIATCOIN como seed).
- **Vision larga**: hub cooperativo tecnológico; eventualmente expansión a hardware propio mexicano.
- **Última mención**: 2026-05-22
- **Refs**: `89c4d616` Plan de negocio Yoltik RegTech; `afae6c4d` Rediseño logo Yoltik; `9b63ab5c` Actividades vulnerables soluciones; `8c9e7f1f` Manual identidad Yoltik; `2c10cfa5` Arquitectura Yoltik dashboard; `e501f6ac` Marketing automation con agentes.

## 5. Ikán (producto Yoltik) *
- **Qué es**: producto SaaS de Yoltik para cumplimiento PLD/FT bajo LFPIORPI (sector SAT — actividades vulnerables). "Ikán" = escudo en náhuatl. Mascota separada en exploración.
- **Estado**: arquitectura "Config over Code" — cada fracción del Art. 17 LFPIORPI se activa como contexto de cumplimiento independiente (campos, umbrales, alertas, formatos de aviso). Demo arranca con Fracción XVI cripto (campos Anexo 16, umbrales 645/3210 UMA). Operado por Kawiil para clientes.
- **Refs**: `5b1be6c9` Prompts logos Ikán/Yoltik; `dec2bff7` Significado de Ikán.

## 6. Tonatiuh (security)
- **Qué es**: tercera marca de Polo, empresa de seguridad privada bajo Ley Federal de Seguridad Privada (LFSP). Operará Modalidades I (Vigilancia), III (Investigación) y IV (Sistemas Tecnológicos). Cinco líneas de servicio: Inteligencia Corporativa, Seguridad Física, Sistemas/Tecnología, Ciberseguridad, Compliance de Seguridad.
- **Estado**: en diseño. Logo definido (Sol Azteca con 8 rayos). Inicio CDMX, expansión EdoMex después. Polo será director fundador buscando socio operativo con perfil certificado.
- **Vinculación**: alimentado por el Doctorado en Inteligencia, Prevención y Seguridad de Polo (INAP, inicia 22-may-2026).
- **Última mención**: 2026-05-20
- **Refs**: `d1488b26` Pool de servicios seguridad integral.

## 7. Kuali A.C. (@kuali_justicia)
- **Qué es**: asociación civil de alfabetización mediática crítica y pensamiento colectivo dirigida a universitarios y prepa. Lema: *"Entender para transformar. Pensar en colectivo no es renunciar al individuo, es potenciarlo."* Polo es fundador/Presidente del Consejo.
- **Estado**: Instagram activo (@kuali_justicia), pivote de enfoque legal/pro bono a educación cívica. Paleta: jade `#2D6A4F`, ámbar `#F4A261`, terracota `#C1440E`. Logo en exploración (glifo mesoamericano).
- **Refs**: `823e4167` Sociología de influencers; `c5c8114a` Mensaje presentación asociación social.

## 8. TEQUIO (en desarrollo, no público)
- **Qué es**: ecosistema tokenizado de inversión colectiva (tokenización de procesos operativos/financieros de empresas vetadas + agentes IA monitoreo + acceso vía cripto). Foco inicial: real estate y negocios locales CDMX/EdoMex/Yucatán. 3 fundadores: Polo (cumplimiento), Viri (admin financiera/contable), Jesús (estructura legal corporativa).
- **Principio**: derechos laborales como condición no negociable de entrada.
- **Estado**: en desarrollo; no público. Removido de bios públicas de Polo.
- **Refs**: `64b1992a` Plataforma de inversión en proyectos mexicanos.

## 9. Vizum Technologies S.A.P.I. de C.V. *
- **Qué es**: transmisor de dinero (CASFIM No. 22443, Art. 81-A Bis LGOAAC), regulado por CNBV. Polo es **Oficial de Cumplimiento PLD/FT**. Director General / Rep Legal: Gonzalo Rivera Curbelo. Consejo: Gabino Fraga Jesterhoudt, Veridiana/Viridiana García Turcott (Comisaria). App: app.vizum.mx.
- **Equipo Vizum identificado**: Patricio Tena Zozaya (CTO), Jorge García Martínez (Director Seguridad), Jesús Perea Villegas (CTO SCP), Luis Adrián Villarreal Castillo (Director Comercial), Gabriel Alamilla López (Director Crecimiento Ventas), Christian Fernando Bonner Barbosa (COO), José Luis Camacho Torres / Adalberto Valles Bonilla (Devs SR), Diego Mendoza Salas (Dev JR), Fernando (atención a clientes/KYC).
- **Estado regulatorio**:
  - Auditoría externa 2025 DNA Compliance (auditor Aurelio Facio Salazar, cert FASA6M2-2015-4575-NCOC): 15 hallazgos, 2 críticos (Lista PB no cargada; OR Q2-2025 no presentado).
  - Multa CNBV via Oficio 211/40730-DGDS/2025 (notif 24-ene-2025) por OR Q2-2024 en ceros no presentado en SITI: actualmente en **Juicio de Amparo Directo D.A. 37/2026** (Tribunal Colegiado Materia Administrativa Primer Circuito, presentado 19-ene-2026). El amparo indirecto 1902/2025 fue redirigido como D.A. 17/2026.
  - **IDNP (Identificación No Presencial)**: solicitud denegada por CNBV via Oficio 311-751972/2026 (6-mar-2026); estrategia es resometer (no recurso de revisión), deadline ~3-abr-2026 (20 días hábiles). Posteriormente: Oficio 221/DGPORPIA-14556950/2026 (14-abr-2026, 7 observaciones + 2 recomendaciones de Cristóbal Ramírez Viveros DGPORPIA-A); acuse 16-abr, respuesta 14-may-2026. Reforma Anexo 2 trabajada con dos capas: Capa 1 Anexo 2, Capa 2 expediente completo. Validación e.firma a través de PSC (WeeTrust evaluado; SohoSign descartado); Didit para biometría.
  - DDR BBVA México presentado.
  - Visita inspección CNBV presencial febrero 2026 (2 semanas).
- **Última mención**: 2026-05-20 (grupo Sylon).
- **Refs**: `a58540c8` Auditoría DNA; `48502722` CNBV deniega IDNP; `76aef77c` Oficio 14556950; `3326ce0f` Litigio multa CNBV; `19274722` Guía Sylon-Visum.

## 10. Sylon Asesores (Rivium) — *Sylon Capital* — Grupo Sylon
- **Qué es**: Grupo financiero con dos entidades. **Sylon Capital S.A.P.I. de C.V.** = IFPE recientemente autorizada CNBV (noviembre 2025), inicio operaciones esperado 2026; estados financieros 2025 con opinión con salvedades (NIF C-8). **Sylon Asesores S.A. de C.V., Asesor en Inversiones Independiente** (folio CNBV 30176) — marca comercial **Rivium**. Polo lleva el cumplimiento. Sylon Capital opera como Participante Indirecto del SPEI via STP (CLABE 646).
- **Equipo en Sylon Asesores/Capital**: Viridiana García Turcott (entrante OC y Comisaria Sylon Asesores), Jesús García Turcott (CTO entrante / Controlador Interno), Luis Roberto Ponce Nieto (Consejero Independiente), C.P. Viridiana (contadora del cliente, externa).
- **Estado**: reportes mensuales STP (Formularios A/B/C); abril 2026 con 6 cuentas activas. Despacho externo contratado para Sylon Capital con ventana evaluación mayo-junio. Aviso GIIN del SAT (239/CFF) en proceso.
- **Última mención**: 2026-05-20
- **Refs**: `e103c65a` Auditoría Sylon CNBV; `19274722` Guía Sylon; `c038a5a8` Reporte abril Sylon Capital STP.

## 11. Fiatcoin Network / Fiat Coin Pay / Fiat Coin Rample (FIATCOIN RAMPLE) *
- **Qué es**: dos entidades hermanas. **Rample** = exchange de criptoactivos (actividad vulnerable LFPIORPI SAT). **Pay** = transmisor de dinero (en desarrollo). Polo será **Oficial de Cumplimiento de Rample**. Contraparte Fiatcoin: Ismael (operaciones/estrategia) y Ricardo (technical lead, plataforma/DB/onboarding).
- **Estado**:
  - Acuerdo con Polo formalizado. Kawiil presta servicios externos de cumplimiento.
  - Manual PLD R2 Unificado (46 pp) incorporando reforma DOF 16/07/2025 + decisión corporativa de rechazo automático de PEPs.
  - Catálogo RIPS vs UIF SPPLD: nacionalidad y actividad económica separadas en RIPS, concatenadas al generar aviso (",", "||").
  - Bloqueador clave: Fiatcoin no ha entregado documentación de licencias canadienses → bloquea modelos multijurisdicción y validación del despacho legal canadiense.
  - Junta semanal jueves; manuales abril, ruling mayo. Demanda civil 459/2025 (Fiatcoin Network vs STP) en desistimiento (sin causa, STP defendió con Circular 14/2017 Banxico).
- **Última mención**: 2026-05-08
- **Refs**: `3c2c57c7` Reunión semanal 10:00 Fiat Coin Pay/Rampool; `df0cb0ec` Coordinación junta Fiatcoin; `70d9aaab` Desistimiento Fiatcoin vs STP; `3b6be489` Configuración sistema cripto Fiatcoin.

## 12. Plankton Wallet S.A.P.I. de C.V. (caso LVGS) *
- **Qué es**: empresa fintech wallet/cripto. Polo es **Representante Legal** (con poderes limitados de gerencia). Constituida por Póliza 23,298, Corredor Público 69 CDMX, 26-sep-2023. Beneficial owner real: Luis Vicente Guzmán Sánchez (LVGS).
- **Estado**: en crisis. Caso criminal activo (CI-FIEDF/T01-1 S/D/02199/10-2025 D-1 ante FGJCDMX, Fiscal Mtro. Ulises Soteno Torres). Polo es denunciante. Abogados autorizados: Lic. Fernando Javier Moreno García (1717482) y Lic. Silvia Tenorio Contreras (10882072). Plan Maestro Denuncia LVGS llegó a versión 6.0. Forensia: 201–202 operaciones manuales atribuidas a LVGS por $43,897,166.62 MXN (ago/2024 – jul/2025). Karina Lizeth Campos Herrera recibió $11.7M (CLABE 072957012788062536). $850K transferidos a "Bassoco Vega Salas Morales" requieren formalizar Reconocimiento de Deuda antes de ratificación.
- **Refs**: `a651255e` Plan Maestro V1; `a6c83320` Plan Maestro V4; `e229ba74` Diagrama LVGS; `64dbf9b8` Análisis Kailash-Plankton-Infinitech; `a848bea8` Estrategia poderes y OPM.

## 13. Infinitech Labs S.A.P.I. de C.V. (caso LVGS)
- **Qué es**: empresa del ecosistema LVGS. **Viridiana García Turcott 99% (Presidenta CA) y Jesús García Turcott 1% (Secretario)**, ambos son socios del despacho Bassoco/Kawiil — fueron usados como administradores nominales bajo contrato con LVGS. LVGS dejó de aportar recursos; existe deuda INFONAVIT bajo convenio.
- **Estado**: demanda civil ordinaria por cumplimiento forzoso en preparación. Embargo precautorio sobre propiedad en Yucatán solicitado. Oficios a CNBV, UIF, SAT/SHCP, Archivo Notarías y RPP.
- **Refs**: `86632639` Demanda civil incumplimiento; `0047704e` Daños y perjuicios Viri/Jesús; `e8355173` Contrato prestación servicios Infinitech.

## 14. IB Blockchain S.A.P.I. de C.V. (caso LVGS)
- **Qué es**: empresa del ecosistema LVGS (FME N-2023082060 Guasave). Viridiana García Turcott aparece como Comisaria. Rafael de Jesús Ortega Zulueta es Presidente CA.
- **Estado**: juicio civil de nulidad de asamblea en proceso (Exp. 549/2025, Juzgado 15° Civil TSJCDMX, admitido 3-dic-2025). Cartas rogatorias entregadas a SRE (folios 391 y 392).
- **Refs**: `34ba5cc5` Nulidad asamblea Plan Maestro; `ea9372e5` Promoción entrega cartas rogatorias.

## 15. Otras entidades del ecosistema LVGS
- **Trucapitals SAPI** (Escritura 209,965, Notario 2 Tijuana, 14-sep-2021): LVGS + Marco Aristeo García Madrigal 50/50.
- **Ciudades Ancestrales Sustentables en Quintana Roo SAPI**: Luis Alberto De La Fuente Sánchez Presidente. Propiedad relevante.
- **Biso Innovation SAPI** (FME N-2021027627, Tijuana): Kenia C. Campos co-fundadora 50%.
- **Carpeta paralela en Baja California**: NUC 0204-2024-33701 (Marco Aristeo García Madrigal).

## 16. Kailash Financiera S.A. de C.V. *
- **Qué es**: cliente Plankton/Infinitech. Director General: Héctor Villaseñor; Oficial de Cumplimiento: Mónica Villaseñor; Rep Legal: José Francisco Gaona Morales (fcogaona@capitalsatelite.com). Contrato firmado por Polo bajo premisas falsas (le dijeron que era para nómina/fiscal) → fraude personal contra Polo.
- **Estado**: contrato JC-Kailash en negociación (Juan Carlos como contratado, Polo como responsable solidario, $250,000 MXN). Sprint actual del proyecto Kailash (separado): commits hasta b8fe174/20c7658 según memoria persistente del proyecto. Esperando CORS de Marco (dev externo) para smoke E2E.
- **Refs**: `64dbf9b8` Análisis Kailash; `b4a7cde6` Contrato JC; `e37acac1` Alineación contrato JC-Kailash.

## 17. Grupo Dazon *
Ecosistema fabricación + comercialización de motocarros chinos CKD. Polo es **Director de Control Interno — Backoffice y Cumplimiento** (vía Kawiil). Empresas:
- **Los Perez y Amigos, S.A. de C.V.** (RFC: PAR180521Q99) — fábrica/importación/ensamble.
- **Juoshui Agua Viva Mex, S.A. de C.V.** (RFC: JAV220623HH4) — refacciones comercial. Rep legal: Chu Haiyan / Haiyan Chu.
- **Colewiza / Desheng Victoria** — tercera entidad bajo análisis.
- **Dazon Mex S.A. de C.V.** — marca paraguas. RFC: VAME94081227A (querella Erick Dolores). Rep legal: Edgar Harvey Valencia Matías.

**Actualización mayo 2026**: Dazon absorbido por **Baisiji** (fábrica China). Director Sr. Fung continúa con Kawiil para legal/fiscal/regulatorio en México. ERP propuesto via Compaq SaaS.

**Equipo Dazon**: Ana María Quintero Mera (Dirección General), Marco Beltrán (Coord Comercial, marco.beltran@dazon.mx), Martín (Crédito y Cobranza), Jair Sánchez (Admin Fábrica LPA), Hilario Rico (Almacén/Ensamble), Erika (Fábrica/Producción), Leticia Méndez Guadarrama (RH), Iván (interino ensamble). Lupita = intermediaria flagged en pagos en efectivo (control risk).

**Documentación generada**: Reglamento Interior de Trabajo (DAZ-RH-POL-RIT-01 v1.2), Manual Almacén (DAZ-ALM-MAN-01), Manual Procedimientos RH, organigrama, expedientes comerciales (DAZ-COM-PROC-01), control de producción CONTROL_PRODUCCION_DAZON V4–V6, política venta motocarros (DAZ-LPA-POL-MOT-01), contratos de crédito comercial (ej. VALMO MOTOS $10M).

**Casos abiertos**: querella penal contra ex-empleado Erick Dolores Cante (CPCDMX Arts. 230, 234, 227, 229 — $363K–$393K daño). Querella contra Kevin Arteaga Vázquez + Alexis Rafael Rebollar Morales (robo carburador captado en CCTV). Renuncias procesadas: Kaleb Peñaloza Terrazas, Sabina Arisbeth Ríos Flores, Eduardo Javier Olvera Sánchez, Aridan Uriel Martínez Martínez (rescisión).

- **Refs**: `7838f888` Org motocarros; `f639c249` Control producción V4; `0a8f20cf` App web ensamble; `8a6ea5a5` Baisiji absorbe Dazon.

## 18. Digital Jointly S.A. de C.V. (IFPE)
- **Qué es**: IFPE regulada por CNBV. Administrador Único: Ricardo Gámez Ordoñez. Trabajo gestionado con Gonzalo Rivera.
- **Estado**: paquete de respuesta a CNBV Oficio 311-751822/2026 (contratación con SUMA) y Oficio 311-751852/2026 (contratación con Pomelo Tech). Anexos completos producidos. AWS us-west-2 Oregon como data center primario.
- **Refs**: `388580e2`, `9c3e0463` SUMA; `c8ebe517` Pomelo Tech.

## 19. Otros clientes con cumplimiento PLD/regulatorio activo
- **EFM Sportbook S.A. de C.V.** — mandato de pago de impuestos via Banregio (prospect 2026-04-15, RFC ESP161024G68).
- **CERNA** — desarrollador inmobiliario con varios fideicomisos. Propuesta $77K/mes × 3 meses ($231K).
- **ABEJORRO78 S.A.P.I. DE C.V.** (Cristhian Albert Velazco Solano) — Carta Invitación SAT AV015, Fracción XVI LFPIORPI. Contrato fijo $20,000 MXN.
- **PEYO SOLUTIONS LLC** (Delaware) — KYB Vizum (CEO Miguel Luizaga 58.55%).
- **GRUPO INTERCOMEX LOGÍSTICA / CONFIRATRAN** — Jie Lu como UBO foráneo, alto riesgo PLD.
- **HOLLAND & KNIGHT MEXICO SC**, **Química Apollo SA de CV**, **RAILS TECHNOLOGY INC** (BVI) — KYB Vizum.
- **Coral Premium Service** — referenciado, sin antecedente en Project actual.

## 20. Empresas-cliente backoffice Kawiil
- **Empathy Design / Empathy México / Empathy USA** — estados financieros 2025; contrato de mutuo MX-USA; precios de transferencia (4 socios comunes); proyecto "CUATE" con prórrogas mar/abr/may.
- **MPAM Group SA DE CV** (MGR220202255) — servicios administrativos para Fundación FOSSVI. Cierre fiscal anual 2025.
- **GLOMAD REM S.A.P.I. de C.V.** (GRE240625GE7) — declaración anual 2025.
- **Global Electrik Automatización Sostenible SA de CV** (GEA240628JF2) — estados financieros 2025.
- **Mantenimiento Integral GUSA S.A.S. de C.V.** (MIG190130PF7, RESICO PM) — cierre 2025.
- **LAEVE MX** / **LAEVE SA de CV** (LAE220927KG9) — citatorio Tesorería CDMX 14-abr-2026 (probable ISN), ER fiscal/contable.
- **Bree Health S.A.P.I. de C.V.** — ESF mensual jul-dic 2025 + anual.
- **Malamute Codes** — ER + ESF dic 2025 bajo NIF.
- **JUOSHUI AGUA VIVA MEX, S.A. DE C.V.** — ver Grupo Dazon.
- **Sandra Ríos Valencia** — cliente JUOSHUI, adeudo $1.288M reconocido.
- **MOTONET ATLIXCO** — víctima cliente caso Erick Dolores.
- **U-CO BY UNITED URBANIST S.A. DE C.V.** — gestión propiedades, finiquito Reyes Lopez.
- **CursaLab Group México S.A.P.I. de C.V.** — Polo firma; prórrogas contrato.
- **GAF LION, S.A. de C.V.** (BANL9510206W9) — operador Lion Rolling Circus en México y LATAM; representante Polo. Propuesta a María y Juana Cannabis.
- **JOINTLY** — IFPE (Ricardo Gámez), ver #18.
- **STP** — caso desistimiento Fiatcoin.
- **Bankaool** — contrato bajo negociación.
- **Plankton/Infinitech/Kailash/Bassoco** — ver casos LVGS y Kailash.

## 21. Halunamatara / Hakunamatata
- **Qué es**: marca integral de Polo para consumo legal y desarrollo agrícola cannabis ("Weed Love Desde México"). Ecosistema: Asociación Civil + SAPI + alianzas con María y Juana Cannabis (autorización COFEPRIS, traslado 80kg) y Lion Rolling Circus (parafernalia). App con 7 módulos.
- **Refs**: `90764731` Halunamatara marca integral.

## 22. Mija (cosméticos)
- **Qué es**: marca de cosméticos mexicana inspirada en comida callejera y cultura de mercado. Productos: Café de Olla (lip tint), Tamal de Dulce (blush), Pan de Oro (highlighter), Negro Mole (eyeliner), Guayabita (lip gloss). Modelo maquila (no formulación propia).
- **Estado**: planteamiento conceptual 2026-04-08.

## 23. Tour platform CDMX (sin nombre)
- **Qué es**: plataforma de tours locales CDMX; permitir guías con credencial vigente vender experiencias.
- **Estado**: idea inicial 2026-05-05. Foco inicial: turista/local; dashboard manual.

## 24. Caso Nahum (constitución empresa)
- Cotización constitución + representación. Pendiente respuesta directa Jesús García Turcott.
- **Refs**: `1d11c662`.

## 25. Otros clientes / contratos puntuales recurrentes
- **TERION CONSTRUCTORA**, **Pomelo Tech S.A. de C.V.**, **Bold Moves S.R.L.** (Argentina, CUIT 30719016282), **Ecocar México / EUA**, **Bstrategic S.C.**, **Cursalab Group México**, **Infinitech Labs**, **Plankton Wallet** — contratos diversos (prórrogas, regalías, mutuos, NDAs, prestación servicios).
- **Carlos López** — arrendamiento Blas Pascal 184, $12,500/mes.
- **Joshui** — arrendamiento bodegas Pelícano 216.
- **Anael** — caso pago anticipado (no procede según cláusulas).

