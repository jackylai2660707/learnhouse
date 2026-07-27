import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


REDACTED = "[REDACTED]"
_MAX_REDACTION_DEPTH = 20

_SENSITIVE_KEY_PARTS = (
    "apikey",
    "authorization",
    "clientsecret",
    "connectionstring",
    "cookie",
    "databaseurl",
    "dsn",
    "password",
    "passwd",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessioncookie",
    "token",
)

_CREDENTIAL_URL_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/\s@]+)@",
    re.IGNORECASE,
)
_DATABASE_DSN_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|redis|rediss|mongodb(?:\+srv)?|amqp|amqps)"
    r"://[^\s\"'<>]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key>\b(?:password|passwd|pwd|secret|token|api[-_ ]?key|authorization|cookie|dsn)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|Bearer\s+[^\s,;&]+|[^\s,;&]+)",
    re.IGNORECASE,
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_text(value: str) -> str:
    sanitized = _DATABASE_DSN_RE.sub(REDACTED, value)
    sanitized = _CREDENTIAL_URL_RE.sub(
        lambda match: f"{match.group('scheme')}{REDACTED}@",
        sanitized,
    )
    sanitized = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}{REDACTED}",
        sanitized,
    )
    sanitized = _BEARER_RE.sub(f"Bearer {REDACTED}", sanitized)
    sanitized = _JWT_RE.sub(REDACTED, sanitized)
    return sanitized


def redact_sensitive_data(
    value: Any,
    *,
    key: object | None = None,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> Any:
    if key is not None and is_sensitive_key(key):
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return redact_text(value.decode("utf-8", errors="replace"))
    if _depth >= _MAX_REDACTION_DEPTH:
        return "[TRUNCATED]"

    seen = _seen if _seen is not None else set()
    value_id = id(value)
    if value_id in seen:
        return "[CIRCULAR]"
    seen.add(value_id)
    try:
        if isinstance(value, Mapping):
            return {
                str(item_key): redact_sensitive_data(
                    item_value,
                    key=item_key,
                    _depth=_depth + 1,
                    _seen=seen,
                )
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [
                redact_sensitive_data(item, _depth=_depth + 1, _seen=seen)
                for item in value
            ]
        return redact_text(str(value))
    finally:
        seen.remove(value_id)


def strip_url_query(value: object) -> str:
    text = str(value)
    try:
        parts = urlsplit(text)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return text.split("?", 1)[0].split("#", 1)[0]


def redact_sentry_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry before_send callback using the same recursive secret policy."""

    del hint
    sanitized = redact_sensitive_data(event)
    if not isinstance(sanitized, dict):
        return {"message": REDACTED}

    request = sanitized.get("request")
    if isinstance(request, dict):
        if "url" in request:
            request["url"] = redact_text(strip_url_query(request["url"]))
        for field in ("cookies", "data", "query_string"):
            if field in request:
                request[field] = REDACTED

    user = sanitized.get("user")
    if isinstance(user, dict):
        for field in ("email", "ip_address", "username"):
            if field in user:
                user[field] = REDACTED

    return sanitized
