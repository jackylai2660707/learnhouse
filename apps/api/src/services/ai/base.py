from dataclasses import dataclass
from typing import Dict, Any, AsyncGenerator, Iterable, Optional
from uuid import uuid4
from datetime import datetime, timezone
import io
import logging
import binascii
import redis
import json
import asyncio
import time
import httpx
from google import genai
from PIL import Image, UnidentifiedImageError

from config.config import get_learnhouse_config
from src.services.ai.assignment_config import normalize_openai_base_url
from src.services.utils.ssrf_guard import (
    SSRFBlockedError,
    assert_connected_peer_allowed,
    resolve_and_validate_url,
)

logger = logging.getLogger(__name__)

LH_CONFIG = get_learnhouse_config()

AI_TEXT_TIMEOUT = httpx.Timeout(120.0, connect=10.0, write=30.0, pool=10.0)
# Embeddings are a bulk operation, not an interactive one: a reindex sends a
# whole batch of chunks in one request. On the self-hosted CPU backend a batch
# takes proportionally longer than any chat completion, and at 120s the client
# gave up on work the server then completed — the retry re-ran the same batch
# and timed out identically, so a reindex could never finish.
AI_EMBEDDING_TIMEOUT = httpx.Timeout(600.0, connect=10.0, write=60.0, pool=10.0)
AI_IMAGE_TIMEOUT = httpx.Timeout(240.0, connect=10.0, write=30.0, pool=10.0)
AI_IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(180.0, connect=10.0, write=10.0, pool=10.0)
MAX_GENERATED_IMAGE_BYTES = 25 * 1024 * 1024
MIN_GENERATED_IMAGE_DIMENSION = 64
MAX_GENERATED_IMAGE_DIMENSION = 4096


@dataclass
class _AITextResponse:
    text: str


@dataclass
class _AITextChunk:
    text: str


@dataclass
class _AIEmbedding:
    values: list[float]


@dataclass
class _AIEmbeddingResponse:
    embeddings: list[_AIEmbedding]


@dataclass(frozen=True)
class AIProviderError(Exception):
    code: str
    status_code: int | None = None
    retryable: bool = True


class ImageGenerationProviderError(AIProviderError):
    """Safe image-provider error without raw response content."""


def _safe_provider_error(exc: Exception, *, operation: str) -> AIProviderError:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {400, 404, 422}:
            code = f"ai_{operation}_request_rejected"
            retryable = False
        elif status_code in {401, 403}:
            code = f"ai_{operation}_auth_failed"
            retryable = False
        elif status_code == 429:
            code = f"ai_{operation}_rate_limited"
            retryable = True
        else:
            code = f"ai_{operation}_unavailable"
            retryable = True
        return AIProviderError(code, status_code=status_code, retryable=retryable)
    if isinstance(exc, httpx.TimeoutException):
        return AIProviderError(f"ai_{operation}_timeout", retryable=True)
    if isinstance(exc, (httpx.TransportError, SSRFBlockedError)):
        return AIProviderError(f"ai_{operation}_unavailable", retryable=True)
    return AIProviderError(f"ai_{operation}_invalid_response", retryable=True)


def _get_config_value(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _looks_like_response_format_rejection(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code not in {400, 422}:
        return False
    body = (exc.response.text or "").lower()
    return "response_format" in body or "json_object" in body


def _rejected_optional_openai_params(
    exc: httpx.HTTPStatusError,
    payload: dict[str, Any],
) -> list[str]:
    if exc.response.status_code not in {400, 422}:
        return []

    body = (exc.response.text or "").lower()
    rejected: list[str] = []
    if "response_format" in payload and ("response_format" in body or "json_object" in body):
        rejected.append("response_format")
    if "temperature" in payload and "temperature" in body:
        rejected.append("temperature")
    if "max_tokens" in payload and ("max_tokens" in body or "max completion tokens" in body):
        rejected.append("max_tokens")
    return rejected


def _part_to_openai_content(part: Any) -> list[dict[str, Any]]:
    if not isinstance(part, dict):
        return [{"type": "text", "text": str(part)}]

    if "text" in part:
        return [{"type": "text", "text": part.get("text") or ""}]

    inline_data = part.get("inline_data") or {}
    if inline_data.get("mime_type", "").startswith("image/") and inline_data.get("data"):
        return [{
            "type": "image_url",
            "image_url": {
                "url": f"data:{inline_data.get('mime_type')};base64,{inline_data.get('data')}"
            },
        }]
    if inline_data:
        return [{
            "type": "text",
            "text": f"[Attached file: {inline_data.get('mime_type', 'unknown mime type')}]",
        }]

    file_data = part.get("file_data") or {}
    if file_data:
        return [{
            "type": "text",
            "text": f"[Attached external file: {file_data.get('file_uri', '')}]",
        }]

    return [{"type": "text", "text": json.dumps(part)}]


def _gemini_contents_to_openai_messages(contents: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in contents or []:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue

        raw_role = item.get("role") or "user"
        role = "assistant" if raw_role == "model" else raw_role
        if role not in {"system", "user", "assistant"}:
            role = "user"

        parts = item.get("parts")
        if parts is None:
            content = item.get("content", "")
        else:
            openai_parts: list[dict[str, Any]] = []
            for part in parts:
                openai_parts.extend(_part_to_openai_content(part))
            text_only = all(part.get("type") == "text" for part in openai_parts)
            content = "\n".join(part.get("text", "") for part in openai_parts) if text_only else openai_parts

        messages.append({"role": role, "content": content})
    return messages


class _OpenAICompatibleModels:
    def __init__(self, base_url: str, api_key: str, model: str, embedding_model: str | None = None):
        self.base_url = normalize_openai_base_url(base_url)
        self.api_key = api_key
        self.model = model
        self.embedding_model = embedding_model

    def _payload(self, model: str | None, contents: Any, config: Any = None, stream: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model or model,
            "messages": _gemini_contents_to_openai_messages(contents),
            "stream": stream,
        }

        temperature = _get_config_value(config, "temperature")
        if temperature is not None:
            payload["temperature"] = temperature

        max_tokens = _get_config_value(config, "max_output_tokens")
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response_mime_type = _get_config_value(config, "response_mime_type")
        if response_mime_type == "application/json":
            payload["response_format"] = {"type": "json_object"}

        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # An empty key means "this endpoint needs no auth" — the self-hosted
        # embedding service on the internal network is the case in point.
        # Sending a bare "Bearer " is an illegal header value and httpx
        # refuses to send the request at all.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def generate_content(self, *, model: str | None = None, contents: Any = None, config: Any = None) -> _AITextResponse:
        payload = self._payload(model, contents, config, stream=False)
        removed_params: set[str] = set()
        while True:
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=AI_TEXT_TIMEOUT,
                )
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                rejected_params = [
                    key for key in _rejected_optional_openai_params(exc, payload)
                    if key not in removed_params
                ]
                if not rejected_params:
                    error = _safe_provider_error(exc, operation="text")
                    logger.warning(
                        "ai.provider.request_failed",
                        extra={
                            "integration": "ai_text",
                            "operation": "generate_content",
                            "model": self.model,
                            "provider_status": error.status_code,
                            "error_code": error.code,
                            "retryable": error.retryable,
                        },
                    )
                    raise error from exc
                removed_params.update(rejected_params)
                payload = dict(payload)
                for key in rejected_params:
                    payload.pop(key, None)
                logger.warning(
                    "ai.provider.optional_parameters_rejected",
                    extra={
                        "integration": "ai_text",
                        "operation": "generate_content",
                        "model": self.model,
                        "rejected_parameters": rejected_params,
                    },
                )
                continue
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                error = _safe_provider_error(exc, operation="text")
                logger.warning(
                    "ai.provider.request_failed",
                    extra={
                        "integration": "ai_text",
                        "operation": "generate_content",
                        "model": self.model,
                        "provider_status": error.status_code,
                        "error_code": error.code,
                        "retryable": error.retryable,
                    },
                )
                raise error from exc
            except AIProviderError:
                raise

        try:
            data = response.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            first_choice = choices[0] if isinstance(choices, list) and choices else None
            text = (
                first_choice.get("message", {}).get("content")
                or first_choice.get("text")
                if isinstance(first_choice, dict)
                else ""
            )
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderError("ai_text_invalid_response") from exc
        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("ai_text_empty_response")
        return _AITextResponse(text=text)

    def generate_content_stream(self, *, model: str | None = None, contents: Any = None, config: Any = None) -> Iterable[_AITextChunk]:
        try:
            with httpx.Client(timeout=AI_TEXT_TIMEOUT) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(model, contents, config, stream=True),
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        choice = (data.get("choices") or [{}])[0]
                        text = (
                            choice.get("delta", {}).get("content")
                            or choice.get("message", {}).get("content")
                            or choice.get("text")
                            or ""
                        )
                        if text:
                            yield _AITextChunk(text=text)
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as exc:
            error = _safe_provider_error(exc, operation="text_stream")
            logger.warning(
                "ai.provider.request_failed",
                extra={
                    "integration": "ai_text",
                    "operation": "generate_content_stream",
                    "model": self.model,
                    "provider_status": error.status_code,
                    "error_code": error.code,
                    "retryable": error.retryable,
                },
            )
            raise error from exc

    def embed_content(self, *, model: str | None = None, contents: Any = None, config: Any = None) -> _AIEmbeddingResponse:
        requested_model = model if model and not model.startswith("gemini-") else None
        embedding_model = self.embedding_model or requested_model or "text-embedding-3-small"
        input_texts = contents or []
        if isinstance(input_texts, str):
            input_texts = [input_texts]

        payload: dict[str, Any] = {
            "model": embedding_model,
            "input": input_texts,
        }

        dimensions = _get_config_value(config, "output_dimensionality")
        if dimensions:
            payload["dimensions"] = dimensions

        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json=payload,
                timeout=AI_EMBEDDING_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ValueError("missing embedding data")
            embeddings = [
                _AIEmbedding(values=list(item.get("embedding") or []))
                for item in sorted(items, key=lambda item: item.get("index", 0))
                if isinstance(item, dict)
            ]
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as exc:
            error = _safe_provider_error(exc, operation="embedding")
            logger.warning(
                "ai.provider.request_failed",
                extra={
                    "integration": "ai_embedding",
                    "operation": "embed_content",
                    "model": embedding_model,
                    "provider_status": error.status_code,
                    "error_code": error.code,
                    "retryable": error.retryable,
                },
            )
            raise error from exc
        except (AttributeError, TypeError, ValueError) as exc:
            raise AIProviderError("ai_embedding_invalid_response") from exc
        if (
            len(embeddings) != len(input_texts)
            or not embeddings
            or any(not embedding.values for embedding in embeddings)
            or len({len(embedding.values) for embedding in embeddings}) != 1
        ):
            raise AIProviderError("ai_embedding_invalid_response")
        return _AIEmbeddingResponse(embeddings=embeddings)


class _OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str, embedding_model: str | None = None):
        self.models = _OpenAICompatibleModels(base_url, api_key, model, embedding_model)


def validate_generated_image(
    image_bytes: bytes,
    *,
    claimed_format: str | None = None,
) -> tuple[str, int, int]:
    if not image_bytes or len(image_bytes) > MAX_GENERATED_IMAGE_BYTES:
        raise ImageGenerationProviderError(
            "ai_image_invalid_bytes",
            retryable=True,
        )
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            detected_format = (image.format or "").lower()
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as exc:
        raise ImageGenerationProviderError(
            "ai_image_invalid_bytes",
            retryable=True,
        ) from exc

    format_aliases = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}
    output_format = format_aliases.get(detected_format)
    if output_format is None:
        raise ImageGenerationProviderError(
            "ai_image_unsupported_format",
            retryable=False,
        )
    if (
        width < MIN_GENERATED_IMAGE_DIMENSION
        or height < MIN_GENERATED_IMAGE_DIMENSION
        or width > MAX_GENERATED_IMAGE_DIMENSION
        or height > MAX_GENERATED_IMAGE_DIMENSION
    ):
        raise ImageGenerationProviderError(
            "ai_image_dimensions_too_small",
            retryable=True,
        )

    normalized_claim = format_aliases.get((claimed_format or "").lower())
    if normalized_claim and normalized_claim != output_format:
        logger.warning(
            "ai.image.format_mismatch",
            extra={
                "integration": "ai_image",
                "operation": "validate_image",
                "claimed_format": normalized_claim,
                "detected_format": output_format,
            },
        )
    return output_format, width, height


def _download_generated_image(image_url: str) -> bytes:
    try:
        validated_ips = resolve_and_validate_url(image_url, allow_http=False)
        chunks: list[bytes] = []
        total_bytes = 0
        with httpx.stream(
            "GET",
            image_url,
            timeout=AI_IMAGE_DOWNLOAD_TIMEOUT,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            assert_connected_peer_allowed(response, validated_ips)
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_GENERATED_IMAGE_BYTES:
                raise ImageGenerationProviderError(
                    "ai_image_too_large",
                    retryable=False,
                )
            for chunk in response.iter_bytes():
                total_bytes += len(chunk)
                if total_bytes > MAX_GENERATED_IMAGE_BYTES:
                    raise ImageGenerationProviderError(
                        "ai_image_too_large",
                        retryable=False,
                    )
                chunks.append(chunk)
        return b"".join(chunks)
    except ImageGenerationProviderError:
        raise
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError, SSRFBlockedError, ValueError) as exc:
        error = _safe_provider_error(exc, operation="image_download")
        raise ImageGenerationProviderError(
            error.code,
            status_code=error.status_code,
            retryable=error.retryable,
        ) from exc


def get_gemini_client():
    """Get the configured AI client.

    The legacy name is kept so existing AI services can keep using the Gemini
    SDK-shaped interface. When configured for an OpenAI-compatible provider,
    this returns a small adapter that accepts the same generate_content calls.
    """
    ai_config = get_learnhouse_config().ai_config
    provider = getattr(ai_config, "provider", "gemini")
    if provider == "openai_compatible":
        base_url = getattr(ai_config, "openai_base_url", None)
        api_key = getattr(ai_config, "openai_api_key", None)
        model = getattr(ai_config, "openai_model", None)
        embedding_model = getattr(ai_config, "openai_embedding_model", None)
        if not base_url or not api_key or not model:
            raise Exception("OpenAI-compatible AI provider is not fully configured")
        return _OpenAICompatibleClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            embedding_model=embedding_model,
        )

    api_key = getattr(ai_config, 'gemini_api_key', None)
    if not api_key:
        raise Exception("Gemini API key not configured")
    return genai.Client(api_key=api_key)


def embedding_backend_description() -> tuple[str, str | None]:
    """Describe where embeddings come from: ("dedicated"|"chat_provider", base_url)."""
    ai_config = get_learnhouse_config().ai_config
    base_url = getattr(ai_config, "embedding_base_url", None)
    if base_url:
        return "dedicated", base_url
    return "chat_provider", getattr(ai_config, "openai_base_url", None)


def get_embedding_client():
    """Get the client used for embeddings only.

    Embeddings are deliberately decoupled from chat. The configured chat
    provider here serves /chat/completions fine but answers 404 on
    /embeddings for every model it advertises, so embeddings are routed to a
    dedicated service via LEARNHOUSE_EMBEDDING_BASE_URL. When that is unset,
    fall back to the chat provider so deployments whose provider does serve
    embeddings keep working unchanged.
    """
    ai_config = get_learnhouse_config().ai_config
    base_url = getattr(ai_config, "embedding_base_url", None)
    if not base_url:
        return get_gemini_client()

    return _OpenAICompatibleClient(
        base_url=base_url,
        # The self-hosted service runs on the internal network and treats an
        # empty key as "no auth required"; a shared secret is still supported.
        api_key=getattr(ai_config, "embedding_api_key", None) or "",
        model=getattr(ai_config, "openai_model", None) or "",
        embedding_model=getattr(ai_config, "openai_embedding_model", None),
    )


def generate_openai_compatible_image(
    prompt: str,
    *,
    model: str | None = None,
    size: str = "1024x1024",
    quality: str | None = None,
    background: str | None = None,
) -> tuple[bytes, str, str | None]:
    """Generate one image through an OpenAI-compatible /images/generations API.

    Returns (image_bytes, output_format, revised_prompt).
    """
    ai_config = get_learnhouse_config().ai_config
    base_url = getattr(ai_config, "openai_base_url", None)
    api_key = getattr(ai_config, "openai_api_key", None)
    image_model = model or getattr(ai_config, "openai_image_model", None) or "gpt-image-2"
    if not base_url or not api_key:
        raise ImageGenerationProviderError(
            "ai_image_not_configured",
            retryable=False,
        )
    base_url = normalize_openai_base_url(base_url)

    payload: dict[str, Any] = {
        "model": image_model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    if quality:
        payload["quality"] = quality
    if background:
        payload["background"] = background

    endpoint = f"{base_url}/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    max_attempts = 3
    last_error: ImageGenerationProviderError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = httpx.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=AI_IMAGE_TIMEOUT,
            )
            response.raise_for_status()
            break
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as exc:
            safe_error = _safe_provider_error(exc, operation="image")
            last_error = ImageGenerationProviderError(
                safe_error.code,
                status_code=safe_error.status_code,
                retryable=safe_error.retryable,
            )
            logger.warning(
                "ai.provider.request_failed",
                extra={
                    "integration": "ai_image",
                    "operation": "generate_image",
                    "model": image_model,
                    "size": size,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "provider_status": last_error.status_code,
                    "error_code": last_error.code,
                    "retryable": last_error.retryable,
                },
            )
            if attempt == max_attempts or not last_error.retryable:
                raise last_error from exc
            time.sleep(2 if attempt == 1 else 6)
    else:
        raise last_error or ImageGenerationProviderError("ai_image_unavailable")

    try:
        data = response.json()
        image_items = data.get("data") if isinstance(data, dict) else None
        image_item = image_items[0] if isinstance(image_items, list) and image_items else None
        if not isinstance(image_item, dict):
            raise ValueError("missing image item")
        output_format = str(data.get("output_format") or "png").lower()
        revised_prompt_value = image_item.get("revised_prompt")
        revised_prompt = (
            revised_prompt_value
            if isinstance(revised_prompt_value, str)
            else None
        )
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise ImageGenerationProviderError("ai_image_invalid_response") from exc

    b64_json = image_item.get("b64_json")
    if b64_json:
        import base64
        try:
            image_bytes = base64.b64decode(b64_json, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ImageGenerationProviderError("ai_image_invalid_response") from exc
        output_format, _, _ = validate_generated_image(
            image_bytes,
            claimed_format=output_format,
        )
        return image_bytes, output_format, revised_prompt

    image_url = image_item.get("url")
    if isinstance(image_url, str) and image_url:
        image_bytes = _download_generated_image(image_url)
        output_format, _, _ = validate_generated_image(
            image_bytes,
            claimed_format=output_format,
        )
        return image_bytes, output_format, revised_prompt

    raise ImageGenerationProviderError("ai_image_invalid_response")


def ask_ai(
    question: str,
    message_history: Any,
    text_reference: str,
    message_for_the_prompt: str,
    gemini_model_name: str,
) -> Dict[str, Any]:
    """
    Process an AI query using Google Gen AI SDK with course content as context
    """
    try:
        # Use Gemini 2.0 Flash as default if no model specified or if OpenAI model
        if not gemini_model_name or gemini_model_name.startswith("gpt-"):
            gemini_model_name = "gemini-2.5-flash"

        client = get_gemini_client()

        # Build conversation contents
        contents = []

        # Add system instruction as the first message
        system_instruction = f"{message_for_the_prompt}\n\nCourse Content Context:\n{text_reference}"
        contents.append({"role": "user", "parts": [{"text": system_instruction}]})
        contents.append({"role": "model", "parts": [{"text": "I understand. I'm ready to help with questions about this course content."}]})

        # Add message history if available
        if hasattr(message_history, 'messages'):
            for msg in message_history.messages:
                if hasattr(msg, 'type') and hasattr(msg, 'content'):
                    role = "user" if msg.type == "human" else "model"
                    contents.append({"role": role, "parts": [{"text": msg.content}]})
        elif isinstance(message_history, list):
            # Handle simple list format
            for msg in message_history:
                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                    contents.append({"role": msg['role'], "parts": [{"text": msg['content']}]})

        # Add current question
        contents.append({"role": "user", "parts": [{"text": question}]})

        # Generate response (60s timeout)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.models.generate_content,
                model=gemini_model_name,
                contents=contents,
            )
            try:
                response = future.result(timeout=60.0)
            except concurrent.futures.TimeoutError:
                raise TimeoutError("Gemini API request timed out after 60s")

        return {
            "output": response.text,
            "intermediate_steps": []
        }

    except Exception as e:
        raise Exception(f"Error processing AI request: {str(e)}")

def get_chat_session_history(aichat_uuid: Optional[str] = None) -> Dict[str, Any]:
    """Get or create a new chat session history using Redis"""
    session_id = aichat_uuid if aichat_uuid else f"aichat_{uuid4()}"
    
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    message_history = []
    
    if redis_conn_string:
        try:
            # Connect to Redis and get message history
            r = redis.from_url(redis_conn_string, socket_connect_timeout=5, socket_timeout=5)
            history_data = r.get(f"chat_history:{session_id}")
            if history_data:
                if isinstance(history_data, bytes):
                    message_history = json.loads(history_data.decode('utf-8'))
                elif isinstance(history_data, str):
                    message_history = json.loads(history_data)
                else:
                    message_history = []
        except Exception as e:
            logger.error("Failed to connect to Redis: %s, using empty history", e, exc_info=True)
            message_history = []
    else:
        logger.warning("Redis connection string not found, using empty history")
        message_history = []

    return {
        "message_history": message_history,
        "aichat_uuid": session_id
    }

def save_message_to_history(aichat_uuid: str, user_message: str, ai_response: str, user_id: Optional[int] = None, course_uuid: Optional[str] = None, sources: Optional[list] = None, mode: str = "course_only", org_id: Optional[int] = None):
    """Save a message exchange to Redis history. Auto-creates session metadata on first message."""
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        return

    try:
        r = redis.from_url(redis_conn_string, socket_connect_timeout=5, socket_timeout=5)

        # Get existing history
        history_key = f"chat_history:{aichat_uuid}"
        history_data = r.get(history_key)
        if history_data:
            if isinstance(history_data, bytes):
                history = json.loads(history_data.decode('utf-8'))
            elif isinstance(history_data, str):
                history = json.loads(history_data)
            else:
                history = []
        else:
            history = []

        # Auto-create session metadata on first message pair
        is_first_message = len(history) == 0

        # Add new messages
        history.append({"role": "user", "content": user_message})
        model_msg = {"role": "model", "content": ai_response}
        if sources:
            model_msg["sources"] = sources
        history.append(model_msg)

        # Keep only last 20 messages to prevent unlimited growth
        if len(history) > 20:
            history = history[-20:]

        # Save back to Redis with TTL of 25 days
        r.setex(history_key, 2160000, json.dumps(history))

        # Create session metadata on first message
        if is_first_message and user_id is not None:
            title = user_message[:50].strip()
            if len(user_message) > 50:
                title += "..."
            save_chat_session_meta(aichat_uuid, user_id, title, course_uuid, mode=mode, org_id=org_id)

    except Exception as e:
        logger.error("Failed to save message to Redis: %s", e, exc_info=True)


CHAT_TTL = 2160000  # 25 days in seconds


def _get_redis():
    """Get a Redis connection."""
    LH_CONFIG = get_learnhouse_config()
    conn = LH_CONFIG.redis_config.redis_connection_string
    if not conn:
        return None
    return redis.from_url(conn, socket_connect_timeout=5, socket_timeout=5)


def save_chat_session_meta(aichat_uuid: str, user_id: int, title: str, course_uuid: Optional[str] = None, mode: str = "course_only", org_id: Optional[int] = None):
    """Store session metadata and add to user's session index."""
    r = _get_redis()
    if not r:
        return
    try:
        now = datetime.now(timezone.utc)
        meta = {
            "aichat_uuid": aichat_uuid,
            "user_id": user_id,
            "org_id": org_id,
            "title": title,
            "course_uuid": course_uuid,
            "created_at": now.isoformat(),
            "favorite": False,
            "mode": mode,
        }
        r.setex(f"chat_meta:{aichat_uuid}", CHAT_TTL, json.dumps(meta))
        r.zadd(f"user_chats:{user_id}", {aichat_uuid: now.timestamp()})
        r.expire(f"user_chats:{user_id}", CHAT_TTL)
    except Exception as e:
        logger.error("Failed to save chat session meta: %s", e, exc_info=True)


def update_chat_session_meta(aichat_uuid: str, user_id: int, title: Optional[str] = None, favorite: Optional[bool] = None) -> Optional[dict]:
    """Update title and/or favorite flag on a session. Returns updated meta or None."""
    r = _get_redis()
    if not r:
        return None
    try:
        meta_key = f"chat_meta:{aichat_uuid}"
        meta_data = r.get(meta_key)
        if not meta_data:
            return None
        meta = json.loads(meta_data.decode("utf-8") if isinstance(meta_data, bytes) else meta_data)
        if meta.get("user_id") != user_id:
            return None

        if title is not None:
            meta["title"] = title
        if favorite is not None:
            meta["favorite"] = favorite

        ttl = r.ttl(meta_key)
        if ttl and ttl > 0:
            r.setex(meta_key, ttl, json.dumps(meta))
        else:
            r.setex(meta_key, CHAT_TTL, json.dumps(meta))

        return meta
    except Exception as e:
        logger.error("Failed to update chat session meta: %s", e, exc_info=True)
        return None


def get_user_chat_sessions(user_id: int, org_id: Optional[int] = None) -> list[dict]:
    """Return all chat sessions for a user, newest first. Optionally filter by org_id."""
    r = _get_redis()
    if not r:
        return []
    try:
        # Get all session UUIDs from sorted set, newest first
        members = r.zrevrange(f"user_chats:{user_id}", 0, -1)
        if not members:
            return []

        # Decode all UUIDs upfront
        uuid_strs = [
            m.decode("utf-8") if isinstance(m, bytes) else m for m in members
        ]

        # Batch-fetch all metadata in a single MGET call (avoids N+1 round-trips)
        meta_keys = [f"chat_meta:{u}" for u in uuid_strs]
        meta_values = r.mget(meta_keys)

        sessions = []
        expired_uuids = []
        for uuid_str, meta_data in zip(uuid_strs, meta_values):
            if not meta_data:
                expired_uuids.append(uuid_str)
                continue
            meta = json.loads(meta_data.decode("utf-8") if isinstance(meta_data, bytes) else meta_data)
            # Filter by org_id if provided
            if org_id is not None and meta.get("org_id") != org_id:
                continue
            sessions.append(meta)

        # Clean up expired entries from the sorted set
        if expired_uuids:
            r.zrem(f"user_chats:{user_id}", *expired_uuids)

        return sessions
    except Exception as e:
        logger.error("Failed to get user chat sessions: %s", e, exc_info=True)
        return []


def get_chat_messages(aichat_uuid: str, user_id: int) -> Optional[list[dict]]:
    """Get messages for a chat session, validating ownership. Returns None if not owned."""
    r = _get_redis()
    if not r:
        return None
    try:
        # Validate ownership
        meta_data = r.get(f"chat_meta:{aichat_uuid}")
        if not meta_data:
            return None
        meta = json.loads(meta_data.decode("utf-8") if isinstance(meta_data, bytes) else meta_data)
        if meta.get("user_id") != user_id:
            return None

        # Get messages
        history_data = r.get(f"chat_history:{aichat_uuid}")
        if not history_data:
            return []
        return json.loads(history_data.decode("utf-8") if isinstance(history_data, bytes) else history_data)
    except Exception as e:
        logger.error("Failed to get chat messages: %s", e, exc_info=True)
        return None


def delete_chat_session(aichat_uuid: str, user_id: int) -> bool:
    """Delete a chat session and its metadata. Returns True if deleted."""
    r = _get_redis()
    if not r:
        return False
    try:
        # Validate ownership
        meta_data = r.get(f"chat_meta:{aichat_uuid}")
        if not meta_data:
            return False
        meta = json.loads(meta_data.decode("utf-8") if isinstance(meta_data, bytes) else meta_data)
        if meta.get("user_id") != user_id:
            return False

        r.delete(f"chat_history:{aichat_uuid}", f"chat_meta:{aichat_uuid}")
        r.zrem(f"user_chats:{user_id}", aichat_uuid)
        return True
    except Exception as e:
        logger.error("Failed to delete chat session: %s", e, exc_info=True)
        return False


def generate_chat_title(user_message: str, ai_response: str) -> str:
    """Generate a short summarized title for a chat session using a lightweight model."""
    try:
        client = get_gemini_client()
        prompt = (
            "Summarize this conversation into a very short title (max 6 words). "
            "Output ONLY the title, nothing else. No quotes, no punctuation at the end.\n\n"
            f"User: {user_message[:300]}\n"
            f"Assistant: {ai_response[:300]}"
        )
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config={"max_output_tokens": 30, "temperature": 0.3},
        )
        if response.text:
            title = response.text.strip().strip('"\'').strip()
            if title:
                return title[:60]
    except Exception as e:
        logger.error("Failed to generate chat title: %s", e, exc_info=True)
    # Fallback to truncated message
    fallback = user_message[:50].strip()
    if len(user_message) > 50:
        fallback += "..."
    return fallback


async def ask_ai_stream(
    question: str,
    message_history: Any,
    text_reference: str,
    message_for_the_prompt: str,
    gemini_model_name: str,
) -> AsyncGenerator[str, None]:
    """
    Process an AI query using Google Gen AI SDK with streaming response.
    Yields chunks of the response as they arrive.
    """
    try:
        # Use Gemini 2.0 Flash as default if no model specified or if OpenAI model
        if not gemini_model_name or gemini_model_name.startswith("gpt-"):
            gemini_model_name = "gemini-2.5-flash"

        client = get_gemini_client()

        # Build conversation contents
        contents = []

        # Add system instruction as the first message
        system_instruction = f"{message_for_the_prompt}\n\nCourse Content Context:\n{text_reference}"
        contents.append({"role": "user", "parts": [{"text": system_instruction}]})
        contents.append({"role": "model", "parts": [{"text": "I understand. I'm ready to help with questions about this course content."}]})

        # Add message history if available
        if hasattr(message_history, 'messages'):
            for msg in message_history.messages:
                if hasattr(msg, 'type') and hasattr(msg, 'content'):
                    role = "user" if msg.type == "human" else "model"
                    contents.append({"role": role, "parts": [{"text": msg.content}]})
        elif isinstance(message_history, list):
            # Handle simple list format
            for msg in message_history:
                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                    contents.append({"role": msg['role'], "parts": [{"text": msg['content']}]})

        # Add current question
        contents.append({"role": "user", "parts": [{"text": question}]})

        # Generate response with streaming (run sync SDK in thread to allow timeout)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content_stream,
                model=gemini_model_name,
                contents=contents,
            ),
            timeout=90.0,
        )

        for chunk in response:
            if chunk.text:
                yield chunk.text
                await asyncio.sleep(0.01)

    except asyncio.TimeoutError:
        logger.error("ask_ai_stream timed out after 90s")
        raise
    except Exception as e:
        logger.error("ask_ai_stream failed: %s", e, exc_info=True)
        raise


async def generate_follow_up_suggestions(
    ai_response: str,
    context: str,
    gemini_model_name: str,
    user_message: str = "",
) -> list[str]:
    """
    Generate 3 contextual follow-up questions based on the AI response.
    Returns a list of suggested follow-up questions.
    Uses a fast model with minimal prompt for quick generation.
    Questions are generated in the same language as the user's message.
    """
    try:
        client = get_gemini_client()

        # Use only a small snippet of the response for speed
        response_snippet = ai_response[:500] if len(ai_response) > 500 else ai_response

        # Short, direct prompt for fast generation - respond in same language as user
        prompt = f"""Given this educational response, suggest 3 brief follow-up questions a student might ask. Output only the questions, one per line. IMPORTANT: Write the questions in the same language as the user's question.

User's question: {user_message[:200]}
Response: {response_snippet}

Questions:"""

        contents = [{"role": "user", "parts": [{"text": prompt}]}]

        # Use flash model with limited output for speed
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=contents,
            config={
                "max_output_tokens": 150,
                "temperature": 0.7,
            }
        )

        # Parse the response into a list of questions
        if response.text:
            questions = [q.strip().lstrip('0123456789.-) ') for q in response.text.strip().split('\n') if q.strip() and '?' in q]
            return questions[:3]

        return []

    except Exception as e:
        logger.error("Failed to generate follow-up suggestions: %s", e, exc_info=True)
        return []
