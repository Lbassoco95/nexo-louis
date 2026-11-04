#!/usr/bin/env python3
"""slack_check.py — Verifica que los workspaces de Slack (Kawiil, Yoltik…) estén bien conectados.

Solo lectura. NUNCA imprime tokens. Por cada workspace y tipo de token (usuario xoxp- / bot xoxb-):
  • auth.test            → ¿el token es válido? ¿a qué workspace y usuario pertenece?
  • permisos (scopes)    → ¿tiene los que necesita el resumen de Slack?
  • conversaciones       → cuántos canales públicos/privados, grupos y DMs ve.

Uso (en el servidor):  python3 /opt/openclaw/scripts/slack_check.py
Salida: una línea por chequeo (✅ / ⚠️ / ❌) y un veredicto final por workspace.
"""
import sys

import slack_digest as sd

NECESITA_USUARIO = ["channels:read", "groups:read", "im:read", "mpim:read",
                    "channels:history", "groups:history", "im:history", "mpim:history", "users:read"]
NECESITA_BOT = ["chat:write"]


def _scopes(resp) -> set:
    try:
        h = resp.headers or {}
        raw = h.get("x-oauth-scopes") or h.get("X-OAuth-Scopes") or ""
    except Exception:
        raw = ""
    return {x.strip() for x in raw.split(",") if x.strip()}


def _revisa(ws: str, tipo: str, token: str, necesita: list) -> bool:
    """Imprime el resultado de un token. True si quedó sano."""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    etq = f"{ws.title()} · {'usuario' if tipo == 'usuario' else 'bot'}"
    esperado = "xoxp-" if tipo == "usuario" else "xoxb-"
    if not token:
        print(f"  ⚪ {etq}: no configurado")
        return tipo != "usuario"            # el de usuario es el que importa para el resumen
    if not token.startswith(esperado):
        print(f"  ❌ {etq}: el token no empieza con {esperado} (¿se pegó otro tipo o quedó cortado?)")
        return False
    c = WebClient(token=token)
    try:
        r = c.auth_test()
    except SlackApiError as e:
        print(f"  ❌ {etq}: Slack lo rechazó ({e.response.get('error', 'error')})")
        return False
    except Exception as e:
        print(f"  ❌ {etq}: no pude conectar con Slack ({str(e)[:80]})")
        return False
    print(f"  ✅ {etq}: válido — workspace «{r.get('team')}», como «{r.get('user')}»")
    ok = True
    scopes = _scopes(r)
    if scopes:
        faltan = [s for s in necesita if s not in scopes]
        if faltan:
            print(f"     ⚠️ faltan permisos: {', '.join(faltan)}")
            ok = False
        else:
            print(f"     ✅ permisos completos ({len(scopes)} en total)")
    else:
        print("     ⚠️ Slack no devolvió la lista de permisos; no pude verificarlos")
    if tipo == "usuario":
        try:
            cont = {"canales públicos": 0, "canales privados": 0, "grupos": 0, "DMs": 0}
            cursor = None
            for _ in range(15):
                kw = {"types": "public_channel,private_channel,mpim,im", "limit": 200, "exclude_archived": True}
                if cursor:
                    kw["cursor"] = cursor
                resp = c.conversations_list(**kw)
                for ch in resp.get("channels", []):
                    if ch.get("is_im"):
                        cont["DMs"] += 1
                    elif ch.get("is_mpim"):
                        cont["grupos"] += 1
                    elif ch.get("is_private"):
                        cont["canales privados"] += 1
                    elif ch.get("is_member"):
                        cont["canales públicos"] += 1
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            print("     📚 ve: " + ", ".join(f"{n} {k}" for k, n in cont.items()))
            if sum(cont.values()) == 0:
                print("     ⚠️ no ve ninguna conversación (¿faltan permisos *:read?)")
                ok = False
        except Exception as e:
            print(f"     ⚠️ no pude listar conversaciones ({str(e)[:80]})")
            ok = False
    return ok


def main() -> int:
    try:
        import slack_sdk  # noqa: F401
    except ImportError:
        print("❌ slack_sdk no está instalado en el servidor (pip install slack_sdk)")
        return 2
    creds = sd.core.load_env_file(sd.SLACK_ENV)
    spaces = [w for w in sd._workspaces(creds) if w[1] or w[2]]
    if not spaces:
        print(f"❌ No hay ningún token de Slack en {sd.SLACK_ENV}")
        return 1
    mal = 0
    for ws, user, bot, owner in spaces:
        print(f"\n== {ws.title()} ==")
        ok_u = _revisa(ws, "usuario", user, NECESITA_USUARIO)
        ok_b = _revisa(ws, "bot", bot, NECESITA_BOT)
        if not user:
            print("  ⚠️ sin token de usuario: el resumen verá solo canales donde invites al bot (cobertura limitada)")
        veredicto = "LISTO" if (ok_u and ok_b and user) else "REVISAR"
        print(f"  → {ws.title()}: {veredicto}")
        mal += 0 if veredicto == "LISTO" else 1
    print(f"\n{'✅ Todo conectado' if not mal else '⚠️ Hay ' + str(mal) + ' workspace(s) por revisar'}")
    return 0 if not mal else 1


if __name__ == "__main__":
    sys.exit(main())
