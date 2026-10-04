import keyvault
from ai_invoice_extractor import load_api_key


def test_encrypt_roundtrip_hides_plaintext():
    blob = keyvault.encrypt("tok-äöü-123")
    assert "tok" not in blob
    assert keyvault.decrypt(blob) == "tok-äöü-123"


def test_tampered_or_garbage_blob_returns_none():
    blob = keyvault.encrypt("secret")
    assert keyvault.decrypt(blob[:-2] + "zz") is None
    assert keyvault.decrypt("not a blob") is None


def test_embedded_token_is_last_fallback(monkeypatch, tmp_path):
    for name in ("UNSLOTH_API_KEY", "OPENAI_API_KEY", "AI_API_KEY", "OPENAI_COMPATIBLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(keyvault, "embedded_token", lambda: "embedded")
    assert load_api_key(tmp_path / "missing.env") == "embedded"
    env = tmp_path / ".env"
    env.write_text("UNSLOTH_API_KEY=from-env-file\n", encoding="utf-8")
    assert load_api_key(env) == "from-env-file"

