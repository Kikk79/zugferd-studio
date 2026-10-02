from decimal import Decimal as D

import pytest

from invoice_logic import compute_totals, iban_valid, parse_amount, validate_invoice


@pytest.mark.parametrize("text,expected", [
    ("1.250,50", "1250.50"), ("1,250.50", "1250.50"), ("595,00", "595.00"), ("595.00", "595.00"),
    ("1.000", "1000"), ("€ 12,5", "12.5"), ("", "0"), (None, "0"), (19.99, "19.99"),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == D(expected)


def test_iban_checksum():
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE89 3704 0044 0532 0130 01")


def test_totals_multi_rate_prepaid(invoice):
    invoice["prepaid_amount"] = "100,00"
    t = compute_totals(invoice)
    assert t["line_total"] == D("339.98")
    assert {g["percent"]: g["tax"] for g in t["tax_groups"]} == {D(7): D("2.80"), D(19): D("57.00")}
    assert t["grand_total"] == D("399.78")
    assert t["due"] == D("299.78")


def test_valid_invoice_has_no_errors(invoice):
    assert [i for i in validate_invoice(invoice) if i["level"] == "error"] == []


def test_missing_seller_vat_is_error_not_invented(invoice):
    invoice["seller"]["vat_id"] = ""
    errors = [i["field"] for i in validate_invoice(invoice) if i["level"] == "error"]
    assert "seller.vat_id" in errors


def test_bad_iban_and_overpaid(invoice):
    invoice["payment"]["iban"] = "DE00 0000 0000 0000 0000 00"
    invoice["prepaid_amount"] = 10_000
    errors = {i["field"] for i in validate_invoice(invoice) if i["level"] == "error"}
    assert {"payment.iban", "prepaid_amount"} <= errors
