"""The customer never leaves the machine: masked in data/text/images, no review for buyer-only findings."""
import json
from copy import deepcopy

import httpx
import pytest

import ai_invoice_extractor as ai
import anonymize
import extractor
from pdf_renderer import create_invoice_pdf_from_data

BUYER_LINES = ("Kunde AG", "Allee 2", "10115 Berlin")


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("UNSLOTH_API_KEY", "test-token-never-real")


def _review(invoice, plain, layout="", pdf=None, notes=(), issues=(), response=None):
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.content)
        body = response if response is not None else {"invoice_data": invoice, "uncertain_fields": [], "notes": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})

    result = ai.review_invoice_with_ai(
        pdf_bytes=pdf if pdf is not None else create_invoice_pdf_from_data(invoice),
        plain_text=plain, layout_text=layout, invoice_data=invoice,
        extraction_notes=list(notes), validation_issues=list(issues),
        transport=httpx.MockTransport(handler),
    )
    return result, captured["payload"]


def _plain(invoice):
    s, b = invoice["seller"], invoice["buyer"]
    return "\n".join([
        f"{s['name']}   {s['street']}   {s['postcode']} {s['city']}",
        b["name"], b["street"], f"{b['postcode']} {b['city']}",
        "Rechnung Nr. RE-1", "Beratung 3 100,00 300,00",
    ])


def _sent_text(payload):
    parts = payload["messages"][1]["content"]
    return "".join(p["text"] for p in parts if p["type"] == "text")


def test_customer_is_replaced_in_everything_sent(invoice):
    plain = _plain(invoice)
    _result, payload = _review(invoice, plain, layout=plain.replace("\n", "\n  "))
    sent = json.dumps(payload, ensure_ascii=False)
    for secret in BUYER_LINES + ("Berlin", "10115"):
        assert secret not in sent
    assert anonymize.PLACEHOLDER_NAME in sent and "12345 Musterstadt" in sent
    assert "nicht Teil der Aufgabe" in sent
    assert "Kundendaten gehören nicht zu deiner Aufgabe" in payload["messages"][0]["content"]
    assert "München" in sent  # the seller is untouched


def test_seller_is_not_masked_when_it_shares_data_with_the_buyer(invoice):
    invoice["buyer"]["city"] = invoice["seller"]["city"]
    invoice["buyer"]["postcode"] = invoice["seller"]["postcode"]
    _result, payload = _review(invoice, _plain(invoice))
    assert "80331 München" in _sent_text(payload)


def test_buyer_is_restored_after_the_review_and_ai_cannot_change_it(invoice):
    answer = deepcopy(invoice)
    answer["buyer"] = {"name": "Erfunden GmbH", "street": "X 1", "postcode": "99999", "city": "Nirgendwo", "country": "FR"}
    answer["invoice_id"] = "RE-NEU"
    result, _payload = _review(invoice, _plain(invoice), response={"invoice_data": answer, "uncertain_fields": [], "notes": []})
    assert result["invoice_data"]["buyer"] == invoice["buyer"]
    assert result["invoice_data"]["invoice_id"] == "RE-NEU"


def test_placeholder_echoed_into_a_line_item_is_restored(invoice):
    answer = deepcopy(invoice)
    answer["items"][0]["name"] = "Beratung für Max Mustermann"
    result, _payload = _review(invoice, _plain(invoice), response={"invoice_data": answer, "uncertain_fields": [], "notes": []})
    assert result["invoice_data"]["items"][0]["name"] == "Beratung für Kunde AG"


def test_page_images_are_blanked_where_the_customer_stood(invoice):
    import pypdfium2 as pdfium

    pdf = create_invoice_pdf_from_data(invoice)
    doc = pdfium.PdfDocument(pdf)
    page = doc[0]
    scale = 1.25
    image = page.render(scale=scale).to_pil().convert("RGB")
    left, _b, _r, top = page.get_cropbox()
    textpage = page.get_textpage()

    def dark_pixels(query):
        count = 0
        searcher = textpage.search(query)
        index, n = searcher.get_next()
        boxes = [textpage.get_charbox(i) for i in range(index, index + n)]
        x0, x1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
        y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
        box = ((x0 - left) * scale, (top - y1) * scale, (x1 - left) * scale, (top - y0) * scale)
        region = image.crop(tuple(int(v) for v in box)).convert("L")
        return sum(1 for px in region.getdata() if px < 128)

    assert dark_pixels("Kunde AG") > 0 and dark_pixels("Muster GmbH") > 0  # visible before
    terms = anonymize.build_terms(invoice["buyer"], invoice["seller"])
    found = anonymize.redact_page(page, image, terms, scale)
    assert {"Kunde AG", "Allee 2", "10115 Berlin"} <= found
    assert dark_pixels("Kunde AG") == 0 and dark_pixels("Allee 2") == 0  # blanked
    assert dark_pixels("Muster GmbH") > 0  # seller still readable


def test_images_are_dropped_when_the_customer_cannot_be_blanked(invoice, monkeypatch):
    monkeypatch.setattr(ai, "redact_page", lambda *_a, **_k: set())
    result, payload = _review(invoice, _plain(invoice))
    assert not any(p["type"] == "image_url" for p in payload["messages"][1]["content"])
    assert any("Seitenbilder wurden nicht übertragen" in n for n in result["notes"])


def test_unparsed_recipient_block_is_cut_out_and_nothing_else_about_the_customer_is_sent(invoice):
    invoice["buyer"] = {"name": "", "street": "", "postcode": "", "city": "", "country": ""}
    plain = "\n".join([
        "Muster GmbH   Weg 1   80331 München",
        "Geheimfirma Wuppertal GmbH", "Hinterhof 7", "Rechnung Nr. RE-1", "Beratung 3 100,00 300,00",
    ])
    result, payload = _review(invoice, plain, layout="LAYOUT Geheimfirma Wuppertal")
    sent = json.dumps(payload, ensure_ascii=False)
    assert "Geheimfirma" not in sent and "Hinterhof" not in sent and "LAYOUT" not in sent
    assert "Rechnung Nr. RE-1" in sent and "Beratung" in sent
    assert not any(p["type"] == "image_url" for p in payload["messages"][1]["content"])
    assert any("Empfängerblock" in n for n in result["notes"])


def test_buyer_findings_are_not_sent_to_the_ai(invoice):
    notes = ["Empfängeradresse konnte nicht gelesen werden – bitte Käuferdaten prüfen.", "Rechnungsnummer nicht gefunden."]
    issues = [{"level": "error", "field": "buyer.name", "message": "Name des Käufers fehlt."},
              {"level": "error", "field": "invoice_id", "message": "Rechnungsnummer fehlt."}]
    _result, payload = _review(invoice, _plain(invoice), notes=notes, issues=issues)
    sent = _sent_text(payload)
    assert "Rechnungsnummer nicht gefunden" in sent and "Rechnungsnummer fehlt" in sent
    assert "Empfängeradresse" not in sent and "Käufers" not in sent


def test_scan_without_text_layer_sends_nothing():
    def boom(request):
        raise AssertionError("no request may be made for a scan")

    with pytest.raises(ai.InvoiceReviewError, match="Textebene"):
        ai.review_invoice_with_ai(
            pdf_bytes=b"%PDF", plain_text="  ", layout_text="", invoice_data={"buyer": {"name": "Geheim"}},
            extraction_notes=[], validation_issues=[], transport=httpx.MockTransport(boom),
        )


# ------------------------------------------------------------ when is the AI used at all?

def test_only_the_buyer_unreadable_means_no_review(invoice):
    buyer_note = ["Empfängeradresse konnte nicht gelesen werden – bitte Käuferdaten prüfen."]
    broken = deepcopy(invoice)
    broken["buyer"] = {"name": "", "street": "", "postcode": "", "city": "", "country": ""}
    assert ai.should_review_with_ai(plain_text="Rechnung", invoice_data=broken, extraction_notes=buyer_note) is False


def test_other_findings_still_trigger_the_review(invoice):
    buyer_note = ["Empfängeradresse konnte nicht gelesen werden – bitte Käuferdaten prüfen."]
    assert ai.should_review_with_ai(plain_text="x", invoice_data=invoice, extraction_notes=buyer_note + ["Keine gültige IBAN gefunden."])
    broken = deepcopy(invoice)
    broken["invoice_id"] = ""
    assert ai.should_review_with_ai(plain_text="x", invoice_data=broken, extraction_notes=buyer_note)
    assert ai.should_review_with_ai(plain_text="", invoice_data=invoice, extraction_notes=[])  # scan


def test_parse_flow_skips_the_ai_when_only_the_buyer_is_unclear(monkeypatch, invoice):
    monkeypatch.setenv("ZUGFERD_AI_ENABLED", "true")
    broken = deepcopy(invoice)
    broken["buyer"] = {"name": "", "street": "", "postcode": "", "city": "", "country": ""}
    calls = []
    monkeypatch.setattr(extractor, "check_existing_zugferd", lambda _pdf: None)
    monkeypatch.setattr(extractor, "extract_text_from_pdf", lambda _pdf: "Rechnung RE-1")
    monkeypatch.setattr(extractor, "extract_layout_text_from_pdf", lambda _pdf: "")
    monkeypatch.setattr(
        extractor, "extract_invoice_data_from_text",
        lambda _p, _l, notes: notes.append("Empfängeradresse konnte nicht gelesen werden – bitte Käuferdaten prüfen.") or deepcopy(broken),
    )
    monkeypatch.setattr(extractor, "review_invoice_with_ai", lambda **kw: calls.append(kw), raising=False)

    result = extractor.parse_uploaded_file(b"%PDF", "x.pdf")

    assert calls == []
    assert result["ai_review_needed"] is False and result["ai_review_attempted"] is False
    assert any("Käuferdaten prüfen" in n for n in result["extraction_notes"])
