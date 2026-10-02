"""Tax-free supplies (export, intra-EU, reverse charge) and the real RE 379/2026 invoice."""
from decimal import Decimal as D
from pathlib import Path

import pytest

from extractor import parse_uploaded_file
from invoice_logic import compute_totals, validate_invoice
from schematron import check_business_rules
from zugferd_generator import build_zugferd_xml

REAL = Path(__file__).resolve().parent.parent / "RE 379 _3-3665.01_Schweiz steuerfreie Lief. AG_Glasbruch_260603.pdf"


def export_invoice(invoice):
    invoice["buyer"].update(country="CH", name="Kunde AG")
    for item in invoice["items"]:
        item["tax_percent"] = 0
    invoice["tax_category"] = "G"
    invoice["tax_exemption_reason"] = "Steuerfreie Lieferung gemäß § 4 Nr. 1a UStG"
    return invoice


def test_export_category_in_xml_and_rules(invoice):
    xml = build_zugferd_xml(export_invoice(invoice))
    assert "<ram:CategoryCode>G</ram:CategoryCode>" in xml
    assert "<ram:ExemptionReasonCode>VATEX-EU-G</ram:ExemptionReasonCode>" in xml
    assert "§ 4 Nr. 1a UStG" in xml
    result = check_business_rules(xml)
    if result["status"] == "unavailable":
        pytest.skip(result["detail"])
    assert result["status"] == "passed", result["failures"]


def test_totals_without_vat(invoice):
    t = compute_totals(export_invoice(invoice))
    assert t["tax_total"] == D(0) and t["grand_total"] == t["line_total"]


def test_exempt_category_needs_reason(invoice):
    export_invoice(invoice)
    invoice["tax_category"], invoice["tax_exemption_reason"] = "E", ""
    assert "tax_exemption_reason" in {i["field"] for i in validate_invoice(invoice) if i["level"] == "error"}


def test_intra_eu_requires_buyer_vat_and_delivery_date(invoice):
    export_invoice(invoice)
    invoice["tax_category"] = "K"
    fields = {i["field"] for i in validate_invoice(invoice) if i["level"] == "error"}
    assert {"buyer.vat_id", "delivery_date"} <= fields


@pytest.mark.skipif(not REAL.exists(), reason="Beispielrechnung nicht vorhanden")
def test_real_tax_free_invoice():
    d = parse_uploaded_file(REAL.read_bytes(), "x.pdf")["invoice_data"]
    assert d["invoice_id"] == "379/2026" and d["currency"] == "CHF"
    assert d["tax_category"] == "G" and "§ 4 Nr. 1a" in d["tax_exemption_reason"]
    assert d["buyer"]["country"] == "CH"
    assert d["payment"]["iban"] == "CH4308710040003382001" and d["payment"]["bic"] == "CIALCHBB"
    t = compute_totals(d)
    assert (t["line_total"], t["tax_total"], t["grand_total"]) == (D("11056.00"), D(0), D("11056.00"))
    assert [i for i in validate_invoice(d) if i["level"] == "error"] == []
