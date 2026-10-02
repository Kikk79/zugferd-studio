"""
pdf_renderer.py - Creates invoice PDFs (sample / fallback) and converts Word files.

* create_invoice_pdf_from_data(): ReportLab rendering of structured invoice data.
  Uses an embedded TrueType font when one is available (needed for PDF/A), and
  the shared compute_totals() so the printed amounts match the XML exactly.
* convert_docx_to_pdf(): Microsoft Word (via COM) exports DOC/DOCX to PDF/A-1.
  Without Word, only DOCX can be re-typeset by the simplified fallback.
"""

import io
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from invoice_logic import compute_totals, parse_amount

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


class ConversionError(RuntimeError):
    """Raised when a Word document cannot be converted to PDF."""


def _register_fonts() -> Tuple[str, str]:
    """Embed a TrueType font if possible (PDF/A requires embedded fonts)."""
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("InvoiceSans", regular))
                pdfmetrics.registerFont(TTFont("InvoiceSans-Bold", bold))
                return "InvoiceSans", "InvoiceSans-Bold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


def _money(value, currency: str) -> str:
    symbol = "€" if currency == "EUR" else currency
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {symbol}"


def create_invoice_pdf_from_data(data: Dict[str, Any]) -> bytes:
    """Render a clean A4 invoice from structured data (DIN 5008-like layout)."""
    font, bold_font = _register_fonts()
    totals = compute_totals(data)
    cur = totals["currency"]
    esc = lambda v: escape(str(v or ""))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm,
                            title=f"Rechnung {data.get('invoice_id', '')}",
                            author=(data.get("seller") or {}).get("name", ""))

    styles = getSampleStyleSheet()
    ink, muted = colors.HexColor("#1e293b"), colors.HexColor("#64748b")
    normal = ParagraphStyle("N", parent=styles["Normal"], fontName=font, fontSize=9, leading=13,
                            textColor=colors.HexColor("#334155"))
    bold = ParagraphStyle("B", parent=normal, fontName=bold_font, textColor=ink)
    right = ParagraphStyle("R", parent=normal, alignment=2)
    right_bold = ParagraphStyle("RB", parent=bold, alignment=2)
    small = ParagraphStyle("S", parent=normal, fontSize=7, leading=9, textColor=muted)
    title = ParagraphStyle("T", parent=bold, fontSize=18, leading=22)

    seller, buyer, payment = data.get("seller") or {}, data.get("buyer") or {}, data.get("payment") or {}
    seller_addr = f"{esc(seller.get('postcode'))} {esc(seller.get('city'))}".strip()
    buyer_addr = f"{esc(buyer.get('postcode'))} {esc(buyer.get('city'))}".strip()

    story = []
    contact = [esc(seller.get("name")), esc(seller.get("street")), seller_addr]
    if seller.get("vat_id"):
        contact.append(f"USt-IdNr.: {esc(seller['vat_id'])}")
    if seller.get("tax_number"):
        contact.append(f"Steuernr.: {esc(seller['tax_number'])}")
    if seller.get("email"):
        contact.append(f"E-Mail: {esc(seller['email'])}")
    head = Table([[Paragraph(f"<b>{esc(seller.get('name'))}</b>", ParagraphStyle("H", parent=bold, fontSize=14)),
                   Paragraph("<br/>".join(c for c in contact if c), right)]], colWidths=[90 * mm, 80 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [head, Spacer(1, 12 * mm)]

    story.append(Paragraph(" • ".join(x for x in (esc(seller.get("name")), esc(seller.get("street")), seller_addr) if x), small))
    story.append(Spacer(1, 2 * mm))

    meta = [f"<b>Rechnungsnummer:</b> {esc(data.get('invoice_id'))}",
            f"<b>Rechnungsdatum:</b> {esc(data.get('issue_date'))}"]
    if data.get("delivery_date"):
        meta.append(f"<b>Lieferdatum:</b> {esc(data['delivery_date'])}")
    if data.get("due_date"):
        meta.append(f"<b>Fällig am:</b> {esc(data['due_date'])}")
    addr = Table([[Paragraph("<br/>".join(x for x in (f"<b>{esc(buyer.get('name'))}</b>", esc(buyer.get("street")),
                                                       buyer_addr) if x), normal),
                   Paragraph("<br/>".join(meta), right)]], colWidths=[90 * mm, 80 * mm])
    addr.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [addr, Spacer(1, 10 * mm), Paragraph(f"Rechnung {esc(data.get('invoice_id'))}", title), Spacer(1, 3 * mm)]
    if data.get("note"):
        story += [Paragraph(esc(data["note"]), normal), Spacer(1, 3 * mm)]
    story.append(Spacer(1, 3 * mm))

    rows = [[Paragraph(f"<b>{h}</b>", normal if i < 2 else right)
             for i, h in enumerate(("Pos.", "Beschreibung", "Menge", "Einzelpreis", "MwSt.", "Gesamt"))]]
    for item, line in zip(data.get("items") or [], totals["lines"]):
        rows.append([
            Paragraph(line["line_id"], normal),
            Paragraph(f"<b>{esc(item.get('name'))}</b>", normal),
            Paragraph(f"{line['quantity']:g} {esc(item.get('unit') if item.get('unit') not in (None, 'C62') else '')}".strip(), right),
            Paragraph(_money(line["unit_price"], cur), right),
            Paragraph(f"{line['tax_percent']:g} %", right),
            Paragraph(_money(line["net"], cur), right_bold),
        ])
    table = Table(rows, colWidths=[12 * mm, 70 * mm, 22 * mm, 24 * mm, 14 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#cbd5e1")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]))
    story += [table, Spacer(1, 5 * mm)]

    summary = [[Paragraph("Nettobetrag:", right), Paragraph(_money(totals["line_total"], cur), right)]]
    for g in totals["tax_groups"]:
        summary.append([Paragraph(f"zzgl. {g['percent']:g} % USt auf {_money(g['basis'], cur)}:", right),
                        Paragraph(_money(g["tax"], cur), right)])
    summary.append([Paragraph("<b>Bruttobetrag:</b>", right_bold), Paragraph(_money(totals["grand_total"], cur), right_bold)])
    if totals["prepaid"]:
        summary.append([Paragraph("abzgl. Anzahlungen:", right), Paragraph("– " + _money(totals["prepaid"], cur), right)])
    summary.append([Paragraph("<b>Zahlbetrag:</b>", right_bold), Paragraph(f"<b>{_money(totals['due'], cur)}</b>", right_bold)])
    st = Table(summary, colWidths=[90 * mm, 32 * mm], hAlign="RIGHT")
    st.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#94a3b8"))]))
    story += [st, Spacer(1, 10 * mm)]

    pay_lines = []
    if payment.get("iban"):
        bank = f"<b>IBAN:</b> {esc(payment['iban'])}"
        if payment.get("bic"):
            bank += f" &nbsp;|&nbsp; <b>BIC:</b> {esc(payment['bic'])}"
        pay_lines.append(bank)
    if payment.get("terms"):
        pay_lines.append(esc(payment["terms"]))
    if pay_lines:
        story.append(Paragraph("<b>Zahlungsinformationen</b><br/>" + "<br/>".join(pay_lines), normal))
        story.append(Spacer(1, 6 * mm))
    story += [HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")), Spacer(1, 2 * mm),
              Paragraph(f"{esc(seller.get('name'))} • {esc(seller.get('street'))} • {seller_addr}",
                        ParagraphStyle("F", parent=small, alignment=1))]
    doc.build(story)
    return buf.getvalue()


def _convert_with_word(source: Path, target: Path) -> None:
    """Export via Word COM (own hidden Word instance, PDF/A-1 if supported)."""
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise ConversionError("pywin32 ist nicht installiert (pip install pywin32).") from exc

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(source.resolve()), ReadOnly=True, AddToRecentFiles=False,
                                  ConfirmConversions=False)
        try:
            try:
                doc.ExportAsFixedFormat(OutputFileName=str(target.resolve()), ExportFormat=17,
                                        UseISO19005_1=True)
            except Exception:
                doc.SaveAs2(str(target.resolve()), FileFormat=17)
        finally:
            doc.Close(SaveChanges=0)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Microsoft Word konnte die Datei nicht konvertieren: {exc}") from exc
    finally:
        if word is not None:
            try:
                word.Quit(SaveChanges=0)
            except Exception:
                pass
        pythoncom.CoUninitialize()


def convert_docx_to_pdf(docx_path: str) -> Tuple[bytes, bool]:
    """
    Convert DOC/DOCX to PDF. Returns (pdf_bytes, used_word).
    Word gives a pixel-exact PDF. Without Word, a DOCX is re-typeset in a simplified
    layout (used_word=False); a legacy .doc cannot be read without Word.
    """
    source = Path(docx_path)
    word_error = None
    target = Path(tempfile.mkdtemp(prefix="docx2pdf_")) / (source.stem + ".pdf")
    try:
        _convert_with_word(source, target)
        if target.exists():
            return target.read_bytes(), True
    except ConversionError as exc:
        word_error = exc
    finally:
        try:
            target.unlink(missing_ok=True)
            target.parent.rmdir()
        except OSError:
            pass

    if source.suffix.lower() == ".docx":
        import docx
        from extractor import extract_invoice_data_from_text
        text = "\n".join(p.text for p in docx.Document(str(source)).paragraphs if p.text.strip())
        return create_invoice_pdf_from_data(extract_invoice_data_from_text(text)), False
    raise ConversionError(
        f"Alte .doc-Dateien benötigen Microsoft Word zur Konvertierung ({word_error}). "
        "Bitte als PDF oder DOCX speichern und erneut hochladen.")
