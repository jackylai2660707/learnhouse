import json
import logging

from src.core.events.logs import (
    JsonLogFormatter,
    SecretRedactionFilter,
    configure_logging,
)
from src.core.observability.redaction import (
    REDACTED,
    redact_sensitive_data,
    redact_sentry_event,
    redact_text,
)


def test_recursive_redaction_masks_sensitive_keys_and_values():
    source = {
        "password": "plain-password",
        "nested": {
            "api_key": "plain-api-key",
            "message": "Authorization: Bearer plain-token",
            "database": "postgresql://user:plain-db-password@db.example/app",
        },
        "safe": "assignment-ready",
    }

    result = redact_sensitive_data(source)

    assert result["password"] == REDACTED
    assert result["nested"]["api_key"] == REDACTED
    assert "plain-token" not in result["nested"]["message"]
    assert "plain-db-password" not in result["nested"]["database"]
    assert result["safe"] == "assignment-ready"


def test_redact_text_masks_credential_urls_and_jwts():
    jwt = "eyJabcdefgh.abcdefgh.abcdefgh"
    value = f"url=https://teacher:plain-password@example.test/path token={jwt}"

    result = redact_text(value)

    assert "plain-password" not in result
    assert jwt not in result
    assert REDACTED in result


def test_json_formatter_emits_single_line_sanitized_structured_event():
    record = logging.LogRecord(
        name="learnhouse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="login failed password=%s",
        args=("plain-password",),
        exc_info=None,
    )
    record.request_id = "request-123"
    record.method = "POST"
    record.path = "/api/v1/auth/login"
    record.status = 401
    record.duration_ms = 1.25
    record.authorization = "Bearer plain-token"

    assert SecretRedactionFilter().filter(record) is True
    output = JsonLogFormatter().format(record)
    payload = json.loads(output)

    assert "\n" not in output
    assert payload["message"] == f"login failed password={REDACTED}"
    assert payload["authorization"] == REDACTED
    assert payload["request_id"] == "request-123"
    assert payload["method"] == "POST"
    assert payload["path"] == "/api/v1/auth/login"
    assert payload["status"] == 401
    assert "plain-password" not in output
    assert "plain-token" not in output


def test_production_logging_uses_json_stdout_without_file_handler(monkeypatch):
    calls = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

    configure_logging(development_mode=False)

    assert calls[0]["level"] == logging.INFO
    assert calls[0]["force"] is True
    assert len(calls[0]["handlers"]) == 1
    handler = calls[0]["handlers"][0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)
    assert isinstance(handler.formatter, JsonLogFormatter)


def test_sentry_before_send_removes_request_payload_query_and_pii():
    event = {
        "request": {
            "url": "https://learn.example/path?token=plain-query-token",
            "query_string": "token=plain-query-token",
            "data": {"answer": "student submission", "password": "plain-password"},
            "cookies": {"session": "plain-cookie"},
            "headers": {
                "Authorization": "Bearer plain-token",
                "Accept": "application/json",
            },
        },
        "user": {
            "id": "internal-id",
            "email": "student@example.test",
            "ip_address": "192.0.2.1",
        },
    }

    result = redact_sentry_event(event)

    assert result["request"]["url"] == "https://learn.example/path"
    assert result["request"]["query_string"] == REDACTED
    assert result["request"]["data"] == REDACTED
    assert result["request"]["cookies"] == REDACTED
    assert result["request"]["headers"]["Authorization"] == REDACTED
    assert result["request"]["headers"]["Accept"] == "application/json"
    assert result["user"]["id"] == "internal-id"
    assert result["user"]["email"] == REDACTED
    assert result["user"]["ip_address"] == REDACTED
    assert "plain-" not in json.dumps(result)
