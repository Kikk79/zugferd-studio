import pytest
from fastapi.testclient import TestClient

import app as appmod
from pdf_renderer import create_invoice_pdf_from_data

client = TestClient(appmod.app)
UNKNOWN = "00000000-0000-0000-0000-000000000000"


def test_sample_roundtrip():
    j = client.get("/api/sample").json()
    assert j["status"] == "valid_zugferd" and j["business_rules"]["status"] in ("passed", "unavailable")
    assert client.get(j["pdf_download_url"]).content.startswith(b"%PDF")
    assert client.get(j["xml_download_url"]).status_code == 200


def test_upload_sanitizes_path_in_filename(invoice):
    """A traversal-shaped filename is reduced to its basename, never used as a path."""
    pdf_bytes = create_invoice_pdf_from_data(invoice)
    r = client.post("/api/upload", files={"file": ("..\\..\\x/rechnung.pdf", pdf_bytes, "application/pdf")})
    j = r.json()
    assert r.status_code == 200 and j["filename"] == "rechnung.pdf"


def test_upload_then_generate_with_corrected_data(invoice):
    pdf_bytes = create_invoice_pdf_from_data(invoice)
    r = client.post("/api/upload", files={"file": ("rechnung.pdf", pdf_bytes, "application/pdf")})
    j = r.json()
    assert r.status_code == 200 and j["filename"] == "rechnung.pdf"
    session_id = j["session_id"]

    # Regardless of what the heuristic extractor read from the rendered PDF, feeding
    # back known-incomplete data must block generation, and known-good data must succeed.
    incomplete = {**invoice, "seller": {**invoice["seller"], "vat_id": ""}}
    bad = client.post("/api/generate", json={"session_id": session_id, "invoice_data": incomplete})
    assert bad.status_code == 422 and bad.json()["issues"]["errors"]

    good = client.post("/api/generate", json={"session_id": session_id, "profile": "basic", "invoice_data": invoice})
    assert good.status_code == 200 and good.json()["profile"] == "basic" and good.json()["totals"]["gross"] == 399.78


def test_existing_zugferd_is_recognised():
    sample = client.get("/api/sample").json()
    pdf = client.get(sample["pdf_download_url"]).content
    j = client.post("/api/upload", files={"file": ("z.pdf", pdf, "application/pdf")}).json()
    assert j["status"] == "already_zugferd" and j["invoice_data"]["invoice_id"] == sample["invoice_data"]["invoice_id"]


def test_calculate_endpoint():
    inv = client.get("/api/sample").json()["invoice_data"]
    r = client.post("/api/calculate", json={"invoice_data": inv}).json()
    assert r["totals"]["gross"] == 2772.7


@pytest.mark.parametrize("path", ["/api/download/pdf/..", "/api/download/pdf/not-a-uuid", f"/api/preview/{UNKNOWN}"])
def test_bad_session_ids_are_404(path):
    assert client.get(path).status_code == 404


def test_upload_rejections():
    assert client.post("/api/upload", files={"file": ("x.exe", b"abc")}).status_code == 400
    assert client.post("/api/upload", files={"file": ("x.pdf", b"")}).status_code == 400
    assert client.post("/api/upload", files={"file": ("x.pdf", b"not a pdf")}).status_code == 422
