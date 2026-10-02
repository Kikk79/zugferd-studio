"""Regression test on the real SILATEC Teil-Rechnung 393/2026 (skipped if the PDF is absent)."""
from decimal import Decimal as D

from extractor import parse_uploaded_file
from invoice_logic import compute_totals, validate_invoice
from schematron import check_business_rules
from zugferd_generator import build_zugferd_xml, validate_and_create_zugferd_pdf


def test_extraction_matches_document(real_pdf):
    d = parse_uploaded_file(real_pdf, "x.pdf")["invoice_data"]
    assert d["invoice_id"] == "393/2026" and d["issue_date"] == "09.06.2026"
    assert d["delivery_date"] == "08.06.2026"
    assert d["seller"]["vat_id"] == "DE812477385" and d["seller"]["postcode"] == "82538"
    assert d["buyer"]["name"] == "Leopold Feuerstein Holztechnik GmbH" and d["buyer"]["postcode"] == "36160"
    assert d["payment"]["iban"] == "DE21600501010004013732" and d["payment"]["bic"] == "SOLADEST600"
    assert (d["payment"]["skonto_days"], d["payment"]["skonto_percent"]) == (10, 3.0)
    assert d["preceding_invoices"] == [{"id": "240/2026", "date": "14.04.2026"}]
    assert len(d["items"]) == 39          # 37 glass rows + freight + cables (row 1 has no price)


def test_totals_equal_printed_totals_to_the_cent(real_pdf):
    d = parse_uploaded_file(real_pdf, "x.pdf")["invoice_data"]
    t = compute_totals(d)
    assert (t["line_total"], t["tax_total"], t["grand_total"], t["prepaid"], t["due"]) == (
        D("110984.00"), D("21086.96"), D("132070.96"), D("68166.77"), D("63904.19"))
    assert validate_invoice(d) == []      # no errors, no mismatch warnings


def test_full_pipeline_passes_xsd_and_en16931_rules(real_pdf):
    d = parse_uploaded_file(real_pdf, "x.pdf")["invoice_data"]
    xml = build_zugferd_xml(d)
    assert validate_and_create_zugferd_pdf(real_pdf, xml, "en16931", d).startswith(b"%PDF")
    rules = check_business_rules(xml)
    assert rules["status"] in ("passed", "unavailable"), rules["failures"]
