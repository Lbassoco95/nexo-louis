#!/usr/bin/env python3
"""
sjf_pdfs.py — genera en el SERVIDOR el PDF de cada tesis/jurisprudencia del SJF.

Por qué existe (oct-2026): los PDFs de SJF los generaba el scraper original en la Mac
(plantilla externa `build_tesis_pdf` + carpeta de Dropbox). Desde jun-2026 el servidor
es la fuente de la verdad y el harvester/backfill solo guardan texto en la BD, así que
148,125 de 178,554 tesis quedaron sin PDF y los 30,429 existentes viven en la Mac.
Este módulo trae la plantilla al servidor (reportlab, sin dependencias externas) y
procesa los pendientes por lotes, igual que `dof_biblioteca.py generate-pdfs`.

Uso:
    python3 sjf_pdfs.py generate --batch 1000   # un lote de pendientes
    python3 sjf_pdfs.py one 176574               # (re)genera un registro
    python3 sjf_pdfs.py stats                    # hechos / pendientes

Variables de entorno:
    SJF_DB_PATH   BD (default /opt/openclaw/legal/sjf/biblioteca.db)
    SJF_PDF_DIR   carpeta de salida (default /opt/openclaw/legal/sjf/pdfs)

Un PDF se considera pendiente si `pdf_path` está vacío o NO cuelga de SJF_PDF_DIR
(p. ej. las rutas de Dropbox de la Mac). Un error en una tesis no detiene el lote.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = os.environ.get("SJF_DB_PATH", "/opt/openclaw/legal/sjf/biblioteca.db")
PDF_DIR = Path(os.environ.get("SJF_PDF_DIR", "/opt/openclaw/legal/sjf/pdfs"))
SJF_URL = "https://sjf2.scjn.gob.mx/detalle/tesis/{reg}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("sjf_pdfs")

# ── Marca Kawiil (mismos colores que louis_html.py) ──────────────────────────
BLUE = "#1a6ef5"
NAVY = "#0a1a8c"
INK = "#1f1f1f"
GREY = "#666666"

_UNICODE_FONTS: dict | None = None
_STD_FONTS = {"serif": "Times-Roman", "serif_b": "Times-Bold",
              "sans": "Helvetica", "sans_b": "Helvetica-Bold"}
_CAMPOS_TEXTO = ("rubro", "texto", "precedentes", "notas_genericas", "texto_publicacion",
                 "localizacion", "materias", "sala", "fuente", "clave_tesis", "epoca", "instancia")


def _fonts(unicode_: bool) -> dict:
    """Fuentes estándar de PDF (no se incrustan: ~5-10 KB por archivo) y, solo si el
    texto trae caracteres fuera de Latin-1/cp1252, DejaVu (se incrusta: ~60 KB)."""
    global _UNICODE_FONTS
    if not unicode_:
        return _STD_FONTS
    if _UNICODE_FONTS is None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        base = Path("/usr/share/fonts/truetype/dejavu")
        try:
            pdfmetrics.registerFont(TTFont("SJF-Serif", str(base / "DejaVuSerif.ttf")))
            pdfmetrics.registerFont(TTFont("SJF-Serif-Bold", str(base / "DejaVuSerif-Bold.ttf")))
            pdfmetrics.registerFont(TTFont("SJF-Sans", str(base / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("SJF-Sans-Bold", str(base / "DejaVuSans-Bold.ttf")))
            _UNICODE_FONTS = {"serif": "SJF-Serif", "serif_b": "SJF-Serif-Bold",
                              "sans": "SJF-Sans", "sans_b": "SJF-Sans-Bold"}
        except Exception:
            _UNICODE_FONTS = _STD_FONTS      # sin DejaVu: se degrada a estándar
    return _UNICODE_FONTS


def necesita_unicode(row) -> bool:
    """True si algún campo de texto trae caracteres que cp1252 no puede representar."""
    for k in _CAMPOS_TEXTO:
        try:
            v = row[k]
        except (KeyError, IndexError):
            continue
        if v:
            try:
                str(v).encode("cp1252")
            except UnicodeEncodeError:
                return True
    return False


# ── Texto seguro para ReportLab ──────────────────────────────────────────────
def limpia(s) -> str:
    """Texto plano: quita etiquetas HTML residuales, decodifica entidades y normaliza."""
    if not s:
        return ""
    s = str(s)
    # Solo se quitan etiquetas HTML REALES; cualquier otro "<...>" (p. ej. "a < b",
    # "<fuera de juicio>") es texto jurídico legítimo y se conserva (escapado en esc()).
    s = re.sub(r"(?i)<br\b[^>]*>|</(?:p|div|li|tr|h[1-6]|blockquote)\s*>", "\n", s)
    s = re.sub(r"(?i)</?(?:p|br|div|span|b|i|u|em|strong|ul|ol|li|table|thead|tbody|tr|td|th|"
               r"h[1-6]|font|a|sup|sub|center|blockquote)\b[^>]*>", "", s)
    s = html.unescape(s).replace("\xa0", " ").replace("\r", "")
    return s.strip()


def esc(s: str) -> str:
    """Escapa para el mini-marcado de ReportLab (un '<' suelto rompe el Paragraph)."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parrafos(s: str) -> list[str]:
    """Párrafos ya escapados; un salto simple dentro del párrafo pasa a <br/>."""
    out = []
    for p in re.split(r"\n\s*\n", limpia(s)):
        p = p.strip()
        if len(p) >= 2:
            out.append(esc(p).replace("\n", "<br/>"))
    return out


def tipo_legible(row) -> str:
    t = (row["tipo_tesis"] or "").lower()
    if "jurisprud" in t:
        return "Jurisprudencia"
    if "aislad" in t:
        return "Tesis aislada"
    return (row["tipo_tesis"] or "Tesis").strip() or "Tesis"


def destino(reg: int) -> Path:
    """Reparte en subcarpetas de 10,000 registros para no tener 180k archivos juntos."""
    return PDF_DIR / f"{reg // 10000 * 10000:07d}" / f"Tesis{reg}.pdf"


# ── Plantilla ────────────────────────────────────────────────────────────────
def build_pdf(row, out: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    f = _fonts(necesita_unicode(row))
    reg = row["registro_digital"]
    tipo = tipo_legible(row)
    epoca = (row["epoca"] or "").strip()
    instancia = (row["instancia"] or "").strip()

    def hdr_ftr(c, doc):
        w, h = A4
        c.saveState()
        c.setFillColor(colors.HexColor(NAVY))
        c.rect(0, h - 20 * mm, w, 20 * mm, stroke=0, fill=1)
        c.setFillColor(colors.HexColor(BLUE))
        c.rect(0, h - 21.4 * mm, w, 1.4 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(f["sans_b"], 12.5)
        c.drawString(14 * mm, h - 11.5 * mm, "Semanario Judicial de la Federación")
        c.setFont(f["sans"], 8)
        c.drawString(14 * mm, h - 16.2 * mm, "Biblioteca Legal Kawiil")
        c.setFont(f["sans_b"], 9)
        c.drawRightString(w - 14 * mm, h - 11.5 * mm, tipo.upper())
        c.setFont(f["sans"], 8)
        c.drawRightString(w - 14 * mm, h - 16.2 * mm, f"Registro digital {reg}")
        # pie
        c.setStrokeColor(colors.lightgrey)
        c.setLineWidth(0.4)
        c.line(14 * mm, 17 * mm, w - 14 * mm, 17 * mm)
        c.setFillColor(colors.HexColor(GREY))
        c.setFont(f["sans"], 7)
        c.drawString(14 * mm, 12.5 * mm, SJF_URL.format(reg=reg))
        c.drawCentredString(w / 2, 12.5 * mm, f"Pág. {doc.page}")
        c.drawRightString(w - 14 * mm, 12.5 * mm,
                          "Generado " + dt.date.today().strftime("%d/%m/%Y"))
        c.setFont(f["sans"], 6)
        c.drawCentredString(w / 2, 8 * mm,
                            "Copia de consulta elaborada con datos públicos del Semanario Judicial "
                            "de la Federación (SCJN). La fuente oficial es el enlace indicado.")
        c.restoreState()

    st_rubro = ParagraphStyle("rubro", fontName=f["serif_b"], fontSize=11.5, leading=15,
                              alignment=TA_JUSTIFY, textColor=colors.HexColor(INK),
                              spaceBefore=2, spaceAfter=10)
    st_h = ParagraphStyle("h", fontName=f["sans_b"], fontSize=8, leading=10,
                          textColor=colors.HexColor(BLUE), spaceBefore=10, spaceAfter=4)
    st_body = ParagraphStyle("body", fontName=f["serif"], fontSize=9.5, leading=13.2,
                             alignment=TA_JUSTIFY, textColor=colors.HexColor(INK), spaceAfter=5)
    st_meta_k = ParagraphStyle("mk", fontName=f["sans_b"], fontSize=7.5, leading=10,
                               textColor=colors.HexColor(GREY))
    st_meta_v = ParagraphStyle("mv", fontName=f["sans"], fontSize=8.5, leading=11,
                               textColor=colors.HexColor(INK))
    st_epoca = ParagraphStyle("ep", fontName=f["sans"], fontSize=8.5, leading=11,
                              alignment=TA_CENTER, textColor=colors.HexColor(GREY), spaceAfter=2)

    story = []
    if epoca or instancia:
        story.append(Paragraph(esc(" · ".join(x for x in (epoca, instancia) if x)), st_epoca))

    # Ficha técnica (dos columnas de pares clave/valor)
    fecha = (row["fecha_publicacion"] or "")[:10]
    loc = " · ".join(x for x in (
        f"Tomo {row['tomo']}" if row["tomo"] else "",
        f"Vol. {row['volumen']}" if row["volumen"] else "",
        f"Pág. {row['pagina']}" if row["pagina"] else "") if x)
    pares = [
        ("Tipo", tipo), ("Clave de tesis", row["clave_tesis"] or ""),
        ("Materia(s)", row["materias"] or ""), ("Sala / órgano", row["sala"] or ""),
        ("Fuente", row["fuente"] or ""), ("Publicación", fecha),
        ("Localización", (row["localizacion"] or "").strip() or loc), ("Época", epoca),
    ]
    pares = [(k, esc(limpia(v))) for k, v in pares if limpia(v)]
    filas = []
    for i in range(0, len(pares), 2):
        a = pares[i]
        b = pares[i + 1] if i + 1 < len(pares) else ("", "")
        filas.append([Paragraph(a[0], st_meta_k), Paragraph(a[1], st_meta_v),
                      Paragraph(b[0], st_meta_k), Paragraph(b[1], st_meta_v)])
    if filas:
        t = Table(filas, colWidths=[24 * mm, 59 * mm, 24 * mm, 59 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fe")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dde6fb")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story += [t, Spacer(1, 6)]

    def seccion(titulo, valor, estilo=st_body):
        ps = parrafos(valor)
        if not ps:
            return
        story.append(KeepTogether([Paragraph(titulo, st_h), Paragraph(ps[0], estilo)]))
        for p in ps[1:]:
            story.append(Paragraph(p, estilo))

    rub = parrafos(row["rubro"])
    story.append(Paragraph("RUBRO", st_h))
    story.append(Paragraph(" ".join(rub) if rub else "(sin rubro)", st_rubro))
    seccion("TEXTO", row["texto"])
    seccion("PRECEDENTES", row["precedentes"])
    seccion("NOTAS", row["notas_genericas"])
    seccion("PUBLICACIÓN", row["texto_publicacion"])

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".pdf.tmp")
    doc = SimpleDocTemplate(str(tmp), pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=27 * mm, bottomMargin=22 * mm,
                            title=f"Tesis {reg}", author="Biblioteca Legal Kawiil",
                            subject=(limpia(row["rubro"]) or "")[:200])
    doc.build(story, onFirstPage=hdr_ftr, onLaterPages=hdr_ftr)
    tmp.replace(out)            # escritura atómica: nunca queda un PDF a medias


def build_pdf_seguro(row, out: Path) -> None:
    """Reintenta con texto sin caracteres no imprimibles si el primer intento falla."""
    try:
        build_pdf(row, out)
    except Exception as e1:
        log.warning("tesis %s: primer intento falló (%s); reintento saneado", row["registro_digital"], str(e1)[:80])
        limpio = {k: (re.sub(r"[^\x09\x0a\x20-\x7e -ɏ‐-‧]", " ", str(row[k]))
                      if row[k] is not None else None) for k in row.keys()}
        build_pdf(_Fila(limpio), out)


class _Fila(dict):
    """dict con interfaz de sqlite3.Row (acceso por clave)."""
    def keys(self):  # type: ignore[override]
        return list(super().keys())


# ── Comandos ─────────────────────────────────────────────────────────────────
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def _pendiente_sql() -> str:
    return ("(pdf_path IS NULL OR pdf_path='' OR pdf_path NOT LIKE ? || '%') "
            "AND rubro IS NOT NULL AND rubro!=''")


def cmd_generate(batch: int) -> int:
    conn = _conn()
    pref = str(PDF_DIR)
    rows = conn.execute(
        f"SELECT * FROM tesis WHERE {_pendiente_sql()} ORDER BY registro_digital DESC LIMIT ?",
        (pref, batch)).fetchall()
    if not rows:
        log.info("generate: nada pendiente.")
        return 0
    log.info("generate: %d tesis pendientes en este lote", len(rows))
    ok = err = 0
    for i, row in enumerate(rows, 1):
        reg = row["registro_digital"]
        try:
            out = destino(reg)
            build_pdf_seguro(row, out)
            conn.execute("UPDATE tesis SET pdf_generated=1, pdf_path=? WHERE registro_digital=?",
                         (str(out), reg))
            ok += 1
        except Exception as e:  # una tesis mala no tumba el lote
            err += 1
            log.error("tesis %s falló: %s", reg, str(e)[:160])
        if i % 50 == 0:
            conn.commit()
            log.info("  [%d/%d] ok=%d err=%d", i, len(rows), ok, err)
    conn.commit()
    log.info("generate terminado: %d ok, %d errores", ok, err)
    conn.close()
    return 0


def cmd_one(reg: int) -> int:
    conn = _conn()
    row = conn.execute("SELECT * FROM tesis WHERE registro_digital=?", (reg,)).fetchone()
    if not row:
        log.error("registro %d no está en la BD", reg)
        return 1
    out = destino(reg)
    build_pdf_seguro(row, out)
    conn.execute("UPDATE tesis SET pdf_generated=1, pdf_path=? WHERE registro_digital=?", (str(out), reg))
    conn.commit()
    log.info("PDF generado: %s (%d bytes)", out, out.stat().st_size)
    return 0


def cmd_stats() -> int:
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM tesis").fetchone()[0]
    pend = conn.execute(f"SELECT COUNT(*) FROM tesis WHERE {_pendiente_sql()}", (str(PDF_DIR),)).fetchone()[0]
    print(f"Tesis: {total:,} | PDF en servidor: {total - pend:,} | pendientes: {pend:,}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("generate", "one", "stats"):
        print(__doc__)
        return 2
    if args[0] == "stats":
        return cmd_stats()
    if args[0] == "one":
        return cmd_one(int(args[1]))
    batch = int(args[args.index("--batch") + 1]) if "--batch" in args else 500
    return cmd_generate(batch)


if __name__ == "__main__":
    sys.exit(main())
