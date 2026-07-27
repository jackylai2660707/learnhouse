"""
Regression tests for the F-06 atomic AI-credit reservation fix.

Before the fix, ``check_ai_credits`` + ``deduct_ai_credit`` were two separate
Redis operations, so N concurrent requests at ``remaining=1`` could all pass
the check and all decrement — burning N model calls while billing for 1.

``reserve_ai_credit`` bundles the check + increment into a single Redis Lua
script, so at most one caller can cross the boundary per reservation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.security.features_utils import usage


class _FakeRedis:
    """Minimal Redis stand-in covering only the methods ``usage`` uses. The
    production code no longer calls ``.eval`` directly; it goes through
    ``register_script``, which we emulate here with a callable that performs
    the same check-and-increment that the Lua script does atomically in
    real Redis.
    """

    def __init__(self):
        self._store: dict[str, int] = {}

    def get(self, key: str):
        val = self._store.get(key)
        return None if val is None else str(val).encode()

    def mget(self, *keys):
        return [self.get(k) for k in keys]

    def set(self, key: str, value):
        self._store[key] = int(value)

    def incrby(self, key: str, amount: int):
        self._store[key] = self._store.get(key, 0) + int(amount)
        return self._store[key]

    def register_script(self, src: str):
        fake = self
        is_reserve = "INCRBY" in src and "ARGV[4]" in src
        is_period_reset = 'redis.call("INCR", KEYS[2])' in src

        class _Script:
            def __call__(self, keys, args):
                if is_period_reset:
                    used_key, period_key = keys
                    fake._store[period_key] = fake._store.get(period_key, 0) + 1
                    fake._store[used_key] = 0
                    return fake._store[period_key]
                if is_reserve:
                    used_key, purchased_key = keys[0], keys[1]
                    reservation_key = keys[2] if len(keys) > 2 else None
                    if reservation_key and reservation_key in fake._store:
                        return fake._store.get(used_key, 0)
                    if len(keys) > 3:
                        current_period = str(fake._store.get(keys[3], 0))
                        if current_period != str(args[4]):
                            return -2
                    base = int(args[0])
                    extra = int(args[1])
                    amount = int(args[2])
                    unlimited = args[3] == "1"
                    if unlimited:
                        fake._store[used_key] = fake._store.get(used_key, 0) + amount
                        if reservation_key:
                            fake._store[reservation_key] = amount
                        return fake._store[used_key]
                    purchased = fake._store.get(purchased_key, 0)
                    used = fake._store.get(used_key, 0)
                    total = base + extra + purchased
                    if total - used < amount:
                        return -1
                    fake._store[used_key] = used + amount
                    if reservation_key:
                        fake._store[reservation_key] = amount
                    return fake._store[used_key]
                # refund script
                used_key = keys[0]
                if len(keys) > 2:
                    reservation_key, refund_key = keys[1], keys[2]
                    if refund_key in fake._store:
                        return fake._store.get(used_key, 0)
                    reserved = fake._store.get(reservation_key, 0)
                    if reserved <= 0:
                        return fake._store.get(used_key, 0)
                    if len(keys) > 3:
                        current_period = str(fake._store.get(keys[3], 0))
                        if current_period != str(args[1]):
                            fake._store[refund_key] = 0
                            return fake._store.get(used_key, 0)
                    decrement = min(reserved, int(args[0]))
                    current = fake._store.get(used_key, 0)
                    new_val = max(0, current - decrement)
                    fake._store[used_key] = new_val
                    fake._store[refund_key] = decrement
                    return new_val
                decrement = int(args[0])
                current = fake._store.get(used_key, 0)
                new_val = max(0, current - decrement)
                fake._store[used_key] = new_val
                return new_val

        return _Script()


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def patched_usage(fake_redis):
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage, "_is_non_saas", return_value=False
    ), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config={"plan": "standard"}),
    ), patch.object(
        usage, "_get_org_plan", return_value="standard"
    ), patch.object(
        usage, "get_ai_credit_limit", return_value=10
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": True, "limit": 10},
    ):
        yield


async def test_reserve_grants_when_under_limit(patched_usage, fake_redis):
    new_used = await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert new_used == 1
    assert fake_redis._store["ai_credits_used:1"] == 1


async def test_reserve_rejects_at_limit(patched_usage, fake_redis):
    fake_redis.set("ai_credits_used:1", 10)
    with pytest.raises(HTTPException) as exc:
        await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert exc.value.status_code == 403
    assert fake_redis._store["ai_credits_used:1"] == 10


async def test_reserve_rejects_oversize_request(patched_usage, fake_redis):
    fake_redis.set("ai_credits_used:1", 8)
    with pytest.raises(HTTPException):
        await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=5)
    assert fake_redis._store["ai_credits_used:1"] == 8


async def test_concurrent_burst_cannot_exceed_limit(patched_usage, fake_redis):
    """50 rapid-fire calls with 3 remaining: only 3 succeed."""
    fake_redis.set("ai_credits_used:1", 7)

    successes = 0
    failures = 0
    for _ in range(50):
        try:
            await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
            successes += 1
        except HTTPException:
            failures += 1

    assert successes == 3
    assert failures == 47
    assert fake_redis._store["ai_credits_used:1"] == 10


def test_refund_clamps_at_zero(patched_usage, fake_redis):
    fake_redis.set("ai_credits_used:1", 1)
    new_used = usage.refund_ai_credit(org_id=1, amount=5)
    assert new_used == 0
    assert fake_redis._store["ai_credits_used:1"] == 0


async def test_refund_then_reserve_roundtrip(patched_usage, fake_redis):
    fake_redis.set("ai_credits_used:1", 9)

    await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert fake_redis._store["ai_credits_used:1"] == 10

    usage.refund_ai_credit(org_id=1, amount=1)
    assert fake_redis._store["ai_credits_used:1"] == 9

    await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert fake_redis._store["ai_credits_used:1"] == 10


async def test_durable_operation_reserves_and_refunds_exactly_once(
    patched_usage, fake_redis
):
    first = await usage.reserve_ai_credit_once(
        org_id=1,
        db_session=MagicMock(),
        operation_key="pdfbuild_test",
        amount=8,
    )
    second = await usage.reserve_ai_credit_once(
        org_id=1,
        db_session=MagicMock(),
        operation_key="pdfbuild_test",
        amount=8,
    )

    assert first == second == 8
    assert fake_redis._store["ai_credits_used:1"] == 8

    usage.refund_ai_credit_once(1, "pdfbuild_test", 8)
    usage.refund_ai_credit_once(1, "pdfbuild_test", 8)
    assert fake_redis._store["ai_credits_used:1"] == 0


async def test_durable_credit_refund_never_decrements_a_new_billing_period(
    patched_usage, fake_redis
):
    period_zero = usage.get_ai_credit_period_token(1)
    await usage.reserve_ai_credit_once(
        1, MagicMock(), "old-job", amount=8, period_token=period_zero
    )
    assert fake_redis._store["ai_credits_used:1"] == 8

    usage.reset_ai_credits_usage(1)
    period_one = usage.get_ai_credit_period_token(1)
    assert period_one != period_zero
    await usage.reserve_ai_credit_once(
        1, MagicMock(), "new-job", amount=3, period_token=period_one
    )
    assert fake_redis._store["ai_credits_used:1"] == 3

    usage.refund_ai_credit_once(
        1, "old-job", amount=8, period_token=period_zero
    )
    usage.refund_ai_credit_once(
        1, "old-job", amount=8, period_token=period_zero
    )
    assert fake_redis._store["ai_credits_used:1"] == 3


async def test_pending_operation_cannot_reserve_after_billing_period_reset(
    patched_usage, fake_redis
):
    old_period = usage.get_ai_credit_period_token(1)
    usage.reset_ai_credits_usage(1)

    with pytest.raises(HTTPException) as exc_info:
        await usage.reserve_ai_credit_once(
            1, MagicMock(), "stale-job", amount=8, period_token=old_period
        )

    assert exc_info.value.status_code == 409
    assert fake_redis._store["ai_credits_used:1"] == 0


async def test_reserved_operation_recovers_after_db_commit_crash_and_period_reset(
    patched_usage, fake_redis
):
    old_period = usage.get_ai_credit_period_token(1)

    # Redis succeeds, then the process crashes before the job's PENDING state
    # can be committed as RESERVED in PostgreSQL.
    first = await usage.reserve_ai_credit_once(
        1, MagicMock(), "db-commit-crash", amount=8, period_token=old_period
    )
    persisted_credit_state = "pending"
    assert first == 8
    assert persisted_credit_state == "pending"

    usage.reset_ai_credits_usage(1)
    assert usage.get_ai_credit_period_token(1) != old_period

    recovered = await usage.reserve_ai_credit_once(
        1, MagicMock(), "db-commit-crash", amount=8, period_token=old_period
    )
    persisted_credit_state = "reserved"

    assert recovered == 0
    assert persisted_credit_state == "reserved"
    assert fake_redis._store["ai_credits_used:1"] == 0
    assert (
        fake_redis._store[
            f"ai_credit_reservation:1:{old_period}:db-commit-crash"
        ]
        == 8
    )


def test_refund_no_op_on_zero_or_negative_amount(patched_usage, fake_redis):
    fake_redis.set("ai_credits_used:1", 5)
    assert usage.refund_ai_credit(org_id=1, amount=0) == 0
    assert usage.refund_ai_credit(org_id=1, amount=-3) == 0
    # Must not touch the counter.
    assert fake_redis._store["ai_credits_used:1"] == 5


# --- reserve_ai_credit early-exit branches ----------------------------------


async def test_reserve_raises_404_when_org_has_no_config(fake_redis):
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage, "_load_org_config_for_ai", new_callable=AsyncMock, return_value=None
    ):
        with pytest.raises(HTTPException) as exc:
            await usage.reserve_ai_credit(org_id=99, db_session=MagicMock(), amount=1)
    assert exc.value.status_code == 404


async def test_reserve_raises_403_when_ai_feature_disabled(fake_redis):
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config={"plan": "free"}),
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": False, "limit": 0},
    ):
        with pytest.raises(HTTPException) as exc:
            await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert exc.value.status_code == 403
    assert "not enabled" in exc.value.detail.lower()


async def test_reserve_raises_403_on_free_plan_with_zero_credits(fake_redis):
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage, "_is_non_saas", return_value=False
    ), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config={"plan": "free"}),
    ), patch.object(
        usage, "_get_org_plan", return_value="free"
    ), patch.object(
        usage, "get_ai_credit_limit", return_value=0
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": True, "limit": 0},
    ):
        with pytest.raises(HTTPException) as exc:
            await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert exc.value.status_code == 403
    assert "free plan" in exc.value.detail.lower()


async def test_reserve_in_non_saas_mode_increments_without_limit(fake_redis):
    """OSS/EE deployments track usage but never gate on the limit."""
    fake_redis.set("ai_credits_used:1", 9999)
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage, "_is_non_saas", return_value=True
    ), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config={"plan": "free"}),
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": True, "limit": 0},
    ):
        new_used = await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=2)
    assert new_used == 10001


async def test_reserve_raises_503_when_redis_script_fails():
    failing_redis = MagicMock()
    failing_redis.register_script.side_effect = RuntimeError("lua boom")

    with patch.object(usage, "_get_redis_client", return_value=failing_redis), patch.object(
        usage, "_is_non_saas", return_value=False
    ), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config={"plan": "standard"}),
    ), patch.object(
        usage, "_get_org_plan", return_value="standard"
    ), patch.object(
        usage, "get_ai_credit_limit", return_value=100
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": True, "limit": 100},
    ):
        with pytest.raises(HTTPException) as exc:
            await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=1)
    assert exc.value.status_code == 503
    assert "temporarily unavailable" in exc.value.detail.lower()


async def test_reserve_unlimited_plan_always_grants(fake_redis):
    """``base_credits == -1`` signals unlimited; every reserve succeeds."""
    fake_redis.set("ai_credits_used:1", 100)
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage, "_is_non_saas", return_value=False
    ), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config={"plan": "enterprise"}),
    ), patch.object(
        usage, "_get_org_plan", return_value="enterprise"
    ), patch.object(
        usage, "get_ai_credit_limit", return_value=-1
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": True, "limit": -1},
    ):
        new_used = await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=5)
    assert new_used == 105


async def test_reserve_uses_v2_config_extra_limit(fake_redis):
    """v2 configs expose extra credits via ``overrides.ai.extra_limit``."""
    fake_redis.set("ai_credits_used:1", 10)
    v2_config = {
        "config_version": "2.0",
        "plan": "standard",
        "overrides": {"ai": {"extra_limit": 5}},
    }
    with patch.object(usage, "_get_redis_client", return_value=fake_redis), patch.object(
        usage, "_is_non_saas", return_value=False
    ), patch.object(
        usage,
        "_load_org_config_for_ai",
        new_callable=AsyncMock,
        return_value=MagicMock(config=v2_config),
    ), patch.object(
        usage, "_get_org_plan", return_value="standard"
    ), patch.object(
        usage, "get_ai_credit_limit", return_value=10
    ), patch(
        "src.security.features_utils.resolve.resolve_feature",
        return_value={"enabled": True, "limit": 10},
    ):
        # base 10 + extra 5 = 15 total; used=10, so 5 remaining. Take 5 → OK.
        new_used = await usage.reserve_ai_credit(org_id=1, db_session=MagicMock(), amount=5)
    assert new_used == 15
