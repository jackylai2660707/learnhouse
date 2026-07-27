import logging
import re

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.core.middleware.request_logging import RequestLoggingMiddleware
from src.core.observability.request_context import get_request_id


def _test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.post("/submit")
    async def submit(request: Request):
        await request.body()
        return {"request_id": get_request_id()}

    return app


def test_request_middleware_preserves_valid_request_id_and_logs_safe_fields(caplog):
    app = _test_app()

    with caplog.at_level(logging.INFO, logger="learnhouse.access"):
        response = TestClient(app).post(
            "/submit?token=plain-query-token",
            content="plain-student-answer",
            headers={
                "X-Request-ID": "school-request-123",
                "Authorization": "Bearer plain-token",
                "Cookie": "session=plain-cookie",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "school-request-123"
    assert response.json()["request_id"] == "school-request-123"

    record = next(
        record for record in caplog.records if record.name == "learnhouse.access"
    )
    assert record.getMessage() == "request.complete"
    assert record.request_id == "school-request-123"
    assert record.method == "POST"
    assert record.path == "/submit"
    assert record.status == 200
    assert record.duration_ms >= 0
    emitted = " ".join([record.getMessage(), record.path, str(record.__dict__)])
    assert "plain-query-token" not in emitted
    assert "plain-student-answer" not in emitted
    assert "plain-token" not in emitted
    assert "plain-cookie" not in emitted


def test_request_middleware_replaces_invalid_request_id():
    response = TestClient(_test_app()).post(
        "/submit",
        headers={"X-Request-ID": "invalid request id"},
    )

    generated = response.headers["X-Request-ID"]
    assert generated != "invalid request id"
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    assert response.json()["request_id"] == generated
