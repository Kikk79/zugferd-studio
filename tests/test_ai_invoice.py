from copy import deepcopy

import extractor


def test_missing_local_field_is_repaired_by_ai_review(monkeypatch, invoice):
    monkeypatch.setenv("ZUGFERD_AI_ENABLED", "true")
    local_invoice = deepcopy(invoice)
    local_invoice["invoice_id"] = ""
    local_notes = ["Rechnungsnummer nicht gefunden."]
    calls = []

    monkeypatch.setattr(extractor, "check_existing_zugferd", lambda _pdf: None)
    monkeypatch.setattr(
        extractor,
        "extract_text_from_pdf",
        lambda _pdf: "Rechnung ohne erkannte Nummer",
    )
    monkeypatch.setattr(extractor, "extract_layout_text_from_pdf", lambda _pdf: "")
    monkeypatch.setattr(
        extractor,
        "extract_invoice_data_from_text",
        lambda _plain, _layout, notes: (
            notes.extend(local_notes) or deepcopy(local_invoice)
        ),
    )

    def review_invoice_with_ai(**kwargs):
        calls.append(kwargs)
        corrected = deepcopy(kwargs["invoice_data"])
        corrected["invoice_id"] = "RE-2026-1042"
        return {
            "invoice_data": corrected,
            "uncertain_fields": [],
            "model": "unsloth/Qwen3.8-27B-GGUF",
        }

    monkeypatch.setattr(
        extractor, "review_invoice_with_ai", review_invoice_with_ai, raising=False
    )

    result = extractor.parse_uploaded_file(b"synthetic-pdf", "rechnung.pdf")

    assert len(calls) == 1
    assert calls[0]["plain_text"] == "Rechnung ohne erkannte Nummer"
    assert result["invoice_data"]["invoice_id"] == "RE-2026-1042"
    assert result["ai_review_used"] is True
    assert result["ai_model"] == "unsloth/Qwen3.8-27B-GGUF"
    assert any("Qwen3.8-27B-GGUF" in note for note in result["extraction_notes"])
    assert any("KI-Prüfung" in note for note in result["extraction_notes"])


def test_ai_reviewer_uses_unsloth_model_and_preserves_uncertain_fields(
    monkeypatch, invoice
):
    import json

    import httpx

    from ai_invoice_extractor import (
        DEFAULT_BASE_URL,
        DEFAULT_MODEL,
        review_invoice_with_ai,
    )
    from pdf_renderer import create_invoice_pdf_from_data

    monkeypatch.setenv("UNSLOTH_API_KEY", "test-token-never-real")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        response_data = {
            "invoice_data": {**invoice, "invoice_id": "RE-2026-1042"},
            "uncertain_fields": ["seller.vat_id"],
            "notes": [],
        }
        content = json.dumps(response_data)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    reviewed = review_invoice_with_ai(
        pdf_bytes=create_invoice_pdf_from_data(invoice),
        plain_text="Rechnung RE-1",
        layout_text="",
        invoice_data=invoice,
        extraction_notes=["Rechnungsnummer prüfen"],
        validation_issues=[],
        transport=httpx.MockTransport(handler),
    )

    assert captured["url"] == f"{DEFAULT_BASE_URL}/chat/completions"
    assert captured["authorization"] == "Bearer test-token-never-real"
    assert captured["payload"]["model"] == DEFAULT_MODEL == "prism-ml/Ternary-Bonsai-2-27B-gguf:PQ2_0"
    assert any(
        part["type"] == "image_url"
        for part in captured["payload"]["messages"][1]["content"]
    )
    assert reviewed["invoice_data"]["invoice_id"] == "RE-2026-1042"
    assert reviewed["invoice_data"]["seller"]["vat_id"] == invoice["seller"]["vat_id"]
    assert reviewed["uncertain_fields"] == ["seller.vat_id"]


def test_api_key_can_be_loaded_from_dotenv_without_logging_it(monkeypatch, tmp_path):
    from ai_invoice_extractor import load_api_key

    env_file = tmp_path / ".env"
    env_file.write_text("UNSLOTH_API_KEY='test-token-never-real'\n", encoding="utf-8")
    monkeypatch.delenv("UNSLOTH_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert load_api_key(env_file) == "test-token-never-real"


def test_api_key_loader_accepts_a_bare_token_with_padding(monkeypatch, tmp_path):
    from ai_invoice_extractor import load_api_key

    env_file = tmp_path / ".env"
    env_file.write_text("test-token-with-padding==\n", encoding="utf-8")
    for name in (
        "UNSLOTH_API_KEY",
        "OPENAI_API_KEY",
        "AI_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    assert load_api_key(env_file) == "test-token-with-padding=="


def test_ai_auth_failure_keeps_local_fields_and_explains_manual_review(
    monkeypatch, invoice
):
    import extractor
    from ai_invoice_extractor import InvoiceReviewError

    monkeypatch.setenv("ZUGFERD_AI_ENABLED", "true")
    local_invoice = deepcopy(invoice)
    local_invoice["invoice_id"] = ""
    monkeypatch.setattr(extractor, "check_existing_zugferd", lambda _pdf: None)
    monkeypatch.setattr(extractor, "extract_text_from_pdf", lambda _pdf: "Rechnung")
    monkeypatch.setattr(extractor, "extract_layout_text_from_pdf", lambda _pdf: "")
    monkeypatch.setattr(
        extractor,
        "extract_invoice_data_from_text",
        lambda _plain, _layout, notes: (
            notes.append("Rechnungsnummer nicht gefunden.") or local_invoice
        ),
    )

    def rejected(**_kwargs):
        raise InvoiceReviewError("Der KI-Endpunkt antwortete mit HTTP 401.")

    monkeypatch.setattr(extractor, "review_invoice_with_ai", rejected)
    result = extractor.parse_uploaded_file(b"synthetic-pdf", "rechnung.pdf")

    assert result["invoice_data"]["invoice_id"] == ""
    assert result["ai_review_needed"] is True
    assert result["ai_review_attempted"] is True
    assert result["ai_review_used"] is False
    assert any("HTTP 401" in note for note in result["extraction_notes"])


def test_ai_review_required_blocks_automatic_zugferd_creation(monkeypatch, invoice):
    import app as appmod
    from pdf_renderer import create_invoice_pdf_from_data

    monkeypatch.setattr(
        appmod,
        "parse_uploaded_file",
        lambda *_args: {
            "invoice_data": invoice,
            "extraction_notes": ["KI-Prüfung nicht verfügbar (HTTP 401)."],
            "existing_zugferd_info": None,
            "ai_review_needed": True,
            "ai_review_attempted": True,
            "ai_review_used": False,
            "ai_model": None,
        },
    )

    result = appmod._process_document(
        create_invoice_pdf_from_data(invoice),
        "review.pdf",
        "en16931",
        auto_generate=True,
    )

    assert result["status"] == "needs_review"
    assert result["pdf_download_url"] is None
    assert result["ai_review_attempted"] is True
    assert result["ai_review_used"] is False


def test_thinking_effort_and_context_budget_reach_the_request(monkeypatch, invoice):
    import json

    import httpx

    import output
    from ai_invoice_extractor import review_invoice_with_ai
    from pdf_renderer import create_invoice_pdf_from_data

    monkeypatch.setenv("UNSLOTH_API_KEY", "test-token-never-real")
    monkeypatch.delenv("UNSLOTH_MODEL", raising=False)
    output.CONFIG_PATH.write_text(
        output.render_config(ai_model="ini-model", ai_thinking="hoch", ai_context=4096), encoding="utf-8"
    )
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"invoice_data": invoice})}}]}
        )

    try:
        result = review_invoice_with_ai(
            pdf_bytes=create_invoice_pdf_from_data(invoice),
            plain_text="x" * 50_000,
            layout_text="",
            invoice_data=invoice,
            extraction_notes=["prüfen"],
            validation_issues=[],
            transport=httpx.MockTransport(handler),
        )
    finally:
        output.CONFIG_PATH.write_text(output.DEFAULT_CONFIG, encoding="utf-8")

    payload = captured[0]
    assert payload["model"] == "ini-model" == result["model"]
    assert payload["reasoning_effort"] == "high"
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    sent = json.loads(payload["messages"][1]["content"][0]["text"])
    assert len(sent["pdf_text"]) < 50_000  # trimmed to the small context budget


def test_thinking_off_standard_and_plain_retry():
    from ai_invoice_extractor import _text_budgets, _thinking_params

    assert _thinking_params("standard") == {}
    assert _thinking_params("aus") == {"chat_template_kwargs": {"enable_thinking": False}}
    assert _thinking_params("niedrig")["reasoning_effort"] == "low"
    assert _thinking_params("xhigh")["reasoning_effort"] == "xhigh"
    text_cap, layout_cap = _text_budgets(32768, 0, 10_000, 10_000)
    assert text_cap + layout_cap <= (32768 - 6000) * 3 and layout_cap == 10_000


def test_list_ai_models(monkeypatch):
    import httpx

    from ai_invoice_extractor import list_ai_models

    monkeypatch.setenv("UNSLOTH_API_KEY", "test-token-never-real")
    handler = lambda request: httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]})
    assert list_ai_models(transport=httpx.MockTransport(handler)) == ["a", "b"]
