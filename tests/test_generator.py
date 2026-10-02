import facturx
import pytest

from invoice_logic import InvoiceValidationError
from pdf_renderer import create_invoice_pdf_from_data
from schematron import check_business_rules
from zugferd_generator import build_zugferd_xml, validate_and_create_zugferd_pdf


@pytest.mark.parametrize("profile", ["en16931", "basic"])
def test_xml_is_xsd_valid_and_embeds(invoice, profile):
    xml = build_zugferd_xml(invoice, profile)
    pdf = validate_and_create_zugferd_pdf(create_invoice_pdf_from_data(invoice), xml, profile, invoice)
    name, embedded = facturx.get_facturx_xml_from_pdf(pdf, check_xsd=True)
    assert name == "factur-x.xml" and b"RE-1" in embedded


def test_xml_escapes_special_characters(invoice):
    assert "Beratung &amp; Support" in build_zugferd_xml(invoice)


def test_prepayment_skonto_and_delivery_in_xml(invoice):
    invoice.update(prepaid_amount=100, delivery_date="30.09.2026",
                   preceding_invoices=[{"id": "A-7", "date": "01.09.2026"}])
    invoice["payment"].update(skonto_days=10, skonto_percent=3)
    xml = build_zugferd_xml(invoice)
    for needle in ("<ram:TotalPrepaidAmount>100.00", "<ram:DuePayableAmount>299.78",
                   "#SKONTO#TAGE=10#PROZENT=3.00#", "<ram:IssuerAssignedID>A-7", "20260930"):
        assert needle in xml


def test_generation_refuses_incomplete_data(invoice):
    invoice["seller"]["vat_id"] = ""
    with pytest.raises(InvoiceValidationError):
        build_zugferd_xml(invoice)


def test_schematron_business_rules(invoice):
    result = check_business_rules(build_zugferd_xml(invoice))
    if result["status"] == "unavailable":
        pytest.skip(result["detail"])
    assert result["status"] == "passed", result["failures"]


def test_schematron_detects_wrong_total(invoice):
    xml = build_zugferd_xml(invoice).replace("<ram:GrandTotalAmount>399.78", "<ram:GrandTotalAmount>399.79")
    result = check_business_rules(xml)
    if result["status"] == "unavailable":
        pytest.skip(result["detail"])
    assert result["status"] == "failed"
