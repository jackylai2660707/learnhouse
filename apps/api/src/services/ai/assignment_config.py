from typing import Any
from urllib.parse import urlparse

from config.config import get_learnhouse_config


CONFIG_READ_ERROR_PREFIX = "AI 出題配置讀取失敗"
CONFIG_READY_MESSAGE = "AI 出題已配置，可一鍵生成選擇、填空和短問答，並自動批改。"
OPENAI_BASE_URL_ERROR = (
    "LEARNHOUSE_OPENAI_BASE_URL 需使用完整 http(s) URL，"
    "例如 https://api.example.com/v1。"
)


def _config_read_error_message(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"{CONFIG_READ_ERROR_PREFIX}：{detail}"


def _ai_config(config: Any | None = None) -> Any:
    return (config or get_learnhouse_config()).ai_config


def normalize_openai_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/images/generations", "/embeddings"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip("/")
            break

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(OPENAI_BASE_URL_ERROR)
    return text


def assignment_ai_configuration_error(config: Any | None = None) -> str | None:
    try:
        ai_config = _ai_config(config)
    except Exception as exc:
        return _config_read_error_message(exc)

    provider = getattr(ai_config, "provider", "gemini")

    if provider == "openai_compatible":
        base_url = getattr(ai_config, "openai_base_url", None)
        missing = [
            label
            for label, value in {
                "LEARNHOUSE_OPENAI_BASE_URL": str(base_url or "").strip(),
                "LEARNHOUSE_OPENAI_API_KEY": getattr(ai_config, "openai_api_key", None),
                "LEARNHOUSE_OPENAI_MODEL": getattr(ai_config, "openai_model", None),
            }.items()
            if not value
        ]
        if missing:
            return f"AI 出題尚未配置完整：請設定 {', '.join(missing)}。"
        try:
            normalize_openai_base_url(base_url)
        except ValueError as exc:
            return f"AI 出題尚未配置完整：{exc}"
        return None

    if provider == "gemini":
        if not getattr(ai_config, "gemini_api_key", None):
            return "AI 出題尚未配置完整：請設定 LEARNHOUSE_GEMINI_API_KEY，或改用 OpenAI-compatible 端點。"
        return None

    return "AI 出題供應商設定不正確：請使用 gemini 或 openai_compatible。"


def assignment_generation_model(config: Any | None = None) -> str:
    try:
        ai_config = _ai_config(config)
    except Exception:
        return ""

    provider = getattr(ai_config, "provider", "gemini")
    if provider == "openai_compatible":
        return getattr(ai_config, "openai_model", None) or ""
    return "gemini-2.0-flash"


def assignment_ai_status(config: Any | None = None) -> dict:
    try:
        ai_config = _ai_config(config)
        provider = getattr(ai_config, "provider", "gemini")
    except Exception as exc:
        return {
            "ready": False,
            "provider": "unknown",
            "model": "",
            "message": _config_read_error_message(exc),
        }

    error = assignment_ai_configuration_error(config)
    return {
        "ready": error is None,
        "provider": provider,
        "model": assignment_generation_model(config),
        "message": error or CONFIG_READY_MESSAGE,
    }
