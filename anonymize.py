"""Anonymise the invoice recipient (the customer) before anything goes to the AI endpoint.

The customer is not part of the AI's job. Whatever leaves the machine therefore carries a
stand-in ("Max Mustermann", "Musterstraße 1", ...) instead of the real recipient, or nothing
at all where the recipient block could not be identified:

* invoice data: buyer fields are replaced by placeholders (the country stays, it is needed
  for the tax treatment and does not identify anyone),
* text: every occurrence of the buyer's name / street / ZIP+city / VAT id / e-mail / phone
  is replaced,
* page images: the same strings are blanked out via the PDF text layer; if that cannot be
  done reliably the images are not sent at all.

Best effort by value: free-text mentions the parser never saw (a contact person, a customer
number) are not masked.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

PLACEHOLDER_NAME = "Max Mustermann"
PLACEHOLDER_STREET = "Musterstraße 1"
PLACEHOLDER_POSTCODE = "12345"
PLACEHOLDER_CITY = "Musterstadt"
PLACEHOLDER_EMAIL = "max.mustermann@example.com"
PLACEHOLDER_PHONE = "+49 000 0000000"

# Pieces of a company name that say nothing about who the customer is.
_GENERIC_NAME_PIECES = {
    "gmbh", "ag", "kg", "ohg", "ug", "se", "gbr", "mbh", "e.k.", "e.v.", "co.", "& co. kg",
    "gmbh & co. kg", "herr", "frau", "firma",
}
_BUYER_NOTE_RE = re.compile(r"Käufer|Empfänger")


@dataclass(frozen=True)
class Term:
    """One piece of customer data: what to find, what to put there instead, how to match it."""

    original: str
    placeholder: str
    kind: str  # name | street | city | vat | email | phone
    required: bool = False  # must be located on the page, or the page images are not sent


def is_buyer_note(note: str) -> bool:
    return bool(_BUYER_NOTE_RE.search(note))


def is_buyer_issue(issue: dict[str, str]) -> bool:
    field = str(issue.get("field") or "")
    return field == "buyer" or field.startswith("buyer.")


def buyer_identified(buyer: dict[str, Any]) -> bool:
    """Enough of the recipient is known to mask it by value (name and ZIP + city)."""
    return bool(
        str(buyer.get("name") or "").strip()
        and str(buyer.get("postcode") or "").strip()
        and str(buyer.get("city") or "").strip()
    )


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _seller_strings(seller: dict[str, Any]) -> set[str]:
    values = {_clean(seller.get(k)).lower() for k in ("name", "street", "city", "email", "phone", "vat_id")}
    values.add(f"{_clean(seller.get('postcode'))} {_clean(seller.get('city'))}".strip().lower())
    return {v for v in values if v}


def build_terms(buyer: dict[str, Any], seller: dict[str, Any]) -> list[Term]:
    """Everything that identifies the buyer, minus strings the seller shares (never mask those)."""
    seller_values = _seller_strings(seller or {})
    terms: list[Term] = []

    def add(original: str, placeholder: str, kind: str, required: bool = False, min_len: int = 3) -> None:
        original = _clean(original)
        if len(original) < min_len or original.lower() in seller_values:
            return
        if any(t.original.lower() == original.lower() and t.kind == kind for t in terms):
            return
        terms.append(Term(original, placeholder, kind, required))

    name = _clean(buyer.get("name"))
    pieces = [p.strip() for p in re.split(r",\s*|\n", str(buyer.get("name") or "")) if p.strip()]
    if len(pieces) <= 1:
        add(name, PLACEHOLDER_NAME, "name", required=True)
    else:
        add(name, PLACEHOLDER_NAME, "name")
        first = True
        for piece in pieces:
            if _clean(piece).lower() in _GENERIC_NAME_PIECES:
                continue
            before = len(terms)
            add(piece, PLACEHOLDER_NAME if first else "", "name", required=first, min_len=4)
            first = first and len(terms) == before
    add(buyer.get("street"), PLACEHOLDER_STREET, "street", required=True)
    postcode, city = _clean(buyer.get("postcode")), _clean(buyer.get("city"))
    if postcode and city:
        add(f"{postcode} {city}", f"{PLACEHOLDER_POSTCODE} {PLACEHOLDER_CITY}", "city", required=True)
    vat = re.sub(r"\s+", "", str(buyer.get("vat_id") or ""))
    if vat:
        add(vat, (vat[:2] if vat[:2].isalpha() else "DE") + "000000000", "vat", min_len=5)
    add(buyer.get("email"), PLACEHOLDER_EMAIL, "email", min_len=5)
    phone_digits = re.sub(r"\D", "", str(buyer.get("phone") or ""))
    if len(phone_digits) >= 6:
        add(phone_digits, PLACEHOLDER_PHONE, "phone", min_len=6)
    return sorted(terms, key=lambda t: len(t.original), reverse=True)


def _pattern(term: Term) -> re.Pattern[str]:
    if term.kind == "phone":  # digits with any separators in between
        return re.compile(r"[\s()/.\-]*".join(re.escape(d) for d in term.original))
    if term.kind == "vat":  # "DE 123 456 789" == "DE123456789"
        return re.compile(r"\s*".join(re.escape(c) for c in term.original), re.IGNORECASE)
    words = term.original.split(" ")
    return re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE)


def scrub_text(text: str, terms: list[Term]) -> str:
    """Replace every occurrence of the buyer's data in free text."""
    for term in terms:
        text = _pattern(term).sub(term.placeholder, text)
    return text


def restore_text(text: str, terms: list[Term]) -> str:
    """Put the real values back where the AI echoed a placeholder (e.g. inside a line item)."""
    for term in terms:
        if term.placeholder and term.kind in ("name", "street", "city"):
            text = text.replace(term.placeholder, term.original)
    return text


def restore_value(value: Any, terms: list[Term]) -> Any:
    if isinstance(value, str):
        return restore_text(value, terms)
    if isinstance(value, list):
        return [restore_value(v, terms) for v in value]
    if isinstance(value, dict):
        return {k: restore_value(v, terms) for k, v in value.items()}
    return value


def anonymise_invoice(data: dict[str, Any]) -> dict[str, Any]:
    """Copy of the invoice data in which the buyer is a stand-in (country kept)."""
    out = copy.deepcopy(data)
    buyer = out.get("buyer")
    if not isinstance(buyer, dict):
        return out
    vat = re.sub(r"\s+", "", str(buyer.get("vat_id") or ""))
    stand_in = {
        "name": PLACEHOLDER_NAME,
        "street": PLACEHOLDER_STREET,
        "postcode": PLACEHOLDER_POSTCODE,
        "city": PLACEHOLDER_CITY,
        "vat_id": (vat[:2] if vat[:2].isalpha() else "DE") + "000000000",
        "email": PLACEHOLDER_EMAIL,
        "phone": PLACEHOLDER_PHONE,
    }
    for key, placeholder in stand_in.items():
        if str(buyer.get(key) or "").strip():
            buyer[key] = placeholder
    return out


# ---------------------------------------------------------------- page images

def _line_groups(boxes: list[tuple[float, float, float, float]]) -> list[list[tuple[float, float, float, float]]]:
    """Split character boxes (in text order) into one group per visual line."""
    groups: list[list[tuple[float, float, float, float]]] = []
    for box in boxes:
        if box[2] - box[0] <= 0 and box[3] - box[1] <= 0:
            continue
        centre, height = (box[1] + box[3]) / 2, max(box[3] - box[1], 1.0)
        if groups:
            last = groups[-1][-1]
            if abs((last[1] + last[3]) / 2 - centre) <= height * 0.6:
                groups[-1].append(box)
                continue
        groups.append([box])
    return groups


def redact_page(page: Any, image: Any, terms: list[Term], scale: float) -> set[str]:
    """Blank every occurrence of the terms in the rendered page image.

    Returns the originals that were located on this page. Raises ValueError if the page
    geometry is not one we can map reliably (rotated pages).
    """
    from PIL import ImageDraw

    if page.get_rotation() % 360:
        raise ValueError("rotated page")
    left, _bottom, _right, top = page.get_cropbox()
    textpage = page.get_textpage()
    draw = ImageDraw.Draw(image)
    found: set[str] = set()
    pad = 2.0
    try:
        for term in terms:
            if term.kind == "phone":
                continue  # digits only; masked in the text, not worth a fragile page search
            queries = {term.original, term.original.replace(" ", "")} if term.kind == "vat" else {term.original}
            for query in queries:
                searcher = textpage.search(query, match_case=False)
                while True:
                    hit = searcher.get_next()
                    if not hit:
                        break
                    index, count = hit
                    boxes = [textpage.get_charbox(i) for i in range(index, index + count)]
                    for group in _line_groups(boxes):
                        l = min(b[0] for b in group) - pad
                        b_ = min(b[1] for b in group) - pad
                        r = max(b[2] for b in group) + pad
                        t = max(b[3] for b in group) + pad
                        draw.rectangle(
                            [(l - left) * scale, (top - t) * scale, (r - left) * scale, (top - b_) * scale],
                            fill=(255, 255, 255),
                        )
                    found.add(term.original)
    finally:
        textpage.close()
    return found
