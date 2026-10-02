"""Output location and naming: <source>_Zug.pdf, config path, source folder, fallback next to the .exe."""
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

import app as appmod
import output
from pdf_renderer import create_invoice_pdf_from_data

client = TestClient(appmod.app)


@pytest.fixture()
def config(tmp_path):
    """Write ZUGFeRD-Studio.ini for one test, restore the default afterwards."""
    def write(pfad=""):
        output.CONFIG_PATH.write_text(f"[Ausgabe]\npfad = {pfad}\noeffnen = nein\n", encoding="utf-8")
    yield write
    output.CONFIG_PATH.write_text(output.DEFAULT_CONFIG, encoding="utf-8")


def test_default_config_is_created_next_to_exe(exe_dir):
    assert (exe_dir / "ZUGFeRD-Studio.ini").exists()
    assert output.load_config()["output_dir"] is None


def test_name_is_source_stem_plus_zug():
    assert output.output_name("Rechnung 393.pdf") == "Rechnung 393_Zug.pdf"
    assert output.output_name("Angebot.docx") == "Angebot_Zug.pdf"


def test_saved_next_to_source_and_nothing_else(tmp_path, config):
    config("")
    src_dir = tmp_path / "Rechnungen"
    src_dir.mkdir()
    source = src_dir / "Rechnung 7.pdf"
    source.write_bytes(b"%PDF-original")
    result = output.save_output(b"%PDF-zugferd", source.name, str(source))
    assert result["saved"] and result["path"] == str(src_dir / "Rechnung 7_Zug.pdf")
    assert sorted(p.name for p in src_dir.iterdir()) == ["Rechnung 7.pdf", "Rechnung 7_Zug.pdf"]
    assert source.read_bytes() == b"%PDF-original"


def test_config_path_wins_over_source_folder(tmp_path, config):
    target = tmp_path / "ZUGFeRD-Ausgang"
    config(str(target))
    source = tmp_path / "a.pdf"
    source.write_bytes(b"x")
    result = output.save_output(b"%PDF", "a.pdf", str(source))
    assert result["saved"] and (target / "a_Zug.pdf").exists()


def test_unknown_folder_falls_back_next_to_exe(exe_dir, config):
    config("")
    result = output.save_output(b"%PDF", "browser.pdf", None)
    assert result["saved"] and result["path"] == str(exe_dir / "browser_Zug.pdf")


def test_dialog_upload_saves_next_to_source(tmp_path, invoice, monkeypatch, config):
    config("")
    source = tmp_path / "Kunde X Rechnung.pdf"
    source.write_bytes(create_invoice_pdf_from_data(invoice))
    monkeypatch.setattr(appmod, "pick_file_dialog", lambda: str(source))
    j = client.post("/api/pick-file", json={"profile": "en16931"}).json()
    assert j["source_path"] == str(source.resolve())
    # The heuristic extractor may or may not get everything from the rendered PDF;
    # once valid data is generated, the file lands next to the source.
    r = client.post("/api/generate", json={"session_id": j["session_id"], "invoice_data": invoice}).json()
    assert r["output"]["saved"] and r["output"]["path"] == str(tmp_path / "Kunde X Rechnung_Zug.pdf")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Kunde X Rechnung.pdf", "Kunde X Rechnung_Zug.pdf"]


def test_dialog_cancel(monkeypatch):
    monkeypatch.setattr(appmod, "pick_file_dialog", lambda: None)
    assert client.post("/api/pick-file", json={}).json() == {"cancelled": True}


def test_browser_upload_saves_next_to_exe(exe_dir, invoice, config):
    config("")
    pdf = create_invoice_pdf_from_data(invoice)
    j = client.post("/api/upload", files={"file": ("Browser Rechnung.pdf", pdf, "application/pdf")}).json()
    r = client.post("/api/generate", json={"session_id": j["session_id"], "invoice_data": invoice}).json()
    assert r["output"]["path"] == str(exe_dir / "Browser Rechnung_Zug.pdf")
    disposition = unquote(client.get(r["pdf_download_url"]).headers["content-disposition"])
    assert "Browser Rechnung_Zug.pdf" in disposition


def test_sample_is_not_written_to_disk(exe_dir):
    before = set(exe_dir.iterdir())
    j = client.get("/api/sample").json()
    assert j["output"] is None and set(exe_dir.iterdir()) == before


def test_pending_file_from_exe_drop(tmp_path, invoice, config):
    config("")
    source = tmp_path / "gezogen.pdf"
    source.write_bytes(create_invoice_pdf_from_data(invoice))
    appmod.queue_file(str(source))
    j = client.post("/api/pending", json={}).json()
    assert j["source_path"] == str(source.resolve())
    assert client.post("/api/pending", json={}).json() == {"empty": True}


def test_queue_file_requires_instance_token(tmp_path):
    assert client.post("/api/queue-file", json={"path": str(tmp_path)}).status_code == 403
    ok = client.post("/api/queue-file", json={"path": "x"}, headers={"X-Instance-Token": appmod.INSTANCE_TOKEN})
    assert ok.status_code == 200
    appmod._pending.clear()


def test_foreign_origin_is_rejected():
    r = client.post("/api/pick-file", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
