import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from src.core.observability.redaction import redact_sensitive_data, redact_text
from src.core.observability.request_context import get_request_id


_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__
) | {"asctime", "message"}


class SecretRedactionFilter(logging.Filter):
    """Sanitize a record before any configured handler emits it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered_message = record.getMessage()
        except (TypeError, ValueError):
            rendered_message = str(record.msg)
        record.msg = redact_text(rendered_message)
        record.args = ()

        for key, value in tuple(record.__dict__.items()):
            if key not in _STANDARD_LOG_RECORD_FIELDS:
                record.__dict__[key] = redact_sensitive_data(value, key=key)
        return True


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per line for container log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = redact_sensitive_data(value, key=key)

        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact_text(self.formatStack(record.stack_info))

        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class RedactedHumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(*, development_mode: bool) -> None:
    """Configure application logging without writing to container-local files."""

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SecretRedactionFilter())
    if development_mode:
        handler.setFormatter(
            RedactedHumanFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    else:
        handler.setFormatter(JsonLogFormatter())

    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    # LearnHouse emits its own sanitized access event. Uvicorn's default access
    # line includes the raw query string, so it must not be emitted as a second
    # unsanitized request log.
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False
    uvicorn_access.disabled = True

    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    logging.captureWarnings(True)
    logging.getLogger(__name__).info("logging.configured")
