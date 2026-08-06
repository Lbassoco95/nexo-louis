#!/usr/bin/env python3
"""Motor HTML interactivo ÚNICO de Donna / Kawiil.

Un solo lugar para el "look" de todos los documentos que genera Donna:
dashboard con secciones colapsables, buscador sticky, tablas con celdas de
color, tarjetas (callouts), marca Kawiil y un chat embebido para profundizar
sobre el contenido (apunta al gateway de Donna).

Lo usan:
  - donna_core.py        → análisis legal, documentos de agentes (vía md_to_html)
  - dof_daily_summary.py → boletín diario del DOF (vía render_page)
  - sjf_weekly_summary.py→ resumen semanal del SJF (vía render_page)

Antes cada uno tenía su propio HTML plano; ahora todos comparten este motor,
así que mejorar el look aquí mejora TODOS los documentos a la vez.
"""
import html as _html
import re as _re
import os as _os
import json as _json
import datetime as _dt
import base64 as _b64
from pathlib import Path as _Path

# Directorio de assets de marca (logos). En el server: /opt/openclaw/assets/brand/kawiil
BRAND_DIR = _Path(_os.environ.get("LOUIS_BRAND_DIR", "/opt/openclaw/assets/brand/kawiil"))
_LOGO_CACHE: dict = {}


def _logo_uri(nombre: str = "Manik_1.png") -> str:
    """Devuelve el logo como data URI (base64) para embeberlo SIN depender de red.
    Manik_1.png = isotipo BLANCO (para fondos oscuros, ej. el header azul).
    Devuelve '' si no encuentra el archivo (el header cae a texto '✦ Kawiil')."""
    if nombre in _LOGO_CACHE:
        return _LOGO_CACHE[nombre]
    uri = ""
    try:
        p = BRAND_DIR / nombre
        if p.exists():
            b = p.read_bytes()
            uri = "data:image/png;base64," + _b64.b64encode(b).decode("ascii")
    except Exception:
        uri = ""
    _LOGO_CACHE[nombre] = uri
    return uri

# ── Marca Kawiil ──────────────────────────────────────────────────────────
BRAND = {
    "primary": "#1a6ef5",   # azul Kawiil
    "dark":    "#0a1a8c",    # azul marino Kawiil
    "ok":      "#2e7d32",
    "warn":    "#f9a825",
    "bad":     "#c62828",
    "info":    "#1a6ef5",
}


def celda_clase(texto: str) -> str:
    """Clasifica una celda de tabla por su contenido para colorearla (look dashboard).
    Rojo = no/prohibido/alto; ámbar = depende/medio; verde = sí/permitido/bajo."""
    t = (texto or "").strip().lower()
    if any(k in t for k in ("❌", "🚫", "🔴", "no pueden", "prohib", "ilegal", "infracci", " alto", "alto ", "no aplica")) or t in ("no", "alto"):
        return "c-bad"
    if any(k in t for k in ("⚠", "🟡", "depende", "medio", "gris", "revisar", "condicion")) or t in ("medio", "depende"):
        return "c-warn"
    if any(k in t for k in ("✅", "✔", "🟢", "permitid", "sí pued", "si pued", " bajo", "bajo ", "legal", "procede")) or t in ("sí", "si", "bajo", "legal"):
        return "c-ok"
    return ""


# ── JavaScript del documento (toolbar + colapsables + render markdown del chat) ──
DOC_JS = r"""
function toggleAll(o){document.querySelectorAll('details.sec').forEach(function(d){d.open=o;});}
function filtra(){var q=(document.getElementById('q').value||'').toLowerCase();
  document.querySelectorAll('details.sec').forEach(function(d){
    var hit=!q||d.textContent.toLowerCase().indexOf(q)>=0;
    d.style.display=hit?'':'none'; if(hit&&q){d.open=true;}});}
// Renderizador de markdown -> HTML (para las respuestas del chat: tablas, negritas, listas)
function inl(s){
  s=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  s=s.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  s=s.replace(/\*(.+?)\*/g,'<em>$1</em>');
  s=s.replace(/`(.+?)`/g,'<code>$1</code>');
  return s;
}
function mdToHtml(md){
  var lines=(md||'').split('\n'); var out=[]; var i=0; var m;
  while(i<lines.length){
    var ln=lines[i];
    if(/^\s*\|/.test(ln)){
      var rows=[]; while(i<lines.length && /^\s*\|/.test(lines[i])){ rows.push(lines[i]); i++; }
      var h='<table>'; var first=true;
      for(var r=0;r<rows.length;r++){
        if(/^[\s|:\-]+$/.test(rows[r])){ continue; }
        var cells=rows[r].trim().replace(/^\||\|$/g,'').split('|');
        var tag=first?'th':'td'; first=false;
        h+='<tr>'+cells.map(function(c){return '<'+tag+'>'+inl(c.trim())+'</'+tag+'>';}).join('')+'</tr>';
      }
      out.push(h+'</table>'); continue;
    }
    if(m=ln.match(/^(#{1,4})\s+(.+)/)){ var l=m[1].length; out.push('<h'+l+'>'+inl(m[2])+'</h'+l+'>'); i++; continue; }
    if(/^\s*>\s?/.test(ln)){ out.push('<blockquote>'+inl(ln.replace(/^\s*>\s?/,''))+'</blockquote>'); i++; continue; }
    if(/^---+\s*$/.test(ln)){ out.push('<hr>'); i++; continue; }
    if(ln.match(/^\s*[-*]\s+(.+)/)){ var it=[]; while(i<lines.length && (m=lines[i].match(/^\s*[-*]\s+(.+)/))){ it.push('<li>'+inl(m[1])+'</li>'); i++; } out.push('<ul>'+it.join('')+'</ul>'); continue; }
    if(ln.match(/^\s*\d+\.\s+(.+)/)){ var it2=[]; while(i<lines.length && (m=lines[i].match(/^\s*\d+\.\s+(.+)/))){ it2.push('<li>'+inl(m[1])+'</li>'); i++; } out.push('<ol>'+it2.join('')+'</ol>'); continue; }
    if(ln.trim()===''){ i++; continue; }
    out.push('<p>'+inl(ln)+'</p>'); i++;
  }
  return out.join('');
}
function add(role,txt,isMd){var c=document.getElementById('conv');var d=document.createElement('div');d.className='msg '+role;if(isMd){d.innerHTML=mdToHtml(txt);}else{d.textContent=txt;}c.appendChild(d);return d;}
async function preg(){
  var i=document.getElementById('cq');var q=(i.value||'').trim();if(!q)return;
  i.value='';add('user',q,false);var t=add('bot','pensando…',false);
  try{
    var h={'Content-Type':'application/json'};if(CHAT_TOKEN){h['Authorization']='Bearer '+CHAT_TOKEN;}
    var r=await fetch(CHAT_URL,{method:'POST',headers:h,body:JSON.stringify({messages:[{role:'user',content:'Contexto (documento que el usuario está leyendo):\n'+CTX+'\n\nPregunta de seguimiento: '+q}]})});
    var j=await r.json();
    var a=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||j.error||'(sin respuesta)';
    t.className='msg bot'; t.innerHTML=mdToHtml(a);
  }catch(e){t.textContent='No pude conectar ('+e+'). Abre este HTML en un navegador, no en el visor de Telegram.';}
}
"""


def _css(accent: str, accent_dark: str) -> str:
    return f"""
  body {{ font-family: 'Georgia', serif; max-width: 880px; margin: 40px auto;
         padding: 0 24px; color: #1a1a1a; line-height: 1.7; }}
  h1 {{ font-size: 1.6em; border-bottom: 3px solid {accent_dark}; padding-bottom: 8px; color: {accent_dark}; }}
  h2 {{ font-size: 1.25em; color: {accent_dark}; margin-top: 2em; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h3, h4 {{ color: #34495e; margin-top: 1.5em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: 0.9em; }}
  th {{ background: {accent_dark}; color: white; padding: 8px 12px; text-align: left; }}
  td {{ border: 1px solid #ddd; padding: 7px 12px; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #f8f9fa; }}
  td.cod, td.reg {{ width: 84px; font-size: .85em; white-space: nowrap; }}
  td.cod a, td.reg a {{ color: {accent}; text-decoration: none; font-weight: bold; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 0.88em; }}
  li {{ margin-bottom: 4px; }}
  strong {{ color: #c0392b; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.5em 0; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.4em;
             padding: 14px 18px; background: linear-gradient(90deg,{accent_dark},{accent}); border-radius: 8px;
             font-size: 0.85em; color: #eaf1ff; }}
  .header strong {{ color: #fff; }}
  .header .brand {{ font-size: 1.15em; font-weight: bold; color: #fff; letter-spacing: .5px; display: inline-flex; align-items: center; gap: 9px; }}
  .header .brand img.logo {{ height: 30px; width: auto; display: block; }}
  .resumen {{ background: #eef3fb; border-left: 4px solid {accent}; padding: 11px 15px; margin: 1em 0; font-size: .95em; border-radius: 0 8px 8px 0; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 1em 0 1.4em; }}
  .kpi {{ flex: 1; min-width: 128px; background: #fff; border: 1px solid #e3e6ea;
          border-top: 3px solid {accent}; border-radius: 10px; padding: 12px 14px;
          box-shadow: 0 1px 4px rgba(0,0,0,.04); }}
  .kpi .v {{ font-size: 1.8em; font-weight: bold; color: {accent_dark}; line-height: 1; }}
  .kpi .l {{ font-size: .8em; color: #555; margin-top: 5px; font-family: sans-serif; }}
  .kpi .s {{ font-size: .72em; color: #999; margin-top: 2px; }}
  .destacados {{ background: #fff; border: 1px solid #e3e6ea; border-left: 4px solid {accent};
          border-radius: 0 10px 10px 0; padding: 14px 18px; margin: 1.2em 0; }}
  .destacados h3 {{ margin: 0 0 8px; color: {accent_dark}; }}
  .bar-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: .85em; }}
  .bar-row .bl {{ min-width: 130px; color: #444; }}
  .bar-row .bt {{ flex: 1; background: #eef0f3; border-radius: 6px; overflow: hidden; height: 16px; }}
  .bar-row .bf {{ height: 100%; background: {accent}; }}
  .bar-row .bn {{ min-width: 32px; text-align: right; color: #666; font-weight: bold; }}
  .footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #ddd;
             font-size: 0.8em; color: #999; text-align: center; }}
  details.sec {{ border: 1px solid #e3e6ea; border-radius: 8px; margin: 12px 0; padding: 0 14px; background: #fff; }}
  details.sec[open] {{ box-shadow: 0 1px 6px rgba(0,0,0,.05); }}
  details.sec > summary {{ cursor: pointer; font-size: 1.15em; font-weight: bold; color: {accent_dark};
             padding: 12px 0; list-style: none; }}
  details.sec > summary::-webkit-details-marker {{ display: none; }}
  details.sec > summary::before {{ content: "▸ "; color: {accent}; }}
  details.sec[open] > summary::before {{ content: "▾ "; }}
  details.sec > summary .c {{ color: #999; font-weight: normal; font-size: .82em; }}
  .sec-body {{ padding-bottom: 12px; }}
  .toolbar {{ position: sticky; top: 0; background: #fff; padding: 10px 0; margin-bottom: 8px;
             border-bottom: 1px solid #eee; display: flex; gap: 8px; flex-wrap: wrap; z-index: 5; }}
  .toolbar input {{ flex: 1; min-width: 140px; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: .95em; }}
  .toolbar button {{ padding: 8px 12px; border: 0; border-radius: 6px; background: {accent}; color: #fff; font-size: .85em; cursor: pointer; }}
  .tag {{ font-size: .68em; font-weight: bold; background: #dde9ff; color: {accent_dark}; padding: 1px 6px; border-radius: 4px; margin-right: 4px; }}
  .tag.j {{ background: {accent_dark}; color:#fff; }} .tag.t {{ background:#e8e8e8; color:#555; }}
  .ed {{ font-size: .68em; font-weight: bold; background: {accent_dark}; color: #fff; padding: 1px 6px; border-radius: 4px; margin-right: 4px; }}
  blockquote {{ margin: 12px 0; padding: 10px 14px; background: #eef3fb; border-left: 4px solid {accent};
             border-radius: 0 8px 8px 0; color: #34495e; font-style: normal; }}
  td.c-ok {{ background: #e8f5e9 !important; }}
  td.c-warn {{ background: #fff8e1 !important; }}
  td.c-bad {{ background: #fdecea !important; }}
  .callout {{ border-radius: 8px; padding: 11px 15px; margin: 10px 0; border-left: 5px solid #888; font-size: .96em; }}
  .callout.call-ok {{ background: #e8f5e9; border-left-color: #2e7d32; }}
  .callout.call-bad {{ background: #fdecea; border-left-color: #c62828; }}
  .callout.call-warn {{ background: #fff8e1; border-left-color: #f9a825; }}
  .callout.call-info {{ background: #e3f2fd; border-left-color: {accent}; }}
  .msg.bot table {{ font-size: .85em; margin: 8px 0; }}
  .msg.bot h2, .msg.bot h3 {{ font-size: 1.05em; margin: 8px 0 4px; border: none; color: {accent}; }}
  .msg.bot ul, .msg.bot ol {{ margin: 4px 0 4px 18px; }}
  .chat {{ margin-top: 2.5em; border-top: 2px solid {accent}; padding-top: 1em; }}
  .chat h2 {{ border: none; margin-top: 0; }}
  #conv {{ margin: 10px 0; display: flex; flex-direction: column; }}
  .msg {{ padding: 9px 13px; border-radius: 12px; margin: 6px 0; max-width: 88%; white-space: pre-wrap; font-size: .95em; }}
  .msg.user {{ background: {accent}; color: #fff; align-self: flex-end; }}
  .msg.bot {{ background: #eef3fb; color: #1a1a1a; align-self: flex-start; }}
  .chat-in {{ display: flex; gap: 8px; }}
  .chat-in input {{ flex: 1; padding: 11px; border: 1px solid #ccc; border-radius: 8px; font-size: 1em; }}
  .chat-in button {{ padding: 11px 16px; border: 0; border-radius: 8px; background: {accent}; color: #fff; cursor: pointer; }}
  @media print {{ body {{ margin: 0; padding: 20px; }} .header, .toolbar {{ break-inside: avoid; }}
             .toolbar, .chat {{ display: none; }} details.sec {{ border: none; }} }}
"""


def render_page(titulo: str, agente: str, body_html: str, *,
                ctx_md: str = "", con_chat: bool = True,
                accent: str | None = None, accent_dark: str | None = None,
                resumen: str = "", chat_titulo: str = "",
                fuente: str = "") -> bytes:
    """Arma la página completa (header de marca + toolbar + cuerpo + chat + footer).

    body_html : el cuerpo ya renderizado (secciones <details class="sec">, tablas, etc.)
    ctx_md    : texto que se le pasa al chat como contexto (se trunca a 4000 chars).
    con_chat  : incluir el chat embebido "Pregúntale a Donna".
    """
    accent = accent or BRAND["primary"]
    accent_dark = accent_dark or BRAND["dark"]
    esc = _html.escape
    fecha = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")

    chat_url = _os.environ.get("CHAT_ENDPOINT", "https://louis.kawiil.mx/v1/chat/completions")
    chat_token = _os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    ctx_json = _json.dumps((ctx_md or "")[:4000])
    script_js = (f'const CHAT_URL={_json.dumps(chat_url)};'
                 f'const CHAT_TOKEN={_json.dumps(chat_token)};'
                 f'const CTX={ctx_json};') + DOC_JS

    resumen_html = f'<div class="resumen">{resumen}</div>' if resumen else ""
    chat_html = ""
    if con_chat:
        ct = chat_titulo or "💬 Pregúntale a Donna sobre este documento"
        chat_html = f"""
<div class="chat">
  <h2>{esc(ct)}</h2>
  <div id="conv"></div>
  <div class="chat-in">
    <input id="cq" placeholder="Escribe tu pregunta de seguimiento…" onkeydown="if(event.key==='Enter')preg()">
    <button onclick="preg()">Preguntar</button>
  </div>
  <p style="font-size:.78em;color:#999;margin-top:6px">Ábrelo en un navegador (Safari/Chrome) para que el chat y los botones funcionen — el visor de Telegram bloquea el JavaScript.</p>
</div>"""

    fuente_ft = f" · {esc(fuente)}" if fuente else ""
    logo = _logo_uri("Manik_1.png")
    brand_html = (f'<img class="logo" src="{logo}" alt="Kawiil"><span>Kawiil</span>'
                  if logo else "✦ Kawiil")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(titulo)}</title>
<style>{_css(accent, accent_dark)}</style>
</head>
<body>
<div class="header">
  <span class="brand">{brand_html}</span>
  <span>Elaborado por <strong>Donna</strong> — {esc(agente)} · {fecha}</span>
</div>
<div class="toolbar">
  <input id="q" placeholder="🔎 Buscar en el documento…" oninput="filtra()">
  <button onclick="toggleAll(true)">Expandir todo</button>
  <button onclick="toggleAll(false)">Colapsar todo</button>
</div>
{resumen_html}
{body_html}
{chat_html}
<div class="footer">Documento generado por Donna (Kawiil) · {fecha}{fuente_ft} · Confidencial</div>
<script>{script_js}</script>
</body>
</html>""".encode("utf-8")


def kpi_cards(cards: list) -> str:
    """Fila de tarjetas-KPI para encabezar un dashboard.
    cards: lista de dicts {value, label, sub?}."""
    esc = _html.escape
    out = ['<div class="kpis">']
    for c in cards:
        sub = c.get("sub", "")
        out.append(
            f'<div class="kpi"><div class="v">{esc(str(c.get("value","")))}</div>'
            f'<div class="l">{esc(str(c.get("label","")))}</div>'
            + (f'<div class="s">{esc(str(sub))}</div>' if sub else '')
            + '</div>')
    out.append('</div>')
    return "".join(out)


def barras(items: list, total: int = 0) -> str:
    """Mini gráfica de barras horizontales. items: lista de (etiqueta, n)."""
    esc = _html.escape
    mx = max([n for _, n in items], default=0) or 1
    if total <= 0:
        total = sum(n for _, n in items) or 1
    out = []
    for etq, n in items:
        pct = int(round(100 * n / mx))
        out.append(
            f'<div class="bar-row"><span class="bl">{esc(str(etq))}</span>'
            f'<span class="bt"><span class="bf" style="width:{pct}%"></span></span>'
            f'<span class="bn">{n}</span></div>')
    return "".join(out)


def _md_body(md: str) -> str:
    """Convierte markdown básico al cuerpo HTML (secciones H2 colapsables, tablas con
    celdas de color, callouts, listas, citas). NO incluye header/chat — eso lo hace render_page."""
    esc = _html.escape

    def inline(s: str) -> str:
        """Escapa y aplica markdown inline (negrita/itálica/código). Se usa en TODO
        (encabezados incluidos) para que no queden ** ni * sueltos."""
        s = esc(s)
        s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = _re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
        s = _re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    lines = md.split("\n")
    body_parts: list[str] = []
    in_table = False
    table_rows: list[list[str]] = []
    table_open = False
    open_section = False
    for line in lines:
        if _re.match(r"^\s*\|", line):
            in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if _re.match(r"^[\s|:-]+$", line):
                if table_rows:
                    body_parts.append(
                        '<table><thead><tr>'
                        + "".join(f"<th>{esc(c)}</th>" for c in table_rows[-1])
                        + '</tr></thead><tbody>')
                    table_rows = []
                    table_open = True
                continue
            table_rows.append(cells)
            continue
        else:
            if in_table:
                if not table_open:
                    body_parts.append('<table><tbody>')
                for row in table_rows:
                    body_parts.append(
                        '<tr>' + "".join(f'<td class="{celda_clase(c)}">{esc(c)}</td>' for c in row) + '</tr>')
                body_parts.append('</tbody></table>')
                table_rows = []
                in_table = False
                table_open = False

        line_esc = inline(line)
        m = _re.match(r"^(#{1,4})\s+(.+)", line)
        if m:
            lvl = len(m.group(1))
            txt = inline(m.group(2))
            if lvl == 1:
                body_parts.append(f"<h1>{txt}</h1>")
            elif lvl == 2:
                if open_section:
                    body_parts.append("</div></details>")
                body_parts.append(
                    f'<details class="sec" open><summary>{txt}</summary><div class="sec-body">')
                open_section = True
            else:
                body_parts.append(f"<h{lvl}>{txt}</h{lvl}>")
            continue
        if _re.match(r"^\s*>\s?", line):
            inner = _re.sub(r"^\s*&gt;\s?", "", line_esc)
            body_parts.append(f"<blockquote>{inner}</blockquote>")
            continue
        if _re.match(r"^---+\s*$", line):
            body_parts.append("<hr>")
            continue
        m = _re.match(r"^[-*]\s+(.+)", line)
        if m:
            li = _re.sub(r"^\s*[-*]\s+", "", line_esc)
            body_parts.append(f"<li>{li}</li>")
            continue
        m = _re.match(r"^\d+\.\s+(.+)", line)
        if m:
            body_parts.append(f"<li>{line_esc}</li>")
            continue
        if not line.strip():
            body_parts.append("<br>")
            continue
        mc = _re.match(r"^\s*(✅|✔️|✔|❌|🚫|🔴|⚠️|⚠|🚨|🟡|🟢|💰|📌)", line)
        if mc:
            g = mc.group(1)
            cls = ("call-ok" if g in ("✅", "✔️", "✔", "🟢")
                   else "call-bad" if g in ("❌", "🚫", "🔴")
                   else "call-warn" if g in ("⚠️", "⚠", "🚨", "🟡")
                   else "call-info")
            body_parts.append(f'<div class="callout {cls}">{line_esc}</div>')
            continue
        body_parts.append(f"<p>{line_esc}</p>")

    if in_table:
        if not table_open:
            body_parts.append('<table><tbody>')
        for row in table_rows:
            body_parts.append('<tr>' + "".join(f'<td class="{celda_clase(c)}">{esc(c)}</td>' for c in row) + '</tr>')
        body_parts.append('</tbody></table>')
    if open_section:
        body_parts.append("</div></details>")
    return "\n".join(body_parts)


def md_to_html(titulo: str, agente: str, md: str, *, con_chat: bool = True,
               accent: str | None = None, accent_dark: str | None = None) -> bytes:
    """Markdown → HTML interactivo completo (el camino de los documentos de Donna)."""
    body = _md_body(md)
    return render_page(titulo, agente, body, ctx_md=md, con_chat=con_chat,
                       accent=accent, accent_dark=accent_dark)
