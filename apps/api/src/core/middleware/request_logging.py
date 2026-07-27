import logging
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.observability.request_context import reset_request_id, set_request_id


logger = logging.getLogger("learnhouse.access")

REQUEST_ID_HEADER = b"x-request-id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() != REQUEST_ID_HEADER:
            continue
        decoded = value.decode("latin-1")
        if _REQUEST_ID_RE.fullmatch(decoded):
            return decoded
        return None
    return None


def _response_headers_with_request_id(
    headers: list[tuple[bytes, bytes]], request_id: str
) -> list[tuple[bytes, bytes]]:
    filtered = [
        (name, value) for name, value in headers if name.lower() != REQUEST_ID_HEADER
    ]
    filtered.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
    return filtered


class RequestLoggingMiddleware:
    """Attach a request ID and emit one sanitized access event per HTTP call."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = set_request_id(request_id)
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                message["headers"] = _response_headers_with_request_id(
                    list(message.get("headers", [])), request_id
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            log_method: Callable[..., Any] = (
                logger.warning if status_code >= 500 else logger.info
            )
            try:
                log_method(
                    "request.complete",
                    extra={
                        "request_id": request_id,
                        "method": str(scope.get("method", "")),
                        "path": str(scope.get("path", "")),
                        "status": status_code,
                        "duration_ms": duration_ms,
                    },
                )
            finally:
                reset_request_id(token)
