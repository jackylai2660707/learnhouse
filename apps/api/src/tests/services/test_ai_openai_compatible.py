from types import SimpleNamespace
import base64
import io

import httpx
import pytest
from PIL import Image
from unittest.mock import AsyncMock

import src.services.ai.rag.embedding_service as embedding_service
from src.services.ai.base import (
    AIProviderError,
    ImageGenerationProviderError,
    _OpenAICompatibleModels,
    _download_generated_image,
    _gemini_contents_to_openai_messages,
    generate_openai_compatible_image,
    validate_generated_image,
)
from src.services.ai.courseplanning import get_language_name
from src.services.ai.rag.embedding_service import generate_local_embedding
from src.services.utils.ssrf_guard import SSRFBlockedError


def _image_bytes(image_format: str = "PNG", size: tuple[int, int] = (128, 128)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(32, 128, 224)).save(buffer, format=image_format)
    return buffer.getvalue()


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


def test_openai_compatible_generate_content_normalizes_full_chat_endpoint(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "Hello"}}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)

    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1/chat/completions",
        api_key="secret",
        model="gpt-test",
    )
    model.generate_content(contents=[{"role": "user", "parts": [{"text": "Hi"}]}])

    assert captured["url"] == "https://example.test/v1/chat/completions"


def test_openai_compatible_generate_content_retries_without_json_mode_when_provider_rejects_it(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, *, status_code=200, body=None):
            self.status_code = status_code
            self._body = body or {"choices": [{"message": {"content": '{"ok": true}'}}]}
            self.text = str(self._body)
            self.request = httpx.Request("POST", "https://example.test/v1/chat/completions")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "Bad request",
                    request=self.request,
                    response=httpx.Response(
                        self.status_code,
                        text="unsupported response_format json_object",
                        request=self.request,
                    ),
                )

        def json(self):
            return self._body

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _Response(status_code=400)
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)

    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1",
        api_key="secret",
        model="gpt-test",
    )
    response = model.generate_content(
        contents=[{"role": "user", "parts": [{"text": "Return JSON"}]}],
        config=SimpleNamespace(response_mime_type="application/json"),
    )

    assert response.text == '{"ok": true}'
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_openai_compatible_generate_content_retries_without_rejected_optional_params(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, *, status_code=200):
            self.status_code = status_code
            self.text = "unsupported parameter: temperature and max_tokens"
            self.request = httpx.Request("POST", "https://example.test/v1/chat/completions")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "Bad request",
                    request=self.request,
                    response=httpx.Response(
                        self.status_code,
                        text=self.text,
                        request=self.request,
                    ),
                )

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _Response(status_code=400)
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)

    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1",
        api_key="secret",
        model="gpt-test",
    )
    response = model.generate_content(
        contents=[{"role": "user", "parts": [{"text": "Hi"}]}],
        config=SimpleNamespace(
            temperature=0.2,
            max_output_tokens=30,
            response_mime_type="application/json",
        ),
    )

    assert response.text == "OK"
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["max_tokens"] == 30
    assert "temperature" not in calls[1]
    assert "max_tokens" not in calls[1]
    assert calls[1]["response_format"] == {"type": "json_object"}


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
            assert timeout is not None

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


def test_openai_compatible_embed_content(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)

    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1",
        api_key="secret",
        model="gpt-test",
        embedding_model="embed-test",
    )
    response = model.embed_content(
        model="gemini-embedding-001",
        contents=["First", "Second"],
        config={"output_dimensionality": 768},
    )

    assert [embedding.values for embedding in response.embeddings] == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"] == "https://example.test/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"] == {
        "model": "embed-test",
        "input": ["First", "Second"],
        "dimensions": 768,
    }


def test_local_embedding_fallback_is_deterministic_768_dimensions():
    first = generate_local_embedding("地理 河流 erosion")
    second = generate_local_embedding("地理 河流 erosion")

    assert len(first) == 768
    assert first == second
    assert any(value != 0 for value in first)


def test_course_planning_language_aliases_include_traditional_chinese():
    assert get_language_name("zh-Hant") == "Traditional Chinese"
    assert get_language_name("zh_TW") == "Traditional Chinese"
    assert get_language_name("zh-HK") == "Traditional Chinese"


def test_generate_openai_compatible_image_from_b64(monkeypatch):
    captured = {}
    generated_bytes = _image_bytes()

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "output_format": "png",
                "data": [{
                    "b64_json": base64.b64encode(generated_bytes).decode(),
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

    assert image_bytes == generated_bytes
    assert image_format == "png"
    assert revised_prompt == "revised"
    assert captured["url"] == "https://example.test/v1/images/generations"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "gpt-image-2"
    assert captured["json"]["prompt"] == "Draw a cube"
    assert captured["json"]["quality"] == "medium"


def test_generate_openai_compatible_image_normalizes_full_image_endpoint(monkeypatch):
    captured = {}
    generated_bytes = _image_bytes()

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "output_format": "png",
                "data": [{"b64_json": base64.b64encode(generated_bytes).decode()}],
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        return _Response()

    monkeypatch.setattr("src.services.ai.base.httpx.post", fake_post)
    monkeypatch.setattr(
        "src.services.ai.base.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                openai_base_url="https://example.test/v1/images/generations",
                openai_api_key="secret",
                openai_image_model="gpt-image-2",
            )
        ),
    )

    generate_openai_compatible_image("Draw a cube")

    assert captured["url"] == "https://example.test/v1/images/generations"


def test_openai_text_provider_error_is_sanitized(monkeypatch, caplog):
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")

    class _Response:
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "provider-internal-secret",
                request=request,
                response=httpx.Response(
                    500,
                    text="provider-internal-secret",
                    request=request,
                ),
            )

    monkeypatch.setattr("src.services.ai.base.httpx.post", lambda *args, **kwargs: _Response())
    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1",
        api_key="synthetic-secret",
        model="gpt-test",
    )

    with pytest.raises(AIProviderError) as exc_info:
        model.generate_content(contents=[{"role": "user", "parts": [{"text": "Hi"}]}])

    assert exc_info.value.code == "ai_text_unavailable"
    assert "provider-internal-secret" not in str(exc_info.value)
    assert "provider-internal-secret" not in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_openai_embedding_rejects_empty_or_wrong_count(monkeypatch):
    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": []}]}

    monkeypatch.setattr("src.services.ai.base.httpx.post", lambda *args, **kwargs: _Response())
    model = _OpenAICompatibleModels(
        base_url="https://example.test/v1",
        api_key="synthetic-secret",
        model="gpt-test",
        embedding_model="embed-test",
    )

    with pytest.raises(AIProviderError) as exc_info:
        model.embed_content(contents=["First", "Second"])

    assert exc_info.value.code == "ai_embedding_invalid_response"


def test_validate_generated_image_detects_format_and_minimum_dimensions():
    image_format, width, height = validate_generated_image(
        _image_bytes("JPEG", (128, 96)),
        claimed_format="png",
    )

    assert image_format == "jpg"
    assert (width, height) == (128, 96)

    with pytest.raises(ImageGenerationProviderError) as exc_info:
        validate_generated_image(_image_bytes("PNG", (32, 32)))

    assert exc_info.value.code == "ai_image_dimensions_too_small"


def test_generate_image_provider_error_does_not_include_raw_body(monkeypatch, caplog):
    request = httpx.Request("POST", "https://example.test/v1/images/generations")

    class _Response:
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "provider-internal-secret",
                request=request,
                response=httpx.Response(
                    400,
                    text="provider-internal-secret",
                    request=request,
                ),
            )

    monkeypatch.setattr("src.services.ai.base.httpx.post", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(
        "src.services.ai.base.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                openai_base_url="https://example.test/v1",
                openai_api_key="synthetic-secret",
                openai_image_model="gpt-image-2",
            )
        ),
    )

    with pytest.raises(ImageGenerationProviderError) as exc_info:
        generate_openai_compatible_image("Draw a synthetic cube")

    assert exc_info.value.code == "ai_image_request_rejected"
    assert "provider-internal-secret" not in str(exc_info.value)
    assert "provider-internal-secret" not in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_generated_image_url_rejects_ssrf_target_before_download(monkeypatch):
    monkeypatch.setattr(
        "src.services.ai.base.resolve_and_validate_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            SSRFBlockedError("provider-internal-secret")
        ),
    )

    with pytest.raises(ImageGenerationProviderError) as exc_info:
        _download_generated_image("https://127.0.0.1/internal-image")

    assert exc_info.value.code == "ai_image_download_unavailable"
    assert "provider-internal-secret" not in str(exc_info.value)


@pytest.fixture(autouse=True)
def _clear_embedding_degradation():
    """The degradation marker is module state shared across tests."""
    embedding_service.reset_embedding_status()
    yield
    embedding_service.reset_embedding_status()


@pytest.mark.asyncio
async def test_embedding_fails_loudly_when_client_is_unconfigured(monkeypatch):
    """The default must be an error, never a silent lexical hash.

    Returning hash vectors here is how RAG shipped broken: they are the right
    shape and non-zero, so nothing downstream could tell.
    """
    monkeypatch.delenv(embedding_service.HASH_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: (_ for _ in ()).throw(RuntimeError("provider-internal-secret")),
    )

    with pytest.raises(embedding_service.EmbeddingUnavailableError) as exc_info:
        await embedding_service.generate_embeddings(["澳門地理合成內容"])

    assert exc_info.value.operation == "embed_batch"
    assert "provider-internal-secret" not in str(exc_info.value)
    assert embedding_service.embedding_status()["degraded"] is True


@pytest.mark.asyncio
async def test_embedding_hash_fallback_is_opt_in_and_flagged(monkeypatch):
    monkeypatch.setenv(embedding_service.HASH_FALLBACK_ENV, "true")
    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: (_ for _ in ()).throw(RuntimeError("provider-internal-secret")),
    )

    result = await embedding_service.generate_embeddings(["澳門地理合成內容"])

    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == 768
    assert any(value != 0 for value in result.vectors[0])
    # The whole point: the caller cannot receive these without being told.
    assert result.degraded is True
    assert result.degraded_reason == "RuntimeError"
    assert embedding_service.embedding_status()["degraded"] is True


@pytest.mark.asyncio
async def test_embedding_does_not_retry_non_retryable_provider_rejection(monkeypatch):
    calls = 0

    class _Models:
        def embed_content(self, **kwargs):
            nonlocal calls
            calls += 1
            raise AIProviderError(
                "ai_embedding_request_rejected",
                status_code=400,
                retryable=False,
            )

    monkeypatch.delenv(embedding_service.HASH_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: SimpleNamespace(models=_Models()),
    )
    sleep = AsyncMock()
    monkeypatch.setattr(embedding_service.asyncio, "sleep", sleep)

    with pytest.raises(embedding_service.EmbeddingUnavailableError) as exc_info:
        await embedding_service.generate_embeddings(["澳門科學合成內容"])

    assert calls == 1
    sleep.assert_not_awaited()
    assert exc_info.value.code == "ai_embedding_request_rejected"


@pytest.mark.asyncio
async def test_successful_embedding_is_not_flagged_degraded(monkeypatch):
    class _Models:
        def embed_content(self, **kwargs):
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5] * 768)]
            )

    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: SimpleNamespace(models=_Models()),
    )

    result = await embedding_service.generate_embeddings(["澳門科學合成內容"])

    assert result.degraded is False
    assert result.degraded_reason is None
    assert result.vectors[0][:3] == [0.5, 0.5, 0.5]
    assert embedding_service.embedding_status()["degraded"] is False


def test_lexical_hash_is_not_semantic_search():
    """Documents exactly why the fallback must never masquerade as working.

    If this ever starts passing as "semantic", the fallback has changed and
    the loudness guarantees around it should be revisited.
    """
    def cosine(left, right):
        dot = sum(a * b for a, b in zip(left, right))
        norm = sum(a * a for a in left) ** 0.5 * sum(b * b for b in right) ** 0.5
        return dot / norm if norm else 0.0

    volcano = generate_local_embedding("volcano")
    volcanoes = generate_local_embedding("volcanoes")

    # Near-synonyms hash to unrelated buckets: no semantic signal whatsoever.
    assert cosine(volcano, volcanoes) == 0.0
