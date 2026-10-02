"""
invoice_logic.py - Shared invoice arithmetic, normalisation and validation.

Single source of truth for amounts: the XML generator, the PDF renderer and the
API all call compute_totals(), so the numbers shown in the UI, printed in a
generated PDF and written into the factur-x.xml can never disagree.

Invoice data schema (plain dict, JSON-friendly):

    invoice_id, issue_date, due_date, delivery_date, currency, note, buyer_reference
    seller:  name, street, postcode, city, country, vat_id, tax_number, email, phone
    buyer:   name, street, postcode, city, country, vat_id, email
    payment: iban, bic, account_holder, terms, skonto_days, skonto_percent
    items:   [{name, quantity, unit, unit_price, tax_percent}]
    tax_category, tax_exemption_reason   VAT category of all 0 % lines: Z (zero rate), E (exempt),
                                        G (export, tax-free), K (intra-EU supply), AE (reverse charge)
    prepaid_amount                      gross amount already paid (Anzahlungen)
    preceding_invoices: [{id, date}]    invoices referenced (e.g. Anzahlungsrechnung)
    stated:  {net, tax, gross, prepaid, due}   values printed on the source document
                                        (only used to warn about mismatches)
"""

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, List, Optional

D = Decimal
CENT = D("0.01")

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d.%m.%y")

# German / common free-text units -> UN/ECE Recommendation 20 codes
UNIT_ALIASES = {
    "stk": "C62", "stk.": "C62", "stück": "C62", "st": "C62", "st.": "C62", "stueck": "C62",
    "pcs": "C62", "pc": "C62", "x": "C62", "einheit": "C62", "psch": "LS", "pauschal": "LS",
    "h": "HUR", "std": "HUR", "std.": "HUR", "stunde": "HUR", "stunden": "HUR",
    "tag": "DAY", "tage": "DAY", "d": "DAY", "monat": "MON", "monate": "MON", "mon": "MON",
    "m": "MTR", "lfm": "MTR", "m2": "MTK", "m²": "MTK", "qm": "MTK", "m3": "MTQ", "m³": "MTQ",
    "kg": "KGM", "t": "TNE", "l": "LTR", "ltr": "LTR", "km": "KMT", "set": "SET", "satz": "SET",
}


# VAT categories for 0 % lines -> (needs reason text, auto exemption code per VATEX list)
ZERO_RATE_CATEGORIES = {
    "Z": (False, None),
    "E": (True, None),
    "G": (False, "VATEX-EU-G"),
    "K": (False, "VATEX-EU-IC"),
    "AE": (False, "VATEX-EU-AE"),
    "O": (True, "VATEX-EU-O"),
}


def zero_rate_category(data: Dict[str, Any]) -> str:
    cat = str(data.get("tax_category") or "Z").upper()
    return cat if cat in ZERO_RATE_CATEGORIES else "Z"


class InvoiceValidationError(ValueError):
    """Raised when invoice data cannot be turned into a valid e-invoice."""

    def __init__(self, issues: List[Dict[str, str]]):
        self.issues = issues
        super().__init__("; ".join(i["message"] for i in issues))


# ---------------------------------------------------------------- parsing helpers

def parse_amount(value: Any) -> Decimal:
    """Parse numbers/strings in German (1.250,50) or international (1,250.50) notation."""
    if value is None or value == "":
        return D(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return D(int(value))
    if isinstance(value, int):
        return D(value)
    if isinstance(value, float):
        return D(repr(value))
    s = re.sub(r"[€\s ]|EUR|CHF|USD|GBP", "", str(value), flags=re.IGNORECASE)
    s = s.lstrip("+")
    if not s:
        return D(0)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return D(s)
    except InvalidOperation:
        return D(0)


def round2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_unit(unit: Any) -> str:
    u = str(unit or "").strip()
    if not u:
        return "C62"
    if re.fullmatch(r"[A-Z0-9]{2,3}", u):
        return u
    return UNIT_ALIASES.get(u.lower(), "C62")


def clean_id(value: Any) -> str:
    """Remove whitespace and upper-case an identifier (IBAN, VAT id, BIC)."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def iban_valid(iban: str) -> bool:
    iban = clean_id(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def format_decimal(value: Decimal, min_places: int = 2, max_places: int = 4) -> str:
    """Render a Decimal with at least min_places and at most max_places decimals."""
    text = f"{value.quantize(D(1).scaleb(-max_places), rounding=ROUND_HALF_UP):f}"
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    frac = frac.ljust(min_places, "0")
    return whole + ("." + frac if frac else "")


# ---------------------------------------------------------------- calculation

def compute_totals(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    EN 16931 arithmetic (BR-CO-10 .. BR-CO-16):
      line net      = round(quantity * unit price, 2)
      VAT per rate  = round(sum of line nets of that rate * rate, 2)
      grand total   = net total + VAT total
      payable       = grand total - prepaid amount
    """
    default_rate = parse_amount(data.get("tax_percent", 19))
    lines = []
    groups: Dict[tuple, Dict[str, Decimal]] = {}
    zero_cat = zero_rate_category(data)

    for idx, item in enumerate(data.get("items") or [], start=1):
        qty = parse_amount(item.get("quantity", 1))
        price = parse_amount(item.get("unit_price", 0))
        rate = parse_amount(item.get("tax_percent", default_rate))
        net = round2(qty * price)
        category = "S" if rate > 0 else zero_cat
        lines.append({"line_id": str(idx), "quantity": qty, "unit_price": price,
                      "tax_percent": rate, "net": net, "category": category})
        groups.setdefault((category, rate), {"basis": D(0)})["basis"] += net

    tax_groups = []
    tax_total = D(0)
    reason = str(data.get("tax_exemption_reason") or "").strip()
    for category, rate in sorted(groups, key=lambda k: (k[1], k[0])):
        basis = round2(groups[(category, rate)]["basis"])
        tax = round2(basis * rate / D(100))
        tax_total += tax
        tax_groups.append({"percent": rate, "basis": basis, "tax": tax, "category": category,
                           "reason": reason if category not in ("S", "Z") else "",
                           "code": ZERO_RATE_CATEGORIES[category][1] if category in ZERO_RATE_CATEGORIES else None})

    line_total = round2(sum((l["net"] for l in lines), D(0)))
    tax_total = round2(tax_total)
    grand_total = round2(line_total + tax_total)
    prepaid = round2(parse_amount(data.get("prepaid_amount", 0)))
    due = round2(grand_total - prepaid)

    return {
        "currency": (data.get("currency") or "EUR").upper(),
        "lines": lines,
        "line_total": line_total,
        "tax_groups": tax_groups,
        "tax_total": tax_total,
        "grand_total": grand_total,
        "prepaid": prepaid,
        "due": due,
    }


def totals_to_json(totals: Dict[str, Any]) -> Dict[str, Any]:
    """Decimal -> float/str structure for the JSON API (UI display only)."""
    f = lambda d: float(d)
    return {
        "currency": totals["currency"],
        "net": f(totals["line_total"]),
        "tax": f(totals["tax_total"]),
        "gross": f(totals["grand_total"]),
        "prepaid": f(totals["prepaid"]),
        "due": f(totals["due"]),
        "tax_groups": [
            {"percent": f(g["percent"]), "basis": f(g["basis"]), "tax": f(g["tax"]), "category": g["category"]}
            for g in totals["tax_groups"]
        ],
        "line_nets": [f(l["net"]) for l in totals["lines"]],
    }


# ---------------------------------------------------------------- validation

def _issue(level: str, field: str, message: str) -> Dict[str, str]:
    return {"level": level, "field": field, "message": message}


def validate_invoice(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Returns a list of issues. level == "error" blocks generation (the result
    would not be a valid EN 16931 invoice or would be wrong); "warning" is
    informational. Nothing is ever invented to paper over a missing field.
    """
    issues: List[Dict[str, str]] = []
    err = lambda f, m: issues.append(_issue("error", f, m))
    warn = lambda f, m: issues.append(_issue("warning", f, m))

    seller = data.get("seller") or {}
    buyer = data.get("buyer") or {}
    payment = data.get("payment") or {}

    if not str(data.get("invoice_id") or "").strip():
        err("invoice_id", "Rechnungsnummer fehlt.")
    if parse_date(data.get("issue_date")) is None:
        err("issue_date", "Rechnungsdatum fehlt oder ist nicht lesbar (Format TT.MM.JJJJ).")
    for key, label in (("due_date", "Fälligkeitsdatum"), ("delivery_date", "Lieferdatum")):
        if str(data.get(key) or "").strip() and parse_date(data.get(key)) is None:
            err(key, f"{label} ist nicht lesbar (Format TT.MM.JJJJ).")
    if not re.fullmatch(r"[A-Za-z]{3}", str(data.get("currency") or "EUR")):
        err("currency", "Währung muss ein 3-stelliger ISO-Code sein (z. B. EUR).")

    # Seller (BG-4)
    if not str(seller.get("name") or "").strip():
        err("seller.name", "Name des Verkäufers fehlt.")
    if not re.fullmatch(r"[A-Za-z]{2}", str(seller.get("country") or "")):
        err("seller.country", "Land des Verkäufers fehlt (2-Buchstaben-Code, z. B. DE).")
    for key, label in (("street", "Straße"), ("postcode", "PLZ"), ("city", "Ort")):
        if not str(seller.get(key) or "").strip():
            warn(f"seller.{key}", f"{label} des Verkäufers fehlt.")
    vat = clean_id(seller.get("vat_id"))
    tax_no = str(seller.get("tax_number") or "").strip()
    if not vat and not tax_no:
        err("seller.vat_id", "USt-IdNr. oder Steuernummer des Verkäufers fehlt (Pflichtangabe nach EN 16931 / § 14 UStG).")
    elif vat and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,12}", vat):
        warn("seller.vat_id", f"USt-IdNr. '{vat}' hat kein gültiges Format.")

    # Buyer (BG-7)
    if not str(buyer.get("name") or "").strip():
        err("buyer.name", "Name des Käufers fehlt.")
    if not re.fullmatch(r"[A-Za-z]{2}", str(buyer.get("country") or "")):
        err("buyer.country", "Land des Käufers fehlt (2-Buchstaben-Code, z. B. DE).")
    for key, label in (("street", "Straße"), ("postcode", "PLZ"), ("city", "Ort")):
        if not str(buyer.get(key) or "").strip():
            warn(f"buyer.{key}", f"{label} des Käufers fehlt.")

    # Payment
    iban = clean_id(payment.get("iban"))
    if iban and not iban_valid(iban):
        err("payment.iban", f"IBAN '{iban}' ist ungültig (Prüfsumme stimmt nicht).")
    elif not iban:
        warn("payment.iban", "Keine IBAN angegeben – die Rechnung enthält keine Zahlungsinformation.")
    bic = clean_id(payment.get("bic"))
    if bic and not re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", bic):
        warn("payment.bic", f"BIC '{bic}' hat kein gültiges Format.")

    # Zero-rate categories (steuerfrei / Ausfuhr / innergemeinschaftlich / Reverse Charge)
    zero_cat = zero_rate_category(data)
    if zero_cat != "Z" and any(parse_amount(i.get("tax_percent", 19)) == 0 for i in data.get("items") or []):
        needs_reason, auto_code = ZERO_RATE_CATEGORIES[zero_cat]
        if needs_reason and not str(data.get("tax_exemption_reason") or "").strip():
            err("tax_exemption_reason", "Für steuerbefreite Positionen fehlt der Befreiungsgrund (z. B. Gesetzesverweis).")
        if zero_cat in ("K", "AE") and not clean_id(buyer.get("vat_id")):
            err("buyer.vat_id", "Bei innergemeinschaftlicher Lieferung / Reverse Charge ist die USt-IdNr. des Käufers Pflicht.")
        if zero_cat == "K" and parse_date(data.get("delivery_date")) is None:
            err("delivery_date", "Bei innergemeinschaftlicher Lieferung ist das Lieferdatum Pflicht.")

    # Items
    items = data.get("items") or []
    if not items:
        err("items", "Die Rechnung enthält keine Positionen.")
    for idx, item in enumerate(items, start=1):
        if not str(item.get("name") or "").strip():
            err(f"items.{idx}", f"Position {idx}: Bezeichnung fehlt.")
        if parse_amount(item.get("quantity", 1)) == 0:
            warn(f"items.{idx}", f"Position {idx}: Menge ist 0.")

    # Amount plausibility
    totals = compute_totals(data)
    if str(seller.get("country") or "").upper() == "DE":
        for g in totals["tax_groups"]:
            if g["percent"] not in (D(0), D(7), D(19)):
                warn("items", f"Steuersatz {g['percent']:g} % ist für einen Verkäufer in DE unüblich "
                              "(üblich: 19 %, 7 %, 0 %) – bitte prüfen.")
    if totals["prepaid"] > totals["grand_total"] and items:
        err("prepaid_amount", "Die Anzahlung ist höher als der Rechnungsbetrag.")
    if totals["prepaid"] < 0:
        err("prepaid_amount", "Die Anzahlung darf nicht negativ sein.")

    # Compare against what the source document says (informational)
    stated = data.get("stated") or {}
    cur = totals["currency"]
    for key, computed, label in (
        ("net", totals["line_total"], "Nettobetrag"),
        ("tax", totals["tax_total"], "Umsatzsteuer"),
        ("gross", totals["grand_total"], "Bruttobetrag"),
        ("due", totals["due"], "Zahlbetrag"),
    ):
        if key in stated and stated[key] not in (None, ""):
            expected = round2(parse_amount(stated[key]))
            if expected != computed:
                warn("stated." + key,
                     f"{label}: berechnet {computed:,.2f} {cur}, im Dokument {expected:,.2f} {cur} – bitte Positionen prüfen.")
    return issues


def has_errors(issues: List[Dict[str, str]]) -> bool:
    return any(i["level"] == "error" for i in issues)


def split_issues(issues: List[Dict[str, str]]):
    return ([i for i in issues if i["level"] == "error"],
            [i for i in issues if i["level"] == "warning"])
