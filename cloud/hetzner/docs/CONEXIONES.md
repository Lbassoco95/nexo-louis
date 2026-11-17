# Conexiones, credenciales y datos verificados de Donna

## Dónde viven las credenciales (y por qué se perdían)
`/opt/openclaw/openclaw.env` es lo que leen los servicios (gateway, scheduler, bridges). `install.sh` lo **regenera en
cada deploy**, pero varios scripts le agregan variables a mano: `setup-kawiil-central-creds.sh` (KAWIIL_CENTRAL_*),
`install-1password.sh` (OP_SERVICE_ACCOUNT_TOKEN), `install-bitwarden.sh` (BITWARDEN_ENV_FILE) e
`install-playwright.sh` (PLAYWRIGHT_BROWSERS_PATH). Antes cada deploy las borraba en silencio y las herramientas
fallaban (Kawiil Central sin credenciales, bóveda, navegador).

Ahora (`openclaw/envmerge.sh`, usado por `install.sh`): se guarda una copia fechada `openclaw.env.bak.<fecha>` (las 5
más recientes) y, tras regenerar, **se conserva toda variable que install.sh no genera**. Además Kawiil Central lee
también `credentials/kawiil-central.env` (0600), que no se regenera, como fuente de respaldo.

## Datos del negocio: nada inventado
- Regla en el prompt: cifras/estado de Kawiil Central solo de una tool `kawiil_central_*` llamada en el turno.
- Cuando una tool falla (❌/🔒/(error…)/✗), el resultado que ve el modelo lleva una nota explícita de que NO tiene
  esos datos (`_tool_failed`, `_tool_fail_note` en louis_core.py).
- Red de seguridad en `call_llm`: si la respuesta presenta un diagnóstico con ≥6 cifras atribuido a Kawiil Central
  («estado real», «total de tareas», «proyectos activos»…) y ninguna tool kawiil_central_* devolvió datos en ese turno,
  se antepone un aviso visible («Esta respuesta no consultó Kawiil Central» / «No pude consultar…»).
  Es una heurística: reduce el riesgo, no lo elimina. La defensa real es que la conexión funcione y se verifique.

## Verificar
`bash /opt/louis/tools/verificar-donna.sh` — servicios, versión del código, llaves, **Kawiil Central (credenciales,
psycopg2 y una consulta real)**, otras integraciones, conversación real e historial.
Pruebas: `tests/test_veracidad.py`, `tests/test_envmerge.py`.
