import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from config.config import Judge0Config


logger = logging.getLogger(__name__)

JUDGE0_TIMEOUT = httpx.Timeout(30.0, connect=5.0, write=10.0, pool=5.0)
MAX_SOURCE_CODE_LENGTH = 200_000
MAX_STDIN_LENGTH = 100_000
MAX_ADDITIONAL_FILES_LENGTH = 4_000_000


@dataclass(frozen=True)
class Judge0ServiceError(Exception):
    code: str
    http_status: int
    retryable: bool


def judge0_headers(config: Judge0Config) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.client_id:
        headers["X-Judge0-Client-ID"] = config.client_id
    if config.client_secret:
        headers["X-Judge0-Client-Secret"] = config.client_secret
    return headers


def _provider_error(status_code: int) -> Judge0ServiceError:
    if status_code in {400, 404, 422}:
        return Judge0ServiceError(
            code="judge0_submission_rejected",
            http_status=400,
            retryable=False,
        )
    if status_code in {401, 403}:
        return Judge0ServiceError(
            code="judge0_auth_failed",
            http_status=503,
            retryable=True,
        )
    if status_code == 429:
        return Judge0ServiceError(
            code="judge0_rate_limited",
            http_status=503,
            retryable=True,
        )
    return Judge0ServiceError(
        code="judge0_unavailable",
        http_status=503,
        retryable=True,
    )


async def submit_judge0(
    config: Judge0Config,
    *,
    language_id: int,
    source_code: str,
    stdin: str,
    additional_files: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    if (
        language_id < 1
        or not source_code
        or len(source_code) > MAX_SOURCE_CODE_LENGTH
        or len(stdin) > MAX_STDIN_LENGTH
        or (
            additional_files is not None
            and len(additional_files) > MAX_ADDITIONAL_FILES_LENGTH
        )
    ):
        raise Judge0ServiceError(
            code="judge0_request_too_large",
            http_status=400,
            retryable=False,
        )

    payload: dict[str, Any] = {
        "language_id": language_id,
        "source_code": source_code,
        "stdin": stdin,
    }
    if additional_files:
        payload["additional_files"] = additional_files

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=JUDGE0_TIMEOUT)
    started_at = time.perf_counter()
    status_code: int | None = None
    try:
        try:
            response = await active_client.post(
                f"{config.api_url.rstrip('/')}/submissions?wait=true",
                json=payload,
                headers=judge0_headers(config),
            )
            status_code = response.status_code
        except httpx.TimeoutException as exc:
            raise Judge0ServiceError(
                code="judge0_timeout",
                http_status=503,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise Judge0ServiceError(
                code="judge0_unavailable",
                http_status=503,
                retryable=True,
            ) from exc

        if response.status_code not in {200, 201}:
            raise _provider_error(response.status_code)
        try:
            result = response.json()
        except ValueError as exc:
            raise Judge0ServiceError(
                code="judge0_invalid_response",
                http_status=503,
                retryable=True,
            ) from exc
        if not isinstance(result, dict) or not isinstance(result.get("status"), dict):
            raise Judge0ServiceError(
                code="judge0_invalid_response",
                http_status=503,
                retryable=True,
            )
        return result
    except Judge0ServiceError as exc:
        logger.warning(
            "judge0.request.failed",
            extra={
                "integration": "judge0",
                "operation": "execute",
                "provider_status": status_code,
                "error_code": exc.code,
                "retryable": exc.retryable,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
            },
        )
        raise
    finally:
        if owns_client:
            await active_client.aclose()
