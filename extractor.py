"""
extractor.py - Reads invoice data from PDF / DOCX documents.

Strategy
  * Letterhead, sender line (DIN 5008 "Absenderzeile") and recipient block come
    from pypdf's plain text, where the columns of a letterhead stay separable.
  * Amounts and the item table come from pypdf's *layout* text, which keeps the
    table rows on one line.
  * Nothing is invented. A field that cannot be found stays empty and is
    reported in `extraction_notes`; invoice_logic.validate_invoice() then tells
    the user what is missing instead of the tool filling in dummy data.
  * A PDF that already contains Factur-X/ZUGFeRD XML is read from that XML.
"""

import io
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import facturx
import pypdf
from lxml import etree

from ai_invoice_extractor import (
    InvoiceReviewError,
    ai_review_enabled,
    review_invoice_with_ai,
    should_review_with_ai,
)
from invoice_logic import (
    D,
    clean_id,
    iban_valid,
    parse_amount,
    parse_date,
    round2,
    validate_invoice,
)

# ------------------------------------------------------------------ patterns

AMOUNT = (r"-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"          # 1.250,50  /  595,00
          r"|-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}")         # 1,250.50  /  595.00
AMOUNT_RE = re.compile(AMOUNT)
DATE_RE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})\b")
EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
LEGAL_FORM_RE = re.compile(
    r"\b(GmbH(?:\s*&\s*Co\.?\s*KG)?|mbH|AG|KG|OHG|UG(?:\s*\(haftungsbeschränkt\))?|GbR|SE|"
    r"e\.\s?K\.|e\.\s?V\.|eG|Ltd\.?|Inc\.?|S\.A\.|B\.V\.)(?=\W|$)")
SUMMARY_WORDS = re.compile(
    r"(?i)gesamtsumme|zwischensumme|^summe|netto|brutto|mehrwertsteuer|mwst|umsatzsteuer|"
    r"\bust\b|zahlbetrag|anzahlung|abschlag|skonto|zahlung|endbetrag|rechnungsbetrag|übertrag|"
    r"gesamtbetrag|vorauszahlung|total|subtotal")
NET_RE = re.compile(
    r"(?i)^\W*(?:Nettobetrag|Netto(?:summe)?\b|Summe\s+netto|Gesamt(?:betrag|summe)?\s*\(?netto\)?|"
    r"Zwischensumme(?:\s+netto)?|Net\s+(?:total|amount)|Subtotal)")
GROSS_RE = re.compile(
    r"(?i)^\W*(?:Bruttobetrag|Brutto(?:summe)?\b|Gesamt(?:betrag|summe)?\s*\(?brutto\)?|Gesamtbetrag|"
    r"Rechnungsbetrag|Endbetrag|Total\s+(?:gross|amount))")
DUE_LABEL = (r"(?:Zahlbetrag|Zu\s+zahlen(?:der\s+Betrag)?|Zahlungsbetrag|Restbetrag|Restforderung|"
             r"Offener\s+Betrag|Fälliger\s+Betrag|Amount\s+due)")
COUNTRY_NAMES = {
    "deutschland": "DE", "germany": "DE", "österreich": "AT", "austria": "AT",
    "schweiz": "CH", "switzerland": "CH", "frankreich": "FR", "italien": "IT",
    "niederlande": "NL", "belgien": "BE", "luxemburg": "LU", "polen": "PL",
    "tschechien": "CZ", "dänemark": "DK", "spanien": "ES",
}


# ------------------------------------------------------------------ text access

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Plain text (reading order, letterhead columns separated by wide gaps)."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(filter(None, (p.extract_text() for p in reader.pages)))


def extract_layout_text_from_pdf(pdf_bytes: bytes) -> str:
    """Layout text (table rows stay on one line)."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text(extraction_mode="layout") or "")
        except Exception:
            parts.append("")
    return "\n".join(parts)


def extract_text_from_docx(docx_bytes: bytes) -> str:
    import docx
    doc = docx.Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    tables = []
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                tables.append("   ".join(cells))
    return "\n".join(paragraphs + tables)


# ------------------------------------------------------------------ small helpers

def _cols(line: str) -> List[str]:
    """Split a line into columns at wide gaps or bullet separators."""
    parts = re.split(r"\s{3,}|\s[•·|]\s|,\s*(?=\d{5}\s)", line.strip())
    return [p.strip() for p in parts if p.strip()]


def _smart_title(s: str) -> str:
    """UPPERCASE address parts -> Title Case, keeping legal forms intact."""
    if not s.isupper():
        return s
    t = s.title()
    for wrong, right in (("Gmbh", "GmbH"), ("Mbh", "mbH"), ("Kg", "KG"), ("Ag", "AG"),
                         ("Ug", "UG"), ("Ohg", "OHG"), ("Gbr", "GbR"), ("Se", "SE")):
        t = re.sub(rf"\b{wrong}\b", right, t)
    return t


def _fmt_date(value: str) -> str:
    d = parse_date(value)
    return d.strftime("%d.%m.%Y") if d else ""


def _doc_number(rest: str) -> str:
    """'CH 008/2026' / '123/2026 vom 14.04.2026' -> the document number (1-2 tokens, must contain a digit)."""
    rest = rest.strip()
    m = re.match(r"([A-Za-z0-9][\w\-/.]*)(?:\s+([\w\-/.]*\d[\w\-/.]*))?", rest)
    if not m:
        return ""
    first, second = m.group(1), m.group(2)
    if not re.search(r"\d", first) and second and not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", second):
        return f"{first} {second}".rstrip(".-/")
    return first.rstrip(".-/")


def _dec(text: str) -> Decimal:
    return parse_amount(text)


def _last_amount(line: str) -> Optional[Decimal]:
    found = AMOUNT_RE.findall(line)
    return _dec(found[-1]) if found else None


def _normalized_plain_lines(plain: str) -> List[str]:
    """Plain text lines; a lone 'EUR' / amount line is glued to the label above it."""
    out: List[str] = []
    for raw in plain.splitlines():
        s = raw.strip()
        if not s:
            continue
        if out and re.fullmatch(rf"(?:[A-Z]{{3}}|€|{AMOUNT})", s):
            out[-1] += "   " + s
        else:
            out.append(raw.rstrip())
    return out


# ------------------------------------------------------------------ party blocks

def _find_sender_line(lines: List[str]) -> Tuple[Optional[int], Dict[str, str]]:
    """DIN 5008 sender line: 'NAME   STREET 12   12345 CITY' above the recipient."""
    for i, line in enumerate(lines[:30]):
        cells = _cols(line)
        if len(cells) < 3:
            continue
        zip_cell = next((c for c in cells if re.fullmatch(r"(?:[A-Z]{1,2}[-\s])?\d{4,5}\s+\S.*", c)), None)
        street_cell = next((c for c in cells[1:] if re.search(r"\d", c) and c is not zip_cell), None)
        if zip_cell and street_cell:
            m = re.match(r"(?:[A-Z]{1,2}[-\s])?(\d{4,5})\s+(.+)", zip_cell)
            return i, {"name": cells[0], "street": _smart_title(street_cell),
                       "postcode": m.group(1), "city": _smart_title(m.group(2).strip())}
    return None, {}


def _find_heading(lines: List[str], sender_idx: Optional[int]) -> Tuple[Optional[int], str]:
    """Invoice heading line ('Rechnung Nr. ...') below the sender line: (index, number)."""
    for i, line in enumerate(lines):
        if sender_idx is not None and i <= sender_idx:
            continue
        m = re.search(r"(?i)\b(?:rechnung|invoice|gutschrift)\b[^\n]*?(?:nr\.?|nummer|no\.?|#)\s*[:.]?\s*(\S.*)", line)
        if m and not re.search(r"(?i)anzahlung|abschlag|vom\s+\d", line):
            return i, _doc_number(m.group(1))
    return None, ""


def strip_recipient_block(plain: str) -> str:
    """The text without the recipient block (between sender line and heading), blank lines dropped.

    Used when the recipient could not be parsed, so it cannot be masked by value.
    """
    lines = [l.rstrip() for l in plain.splitlines() if l.strip()]
    sender_idx, _ = _find_sender_line(lines)
    heading_idx, _ = _find_heading(lines, sender_idx)
    start = (sender_idx + 1) if sender_idx is not None else 0
    end = heading_idx if heading_idx is not None else min(len(lines), start + 12)
    return "\n".join(lines[:start] + lines[end:])


def _seller_name_from_letterhead(letterhead: List[str], sender_name: str) -> str:
    """Join column 1 of the letterhead until the legal form ('... GmbH') is reached."""
    parts: List[str] = []
    for line in letterhead[:6]:
        cells = _cols(line)
        if not cells:
            continue
        first = re.sub(r"\s+", " ", cells[0])
        if re.match(r"(?i)(ust|vat|www\.|tel|t\.|f\.|fax|iban|bic)", first):
            break
        parts.append(first)
        if LEGAL_FORM_RE.search(first):
            break
    name = re.sub(r"(?<=-) (?=\w)", " ", " ".join(parts)).strip()
    if name and LEGAL_FORM_RE.search(name):
        if not sender_name or name.split()[0].lower() == sender_name.split()[0].lower():
            return name
    return _smart_title(sender_name) if sender_name else name


def _parse_buyer(lines: List[str], start: int, end: int) -> Tuple[Dict[str, str], int]:
    """Recipient block between the sender line and the invoice heading."""
    buyer = {"name": "", "street": "", "postcode": "", "city": "", "country": "",
             "vat_id": "", "email": "", "phone": ""}
    zip_idx = None
    for j in range(start, end):
        cells = _cols(lines[j])
        if cells and re.fullmatch(r"(?:[A-Z]{1,2}[-\s])?\d{4,5}\s+[^\d\s].*", cells[0]):
            zip_idx = j
            m = re.match(r"(?:[A-Z]{1,2}[-\s])?(\d{4,5})\s+(.+)", cells[0])
            buyer["postcode"], buyer["city"] = m.group(1), _smart_title(m.group(2).strip())
            break
    if zip_idx is None:
        for j in range(start, end):                       # e.g. anonymised block that only names the country
            for cell in _cols(lines[j]):
                if cell.strip().lower() in COUNTRY_NAMES:
                    buyer["country"] = COUNTRY_NAMES[cell.strip().lower()]
                    return buyer, -1
        return buyer, -1

    block = [(_cols(lines[k]) or [""])[0] for k in range(start, zip_idx)]
    block = [b for b in block if b]
    if block:
        has_street = len(block) >= 2 and (re.search(r"\d", block[-1]) or re.search(r"(?i)str|weg|platz|allee|gasse|ring|damm|ufer", block[-1]))
        buyer["street"] = block[-1] if has_street else ""
        name_lines = block[:-1] if has_street else block
        buyer["name"] = ", ".join(name_lines)

    for k in range(zip_idx + 1, end):
        text = lines[k]
        em = EMAIL_RE.search(text)
        if em and not buyer["email"]:
            buyer["email"] = em.group(0)
        pm = re.search(r"\bT(?:el)?\.?:?\s*(\+?\d[\d\s()/\-]{5,})", text)
        if pm and not buyer["phone"]:
            buyer["phone"] = re.sub(r"\s+", " ", pm.group(1)).strip()
        low = text.strip().lower()
        if low in COUNTRY_NAMES:
            buyer["country"] = COUNTRY_NAMES[low]
    return buyer, zip_idx


# ------------------------------------------------------------------ banking / ids

def _find_iban(text: str) -> Tuple[str, int, int]:
    """First checksum-valid IBAN. Returns (iban, start, end) positions in text."""
    for m in re.finditer(r"(?:IBAN[ \t:.]*)?([A-Z]{2}\d{2}(?:[ \t]?[0-9A-Z]{1,4}){2,9})", text):
        tokens = m.group(1).split()
        for n in range(len(tokens), 0, -1):
            candidate = "".join(tokens[:n])
            if iban_valid(candidate):
                return candidate, m.start(), m.end()
    return "", -1, -1


def _find_bic(text: str, after: int, before: int) -> str:
    segment = text[after:before] if before > after else text[after:]
    m = re.search(r"(?:BIC|S\s*WIFT)[\s:./-]*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", segment)
    return m.group(1) if m else ""


def _find_vat_ids(text: str) -> List[Tuple[str, str]]:
    """[(vat_id, 'own'|'buyer')] in document order."""
    found = []
    for m in re.finditer(r"(?i)(Ihre\s+)?(?:USt[\s.\-]*I[dD][\s.\-]*(?:Nr\.?|Nummer)?|VAT[\s\-]*(?:ID|No\.?|Reg\.?)?|Umsatzsteuer[\s\-]*Identifikationsnummer)[\s.:#-]*([A-Z]{2}(?:[ ]?[0-9A-Z]){8,12})", text):
        vid = clean_id(m.group(2))
        found.append((vid, "buyer" if m.group(1) else "own"))
    return found


# ------------------------------------------------------------------ amounts

def _parse_summary(lines: List[str], notes: List[str]) -> Dict[str, Any]:
    """Net / VAT / gross / payable and prepayment blocks from the document body."""
    net = gross = due = None
    rates: List[Tuple[Decimal, Decimal]] = []     # (rate, vat amount) of the main block
    prepayments: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    net_re, gross_re = NET_RE, GROSS_RE
    due_re = re.compile(r"(?i)^\W*" + DUE_LABEL + r"\b[\s:]*(?:[A-Z]{3}|€)?\s*" + f"({AMOUNT})" + r"\s*$")
    vat_re = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*%\s*(?:[\wäöüß\-]+\s+){0,3}?(?:Mehrwertsteuer|MwSt\.?|Umsatzsteuer|USt\.?|VAT)")
    prepaid_re = re.compile(r"(?i)(Anzahlungs-?\s*Rechnung|Abschlags-?\s*Rechnung|Abschlagszahlung|Vorauszahlung|Teilzahlung|Anzahlung)\b")

    for line in lines:
        s = line.strip()
        if not s:
            continue
        amt = _last_amount(s)

        if prepaid_re.search(s) and amt is not None and re.match(r"^\W*(?:\./\.|abzgl|abzüglich|abz\.|\-|–)", s, re.IGNORECASE):
            idm = re.search(r"Nr\.?\s*(\S.*)", s)
            dm = re.search(r"vom\s+(\d{1,2}\.\d{1,2}\.\d{4})", s)
            cur = {"id": _doc_number(idm.group(1)) if idm else "", "date": _fmt_date(dm.group(1)) if dm else "",
                   "net": amt, "vat": None, "gross": None, "rate": None}
            prepayments.append(cur)
            continue

        if due_re.match(s):
            if due is None:
                due = _dec(due_re.match(s).group(1))
            cur = None
            continue

        vm = vat_re.search(s)
        if vm and amt is not None:
            rate = _dec(vm.group(1))
            if cur is not None:
                cur["vat"], cur["rate"] = amt, rate
            else:
                rates.append((rate, amt))
            continue

        if cur is not None:
            if gross_re.match(s) and amt is not None:
                cur["gross"] = amt
            continue

        if net is None and net_re.match(s) and amt is not None:
            net = amt
        elif gross is None and gross_re.match(s) and amt is not None:
            gross = amt

    prepaid_total = D(0)
    for p in prepayments:
        if p["gross"] is None:
            vat = p["vat"]
            if vat is None and rates:
                vat = round2(p["net"] * rates[0][0] / D(100))
                notes.append("Anzahlung: Umsatzsteuer wurde aus dem Hauptsteuersatz berechnet.")
            p["gross"] = round2(p["net"] + (vat or D(0)))
        prepaid_total += p["gross"]

    return {"net": net, "gross": gross, "due": due, "rates": rates,
            "prepayments": prepayments, "prepaid_total": round2(prepaid_total)}


# ------------------------------------------------------------------ items

def _table_cells(line: str) -> List[str]:
    """Split a layout line at wide gaps; a lone currency cell is glued to the amount after it."""
    cells = re.split(r"\s{2,}", line.strip())
    if len(cells) == 1:                                   # plain text: "label CHF 860,00" on single spaces
        m = re.match(rf"^(.*\S)\s+((?:[A-Z]{{3}}|€)\s*{AMOUNT})$", cells[0])
        if m:
            cells = [m.group(1), m.group(2)]
    merged: List[str] = []
    i = 0
    while i < len(cells):
        if re.fullmatch(r"[A-Z]{3}|€", cells[i]) and i + 1 < len(cells) and re.fullmatch(AMOUNT, cells[i + 1]):
            merged.append(f"{cells[i]} {cells[i + 1]}")
            i += 2
        else:
            merged.append(cells[i])
            i += 1
    return merged


def _plain_row_cells(s: str) -> Optional[List[str]]:
    """'1 1 807 827 Modell li. ob. 2.549,00' -> [pos, '1', '807', '827', 'Modell li. ob.', '2.549,00']."""
    m = re.match(rf"^(\d{{1,3}})\s+(.+?)\s+((?:(?:[A-Z]{{3}}|€)\s*)?{AMOUNT})(?:\s+((?:(?:[A-Z]{{3}}|€)\s*)?{AMOUNT}))?$", s)
    if not m:
        return None
    cells, text_run = [m.group(1)], []
    for tok in m.group(2).split():
        if re.fullmatch(r"\d+(?:,\d+)?", tok):
            if text_run:
                cells.append(" ".join(text_run))
                text_run = []
            cells.append(tok)
        else:
            text_run.append(tok)
    if text_run:
        cells.append(" ".join(text_run))
    return cells + [g for g in m.groups()[2:] if g]


def _parse_items(lines: List[str], product: str, tax_percent: Decimal, notes: List[str]) -> List[Dict[str, Any]]:
    """Table rows ('Pos. ... EUR') plus free rows ('Fracht ... EUR 3.420,00')."""
    items: List[Dict[str, Any]] = []
    header: List[str] = []
    qty_idx: Optional[int] = None
    skipped: List[str] = []
    started = False
    running = D(0)

    for line in lines:
        s = line.strip()
        if not s:
            continue
        cells = _table_cells(s)

        # table header (re-detected on every page); plain text keeps it on single spaces
        if len(cells) == 1 and re.match(r"(?i)^pos\.?\s+\S", s) and \
                re.search(r"(?i)\b(stück|stk|menge|anzahl|anz)\b", s):
            cells = s.split()
        if re.match(r"(?i)^pos\.?$", cells[0]) and len(cells) >= 3:
            header = [c.rstrip(":") for c in cells]
            qty_idx = next((i for i, c in enumerate(header)
                            if re.fullmatch(r"(?i)(stück|stk|menge|anzahl|anz)\.?", c)), None)
            started = True
            continue

        # end of the item region: first net-total line (or the gross total when there is no net line)
        if started and (NET_RE.match(s) or (items and GROSS_RE.match(s))):
            break
        if not started:
            continue
        if header and len(cells) <= 2:
            plain_cells = _plain_row_cells(s)
            if plain_cells:
                cells = plain_cells

        # numbered table row
        if header and re.fullmatch(r"\d{1,3}", cells[0]):
            amounts = []
            for c in reversed(cells[1:]):
                am = re.fullmatch(rf"(?:[A-Z]{{3}}\s+|€\s*)?({AMOUNT})", c)
                if not am:
                    break
                amounts.append(_dec(am.group(1)))
            amounts.reverse()
            if not amounts:
                skipped.append(cells[0])
                continue
            total = amounts[-1]
            body = cells[1:len(cells) - len(amounts)]
            aligned = len(cells) >= len(header)
            qty = D(1)
            if len(amounts) >= 2 and amounts[-2] > 0:
                qty = round(total / amounts[-2], 4)           # quantity = total / unit price
            elif qty_idx is not None and aligned and qty_idx - 1 < len(body) \
                    and re.fullmatch(r"\d+(?:,\d+)?", body[qty_idx - 1]):
                qty = _dec(body[qty_idx - 1])
            if qty <= 0:
                skipped.append(cells[0])
                continue
            price = amounts[-2] if len(amounts) >= 2 and amounts[-2] > 0 else round(total / qty, 4)

            if aligned and qty_idx is not None:
                lead = [f"{'Pos.' if i == 0 else header[i]} {cells[i]}" for i in range(min(qty_idx, len(cells) - 1))
                        if cells[i]]
                rest = cells[qty_idx + 1:len(cells) - len(amounts)]
            else:
                lead = [f"Pos. {cells[0]}"]
                rest = list(body)
                for k, c in enumerate(rest):                  # drop the quantity cell from the description
                    if re.fullmatch(r"\d+(?:,\d+)?", c) and _dec(c) == qty:
                        del rest[k]
                        break
            tail, numeric = [], []
            for c in rest:
                if re.fullmatch(r"\d+(?:,\d+)?", c):
                    numeric.append(c)
                else:
                    if numeric:
                        tail.append(" x ".join(numeric))
                        numeric = []
                    tail.append(c)
            if numeric:
                tail.append(" x ".join(numeric))
            detail = ", ".join(lead) + (": " + ", ".join(tail) if tail else "")
            name = f"{product} – {detail}" if product else detail
            items.append({"name": name, "quantity": float(qty), "unit": "C62",
                          "unit_price": float(price), "tax_percent": float(tax_percent)})
            running += round2(qty * price)
            continue

        # free row: "<text>   EUR  amount"  or  "<qty> <unit> <text>  á EUR p  EUR total"
        if len(cells) >= 2 and re.fullmatch(rf"(?:[A-Z]{{3}}|€)\s*({AMOUNT})", cells[-1]):
            total = _dec(AMOUNT_RE.findall(cells[-1])[-1])
            label = cells[0]
            if SUMMARY_WORDS.search(label) or total <= 0 or abs(total - running) < D("0.005"):
                continue                                      # summary line or running subtotal
            discount = bool(re.match(r"(?i)^\W*(?:\./\.|abzgl\.?|abzüglich|abz\.|[-–])\s*\S", label))
            if discount:
                label = re.sub(r"(?i)^\W*(?:\./\.|abzgl\.?|abzüglich|abz\.|[-–])\s*", "", label)
            qty, unit, price = D(1), "C62", total
            if len(cells) >= 3 and re.fullmatch(rf"(?:á|à|a|je|@)\s*(?:[A-Z]{{3}}|€)\s*({AMOUNT})", cells[-2]):
                price = _dec(AMOUNT_RE.findall(cells[-2])[-1])
                qm = re.match(r"(\d+(?:,\d+)?)\s*(Stück|Stk\.?|St\.?|Std\.?|h|m|kg|Pauschal|psch\.?)?\s+(.+)", label, re.IGNORECASE)
                if qm:
                    qty = _dec(qm.group(1))
                    unit = qm.group(2) or "C62"
                    label = re.sub(r"\s*(?:á|à)\s*$", "", qm.group(3)).strip()
                    if qty > 0 and round2(qty * price) != total:
                        price = round(total / qty, 4)
            if discount:
                qty = -qty                                    # allowance as negative line (valid in EN 16931)
            items.append({"name": label.strip(" ,"), "quantity": float(qty), "unit": unit,
                          "unit_price": float(price), "tax_percent": float(tax_percent)})
            running += round2(qty * price)

    if skipped:
        notes.append("Positionen ohne Preis/Menge übersprungen: " + ", ".join(skipped) + ".")
    return items


def _detect_tax_exemption(text: str) -> Tuple[str, str]:
    """('G'|'K'|'AE'|'E', reason sentence) when the document states a tax-free supply, else ('', '')."""
    rules = [
        ("AE", r"§\s*13b|Steuerschuldnerschaft|reverse[\s-]*charge"),
        ("K", r"§\s*6a|innergemeinschaftliche\s+(?:Lieferung|Warenlieferung)"),
        ("G", r"§\s*4\s*Nr\.?\s*1\s*a|Ausfuhrlieferung|§\s*6\s*Abs"),
        ("E", r"steuerfrei|steuerbefreit"),
    ]
    for category, pattern in rules:
        for line in text.splitlines():
            if re.search(pattern, line, re.IGNORECASE) and re.search(r"(?i)steuerfrei|steuerbefreit|reverse|13b|Ausfuhr", line):
                return category, re.sub(r"\s+", " ", line).strip().rstrip(".")
    return "", ""


# ------------------------------------------------------------------ main extraction

def extract_invoice_data_from_text(plain: str, layout: Optional[str] = None,
                                   notes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Heuristic extraction for German invoices. Unknown fields stay empty."""
    notes = notes if notes is not None else []
    plain_lines = [l.rstrip() for l in plain.splitlines() if l.strip()]
    body_lines = layout.splitlines() if layout and layout.strip() else _normalized_plain_lines(plain)
    full_text = "\n".join(plain_lines)

    data: Dict[str, Any] = {
        "invoice_id": "", "issue_date": "", "due_date": "", "delivery_date": "",
        "currency": "EUR", "note": "", "buyer_reference": "",
        "seller": {"name": "", "street": "", "postcode": "", "city": "", "country": "DE",
                   "vat_id": "", "tax_number": "", "email": "", "phone": ""},
        "buyer": {"name": "", "street": "", "postcode": "", "city": "", "country": "",
                  "vat_id": "", "email": "", "phone": ""},
        "payment": {"iban": "", "bic": "", "account_holder": "", "terms": "",
                    "skonto_days": 0, "skonto_percent": 0},
        "items": [], "prepaid_amount": 0, "preceding_invoices": [], "stated": {},
    }

    # ---- seller
    sender_idx, sender = _find_sender_line(plain_lines)
    letterhead = plain_lines[:sender_idx] if sender_idx else plain_lines[:8]
    seller = data["seller"]
    if sender:
        seller.update({k: sender[k] for k in ("street", "postcode", "city")})
    seller["name"] = _seller_name_from_letterhead(letterhead, sender.get("name", ""))
    head_text = "\n".join(letterhead)
    em = EMAIL_RE.search(head_text)
    seller["email"] = em.group(0) if em else ""
    pm = re.search(r"\bT(?:el)?\.?:?\s*(\+?\d[\d\s()/\-]{5,})", head_text)
    seller["phone"] = re.sub(r"\s+", " ", pm.group(1)).strip() if pm else ""
    if not sender:
        notes.append("Absenderadresse konnte nicht eindeutig gelesen werden – bitte Verkäuferdaten prüfen.")

    vat_ids = _find_vat_ids(full_text)
    own = next((v for v, kind in vat_ids if kind == "own"), "")
    seller["vat_id"] = own
    tm = re.search(r"(?i)(?:Steuer-?Nr\.?|Steuernummer|St\.?-?Nr\.?)[\s:.]*(\d{2,3}\s?/\s?\d{3}\s?/\s?\d{4,5})", full_text)
    seller["tax_number"] = re.sub(r"\s+", "", tm.group(1)) if tm else ""

    # ---- heading / invoice number
    heading_idx, heading_number = _find_heading(plain_lines, sender_idx)
    if heading_idx is not None:
        data["invoice_id"] = heading_number
    if not data["invoice_id"]:
        notes.append("Rechnungsnummer nicht gefunden.")
    if re.search(r"(?i)\bgutschrift\b|\bstorno", full_text):
        notes.append("Das Dokument enthält 'Gutschrift/Storno' – Gutschriften (Typ 381) werden nicht unterstützt.")

    # ---- buyer
    start = (sender_idx + 1) if sender_idx is not None else 0
    end = heading_idx if heading_idx is not None else min(len(plain_lines), start + 12)
    buyer, zip_idx = _parse_buyer(plain_lines, start, end)
    if zip_idx < 0:
        notes.append("Empfängeradresse konnte nicht gelesen werden – bitte Käuferdaten prüfen.")
    if not buyer["country"] and buyer["postcode"]:
        if re.fullmatch(r"\d{5}", buyer["postcode"]):
            buyer["country"] = "DE"
            notes.append("Land des Käufers wurde aus der 5-stelligen PLZ als DE angenommen.")
    if not buyer["name"]:
        for line in plain_lines:
            hm = re.search(r"Seite\s+\d+\s+zur\s+[\w\-]*[Rr]echnung\s+Nr\.?[^–—\n]*?[–—]+\s*([^–—\n]+?)\s*(?:[–—]|$)", line)
            if hm and not hm.group(1).lower().startswith("objekt"):
                buyer["name"] = hm.group(1).strip()
                notes.append("Käufername aus dem Seitenkopf übernommen – bitte Käuferdaten prüfen.")
                break
    buyer_vat = next((v for v, kind in vat_ids if kind == "buyer"), "")
    buyer["vat_id"] = buyer_vat
    data["buyer"] = buyer

    # ---- dates
    region = "\n".join(plain_lines[start:(heading_idx or end) + 1])
    for key, pattern in (
        ("issue_date", r"(?i)(?:Rechnungsdatum|Datum)\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})"),
        ("delivery_date", r"(?i)(?:Lieferdatum|Leistungsdatum|Liefer(?:ung)?\s+am)[^\n\d]*?(\d{1,2}\.\d{1,2}\.\d{4})"),
        ("due_date", r"(?i)(?:Zahlbar\s+bis|Fällig(?:keit(?:sdatum)?)?(?:\s+am)?|Zahlungsziel)\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{4})"),
    ):
        m = re.search(pattern, full_text)
        if m:
            data[key] = _fmt_date(m.group(1))
    if not data["issue_date"]:
        m = DATE_RE.search(region) or DATE_RE.search(full_text)
        if m:
            data["issue_date"] = _fmt_date(m.group(1))
    if not data["issue_date"]:
        notes.append("Rechnungsdatum nicht gefunden.")

    # ---- currency: the code that stands next to amounts (not a substring such as IMPORTEUR)
    codes = re.findall(rf"\b(EUR|CHF|USD|GBP)\s*(?={AMOUNT})|(?<=\d)\s*(EUR|CHF|USD|GBP)\b", full_text)
    counted = [c for pair in codes for c in pair if c]
    if counted:
        data["currency"] = max(set(counted), key=counted.count)
    elif "€" in full_text:
        data["currency"] = "EUR"
    elif "$" in full_text:
        data["currency"] = "USD"
    elif "£" in full_text:
        data["currency"] = "GBP"

    # ---- bank (first checksum-valid IBAN = the letterhead's main account)
    iban, i_start, i_end = _find_iban(full_text)
    if data["currency"] != "EUR":
        # e.g. "... auf unser CHF-Konto bei: Bank X  IBAN CH.." -> use the account named for the currency
        cm = re.search(rf"(?i){data['currency']}[\s\-]*Konto", full_text)
        if cm:
            alt = _find_iban(full_text[cm.end():])
            if alt[0]:
                iban, i_start, i_end = alt[0], cm.end() + alt[1], cm.end() + alt[2]
    payment = data["payment"]
    if iban:
        payment["iban"] = iban
        nxt = _find_iban(full_text[i_end:])
        before = i_end + nxt[1] if nxt[0] else len(full_text)
        payment["bic"] = _find_bic(full_text, i_end, before)
        payment["account_holder"] = seller["name"]
    else:
        notes.append("Keine gültige IBAN gefunden.")

    # ---- tax treatment: explicit "steuerfrei" statements (export, intra-EU supply, reverse charge)
    tax_cat, tax_reason = _detect_tax_exemption(full_text)

    # ---- amounts + items. Layout text first; plain text if that came out garbled or empty.
    pm = re.search(r"(?im)^\s*Bezeichnung\s*:?\s+(\S.*?)\s*$", full_text)
    product = re.sub(r"\s{2,}", " ", pm.group(1)).strip() if pm else ""

    def analyse(lines):
        local: List[str] = []
        summ = _parse_summary(lines, local)
        if tax_cat:
            rate = D(0)
        else:
            rate = summ["rates"][0][0] if summ["rates"] else D(19)
        found = _parse_items(lines, product, rate, local)
        return summ, found, local, rate

    candidates = [_normalized_plain_lines(plain)]          # plain text has the cleaner spacing
    if layout and layout.strip():
        candidates.append(body_lines)                      # layout text keeps wide-gap table columns
    def consistent(result):
        summ, found = result[0], result[1]
        reference = summ["net"] if summ["net"] is not None else (summ["gross"] if tax_cat else None)
        total = sum((round2(D(str(i["quantity"])) * D(str(i["unit_price"]))) for i in found), D(0))
        return bool(found) and reference is not None and abs(total - reference) < D("0.005")

    analysed = [(analyse(lines), lines) for lines in candidates]
    chosen = next((a for a in analysed if consistent(a[0])), None)       # items add up to the printed net
    if chosen is None:
        chosen = max(analysed, key=lambda a: (a[0][0]["net"] is not None or a[0][0]["gross"] is not None, len(a[0][1])))
    best = (0, chosen[0], chosen[1])
    _, (summary, found_items, local_notes, tax_percent), body_lines = best
    notes.extend(local_notes)
    rates = summary["rates"]

    if tax_cat:
        data["tax_category"], data["tax_exemption_reason"] = tax_cat, tax_reason
        notes.append(f"Steuerfreie Lieferung erkannt (Kategorie {tax_cat}) – Befreiungsgrund aus dem Dokument übernommen.")
        if summary["net"] is None and summary["gross"] is not None:
            summary["net"] = summary["gross"]
    elif not rates:
        notes.append("Kein Umsatzsteuersatz im Dokument gefunden – 19 % angenommen.")
    elif len({r for r, _ in rates}) > 1:
        notes.append("Mehrere Steuersätze erkannt – bitte den Steuersatz je Position prüfen.")

    data["items"] = found_items
    if not data["items"] and summary["net"] is not None:
        data["items"] = [{"name": "Leistungen laut Rechnung", "quantity": 1.0, "unit": "C62",
                          "unit_price": float(summary["net"]), "tax_percent": float(tax_percent)}]
        notes.append("Positionen konnten nicht erkannt werden – es wurde eine Sammelposition aus dem Nettobetrag erzeugt.")
    elif not data["items"]:
        notes.append("Keine Positionen erkannt – bitte im Reiter 'Positionen' erfassen.")

    # ---- prepayments
    if summary["prepayments"]:
        data["prepaid_amount"] = float(summary["prepaid_total"])
        data["preceding_invoices"] = [{"id": p["id"], "date": p["date"]} for p in summary["prepayments"] if p["id"]]

    # ---- stated totals (used to flag inconsistencies, never to override)
    stated = {}
    if summary["net"] is not None:
        stated["net"] = float(summary["net"])
    if rates:
        stated["tax"] = float(sum((a for _, a in rates), D(0)))
    elif tax_cat:
        stated["tax"] = 0.0
    if summary["gross"] is not None:
        stated["gross"] = float(summary["gross"])
    if summary["due"] is not None:
        stated["due"] = float(summary["due"])
    data["stated"] = stated

    # ---- payment terms / Skonto
    body_text = "\n".join(body_lines)
    sk = re.search(r"(?i)(\d+)\s*Tage?n?[,;]?\s*(\d+(?:[.,]\d+)?)\s*%\s*Skonto", body_text) or \
         re.search(r"(?i)Skonto\s*(\d+(?:[.,]\d+)?)\s*%[^\n]*?(\d+)\s*Tage", body_text)
    if sk:
        if sk.re.pattern.startswith("(?i)(\\d+)"):
            payment["skonto_days"], payment["skonto_percent"] = int(sk.group(1)), float(_dec(sk.group(2)))
        else:
            payment["skonto_days"], payment["skonto_percent"] = int(sk.group(2)), float(_dec(sk.group(1)))
    if payment["skonto_percent"] and "due" in stated and "gross" in stated:
        base = D(str(stated["gross"])) - D(str(data["prepaid_amount"]))
        after_skonto = round2(base * (D(100) - D(str(payment["skonto_percent"]))) / D(100))
        if abs(D(str(stated["due"])) - after_skonto) <= D("0.01"):
            del stated["due"]                              # "Zahlbetrag" printed after Skonto deduction

    tm = re.search(r"(?im)^\s*(?:ZAHLUNG|Zahlungsbedingungen?|Zahlungsziel)\s*:\s*(\S.*?)\s*$", body_text)
    if tm:
        payment["terms"] = re.sub(r"\s{2,}", " ", tm.group(1))
    else:
        tm = re.search(r"(?i)(zahlbar\s+(?:innerhalb|binnen|sofort)[^.\n]*)", body_text)
        if tm:
            payment["terms"] = tm.group(1).strip()

    # ---- references
    om = re.search(r"(?im)Auftrags-?Nr\.?\s*:\s*(\S.*?)\s*$", body_text)
    obj = re.search(r"(?im)^\s*Objekt\s*:\s*(\S.*?)\s*$", body_text)
    note_parts = []
    if om:
        note_parts.append("Auftrags-Nr.: " + re.sub(r"-\s+", "-", re.sub(r"\s{2,}", " ", om.group(1))))
    if obj:
        note_parts.append(f"Objekt: {re.sub(r'\s{2,}', ' ', obj.group(1))}")
    if product:
        note_parts.append(f"Bezeichnung: {product}")
    data["note"] = " | ".join(note_parts)
    rm = re.search(r"(?im)Ihre\s+(?:Bestell(?:ung|-?Nr)?|Referenz|Best\.-?Nr)\.?\s*:\s*(\S+)", body_text)
    if rm:
        data["buyer_reference"] = rm.group(1)

    return data


# ------------------------------------------------------------------ existing ZUGFeRD

CII_NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
}


def invoice_data_from_cii(xml_bytes: bytes) -> Dict[str, Any]:
    """Read the editable invoice fields back out of a Factur-X / ZUGFeRD XML."""
    root = etree.fromstring(xml_bytes)

    def x(node, path):
        r = node.xpath(path, namespaces=CII_NS)
        return (r[0].text or "").strip() if r else ""

    def party(path):
        n = root.xpath(path, namespaces=CII_NS)
        if not n:
            return {}
        n = n[0]
        vat = n.xpath(".//ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']", namespaces=CII_NS)
        fc = n.xpath(".//ram:SpecifiedTaxRegistration/ram:ID[@schemeID='FC']", namespaces=CII_NS)
        return {
            "name": x(n, "ram:Name"), "street": x(n, "ram:PostalTradeAddress/ram:LineOne"),
            "postcode": x(n, "ram:PostalTradeAddress/ram:PostcodeCode"),
            "city": x(n, "ram:PostalTradeAddress/ram:CityName"),
            "country": x(n, "ram:PostalTradeAddress/ram:CountryID"),
            "vat_id": (vat[0].text or "").strip() if vat else "",
            "tax_number": (fc[0].text or "").strip() if fc else "",
            "email": x(n, "ram:URIUniversalCommunication/ram:URIID") or
                     x(n, "ram:DefinedTradeContact/ram:EmailURIUniversalCommunication/ram:URIID"),
            "phone": x(n, "ram:DefinedTradeContact/ram:TelephoneUniversalCommunication/ram:CompleteNumber"),
        }

    def dt(path):
        return _fmt_date(x(root, path) and _iso(x(root, path)))

    def _iso(s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else s

    items = []
    for li in root.xpath("//ram:IncludedSupplyChainTradeLineItem", namespaces=CII_NS):
        qty = li.xpath("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", namespaces=CII_NS)
        items.append({
            "name": x(li, "ram:SpecifiedTradeProduct/ram:Name"),
            "quantity": float(_dec(qty[0].text)) if qty else 1.0,
            "unit": qty[0].get("unitCode", "C62") if qty else "C62",
            "unit_price": float(_dec(x(li, "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount"))),
            "tax_percent": float(_dec(x(li, "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent"))),
        })

    terms_text = x(root, "//ram:SpecifiedTradePaymentTerms/ram:Description")
    skonto = re.search(r"#SKONTO#TAGE=(\d+)#PROZENT=([\d.]+)", terms_text)
    terms_plain = "\n".join(l for l in terms_text.splitlines() if not l.startswith("#SKONTO#")).strip()

    return {
        "invoice_id": x(root, "//rsm:ExchangedDocument/ram:ID"),
        "issue_date": dt("//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"),
        "due_date": dt("//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString"),
        "delivery_date": dt("//ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString"),
        "currency": x(root, "//ram:InvoiceCurrencyCode") or "EUR",
        "note": x(root, "//rsm:ExchangedDocument/ram:IncludedNote/ram:Content"),
        "buyer_reference": x(root, "//ram:ApplicableHeaderTradeAgreement/ram:BuyerReference"),
        "seller": party("//ram:SellerTradeParty"),
        "buyer": party("//ram:BuyerTradeParty"),
        "payment": {
            "iban": x(root, "//ram:PayeePartyCreditorFinancialAccount/ram:IBANID"),
            "bic": x(root, "//ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID"),
            "account_holder": x(root, "//ram:PayeePartyCreditorFinancialAccount/ram:AccountName"),
            "terms": terms_plain,
            "skonto_days": int(skonto.group(1)) if skonto else 0,
            "skonto_percent": float(skonto.group(2)) if skonto else 0,
        },
        "items": items,
        "prepaid_amount": float(_dec(x(root, "//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TotalPrepaidAmount"))),
        "preceding_invoices": [
            {"id": x(r, "ram:IssuerAssignedID"),
             "date": _fmt_date(_iso(x(r, "ram:FormattedIssueDateTime/qdt:DateTimeString")))}
            for r in root.xpath("//ram:InvoiceReferencedDocument", namespaces=CII_NS)],
        "stated": {},
    }


def check_existing_zugferd(pdf_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Detect an embedded Factur-X / ZUGFeRD XML. Returns level + XML, or None."""
    try:
        xml_tuple = facturx.get_facturx_xml_from_pdf(pdf_bytes, check_xsd=False)
        if xml_tuple and xml_tuple[1]:
            raw = xml_tuple[1]
            raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
            root = etree.fromstring(raw_bytes)
            return {"is_zugferd": True, "level": facturx.get_facturx_level(root),
                    "filename": xml_tuple[0], "xml": raw_bytes.decode("utf-8", errors="ignore")}
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ entry point

def parse_uploaded_file(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Parse an uploaded PDF (DOCX/DOC must be converted to PDF first, see
    pdf_renderer.convert_docx_to_pdf). Returns invoice data, existing-ZUGFeRD
    info and human-readable extraction notes.
    """
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Nur PDF-Dateien können direkt ausgelesen werden.")

    notes: List[str] = []
    existing = check_existing_zugferd(file_bytes)
    ai_review_needed = False
    ai_review_attempted = False
    ai_review_used = False
    ai_model = None
    if existing:
        data = invoice_data_from_cii(existing["xml"].encode("utf-8"))
    else:
        plain = extract_text_from_pdf(file_bytes)
        layout = extract_layout_text_from_pdf(file_bytes)
        if not plain.strip():
            notes.append("Das PDF enthält keinen auslesbaren Text (Scan?) – bitte Daten manuell erfassen.")
        data = extract_invoice_data_from_text(plain, layout, notes)
        ai_review_needed = should_review_with_ai(
            plain_text=plain,
            invoice_data=data,
            extraction_notes=notes,
        )
        if ai_review_needed and ai_review_enabled():
            ai_review_attempted = True
            try:
                review = review_invoice_with_ai(
                    pdf_bytes=file_bytes,
                    plain_text=plain,
                    layout_text=layout,
                    invoice_data=data,
                    extraction_notes=notes,
                    validation_issues=validate_invoice(data),
                )
                reviewed_data = review.get("invoice_data")
                if not isinstance(reviewed_data, dict):
                    raise ValueError("KI-Antwort enthält keine Rechnungsdaten.")
                data = reviewed_data
                ai_review_used = True
                ai_model = str(review.get("model") or "") or None
                model_note = f" mit Modell {ai_model}" if ai_model else ""
                notes.append(
                    f"KI-Prüfung{model_note} abgeschlossen (Kundendaten wurden nicht übertragen); "
                    "bitte alle übernommenen Rechnungsdaten und Positionen prüfen."
                )
                uncertain = review.get("uncertain_fields")
                if isinstance(uncertain, list) and uncertain:
                    notes.append(
                        "KI konnte folgende Felder nicht sicher prüfen: "
                        + ", ".join(str(item) for item in uncertain[:20])
                        + "."
                    )
                extra_notes = review.get("notes")
                if isinstance(extra_notes, list):
                    notes.extend(
                        str(item)[:240] for item in extra_notes if isinstance(item, str) and item.strip()
                    )
            except InvoiceReviewError as exc:
                notes.append(
                    f"KI-Prüfung nicht verfügbar ({exc}); die lokale Erkennung bleibt unverändert. "
                    "Bitte die erkannten Werte manuell kontrollieren."
                )
            except Exception:  # noqa: BLE001 — AI review is best-effort, keep local extraction
                notes.append(
                    "KI-Prüfung war nicht verfügbar; die lokale Erkennung bleibt unverändert. "
                    "Bitte die erkannten Werte manuell kontrollieren."
                )
        elif ai_review_needed:
            notes.append(
                "KI-Prüfung ist deaktiviert; bitte die unvollständige oder auffällige Erkennung manuell kontrollieren."
            )

    return {
        "filename": filename,
        "has_existing_zugferd": existing is not None,
        "existing_zugferd_info": existing,
        "extraction_notes": notes,
        "invoice_data": data,
        "ai_review_needed": ai_review_needed,
        "ai_review_attempted": ai_review_attempted,
        "ai_review_used": ai_review_used,
        "ai_model": ai_model,
    }
