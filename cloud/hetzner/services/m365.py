#!/usr/bin/env python3
"""
m365.py v2 — cliente unificado para Microsoft Graph (Outlook + Calendar).
Control total: leer, marcar, mover, borrar, responder, reenviar, buscar.

Tenant: 'kawiil' o 'yoltik'.

Subcomandos:
  token <tenant>                                    imprime un access_token válido
  leer-correos <tenant> [unread|all] [limit]        lista correos (con IDs)
  buscar <tenant> "<query>" [limit]                 búsqueda full-text
  ver-correo <tenant> <message_id>                  cuerpo completo del correo
  marcar-leido <tenant> <message_id>                marca como leído
  marcar-no-leido <tenant> <message_id>             marca como no leído
  borrar <tenant> <message_id>                      borra (a Deleted Items)
  archivar <tenant> <message_id>                    mueve a carpeta Archive
  mover <tenant> <message_id> <folder>              mueve a carpeta (id o "Archive", "Inbox", etc)
  responder <tenant> <message_id> "<texto>"         responde al remitente
  responder-todos <tenant> <message_id> "<texto>"   responde a todos
  reenviar <tenant> <message_id> <to> "<comentario>"  reenvía a destinatarios
  listar-folders <tenant>                           lista carpetas con sus IDs

  calendario <tenant> [hoy|manana|semana|mes]       lista eventos (con estatus de respuesta)
  crear-evento <tenant> "<subject>" <inicio> <fin> [asistentes] [body]
                                                     formato inicio/fin: YYYY-MM-DDTHH:MM (hora local CDMX)
  responder-evento <tenant> <event_id> <accept|decline|tentative> ["<comentario>"] [send_response]
                                                     acepta/rechaza/tentativo en invitación
  eliminar-evento <tenant> <event_id> ["<comentario>"]
                                                     cancela (organizador→notifica asistentes) o borra el evento
  actualizar-evento <tenant> <event_id> campo=valor [campo=valor ...]
                                                     modifica subject/inicio/fin/asistentes/body/ubicacion
  pendientes-evento <tenant> [hoy|manana|semana|mes] lista solo eventos donde no he respondido (notResponded)

  mandar-correo <tenant> <to> "<subject>" "<body>" [cc] [text|html]
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
GRAPH = "https://graph.microsoft.com/v1.0"
TZ_CDMX = timezone(timedelta(hours=-6))


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


def http_request(method: str, url: str, token: str = None, body=None, content_type: str = "application/json", timeout: int = 30):
    """HTTP request unificado. Soporta GET/POST/PATCH/DELETE."""
    data = None
    if body is not None:
        if content_type == "application/x-www-form-urlencoded":
            data = urllib.parse.urlencode(body).encode()
        else:
            data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("Prefer", 'outlook.timezone="Central Standard Time (Mexico)"')
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
            if not raw:
                return {"_status": status}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors='replace') if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {err_body}")


def graph_get(token: str, path_or_url: str) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{GRAPH}{path_or_url}"
    return http_request("GET", url, token=token)


def graph_get_raw(token: str, path_or_url: str) -> bytes:
    """GET binario (para descargar contenidos: PDFs, imágenes, docs)."""
    url = path_or_url if path_or_url.startswith("http") else f"{GRAPH}{path_or_url}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def graph_post(token: str, path: str, body: dict) -> dict:
    return http_request("POST", f"{GRAPH}{path}", token=token, body=body)


def graph_patch(token: str, path: str, body: dict) -> dict:
    return http_request("PATCH", f"{GRAPH}{path}", token=token, body=body)


def graph_delete(token: str, path: str) -> dict:
    return http_request("DELETE", f"{GRAPH}{path}", token=token)


# ===== Token management =====
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

    if (issued + expires_in - now) > 300 and tokens.get("access_token"):
        return tokens["access_token"]

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("no hay refresh_token, hay que re-correr oauth")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    new = http_request("POST", token_url, body={
        "client_id": client_id,
        "scope": "Mail.ReadWrite Mail.Send Calendars.ReadWrite User.Read offline_access",
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, content_type="application/x-www-form-urlencoded")
    if "access_token" not in new:
        raise RuntimeError(f"refresh falló: {new}")
    new["__issued_at"] = int(time.time())
    tokens_file.write_text(json.dumps(new))
    return new["access_token"]


# ===== Helpers de carpetas =====
def _well_known_folder(name: str) -> str:
    """Retorna el wellKnownName de carpetas Outlook estándar."""
    m = {
        "inbox": "Inbox",
        "archive": "Archive",
        "archivar": "Archive",
        "borradores": "Drafts",
        "drafts": "Drafts",
        "enviados": "SentItems",
        "sent": "SentItems",
        "papelera": "DeletedItems",
        "deleted": "DeletedItems",
        "junk": "JunkEmail",
        "spam": "JunkEmail",
    }
    return m.get(name.lower(), name)


# ===== Comandos =====
def cmd_token(args):
    tenant = args[0] if args else "kawiil"
    print(get_access_token(tenant))


def cmd_leer_correos(args):
    tenant = args[0] if len(args) > 0 else "kawiil"
    filter_ = args[1] if len(args) > 1 else "unread"
    limit = int(args[2]) if len(args) > 2 else 10
    # arg[3] opcional: filtro por remitente (substring case-insensitive en email o nombre)
    sender_filter = args[3].lower() if len(args) > 3 else None

    token = get_access_token(tenant)
    params = {
        "$top": str(limit if not sender_filter else max(limit * 5, 50)),  # más amplio si filtramos local
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments,conversationId",
    }
    if filter_ == "unread":
        params["$filter"] = "isRead eq false"

    data = graph_get(token, f"/me/messages?{urllib.parse.urlencode(params)}")
    msgs = data.get("value", [])
    # Filtro local por remitente (substring en email o nombre)
    if sender_filter:
        filtered = []
        for m in msgs:
            fr = m.get("from", {}).get("emailAddress", {})
            name = (fr.get("name", "") or "").lower()
            addr = (fr.get("address", "") or "").lower()
            if sender_filter in name or sender_filter in addr:
                filtered.append(m)
        msgs = filtered[:limit]
    if not msgs:
        msg_extra = f" (filtro sender='{sender_filter}')" if sender_filter else ""
        print(f"(sin correos en este filtro{msg_extra})" if filter_ == "unread" else f"(sin correos{msg_extra})")
        return

    for m in msgs:
        fr = m.get("from", {}).get("emailAddress", {})
        fr_str = f"{fr.get('name','?')} <{fr.get('address','?')}>"
        date = m.get("receivedDateTime", "?")
        subj = m.get("subject", "(sin asunto)")
        preview = (m.get("bodyPreview", "") or "")[:200].replace("\n", " ")
        attach = "📎" if m.get("hasAttachments") else "  "
        unread = "•" if not m.get("isRead") else " "
        mid = m.get("id", "")
        print(f"{unread} {attach} [{date[:16]}] {fr_str}")
        print(f"      {subj}")
        print(f"      {preview}")
        print(f"      id={mid}")
        print()


def cmd_buscar(args):
    if len(args) < 2:
        print("Uso: m365.py buscar <tenant> \"<query>\" [limit]", file=sys.stderr)
        sys.exit(1)
    tenant, query = args[0], args[1]
    limit = int(args[2]) if len(args) > 2 else 10
    token = get_access_token(tenant)
    params = {
        "$search": f'"{query}"',
        "$top": str(limit),
        "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead",
    }
    data = graph_get(token, f"/me/messages?{urllib.parse.urlencode(params)}")
    msgs = data.get("value", [])
    if not msgs:
        print(f"(sin resultados para «{query}»)")
        return
    for m in msgs:
        fr = m.get("from", {}).get("emailAddress", {})
        date = m.get("receivedDateTime", "?")[:16]
        print(f"  [{date}] {fr.get('name','?')} <{fr.get('address','?')}>")
        print(f"  {m.get('subject','(sin asunto)')}")
        print(f"  id={m.get('id')}")
        print()


def cmd_ver_correo(args):
    if len(args) < 2:
        print("Uso: m365.py ver-correo <tenant> <message_id>", file=sys.stderr)
        sys.exit(1)
    tenant, mid = args[0], args[1]
    token = get_access_token(tenant)
    params = {"$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,isRead"}
    data = graph_get(token, f"/me/messages/{mid}?{urllib.parse.urlencode(params)}")
    fr = data.get("from", {}).get("emailAddress", {})
    tos = [r.get("emailAddress", {}).get("address", "") for r in data.get("toRecipients", [])]
    ccs = [r.get("emailAddress", {}).get("address", "") for r in data.get("ccRecipients", [])]
    print(f"From: {fr.get('name','?')} <{fr.get('address','?')}>")
    print(f"To: {', '.join(tos)}")
    if ccs:
        print(f"Cc: {', '.join(ccs)}")
    print(f"Date: {data.get('receivedDateTime','?')}")
    print(f"Subject: {data.get('subject','(sin asunto)')}")
    print(f"Read: {'sí' if data.get('isRead') else 'no'}")
    if data.get("hasAttachments"):
        print(f"Attachments: sí")
    print("---")
    body = data.get("body", {})
    print(body.get("content", "(sin cuerpo)"))


def cmd_attachments_listar(args):
    """Lista los attachments de un correo (id, nombre, tamaño, content-type)."""
    if len(args) < 2:
        print("Uso: m365.py attachments-listar <tenant> <message_id>", file=sys.stderr)
        sys.exit(1)
    tenant, mid = args[0], args[1]
    token = get_access_token(tenant)
    data = graph_get(token, f"/me/messages/{mid}/attachments?$select=id,name,size,contentType,isInline")
    attachments = data.get("value", [])
    if not attachments:
        print("(sin attachments)")
        return
    for a in attachments:
        size_kb = (a.get("size", 0) or 0) // 1024
        inline = " [inline]" if a.get("isInline") else ""
        print(f"  • {a.get('name','?')} — {a.get('contentType','?')} ({size_kb} KB){inline}")
        print(f"    id={a.get('id','')}")


def cmd_attachment_descargar(args):
    """Descarga un attachment a /opt/openclaw/state/email-attachments/ y devuelve path + preview."""
    if len(args) < 3:
        print("Uso: m365.py attachment-descargar <tenant> <message_id> <attachment_id>", file=sys.stderr)
        sys.exit(1)
    tenant, mid, aid = args[0], args[1], args[2]
    token = get_access_token(tenant)
    # Trae metadata + content base64
    data = graph_get(token, f"/me/messages/{mid}/attachments/{aid}")
    name = data.get("name", f"attachment-{aid[:8]}")
    content_b64 = data.get("contentBytes")
    if not content_b64:
        print(f"ERROR: attachment '{name}' no tiene contentBytes (puede ser referenceAttachment / link a SharePoint)")
        # Si es referenceAttachment (link a SharePoint), exponer el sourceUrl
        if data.get("@odata.type", "").endswith("referenceAttachment"):
            print(f"  Tipo: referenceAttachment (link a SharePoint/OneDrive)")
            print(f"  sourceUrl: {data.get('sourceUrl','(no expuesto)')}")
            print(f"  Para abrir el link, necesitas scope Files.Read.All + Sites.Read.All en Azure")
        sys.exit(1)
    import base64
    raw = base64.b64decode(content_b64)
    out_dir = Path("/opt/openclaw/state/email-attachments")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Sanitiza nombre
    safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"{ts}-{safe_name}"
    out_path.write_bytes(raw)
    print(f"OK descargado: {out_path}")
    print(f"  Tamaño: {len(raw)} bytes ({len(raw)//1024} KB)")
    print(f"  Tipo: {data.get('contentType','?')}")
    # Preview si es texto/PDF/Word
    ctype = (data.get("contentType") or "").lower()
    if "text" in ctype or "json" in ctype or "xml" in ctype:
        try:
            preview = raw[:3000].decode("utf-8", errors="replace")
            print("--- Preview (primeros 3000 chars) ---")
            print(preview)
        except Exception:
            pass
    elif "pdf" in ctype:
        try:
            r = subprocess.run(["pdftotext", "-layout", "-l", "3", str(out_path), "-"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                print("--- Preview PDF (primeras 3 páginas) ---")
                print(r.stdout[:5000])
            else:
                print("(pdftotext no disponible o falló — instala poppler-utils)")
        except FileNotFoundError:
            print("(pdftotext no instalado — apt-get install poppler-utils para preview de PDFs)")
    elif "officedocument" in ctype or "wordprocessing" in ctype:
        print("(Es Word/Excel — Louis puede invocar python-docx/openpyxl para preview, pendiente)")


def cmd_sharepoint_descargar(args):
    """Intenta descargar un archivo de SharePoint/OneDrive vía Graph API.
    REQUIERE scope Files.Read.All y Sites.Read.All en la app de Azure."""
    if len(args) < 2:
        print("Uso: m365.py sharepoint-descargar <tenant> <sharing_url_o_path>", file=sys.stderr)
        sys.exit(1)
    tenant = args[0]
    url_or_path = args[1]
    token = get_access_token(tenant)
    # Si es URL de sharing (https://kawiil-my.sharepoint.com/...), usar /shares/{shareId}
    if url_or_path.startswith("http"):
        import base64
        # Encode URL → shareId según Graph spec
        encoded = base64.urlsafe_b64encode(url_or_path.encode("utf-8")).decode("ascii").rstrip("=")
        share_id = f"u!{encoded}"
        try:
            data = graph_get(token, f"/shares/{share_id}/driveItem")
        except Exception as e:
            print(f"ERROR: no pude resolver el share. {e}")
            print("Probable causa: falta scope `Files.Read.All` o `Sites.Read.All` en la app Azure.")
            print("Fix: ve a Azure Portal → App registrations → tu app → API permissions → Add Microsoft Graph → Delegated → Files.Read.All + Sites.Read.All → Grant admin consent.")
            print("Después: ssh polo@... 'sudo rm /opt/openclaw/credentials/m365-*.json' y vuelve a hacer login OAuth.")
            sys.exit(1)
        item_id = data.get("id")
        name = data.get("name", "downloaded")
        # Descargar content
        try:
            url = f"/me/drive/items/{item_id}/content"
            raw = graph_get_raw(token, url)
        except Exception as e:
            print(f"ERROR descargando content: {e}")
            sys.exit(1)
        out_dir = Path("/opt/openclaw/state/sharepoint-downloads")
        out_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)
        out_path = out_dir / f"{ts}-{safe_name}"
        out_path.write_bytes(raw)
        print(f"OK descargado: {out_path}")
        print(f"  Tamaño: {len(raw)} bytes ({len(raw)//1024} KB)")
        ctype = data.get("file", {}).get("mimeType", "")
        if "pdf" in ctype:
            try:
                r = subprocess.run(["pdftotext", "-layout", "-l", "5", str(out_path), "-"],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    print("--- Preview PDF (primeras 5 páginas) ---")
                    print(r.stdout[:6000])
            except FileNotFoundError:
                print("(pdftotext no instalado)")
        return
    print(f"ERROR: formato no reconocido. Pasa URL de SharePoint completa.", file=sys.stderr)


def cmd_marcar_leido(args):
    if len(args) < 2:
        print("Uso: m365.py marcar-leido <tenant> <message_id>", file=sys.stderr)
        sys.exit(1)
    tenant, mid = args[0], args[1]
    token = get_access_token(tenant)
    graph_patch(token, f"/me/messages/{mid}", {"isRead": True})
    print(f"OK: marcado como leído ({mid[:30]}...)")


def cmd_marcar_no_leido(args):
    if len(args) < 2:
        print("Uso: m365.py marcar-no-leido <tenant> <message_id>", file=sys.stderr)
        sys.exit(1)
    tenant, mid = args[0], args[1]
    token = get_access_token(tenant)
    graph_patch(token, f"/me/messages/{mid}", {"isRead": False})
    print(f"OK: marcado como no leído ({mid[:30]}...)")


def cmd_borrar(args):
    if len(args) < 2:
        print("Uso: m365.py borrar <tenant> <message_id>", file=sys.stderr)
        sys.exit(1)
    tenant, mid = args[0], args[1]
    token = get_access_token(tenant)
    graph_delete(token, f"/me/messages/{mid}")
    print(f"OK: borrado a papelera ({mid[:30]}...)")


def cmd_mover(args):
    if len(args) < 3:
        print("Uso: m365.py mover <tenant> <message_id> <folder>", file=sys.stderr)
        sys.exit(1)
    tenant, mid, folder = args[0], args[1], args[2]
    token = get_access_token(tenant)
    folder_id = _well_known_folder(folder)
    result = graph_post(token, f"/me/messages/{mid}/move", {"destinationId": folder_id})
    print(f"OK: movido a {folder} ({mid[:30]}...)")


def cmd_archivar(args):
    if len(args) < 2:
        print("Uso: m365.py archivar <tenant> <message_id>", file=sys.stderr)
        sys.exit(1)
    cmd_mover([args[0], args[1], "Archive"])


def cmd_responder(args, reply_all=False):
    if len(args) < 3:
        u = "responder-todos" if reply_all else "responder"
        print(f"Uso: m365.py {u} <tenant> <message_id> \"<texto>\"", file=sys.stderr)
        sys.exit(1)
    tenant, mid, body = args[0], args[1], args[2]
    token = get_access_token(tenant)
    endpoint = "replyAll" if reply_all else "reply"
    graph_post(token, f"/me/messages/{mid}/{endpoint}", {"comment": body})
    print(f"OK: respondido{'a todos' if reply_all else ''} ({mid[:30]}...)")


def cmd_reenviar(args):
    if len(args) < 4:
        print("Uso: m365.py reenviar <tenant> <message_id> <to,...> \"<comentario>\"", file=sys.stderr)
        sys.exit(1)
    tenant, mid, to, comment = args[0], args[1], args[2], args[3]
    token = get_access_token(tenant)
    recipients = [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()]
    graph_post(token, f"/me/messages/{mid}/forward", {
        "comment": comment,
        "toRecipients": recipients,
    })
    print(f"OK: reenviado a {to} ({mid[:30]}...)")


def cmd_listar_folders(args):
    tenant = args[0] if args else "kawiil"
    token = get_access_token(tenant)
    data = graph_get(token, "/me/mailFolders?$top=50&$select=id,displayName,totalItemCount,unreadItemCount")
    for f in data.get("value", []):
        print(f"  {f.get('displayName'):30}  unread={f.get('unreadItemCount',0):>4}  total={f.get('totalItemCount',0):>5}  id={f.get('id')[:40]}")


def cmd_calendario(args):
    tenant = args[0] if len(args) > 0 else "kawiil"
    range_ = args[1] if len(args) > 1 else "hoy"
    token = get_access_token(tenant)
    now = datetime.now(TZ_CDMX)
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
        "$select": "id,subject,start,end,location,bodyPreview,organizer,attendees,isOnlineMeeting,onlineMeeting,responseStatus,responseRequested,isOrganizer",
    }
    data = graph_get(token, f"/me/calendarview?{urllib.parse.urlencode(params)}")
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
        # Estatus de respuesta del usuario actual
        rs = (ev.get("responseStatus", {}) or {}).get("response", "none")
        status_marker = _response_status_marker(rs, ev.get("isOrganizer"))
        print(f"{status_marker} [{start} → {end}] {online}{subj}")
        if loc:
            print(f"   📍 {loc}")
        print(f"   👤 {org}")
        # Texto explícito del estatus para que Louis lo lea fácil
        print(f"   📨 Mi respuesta: {_response_status_text(rs, ev.get('isOrganizer'))}")
        attendees = ev.get("attendees", [])
        if attendees:
            names = [a.get("emailAddress", {}).get("name", "?") for a in attendees[:5]]
            tail = "..." if len(attendees) > 5 else ""
            print(f"   👥 {', '.join(names)}{tail}")
        print(f"   id={ev.get('id')}")
        print()


def _response_status_marker(response: str, is_organizer: bool) -> str:
    """Marker visual de una sola letra para listado rápido."""
    if is_organizer:
        return "👑"
    return {
        "accepted": "✓",
        "tentativelyAccepted": "?",
        "declined": "✗",
        "notResponded": "⏳",
        "organizer": "👑",
        "none": "·",
    }.get(response, "·")


def _response_status_text(response: str, is_organizer: bool) -> str:
    """Texto explícito del estatus de respuesta."""
    if is_organizer:
        return "soy organizador (no requiere respuesta)"
    return {
        "accepted": "ACEPTADO",
        "tentativelyAccepted": "tentativo",
        "declined": "rechazado",
        "notResponded": "PENDIENTE de responder",
        "organizer": "soy organizador",
        "none": "sin estatus",
    }.get(response, response)


def cmd_crear_evento(args):
    if len(args) < 4:
        print("Uso: m365.py crear-evento <tenant> \"<subject>\" <inicio> <fin> [asistentes] [body]", file=sys.stderr)
        print("Formato fechas: YYYY-MM-DDTHH:MM (CDMX)", file=sys.stderr)
        sys.exit(1)
    tenant, subject, inicio, fin = args[:4]
    attendees_str = args[4] if len(args) > 4 else ""
    body_str = args[5] if len(args) > 5 else ""

    token = get_access_token(tenant)
    body = {
        "subject": subject,
        "start": {"dateTime": inicio, "timeZone": "Central Standard Time (Mexico)"},
        "end": {"dateTime": fin, "timeZone": "Central Standard Time (Mexico)"},
    }
    if body_str:
        body["body"] = {"contentType": "Text", "content": body_str}
    if attendees_str:
        body["attendees"] = [
            {"emailAddress": {"address": a.strip()}, "type": "required"}
            for a in attendees_str.split(",") if a.strip()
        ]
    result = graph_post(token, "/me/events", body)
    print(f"OK: evento creado ({result.get('id','?')[:30]}...)")
    print(f"    Subject: {subject}")
    print(f"    {inicio} → {fin}")


def cmd_responder_evento(args):
    """Acepta, rechaza o marca tentativo una invitación a evento."""
    if len(args) < 3:
        print("Uso: m365.py responder-evento <tenant> <event_id> <accept|decline|tentative> [\"<comentario>\"] [send_response]", file=sys.stderr)
        sys.exit(1)
    tenant, eid, accion = args[0], args[1], args[2].lower()
    comment = args[3] if len(args) > 3 else ""
    send_response = True
    if len(args) > 4:
        send_response = args[4].lower() not in ("false", "no", "0")

    endpoint_map = {
        "accept": "accept",
        "aceptar": "accept",
        "si": "accept",
        "sí": "accept",
        "decline": "decline",
        "rechazar": "decline",
        "no": "decline",
        "tentative": "tentativelyAccept",
        "tentativo": "tentativelyAccept",
        "tal-vez": "tentativelyAccept",
    }
    endpoint = endpoint_map.get(accion)
    if not endpoint:
        print(f"ERROR: acción '{accion}' inválida. Usa accept|decline|tentative", file=sys.stderr)
        sys.exit(1)
    token = get_access_token(tenant)
    body = {"sendResponse": send_response}
    if comment:
        body["comment"] = comment
    graph_post(token, f"/me/events/{eid}/{endpoint}", body)
    accion_es = {"accept": "ACEPTADO", "decline": "RECHAZADO", "tentativelyAccept": "TENTATIVO"}[endpoint]
    print(f"OK: evento {accion_es} ({eid[:30]}...)")
    if send_response:
        print(f"    Se envió respuesta al organizador")
    else:
        print(f"    NO se envió respuesta al organizador")


def cmd_pendientes_evento(args):
    """Lista solo eventos donde el usuario tiene responseStatus=notResponded."""
    tenant = args[0] if len(args) > 0 else "kawiil"
    range_ = args[1] if len(args) > 1 else "semana"
    token = get_access_token(tenant)
    now = datetime.now(TZ_CDMX)
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
        s, e = today_start, today_start + timedelta(days=7)

    start_utc = s.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_utc = e.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    params = {
        "startDateTime": start_utc,
        "endDateTime": end_utc,
        "$orderby": "start/dateTime",
        "$top": "100",
        "$select": "id,subject,start,end,location,organizer,responseStatus,isOrganizer,responseRequested",
    }
    data = graph_get(token, f"/me/calendarview?{urllib.parse.urlencode(params)}")
    events = data.get("value", [])
    pendientes = [
        ev for ev in events
        if not ev.get("isOrganizer")
        and (ev.get("responseStatus", {}) or {}).get("response") in ("notResponded", "none", None)
    ]
    if not pendientes:
        print(f"(sin invitaciones pendientes en {range_})")
        return
    print(f"=== {len(pendientes)} invitación(es) PENDIENTE(s) en {range_} ===\n")
    for ev in pendientes:
        start = ev.get("start", {}).get("dateTime", "?")[:16].replace("T", " ")
        end = ev.get("end", {}).get("dateTime", "?")[11:16]
        subj = ev.get("subject", "(sin título)")
        org = (ev.get("organizer", {}) or {}).get("emailAddress", {}).get("name", "?")
        org_email = (ev.get("organizer", {}) or {}).get("emailAddress", {}).get("address", "?")
        print(f"⏳ [{start} → {end}] {subj}")
        print(f"   👤 {org} <{org_email}>")
        print(f"   id={ev.get('id')}")
        print()


def cmd_eliminar_evento(args):
    """Cancela un evento (si soy organizador → notifica a los asistentes) o lo
    elimina de mi calendario (si soy invitado o no tiene asistentes)."""
    if len(args) < 2:
        print("Uso: m365.py eliminar-evento <tenant> <event_id> [\"<comentario>\"]", file=sys.stderr)
        sys.exit(1)
    tenant, eid = args[0], args[1]
    comment = args[2] if len(args) > 2 else "Cancelado"
    token = get_access_token(tenant)
    # Averigua si soy el organizador para decidir entre cancelar (notifica) o borrar.
    try:
        ev = graph_get(token, f"/me/events/{eid}?$select=subject,isOrganizer,attendees")
    except RuntimeError as e:
        print(f"ERROR: no encontré el evento {eid[:30]}...: {e}", file=sys.stderr)
        sys.exit(1)
    subj = ev.get("subject", "(sin título)")
    is_org = ev.get("isOrganizer", False)
    tiene_asistentes = bool(ev.get("attendees"))
    if is_org and tiene_asistentes:
        # /cancel envía mensaje de cancelación a los asistentes y cancela la junta.
        graph_post(token, f"/me/events/{eid}/cancel", {"comment": comment})
        print(f"OK: evento CANCELADO — se notificó a los asistentes")
        print(f"    {subj}")
    else:
        graph_delete(token, f"/me/events/{eid}")
        print(f"OK: evento ELIMINADO de tu calendario")
        print(f"    {subj}")


def cmd_actualizar_evento(args):
    """Modifica un evento existente (hora, asunto, asistentes, lugar, etc).
    Si soy organizador y el evento tiene asistentes, Graph les envía la
    actualización automáticamente."""
    if len(args) < 3:
        print("Uso: m365.py actualizar-evento <tenant> <event_id> campo=valor [campo=valor ...]", file=sys.stderr)
        print("Campos: subject, inicio (YYYY-MM-DDTHH:MM), fin, asistentes (a,b,c), body, ubicacion", file=sys.stderr)
        sys.exit(1)
    tenant, eid = args[0], args[1]
    token = get_access_token(tenant)
    body = {}
    for kv in args[2:]:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip().lower()
        if k in ("subject", "asunto", "titulo", "título"):
            body["subject"] = v
        elif k in ("inicio", "start"):
            body["start"] = {"dateTime": v, "timeZone": "Central Standard Time (Mexico)"}
        elif k in ("fin", "end"):
            body["end"] = {"dateTime": v, "timeZone": "Central Standard Time (Mexico)"}
        elif k in ("body", "cuerpo", "descripcion", "descripción"):
            body["body"] = {"contentType": "Text", "content": v}
        elif k in ("ubicacion", "ubicación", "location", "lugar"):
            body["location"] = {"displayName": v}
        elif k in ("asistentes", "attendees"):
            body["attendees"] = [
                {"emailAddress": {"address": a.strip()}, "type": "required"}
                for a in v.split(",") if a.strip()
            ]
    if not body:
        print("ERROR: no diste ningún campo válido a actualizar", file=sys.stderr)
        sys.exit(1)
    graph_patch(token, f"/me/events/{eid}", body)
    print(f"OK: evento actualizado ({eid[:30]}...)")
    print(f"    Campos modificados: {', '.join(body.keys())}")


def cmd_mandar_correo(args):
    if len(args) < 4:
        print("Uso: m365.py mandar-correo <tenant> <to> \"<subject>\" \"<body>\" [cc] [text|html]", file=sys.stderr)
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
    http_request("POST", f"{GRAPH}/me/sendMail", token=token, body=msg)
    print(f"OK: correo enviado a {to}")
    print(f"    Subject: {subject}")


# ===== Main =====
COMMANDS = {
    "token": cmd_token,
    "leer-correos": cmd_leer_correos,
    "buscar": cmd_buscar,
    "ver-correo": cmd_ver_correo,
    "marcar-leido": cmd_marcar_leido,
    "marcar-no-leido": cmd_marcar_no_leido,
    "borrar": cmd_borrar,
    "mover": cmd_mover,
    "archivar": cmd_archivar,
    "responder": lambda args: cmd_responder(args, reply_all=False),
    "responder-todos": lambda args: cmd_responder(args, reply_all=True),
    "reenviar": cmd_reenviar,
    "listar-folders": cmd_listar_folders,
    "calendario": cmd_calendario,
    "crear-evento": cmd_crear_evento,
    "responder-evento": cmd_responder_evento,
    "eliminar-evento": cmd_eliminar_evento,
    "actualizar-evento": cmd_actualizar_evento,
    "pendientes-evento": cmd_pendientes_evento,
    "mandar-correo": cmd_mandar_correo,
    "attachments-listar": cmd_attachments_listar,
    "attachment-descargar": cmd_attachment_descargar,
    "sharepoint-descargar": cmd_sharepoint_descargar,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    handler = COMMANDS.get(cmd)
    if not handler:
        print(f"Comando desconocido: {cmd}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    try:
        handler(args)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
