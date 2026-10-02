"""
zugferd_generator.py - Generates ZUGFeRD 2.x / Factur-X (CII) electronic invoices.

Profiles: 'en16931' (COMFORT) and 'basic'. The XML is built with lxml (no string
templating, so escaping is always correct), validated against the official
Factur-X XSD and embedded into the PDF with the factur-x library.

All amounts come from invoice_logic.compute_totals(); nothing is invented:
missing mandatory data raises InvoiceValidationError instead of being replaced
by placeholder values.
"""

from typing import Any, Dict, Optional

import facturx
from lxml import etree

from invoice_logic import (
    InvoiceValidationError, clean_id, compute_totals, format_decimal, has_errors,
    normalize_unit, parse_amount, parse_date, split_issues, validate_invoice, round2,
)

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
}

PROFILE_URN = {
    "basic": "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic",
    "en16931": "urn:cen.eu:en16931:2017",
    "comfort": "urn:cen.eu:en16931:2017",
}

DOC_TYPE_COMMERCIAL_INVOICE = "380"


def _el(parent: Optional[etree._Element], tag: str, text: Optional[str] = None, **attrs) -> etree._Element:
    prefix, name = tag.split(":")
    qname = f"{{{NS[prefix]}}}{name}"
    node = etree.SubElement(parent, qname) if parent is not None else etree.Element(qname, nsmap=NS)
    if text is not None:
        node.text = text
    for k, v in attrs.items():
        node.set(k, v)
    return node


def _date(parent, tag: str, value, wrapper: str = "udt:DateTimeString") -> None:
    """<tag><udt:DateTimeString format="102">YYYYMMDD</udt:DateTimeString></tag>"""
    d = parse_date(value)
    if d is None:
        return
    outer = _el(parent, tag)
    _el(outer, wrapper, d.strftime("%Y%m%d"), format="102")


def _party(parent, tag: str, p: Dict[str, Any], *, full: bool, seller: bool,
           contact: bool = False) -> None:
    node = _el(parent, tag)
    _el(node, "ram:Name", str(p.get("name") or "").strip())

    email = str(p.get("email") or "").strip()
    phone = str(p.get("phone") or "").strip()
    if full and contact and (email or phone):
        c = _el(node, "ram:DefinedTradeContact")
        _el(c, "ram:PersonName", str(p.get("contact_name") or p.get("name") or "").strip())
        if phone:
            _el(_el(c, "ram:TelephoneUniversalCommunication"), "ram:CompleteNumber", phone)
        if email:
            _el(_el(c, "ram:EmailURIUniversalCommunication"), "ram:URIID", email)

    addr = _el(node, "ram:PostalTradeAddress")
    if str(p.get("postcode") or "").strip():
        _el(addr, "ram:PostcodeCode", str(p["postcode"]).strip())
    if str(p.get("street") or "").strip():
        _el(addr, "ram:LineOne", str(p["street"]).strip())
    if str(p.get("city") or "").strip():
        _el(addr, "ram:CityName", str(p["city"]).strip())
    _el(addr, "ram:CountryID", str(p.get("country") or "DE").strip().upper())

    if full and email:
        _el(_el(node, "ram:URIUniversalCommunication"), "ram:URIID", email, schemeID="EM")

    vat = clean_id(p.get("vat_id"))
    if vat:
        _el(_el(node, "ram:SpecifiedTaxRegistration"), "ram:ID", vat, schemeID="VA")
    tax_no = str(p.get("tax_number") or "").strip()
    if seller and tax_no:
        _el(_el(node, "ram:SpecifiedTaxRegistration"), "ram:ID", tax_no, schemeID="FC")


def _payment_terms_text(data: Dict[str, Any], totals: Dict[str, Any]) -> str:
    payment = data.get("payment") or {}
    lines = []
    terms = str(payment.get("terms") or "").strip()
    if terms:
        lines.append(terms)
    days = int(parse_amount(payment.get("skonto_days") or 0))
    pct = parse_amount(payment.get("skonto_percent") or 0)
    if days > 0 and pct > 0:
        # Structured discount line understood by ZUGFeRD/XRechnung readers
        lines.append(f"#SKONTO#TAGE={days}#PROZENT={pct:.2f}#BASISBETRAG={totals['due']:.2f}#")
    return "\n".join(lines)


def build_zugferd_xml(data: Dict[str, Any], profile: str = "en16931") -> str:
    """Build a Factur-X CII XML document. Raises InvoiceValidationError on hard errors."""
    profile_key = (profile or "en16931").lower()
    if profile_key not in PROFILE_URN:
        profile_key = "en16931"
    full = profile_key != "basic"

    issues = validate_invoice(data)
    if has_errors(issues):
        raise InvoiceValidationError(split_issues(issues)[0])

    totals = compute_totals(data)
    currency = totals["currency"]
    seller = data.get("seller") or {}
    buyer = data.get("buyer") or {}
    payment = data.get("payment") or {}

    root = _el(None, "rsm:CrossIndustryInvoice")

    ctx = _el(root, "rsm:ExchangedDocumentContext")
    _el(_el(ctx, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", PROFILE_URN[profile_key])

    doc = _el(root, "rsm:ExchangedDocument")
    _el(doc, "ram:ID", str(data["invoice_id"]).strip())
    _el(doc, "ram:TypeCode", DOC_TYPE_COMMERCIAL_INVOICE)
    _date(doc, "ram:IssueDateTime", data.get("issue_date"))
    note = str(data.get("note") or "").strip()
    if note:
        _el(_el(doc, "ram:IncludedNote"), "ram:Content", note)

    trade = _el(root, "rsm:SupplyChainTradeTransaction")

    # --- line items
    for item, line in zip(data["items"], totals["lines"]):
        li = _el(trade, "ram:IncludedSupplyChainTradeLineItem")
        _el(_el(li, "ram:AssociatedDocumentLineDocument"), "ram:LineID", line["line_id"])
        _el(_el(li, "ram:SpecifiedTradeProduct"), "ram:Name", str(item.get("name") or "").strip())
        agreement = _el(li, "ram:SpecifiedLineTradeAgreement")
        _el(_el(agreement, "ram:NetPriceProductTradePrice"), "ram:ChargeAmount",
            format_decimal(line["unit_price"], 2, 4))
        delivery = _el(li, "ram:SpecifiedLineTradeDelivery")
        _el(delivery, "ram:BilledQuantity", format_decimal(line["quantity"], 0, 4),
            unitCode=normalize_unit(item.get("unit")))
        settlement = _el(li, "ram:SpecifiedLineTradeSettlement")
        tax = _el(settlement, "ram:ApplicableTradeTax")
        _el(tax, "ram:TypeCode", "VAT")
        _el(tax, "ram:CategoryCode", "S" if line["tax_percent"] > 0 else "Z")
        _el(tax, "ram:RateApplicablePercent", f"{line['tax_percent']:.2f}")
        _el(_el(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation"),
            "ram:LineTotalAmount", f"{line['net']:.2f}")

    # --- header agreement
    agreement = _el(trade, "ram:ApplicableHeaderTradeAgreement")
    buyer_ref = str(data.get("buyer_reference") or "").strip()
    if buyer_ref:
        _el(agreement, "ram:BuyerReference", buyer_ref)
    _party(agreement, "ram:SellerTradeParty", seller, full=full, seller=True, contact=True)
    _party(agreement, "ram:BuyerTradeParty", buyer, full=full, seller=False)

    # --- header delivery
    delivery = _el(trade, "ram:ApplicableHeaderTradeDelivery")
    if parse_date(data.get("delivery_date")):
        event = _el(delivery, "ram:ActualDeliverySupplyChainEvent")
        _date(event, "ram:OccurrenceDateTime", data.get("delivery_date"))

    # --- header settlement
    st = _el(trade, "ram:ApplicableHeaderTradeSettlement")
    _el(st, "ram:InvoiceCurrencyCode", currency)

    iban = clean_id(payment.get("iban"))
    if iban:
        means = _el(st, "ram:SpecifiedTradeSettlementPaymentMeans")
        _el(means, "ram:TypeCode", "58")  # SEPA credit transfer
        account = _el(means, "ram:PayeePartyCreditorFinancialAccount")
        _el(account, "ram:IBANID", iban)
        holder = str(payment.get("account_holder") or "").strip()
        if full and holder:
            _el(account, "ram:AccountName", holder)
        bic = clean_id(payment.get("bic"))
        if full and bic:
            _el(_el(means, "ram:PayeeSpecifiedCreditorFinancialInstitution"), "ram:BICID", bic)

    for g in totals["tax_groups"]:
        tax = _el(st, "ram:ApplicableTradeTax")
        _el(tax, "ram:CalculatedAmount", f"{g['tax']:.2f}")
        _el(tax, "ram:TypeCode", "VAT")
        _el(tax, "ram:BasisAmount", f"{g['basis']:.2f}")
        _el(tax, "ram:CategoryCode", g["category"])
        _el(tax, "ram:RateApplicablePercent", f"{g['percent']:.2f}")

    terms_text = _payment_terms_text(data, totals)
    if terms_text or parse_date(data.get("due_date")):
        terms = _el(st, "ram:SpecifiedTradePaymentTerms")
        if terms_text:
            _el(terms, "ram:Description", terms_text)
        _date(terms, "ram:DueDateDateTime", data.get("due_date"))

    summ = _el(st, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(summ, "ram:LineTotalAmount", f"{totals['line_total']:.2f}")
    _el(summ, "ram:TaxBasisTotalAmount", f"{totals['line_total']:.2f}")
    _el(summ, "ram:TaxTotalAmount", f"{totals['tax_total']:.2f}", currencyID=currency)
    _el(summ, "ram:GrandTotalAmount", f"{totals['grand_total']:.2f}")
    if totals["prepaid"] != 0:
        _el(summ, "ram:TotalPrepaidAmount", f"{totals['prepaid']:.2f}")
    _el(summ, "ram:DuePayableAmount", f"{totals['due']:.2f}")

    for prev in data.get("preceding_invoices") or []:
        if not str(prev.get("id") or "").strip():
            continue
        ref = _el(st, "ram:InvoiceReferencedDocument")
        _el(ref, "ram:IssuerAssignedID", str(prev["id"]).strip())
        if parse_date(prev.get("date")):
            _date(ref, "ram:FormattedIssueDateTime", prev.get("date"), wrapper="qdt:DateTimeString")

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode("utf-8")


def validate_and_create_zugferd_pdf(pdf_bytes: bytes, xml_str: str, profile: str = "en16931",
                                    data: Optional[Dict[str, Any]] = None) -> bytes:
    """
    Validates the XML against the official Factur-X XSD and embeds it into the PDF,
    producing a hybrid ZUGFeRD invoice (PDF with attached factur-x.xml, AFRelationship=Data).
    """
    xml_bytes = xml_str.encode("utf-8")
    level = "basic" if (profile or "").lower() == "basic" else "en16931"

    if not facturx.xml_check_xsd(xml_bytes, "factur-x", level=level):
        raise ValueError(f"XML entspricht nicht dem {level.upper()}-XSD-Schema.")

    metadata = None
    if data:
        seller_name = (data.get("seller") or {}).get("name") or ""
        metadata = {
            "author": seller_name,
            "title": f"{seller_name}: Rechnung {data.get('invoice_id', '')}".strip(": "),
            "subject": f"Rechnung {data.get('invoice_id', '')} von {seller_name}",
        }

    return facturx.generate_from_binary(
        pdf_bytes, xml_bytes, flavor="factur-x", level=level,
        check_xsd=True, check_schematron=False, pdf_metadata=metadata,
    )
