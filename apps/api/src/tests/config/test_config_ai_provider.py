import pytest

from config.config import _read_secret_file, _resolve_ai_provider


def test_resolve_ai_provider_accepts_configured_openai_compatible_with_whitespace():
    assert (
        _resolve_ai_provider(
            " OpenAI_Compatible ",
            openai_base_url=None,
            openai_api_key=None,
        )
        == "openai_compatible"
    )


def test_resolve_ai_provider_infers_openai_compatible_when_endpoint_is_configured():
    assert (
        _resolve_ai_provider(
            "wrong-value",
            openai_base_url="https://api.example.test/v1",
            openai_api_key=None,
        )
        == "openai_compatible"
    )


def test_resolve_ai_provider_infers_openai_compatible_when_api_key_is_configured():
    assert (
        _resolve_ai_provider(
            None,
            openai_base_url=None,
            openai_api_key="secret",
        )
        == "openai_compatible"
    )


def test_resolve_ai_provider_falls_back_to_gemini_without_openai_config():
    assert (
        _resolve_ai_provider(
            "wrong-value",
            openai_base_url=None,
            openai_api_key=None,
        )
        == "gemini"
    )


def test_read_secret_file_strips_newline_without_exposing_value(tmp_path):
    secret_path = tmp_path / "smtp-password"
    secret_path.write_text("strong-test-secret\n", encoding="utf-8")

    assert _read_secret_file(str(secret_path), "SMTP password") == "strong-test-secret"


def test_read_secret_file_reports_only_safe_label(tmp_path):
    missing_path = tmp_path / "missing-secret"

    with pytest.raises(ValueError, match="Unable to read configured SMTP password secret file"):
        _read_secret_file(str(missing_path), "SMTP password")
