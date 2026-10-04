"""Settings tab: the INI is read/written through the API, KI switch lives in the INI."""
import pytest
from fastapi.testclient import TestClient

import ai_invoice_extractor as ai
import app as appmod
import output

client = TestClient(appmod.app)


@pytest.fixture(autouse=True)
def fresh_ini(monkeypatch):
    output.CONFIG_PATH.write_text(output.DEFAULT_CONFIG, encoding="utf-8")
    monkeypatch.delenv("ZUGFERD_AI_ENABLED", raising=False)
    yield
    output.CONFIG_PATH.write_text(output.DEFAULT_CONFIG, encoding="utf-8")


def test_get_returns_defaults(monkeypatch):
    monkeypatch.setenv("ZUGFERD_AI_ENABLED", "false")  # as in the test environment
    body = client.get("/api/settings").json()
    assert body["output_dir"] == ""
    assert body["open_pdf"] is True and body["ai_enabled"] is True
    assert body["ai_forced_by_env"] is True
    assert body["ini_path"] == str(output.CONFIG_PATH)


def test_put_writes_ini_and_load_config_sees_it(tmp_path):
    target = tmp_path / "ausgabe" / "zugferd"
    r = client.put("/api/settings", json={"output_dir": str(target), "open_pdf": False, "ai_enabled": False})
    assert r.status_code == 200
    assert target.is_dir()  # created on save
    cfg = output.load_config()
    assert cfg["output_dir"] == target
    assert cfg["open_pdf"] is False and cfg["ai_enabled"] is False
    text = output.CONFIG_PATH.read_text(encoding="utf-8")
    assert "oeffnen = nein" in text and "aktiv = nein" in text and ";" in text  # comments kept


def test_empty_output_dir_means_source_folder():
    client.put("/api/settings", json={"output_dir": "  ", "open_pdf": True, "ai_enabled": True})
    assert output.load_config()["output_dir"] is None


def test_unusable_output_dir_is_rejected(tmp_path):
    blocker = tmp_path / "datei.txt"
    blocker.write_text("x")
    r = client.put("/api/settings", json={"output_dir": str(blocker / "unter"), "open_pdf": True, "ai_enabled": True})
    assert r.status_code == 400
    assert "Ausgabeordner" in r.json()["detail"]
    assert output.load_config()["output_dir"] is None  # nothing was written


def test_foreign_origin_cannot_change_settings():
    r = client.put("/api/settings", json={"output_dir": "", "open_pdf": False, "ai_enabled": False},
                   headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert output.load_config()["open_pdf"] is True
    assert client.post("/api/pick-folder", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_ai_switch_comes_from_ini_and_env_wins(monkeypatch):
    assert ai.ai_review_enabled() is True
    client.put("/api/settings", json={"output_dir": "", "open_pdf": True, "ai_enabled": False})
    assert ai.ai_review_enabled() is False
    monkeypatch.setenv("ZUGFERD_AI_ENABLED", "true")
    assert ai.ai_review_enabled() is True


def test_old_ini_without_ki_section_keeps_ai_on():
    output.CONFIG_PATH.write_text("[Ausgabe]\npfad =\noeffnen = ja\n", encoding="utf-8")
    assert output.load_config()["ai_enabled"] is True


def test_path_with_backslashes_survives_roundtrip(tmp_path):
    folder = tmp_path / "Rechnungen ÄÖ"
    client.put("/api/settings", json={"output_dir": str(folder), "open_pdf": True, "ai_enabled": True})
    assert client.get("/api/settings").json()["output_dir"] == str(folder)
