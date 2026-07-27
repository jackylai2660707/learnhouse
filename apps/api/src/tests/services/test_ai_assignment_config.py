from types import SimpleNamespace

from src.services.ai.assignment_config import (
    assignment_ai_configuration_error,
    assignment_ai_status,
    assignment_generation_model,
    normalize_openai_base_url,
)


def _config(**values):
    return SimpleNamespace(ai_config=SimpleNamespace(**values))


def test_assignment_ai_status_reports_ready_openai_compatible_endpoint():
    config = _config(
        provider="openai_compatible",
        openai_base_url="https://api.example.test/v1",
        openai_api_key="secret",
        openai_model="gpt-5.4-mini",
    )

    status = assignment_ai_status(config)

    assert status == {
        "ready": True,
        "provider": "openai_compatible",
        "model": "gpt-5.4-mini",
        "message": "AI 出題已配置，可一鍵生成選擇、填空和短問答，並自動批改。",
    }
    assert assignment_ai_configuration_error(config) is None
    assert assignment_generation_model(config) == "gpt-5.4-mini"


def test_assignment_ai_status_reports_missing_openai_compatible_fields():
    config = _config(
        provider="openai_compatible",
        openai_base_url="https://api.example.test/v1",
        openai_api_key="",
        openai_model=None,
    )

    status = assignment_ai_status(config)

    assert status["ready"] is False
    assert status["provider"] == "openai_compatible"
    assert status["model"] == ""
    assert "LEARNHOUSE_OPENAI_API_KEY" in status["message"]
    assert "LEARNHOUSE_OPENAI_MODEL" in status["message"]


def test_assignment_ai_status_reports_invalid_openai_compatible_base_url():
    config = _config(
        provider="openai_compatible",
        openai_base_url="api.example.test/v1",
        openai_api_key="secret",
        openai_model="gpt-test",
    )

    status = assignment_ai_status(config)

    assert status["ready"] is False
    assert "LEARNHOUSE_OPENAI_BASE_URL 需使用完整 http(s) URL" in status["message"]


def test_normalize_openai_base_url_accepts_common_endpoint_paths():
    assert (
        normalize_openai_base_url("https://api.example.test/v1/chat/completions")
        == "https://api.example.test/v1"
    )
    assert (
        normalize_openai_base_url("https://api.example.test/v1/images/generations")
        == "https://api.example.test/v1"
    )
    assert (
        normalize_openai_base_url(" https://api.example.test/v1/ ")
        == "https://api.example.test/v1"
    )


def test_assignment_ai_status_reports_missing_gemini_key():
    config = _config(provider="gemini", gemini_api_key="")

    status = assignment_ai_status(config)

    assert status["ready"] is False
    assert status["provider"] == "gemini"
    assert status["model"] == "gemini-2.0-flash"
    assert "LEARNHOUSE_GEMINI_API_KEY" in status["message"]


def test_assignment_ai_status_rejects_unknown_provider():
    config = _config(provider="unknown")

    status = assignment_ai_status(config)

    assert status["ready"] is False
    assert status["provider"] == "unknown"
    assert status["message"] == "AI 出題供應商設定不正確：請使用 gemini 或 openai_compatible。"


def test_assignment_ai_status_degrades_when_config_reader_fails(monkeypatch):
    def failing_config():
        raise RuntimeError("broken config")

    monkeypatch.setattr("src.services.ai.assignment_config.get_learnhouse_config", failing_config)

    status = assignment_ai_status()

    assert status["ready"] is False
    assert status["provider"] == "unknown"
    assert status["model"] == ""
    assert status["message"] == "AI 出題配置讀取失敗：broken config"


def test_assignment_ai_status_degrades_when_config_has_no_ai_config():
    status = assignment_ai_status(SimpleNamespace())

    assert status["ready"] is False
    assert status["provider"] == "unknown"
    assert status["model"] == ""
    assert "AI 出題配置讀取失敗" in status["message"]
