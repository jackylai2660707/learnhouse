import httpx
import pytest

from config.config import Judge0Config
from src.services.judge0 import (
    JUDGE0_TIMEOUT,
    Judge0ServiceError,
    judge0_headers,
    submit_judge0,
)


def _config() -> Judge0Config:
    return Judge0Config(
        api_url="https://judge.example.test/",
        client_id="synthetic-client",
        client_secret="synthetic-secret",
    )


def test_judge0_headers_include_configured_credentials_without_logging_them():
    headers = judge0_headers(_config())

    assert headers == {
        "Content-Type": "application/json",
        "X-Judge0-Client-ID": "synthetic-client",
        "X-Judge0-Client-Secret": "synthetic-secret",
    }


@pytest.mark.asyncio
async def test_submit_judge0_returns_compatible_success_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://judge.example.test/submissions?wait=true"
        assert request.headers["X-Judge0-Client-ID"] == "synthetic-client"
        assert request.headers["X-Judge0-Client-Secret"] == "synthetic-secret"
        payload = __import__("json").loads(request.content)
        assert payload == {
            "language_id": 71,
            "source_code": "print('ok')",
            "stdin": "",
        }
        return httpx.Response(
            201,
            json={
                "stdout": "ok\n",
                "stderr": None,
                "status": {"id": 3, "description": "Accepted"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await submit_judge0(
            _config(),
            language_id=71,
            source_code="print('ok')",
            stdin="",
            client=client,
        )

    assert result["stdout"] == "ok\n"
    assert result["status"]["id"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "code", "http_status", "retryable"),
    [
        (400, "judge0_submission_rejected", 400, False),
        (401, "judge0_auth_failed", 503, True),
        (429, "judge0_rate_limited", 503, True),
        (500, "judge0_unavailable", 503, True),
    ],
)
async def test_submit_judge0_maps_provider_errors_without_exposing_body(
    caplog,
    provider_status,
    code,
    http_status,
    retryable,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(provider_status, text="provider-internal-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Judge0ServiceError) as exc_info:
            await submit_judge0(
                _config(),
                language_id=71,
                source_code="print('ok')",
                stdin="",
                client=client,
            )

    assert exc_info.value.code == code
    assert exc_info.value.http_status == http_status
    assert exc_info.value.retryable is retryable
    assert "provider-internal-secret" not in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
async def test_submit_judge0_maps_timeout_to_retryable_unavailable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Judge0ServiceError) as exc_info:
            await submit_judge0(
                _config(),
                language_id=71,
                source_code="print('ok')",
                stdin="",
                client=client,
            )

    assert exc_info.value.code == "judge0_timeout"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(201, text="not-json"),
        httpx.Response(201, json={"stdout": "ok"}),
        httpx.Response(201, json=[{"status": {"id": 3}}]),
    ],
)
async def test_submit_judge0_rejects_malformed_success_response(response):
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Judge0ServiceError) as exc_info:
            await submit_judge0(
                _config(),
                language_id=71,
                source_code="print('ok')",
                stdin="",
                client=client,
            )

    assert exc_info.value.code == "judge0_invalid_response"


def test_judge0_timeout_has_bounded_connect_read_write_and_pool_values():
    assert JUDGE0_TIMEOUT.connect == 5.0
    assert JUDGE0_TIMEOUT.read == 30.0
    assert JUDGE0_TIMEOUT.write == 10.0
    assert JUDGE0_TIMEOUT.pool == 5.0


@pytest.mark.asyncio
async def test_submit_judge0_rejects_oversized_request_before_network_call():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={"status": {"id": 3}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Judge0ServiceError) as exc_info:
            await submit_judge0(
                _config(),
                language_id=71,
                source_code="x" * 200_001,
                stdin="",
                client=client,
            )

    assert exc_info.value.code == "judge0_request_too_large"
    assert exc_info.value.retryable is False
    assert called is False
