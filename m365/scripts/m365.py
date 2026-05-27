#!/usr/bin/env python3
"""
m365.py — cliente unificado para Microsoft Graph (Outlook + Calendar).

Subcomandos:
  token <tenant>                              imprime un access_token válido
  leer-correos <tenant> [unread|all] [limit]  lista correos del inbox
  calendario <tenant> [hoy|manana|semana|mes] lista eventos del calendario
  mandar-correo <tenant> <to> <subject> <body> [cc] [text|html]

Tenant: 'kawiil' o 'yoltik' (cuando se configure).
"""

import os
import sys
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
CREDS_DIR = HOME / ".openclaw" / "credentials"


def load_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def http_post_form(url: str, body: dict) -> dict:
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')}")


def http_get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("Prefer", 'outlook.timezone="Central Standard Time (Mexico)"')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')}")


def http_post_json(url: str, token: str, body: dict) -> int:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')}")


def get_access_token(tenant: str) -> str:
    creds_file = CREDS_DIR / f"m365-{tenant}.env"
    tokens_file = CREDS_DIR / f"m365-{tenant}-tokens.json"

    if not tokens_file.exists():
        raise RuntimeError(f"no encontré {tokens_file}. Corre oauth-{tenant}.py primero.")

    env = load_env(creds_file)
    prefix = f"M365_{tenant.upper()}"
    client_id = env.get(f"{prefix}_CLIENT_ID")
    tenant_id = env.get(f"{prefix}_TENANT_ID")
    if not client_id or not tenant_id:
        raise RuntimeError(f"faltan credentials en {creds_file}")

    tokens = json.loads(tokens_file.read_text())
    issued = tokens.get("__issued_at", 0)
    expires_in = tokens.get("expires_in", 0)
    now = int(time.time())

    # Si quedan más de 5 min de vida, devolver el actual
    if (issued + expires_in - now) > 300 and tokens.get("access_token"):
        return tokens["access_token"]

    # Refresh
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("no hay refresh_token, hay que re-correr oauth")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    new = http_post_form(token_url, {
        "client_id": client_id,
        "scope": "Mail.ReadWrite Mail.Send Calendars.ReadWrite User.Read offline_access",
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    if "access_token" not in new:
        raise RuntimeError(f"refresh falló: {new}")
    new["__issued_at"] = int(time.time())
    tokens_file.write_text(json.dumps(new))
    return new["access_token"]


def cmd_token(args):
    tenant = args[0] if args else "kawiil"
    print(get_access_token(tenant))


def cmd_leer_correos(args):
    tenant = args[0] if len(args) > 0 else "kawiil"
    filter_ = args[1] if len(args) > 1 else "unread"
    limit = int(args[2]) if len(args) > 2 else 10

    token = get_access_token(tenant)

    params = {
        "$top": str(limit),
        "$orderby": "receivedDateTime desc",
        "$select": "subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments",
    }
    if filter_ == "unread":
        params["$filter"] = "isRead eq false"

    url = "https://graph.microsoft.com/v1.0/me/messages?" + urllib.parse.urlencode(params)
    data = http_get_json(url, token)
    msgs = data.get("value", [])

    if not msgs:
        print("(sin correos en este filtro)" if filter_ == "unread" else "(sin correos)")
        return

    for m in msgs:
        fr = m.get("from", {}).get("emailAddress", {})
        fr_str = f"{fr.get('name','?')} <{fr.get('address','?')}>"
        date = m.get("receivedDateTime", "?")
        subj = m.get("subject", "(sin asunto)")
        preview = (m.get("bodyPreview", "") or "")[:200].replace("\n", " ")
        attach = "📎" if m.get("hasAttachments") else "  "
        unread = "•" if not m.get("isRead") else " "
        print(f"{unread} {attach} [{date[:16]}] {fr_str}")
        print(f"      {subj}")
        print(f"      {preview}")
        print()


def cmd_calendario(args):
    tenant = args[0] if len(args) > 0 else "kawiil"
    range_ = args[1] if len(args) > 1 else "hoy"

    token = get_access_token(tenant)

    TZ = timezone(timedelta(hours=-6))  # CDMX UTC-6
    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if range_ == "hoy":
        s, e = today_start, today_start + timedelta(days=1)
    elif range_ == "manana":
        s, e = today_start + timedelta(days=1), today_start + timedelta(days=2)
    elif range_ == "semana":
        s, e = today_start, today_start + timedelta(days=7)
    elif range_ == "mes":
        s, e = today_start, today_start + timedelta(days=30)
    else:
        s, e = today_start, today_start + timedelta(days=1)

    start_utc = s.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_utc = e.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    params = {
        "startDateTime": start_utc,
        "endDateTime": end_utc,
        "$orderby": "start/dateTime",
        "$top": "50",
        "$select": "subject,start,end,location,bodyPreview,organizer,attendees,isOnlineMeeting,onlineMeeting",
    }
    url = "https://graph.microsoft.com/v1.0/me/calendarview?" + urllib.parse.urlencode(params)
    data = http_get_json(url, token)
    events = data.get("value", [])

    if not events:
        print(f"(sin eventos en {range_})")
        return

    for ev in events:
        start = ev.get("start", {}).get("dateTime", "?")[:16].replace("T", " ")
        end = ev.get("end", {}).get("dateTime", "?")[11:16]
        subj = ev.get("subject", "(sin título)")
        loc = (ev.get("location", {}) or {}).get("displayName", "") or ""
        org = (ev.get("organizer", {}) or {}).get("emailAddress", {}).get("name", "?")
        online = "🎥 " if ev.get("isOnlineMeeting") else ""
        print(f"[{start} → {end}] {online}{subj}")
        if loc:
            print(f"   📍 {loc}")
        print(f"   👤 {org}")
        attendees = ev.get("attendees", [])
        if attendees:
            names = [a.get("emailAddress", {}).get("name", "?") for a in attendees[:5]]
            tail = "..." if len(attendees) > 5 else ""
            print(f"   👥 {', '.join(names)}{tail}")
        print()


def cmd_mandar_correo(args):
    if len(args) < 4:
        print("Uso: m365.py mandar-correo <tenant> <to> <subject> <body> [cc] [text|html]", file=sys.stderr)
        sys.exit(1)
    tenant, to, subject, body = args[:4]
    cc = args[4] if len(args) > 4 else ""
    content_type = args[5] if len(args) > 5 else "text"

    token = get_access_token(tenant)

    msg = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML" if content_type == "html" else "Text",
                "content": body,
            },
            "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()],
        },
        "saveToSentItems": True,
    }
    if cc:
        msg["message"]["ccRecipients"] = [{"emailAddress": {"address": c.strip()}} for c in cc.split(",") if c.strip()]

    status = http_post_json("https://graph.microsoft.com/v1.0/me/sendMail", token, msg)
    if status in (200, 202):
        print(f"OK: correo enviado a {to}")
        print(f"    Subject: {subject}")
    else:
        print(f"ERROR: HTTP {status}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]

    try:
        if cmd == "token":
            cmd_token(args)
        elif cmd == "leer-correos":
            cmd_leer_correos(args)
        elif cmd == "calendario":
            cmd_calendario(args)
        elif cmd == "mandar-correo":
            cmd_mandar_correo(args)
        else:
            print(f"Comando desconocido: {cmd}", file=sys.stderr)
            print(__doc__, file=sys.stderr)
            sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
