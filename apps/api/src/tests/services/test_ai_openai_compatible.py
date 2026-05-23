from types import SimpleNamespace
import base64

from src.services.ai.base import (
    _OpenAICompatibleModels,
    _gemini_contents_to_openai_messages,
    generate_openai_compatible_image,
)


def test_gemini_contents_to_openai_messages_converts_roles_and_parts():
    messages = _gemini_contents_to_openai_messages([
        {"role": "user", "parts": [{"text": "System prompt"}]},
        {"role": "model", "parts": [{"text": "Acknowledged"}]},
        {"role": "user", "parts": [{"text": "Question"}]},
    ])

    assert messages == [
        {"role": "user", "content": "System prompt"},
        {"role": "assistant", "content": "Acknowledged"},
        {"role": "user", "content": "Question"},
    ]


def test_openai_compatible_generate_content(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "Hello"}}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)

    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1/",
        api_key="secret",
        model="gpt-test",
    )
    response = model.generate_content(
        model="gemini-2.5-flash",
        contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
        config=SimpleNamespace(
            temperature=0.2,
            max_output_tokens=10,
            response_mime_type="application/json",
        ),
    )

    assert response.text == "Hello"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "gpt-test"
    assert captured["json"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert captured["json"]["max_tokens"] == 10
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_generate_content_stream(monkeypatch):
    class _StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            return iter([
                'data: {"choices":[{"delta":{"content":"Hel"}}]}',
                'data: {"choices":[{"delta":{"content":"lo"}}]}',
                "data: [DONE]",
            ])

    class _Client:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return _StreamResponse()

    monkeypatch.setattr("src.services.ai.base.httpx.Client", _Client)

    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1",
        api_key="secret",
        model="gpt-test",
    )

    chunks = list(model.generate_content_stream(
        contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
    ))

    assert [chunk.text for chunk in chunks] == ["Hel", "lo"]


def test_generate_openai_compatible_image_from_b64(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "output_format": "png",
                "data": [{
                    "b64_json": base64.b64encode(b"image-bytes").decode(),
                    "revised_prompt": "revised",
                }],
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)
    monkeypatch.setattr(
        "src.services.ai.base.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                openai_base_url="https://example.test/v1/",
                openai_api_key="secret",
                openai_image_model="gpt-image-2",
            )
        ),
    )

    image_bytes, image_format, revised_prompt = generate_openai_compatible_image(
        "Draw a cube",
        size="1024x1024",
        quality="medium",
    )

    assert image_bytes == b"image-bytes"
    assert image_format == "png"
    assert revised_prompt == "revised"
    assert captured["url"] == "https://example.test/v1/images/generations"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "gpt-image-2"
    assert captured["json"]["prompt"] == "Draw a cube"
    assert captured["json"]["quality"] == "medium"
