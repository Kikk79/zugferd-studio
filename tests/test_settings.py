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


@pytest.fixture()
def clean_env(monkeypatch):
    for name in ai._KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    env = ai.DATA_DIR / ".env"
    env.unlink(missing_ok=True)
    monkeypatch.setattr("keyvault.embedded_token", lambda: None)
    yield env
    env.unlink(missing_ok=True)


def _put(**extra):
    body = {"output_dir": "", "open_pdf": True, "ai_enabled": True, **extra}
    return client.put("/api/settings", json=body)


def test_token_is_stored_in_dotenv_and_never_returned(clean_env):
    assert client.get("/api/settings").json()["token_source"] is None
    r = _put(api_token="tok-secret-12345")
    assert r.status_code == 200
    assert "tok-secret-12345" not in r.text and "tok-secret-12345" not in client.get("/api/settings").text
    assert r.json()["token_source"] == "dotenv"
    assert ai.load_api_key() == "tok-secret-12345"
    assert "UNSLOTH_API_KEY=tok-secret-12345" in clean_env.read_text(encoding="utf-8")


def test_token_unchanged_when_not_sent_and_removed_when_empty(clean_env):
    _put(api_token="keep-me-123456")
    _put()  # api_token omitted -> untouched
    assert ai.load_api_key() == "keep-me-123456"
    r = _put(api_token="")
    assert r.json()["token_source"] is None and ai.load_api_key() is None
    assert not clean_env.exists()


def test_token_replacement_keeps_other_dotenv_lines(clean_env):
    clean_env.write_text("UNSLOTH_MODEL=my-model\nUNSLOTH_API_KEY=old-token\n", encoding="utf-8")
    _put(api_token="new-token-123")
    text = clean_env.read_text(encoding="utf-8")
    assert "UNSLOTH_MODEL=my-model" in text and "old-token" not in text
    assert text.count("UNSLOTH_API_KEY=") == 1 and ai.load_api_key() == "new-token-123"


def test_bare_legacy_token_line_is_replaced(clean_env):
    clean_env.write_text("legacy-bare-token\n", encoding="utf-8")
    _put(api_token="fresh-token-123")
    assert "legacy-bare-token" not in clean_env.read_text(encoding="utf-8")
    assert ai.load_api_key() == "fresh-token-123"


def test_invalid_token_is_rejected_without_side_effects(clean_env):
    for bad in ("two words", "line\nbreak", '"quoted"'):
        r = _put(output_dir="", open_pdf=False, api_token=bad)
        assert r.status_code == 400
    assert output.load_config()["open_pdf"] is True and not clean_env.exists()


def test_env_var_beats_dotenv_and_is_reported(clean_env, monkeypatch):
    _put(api_token="from-dotenv-123")
    monkeypatch.setenv("UNSLOTH_API_KEY", "from-process-env")
    assert client.get("/api/settings").json()["token_source"] == "env"
    assert ai.load_api_key() == "from-process-env"


def test_model_thinking_context_roundtrip_through_ini():
    r = _put(ai_model="unsloth/Test-9B", ai_thinking="xhigh", ai_context=65536)
    assert r.status_code == 200
    body = r.json()
    assert (body["ai_model"], body["ai_thinking"], body["ai_context"]) == ("unsloth/Test-9B", "xhigh", 65536)
    cfg = output.load_config()
    assert (cfg["ai_model"], cfg["ai_thinking"], cfg["ai_context"]) == ("unsloth/Test-9B", "xhigh", 65536)
    text = output.CONFIG_PATH.read_text(encoding="utf-8")
    assert "modell = unsloth/Test-9B" in text and "denken = xhigh" in text and "kontext = 65536" in text


def test_invalid_ai_options_are_rejected_without_side_effects():
    for bad in ({"ai_thinking": "extrem"}, {"ai_context": 100}, {"ai_context": 10**9},
                {"ai_model": "zwei worte"}, {"ai_model": "x ; y"}):
        assert _put(open_pdf=False, **bad).status_code == 400
    assert output.load_config()["open_pdf"] is True


def test_old_ini_without_new_ai_keys_uses_defaults():
    output.CONFIG_PATH.write_text("[KI]\naktiv = ja\n", encoding="utf-8")
    cfg = output.load_config()
    assert (cfg["ai_model"], cfg["ai_thinking"], cfg["ai_context"]) == ("", "xhigh", 32768)


def test_env_model_beats_ini_beats_default(monkeypatch):
    monkeypatch.delenv("UNSLOTH_MODEL", raising=False)
    assert ai.ai_settings()["model"] == ai.DEFAULT_MODEL
    _put(ai_model="ini-model")
    assert ai.ai_settings()["model"] == "ini-model"
    monkeypatch.setenv("UNSLOTH_MODEL", "env-model")
    assert ai.ai_settings()["model"] == "env-model"
    assert client.get("/api/settings").json()["ai_model_forced_by_env"] is True
