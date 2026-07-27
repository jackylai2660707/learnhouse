"""Real-Redis regressions for idempotent AI-credit Lua scripts.

These tests are deliberately opt-in through
``LEARNHOUSE_RUN_REDIS_CREDIT_INTEGRATION``. They use only the caller-supplied,
loopback Redis database named by ``LEARNHOUSE_TEST_REDIS_URL`` and
``LEARNHOUSE_TEST_REDIS_DB``; they never read the application's Redis URL or
flush a database. Each test gets a random organization ID and operation-key
namespace, then deletes only the exact keys it created.
"""

import ipaddress
import os
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import redis
from fastapi import HTTPException

from src.routers.ai.rag import RAG_CREDIT_MARKER_TTL_SECONDS
from src.security.features_utils import usage


_ENABLE_ENV = "LEARNHOUSE_RUN_REDIS_CREDIT_INTEGRATION"
_URL_ENV = "LEARNHOUSE_TEST_REDIS_URL"
_DB_ENV = "LEARNHOUSE_TEST_REDIS_DB"


def _test_redis_db() -> int:
    """Return the explicit non-default database reserved for this test run."""
    raw_database = os.getenv(_DB_ENV)
    try:
        database = int(raw_database or "")
    except ValueError as exc:
        raise pytest.UsageError(f"{_DB_ENV} must be an integer") from exc
    if database <= 0:
        raise pytest.UsageError(
            f"{_DB_ENV} must be a non-zero isolated test database"
        )
    return database


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _isolated_redis_settings() -> tuple[str, int]:
    """Return an explicit loopback Redis endpoint and isolated database."""
    if os.getenv(_ENABLE_ENV, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"set {_ENABLE_ENV}=1 to run Redis credit integration")

    raw_url = os.getenv(_URL_ENV)
    if not raw_url:
        pytest.skip(f"set {_URL_ENV} to an isolated loopback Redis endpoint")

    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise pytest.UsageError(f"{_URL_ENV} must use redis:// or rediss://")
    if not _is_loopback_host(parsed.hostname):
        raise pytest.UsageError(f"{_URL_ENV} must target a loopback host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise pytest.UsageError(f"{_URL_ENV} contains an invalid port") from exc
    if port is None:
        raise pytest.UsageError(f"{_URL_ENV} must include an explicit port")
    if parsed.query:
        raise pytest.UsageError(
            f"{_URL_ENV} must not contain query parameters"
        )

    database = _test_redis_db()
    url_database = parsed.path.lstrip("/")
    if url_database:
        try:
            parsed_database = int(url_database)
        except ValueError as exc:
            raise pytest.UsageError(
                f"{_URL_ENV} path must be a Redis database number"
            ) from exc
        if parsed_database != database:
            raise pytest.UsageError(
                f"{_URL_ENV} database must match {_DB_ENV}"
            )
    return raw_url, database


def test_redis_integration_guard_rejects_remote_endpoint(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(_URL_ENV, "redis://cache.invalid:6379/15")
    monkeypatch.setenv(_DB_ENV, "15")

    with pytest.raises(pytest.UsageError, match="loopback"):
        _isolated_redis_settings()


def test_redis_integration_guard_rejects_query_database_override(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(_URL_ENV, "redis://127.0.0.1:16379/15?db=0")
    monkeypatch.setenv(_DB_ENV, "15")

    with pytest.raises(pytest.UsageError, match="query parameters"):
        _isolated_redis_settings()


class _CreditKeyNamespace:
    """Own and clean up only this test invocation's credit keys."""

    def __init__(self):
        self.org_id = int(uuid4().hex[:12], 16)
        self.operation_prefix = f"pytest-redis-credit:{uuid4().hex}"
        self._operation_periods: set[tuple[str, str]] = set()

    @property
    def base_keys(self) -> set[str]:
        return {
            f"ai_credits_used:{self.org_id}",
            f"ai_credits_purchased:{self.org_id}",
            f"ai_credits_period:{self.org_id}",
        }

    def operation(self, name: str) -> str:
        return f"{self.operation_prefix}:{name}"

    def track(self, period: str, operation_key: str) -> None:
        self._operation_periods.add((period, operation_key))

    def reservation_key(self, period: str, operation_key: str) -> str:
        return f"ai_credit_reservation:{self.org_id}:{period}:{operation_key}"

    def refund_key(self, period: str, operation_key: str) -> str:
        return f"ai_credit_refund:{self.org_id}:{period}:{operation_key}"

    def cleanup(self, client: redis.Redis) -> None:
        keys = set(self.base_keys)
        for period, operation_key in self._operation_periods:
            keys.add(self.reservation_key(period, operation_key))
            keys.add(self.refund_key(period, operation_key))
        client.delete(*keys)


@pytest.fixture
def isolated_redis() -> redis.Redis:
    """Connect only to the explicit integration database, without logging its URL."""
    redis_url, database = _isolated_redis_settings()
    client = redis.Redis.from_url(redis_url, db=database)
    try:
        actual_database = client.connection_pool.connection_kwargs.get("db")
        if actual_database != database:
            raise pytest.UsageError(
                "Redis client did not select the explicit isolated test database"
            )
        try:
            client.ping()
        except redis.RedisError as exc:
            pytest.fail(
                "isolated Redis integration database is unavailable: "
                f"{type(exc).__name__}"
            )
        yield client
    finally:
        client.close()


@pytest.fixture
def credit_namespace(isolated_redis: redis.Redis):
    namespace = _CreditKeyNamespace()
    try:
        yield namespace
    finally:
        namespace.cleanup(isolated_redis)


@pytest.fixture
def real_credit_usage(monkeypatch, isolated_redis: redis.Redis):
    """Keep config checks local while preserving production Redis/Lua code paths."""
    async def load_org_config(*_args, **_kwargs):
        return SimpleNamespace(config={"plan": "standard"})

    monkeypatch.setattr(usage, "_get_redis_client", lambda: isolated_redis)
    monkeypatch.setattr(usage, "_load_org_config_for_ai", load_org_config)
    monkeypatch.setattr(usage, "_is_non_saas", lambda: False)
    monkeypatch.setattr(usage, "_get_org_plan", lambda _config: "standard")
    monkeypatch.setattr(usage, "get_ai_credit_limit", lambda _plan: 3)
    monkeypatch.setattr(
        "src.security.features_utils.resolve.resolve_feature",
        lambda *_args, **_kwargs: {"enabled": True, "limit": 3},
    )


async def test_reserve_amount_two_rejects_with_one_credit_left_without_marker(
    real_credit_usage, isolated_redis: redis.Redis, credit_namespace: _CreditKeyNamespace
):
    """A failed atomic reservation must not increment usage or leave a marker."""
    period = usage.get_ai_credit_period_token(credit_namespace.org_id)
    operation = credit_namespace.operation("reserve-boundary")
    credit_namespace.track(period, operation)
    used_key = f"ai_credits_used:{credit_namespace.org_id}"
    reservation_key = credit_namespace.reservation_key(period, operation)
    isolated_redis.set(used_key, 2)  # Limit is 3, so exactly one credit remains.

    with pytest.raises(HTTPException) as exc_info:
        await usage.reserve_ai_credit_once(
            credit_namespace.org_id,
            MagicMock(),
            operation,
            amount=2,
            period_token=period,
        )

    assert exc_info.value.status_code == 403
    assert isolated_redis.get(used_key) == b"2"
    assert isolated_redis.exists(reservation_key) == 0


async def test_successful_reservation_is_idempotent_on_real_redis(
    real_credit_usage, isolated_redis: redis.Redis, credit_namespace: _CreditKeyNamespace
):
    """A retried successful operation increments usage exactly once."""
    period = usage.get_ai_credit_period_token(credit_namespace.org_id)
    operation = credit_namespace.operation("reserve-idempotent")
    credit_namespace.track(period, operation)

    first_used = await usage.reserve_ai_credit_once(
        credit_namespace.org_id,
        MagicMock(),
        operation,
        amount=2,
        period_token=period,
    )
    second_used = await usage.reserve_ai_credit_once(
        credit_namespace.org_id,
        MagicMock(),
        operation,
        amount=2,
        period_token=period,
    )

    assert first_used == second_used == 2
    assert isolated_redis.get(f"ai_credits_used:{credit_namespace.org_id}") == b"2"
    assert isolated_redis.get(
        credit_namespace.reservation_key(period, operation)
    ) == b"2"


async def test_refund_once_is_idempotent_across_two_finalizers(
    real_credit_usage, isolated_redis: redis.Redis, credit_namespace: _CreditKeyNamespace
):
    """Generator and response finalizers may both refund the same operation safely."""
    period = usage.get_ai_credit_period_token(credit_namespace.org_id)
    operation = credit_namespace.operation("two-finalizers")
    credit_namespace.track(period, operation)

    await usage.reserve_ai_credit_once(
        credit_namespace.org_id, MagicMock(), operation, amount=2, period_token=period
    )
    generator_finalizer = usage.refund_ai_credit_once(
        credit_namespace.org_id, operation, amount=2, period_token=period
    )
    response_finalizer = usage.refund_ai_credit_once(
        credit_namespace.org_id, operation, amount=2, period_token=period
    )

    assert generator_finalizer == response_finalizer == 0
    assert isolated_redis.get(f"ai_credits_used:{credit_namespace.org_id}") == b"0"
    assert (
        isolated_redis.get(credit_namespace.refund_key(period, operation)) == b"2"
    )


async def test_refund_failure_leaves_operation_retryable(
    real_credit_usage, isolated_redis: redis.Redis, credit_namespace: _CreditKeyNamespace, monkeypatch
):
    """A transient client failure cannot create a refund marker before retry."""
    period = usage.get_ai_credit_period_token(credit_namespace.org_id)
    operation = credit_namespace.operation("retry-after-failure")
    credit_namespace.track(period, operation)
    refund_key = credit_namespace.refund_key(period, operation)

    await usage.reserve_ai_credit_once(
        credit_namespace.org_id, MagicMock(), operation, amount=2, period_token=period
    )

    class _FailingRedis:
        def register_script(self, _script):
            raise redis.ConnectionError("simulated isolated-client interruption")

    monkeypatch.setattr(usage, "_get_redis_client", _FailingRedis)
    with pytest.raises(redis.ConnectionError):
        usage.refund_ai_credit_once(
            credit_namespace.org_id, operation, amount=2, period_token=period
        )

    assert isolated_redis.exists(refund_key) == 0
    assert isolated_redis.get(f"ai_credits_used:{credit_namespace.org_id}") == b"2"

    monkeypatch.setattr(usage, "_get_redis_client", lambda: isolated_redis)
    assert usage.refund_ai_credit_once(
        credit_namespace.org_id, operation, amount=2, period_token=period
    ) == 0
    assert isolated_redis.get(refund_key) == b"2"


async def test_late_refund_after_period_reset_cannot_reduce_new_period_usage(
    real_credit_usage, isolated_redis: redis.Redis, credit_namespace: _CreditKeyNamespace
):
    old_period = usage.get_ai_credit_period_token(credit_namespace.org_id)
    old_operation = credit_namespace.operation("old-period")
    credit_namespace.track(old_period, old_operation)
    await usage.reserve_ai_credit_once(
        credit_namespace.org_id,
        MagicMock(),
        old_operation,
        amount=2,
        period_token=old_period,
    )

    assert usage.reset_ai_credits_usage(credit_namespace.org_id) is True
    new_period = usage.get_ai_credit_period_token(credit_namespace.org_id)
    assert new_period != old_period
    new_operation = credit_namespace.operation("new-period")
    credit_namespace.track(new_period, new_operation)
    await usage.reserve_ai_credit_once(
        credit_namespace.org_id,
        MagicMock(),
        new_operation,
        amount=1,
        period_token=new_period,
    )

    assert usage.refund_ai_credit_once(
        credit_namespace.org_id,
        old_operation,
        amount=2,
        period_token=old_period,
    ) == 1
    assert isolated_redis.get(f"ai_credits_used:{credit_namespace.org_id}") == b"1"
    assert isolated_redis.get(credit_namespace.refund_key(old_period, old_operation)) == b"0"


async def test_rag_markers_expire_but_durable_pdf_markers_do_not(
    real_credit_usage, isolated_redis: redis.Redis, credit_namespace: _CreditKeyNamespace
):
    period = usage.get_ai_credit_period_token(credit_namespace.org_id)

    rag_operation = credit_namespace.operation("rag-ttl")
    credit_namespace.track(period, rag_operation)
    await usage.reserve_ai_credit_once(
        credit_namespace.org_id,
        MagicMock(),
        rag_operation,
        amount=1,
        period_token=period,
        marker_ttl_seconds=RAG_CREDIT_MARKER_TTL_SECONDS,
    )
    rag_reservation_ttl = isolated_redis.ttl(
        credit_namespace.reservation_key(period, rag_operation)
    )
    assert 0 < rag_reservation_ttl <= RAG_CREDIT_MARKER_TTL_SECONDS
    usage.refund_ai_credit_once(
        credit_namespace.org_id,
        rag_operation,
        amount=1,
        period_token=period,
        marker_ttl_seconds=RAG_CREDIT_MARKER_TTL_SECONDS,
    )
    rag_refund_ttl = isolated_redis.ttl(credit_namespace.refund_key(period, rag_operation))
    assert 0 < rag_refund_ttl <= RAG_CREDIT_MARKER_TTL_SECONDS

    pdf_operation = credit_namespace.operation("pdf-durable")
    credit_namespace.track(period, pdf_operation)
    await usage.reserve_ai_credit_once(
        credit_namespace.org_id,
        MagicMock(),
        pdf_operation,
        amount=1,
        period_token=period,
    )
    assert isolated_redis.ttl(credit_namespace.reservation_key(period, pdf_operation)) == -1
    usage.refund_ai_credit_once(
        credit_namespace.org_id, pdf_operation, amount=1, period_token=period
    )
    assert isolated_redis.ttl(credit_namespace.refund_key(period, pdf_operation)) == -1
