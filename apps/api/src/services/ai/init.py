from src.services.ai.base import get_gemini_client as _get_configured_ai_client

def get_gemini_client():
    """Backward-compatible entrypoint for the configured AI client."""
    try:
        return _get_configured_ai_client()
    except Exception:
        return None
