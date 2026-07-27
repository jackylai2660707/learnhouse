from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.services.health.health import (
    HealthCheckResult,
    check_core_config_readiness,
    check_database_health,
    check_database_readiness,
    check_health,
    check_migration_readiness,
    check_readiness,
    check_redis_readiness,
    get_liveness,
)


def _healthy(*, required: bool = True) -> HealthCheckResult:
    return HealthCheckResult(status="healthy", required=required, code="ok")


def _config(*, production: bool = False):
    return SimpleNamespace(
        general_config=SimpleNamespace(
            env="production" if production else "dev",
            sentry_config=SimpleNamespace(dsn=None),
        ),
        database_config=SimpleNamespace(sql_connection_string="postgresql://configured"),
        redis_config=SimpleNamespace(redis_connection_string="redis://configured"),
        security_config=SimpleNamespace(auth_jwt_secret_key="x" * 32),
        hosting_config=SimpleNamespace(
            ssl=True,
            domain="learn.school.test",
            content_delivery=SimpleNamespace(
                type="filesystem",
                s3api=SimpleNamespace(bucket_name=None, endpoint_url=None),
            ),
        ),
        ai_config=SimpleNamespace(is_ai_enabled=False),
        judge0_config=None,
        mailing_config=SimpleNamespace(
            email_provider="smtp",
            system_email_address="learnhouse@school.test",
            smtp_host="smtp.school.test",
            resend_api_key=None,
        ),
    )


class TestHealthService:
    @pytest.mark.asyncio
    async def test_check_health_returns_true(self, db):
        assert await check_health(db) is True

    @pytest.mark.asyncio
    async def test_check_database_health_returns_true(self, db):
        assert await check_database_health(db) is True

    @pytest.mark.asyncio
    async def test_check_health_raises_on_unhealthy_db(self):
        mock_session = AsyncMock()
        mock_session.execute.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await check_health(mock_session)

        assert exc_info.value.status_code == 503
        assert "Database is not healthy" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_database_readiness_masks_raw_exception(self):
        session = AsyncMock()
        session.execute.side_effect = RuntimeError(
            "postgresql://user:password@db.example/learnhouse"
        )

        result = await check_database_readiness(session)

        assert result.status == "unhealthy"
        assert result.code == "database_unavailable"
        assert "password" not in result.model_dump_json()

    @pytest.mark.asyncio
    async def test_redis_readiness_handles_missing_and_healthy_client(self):
        with patch("src.services.health.health.get_redis_client", return_value=None):
            missing = await check_redis_readiness()
        assert missing.status == "unhealthy"
        assert missing.code == "redis_not_configured"

        client = MagicMock()
        client.ping.return_value = True
        with patch("src.services.health.health.get_redis_client", return_value=client):
            healthy = await check_redis_readiness()
        assert healthy.status == "healthy"
        assert healthy.code == "ok"

    @pytest.mark.asyncio
    async def test_migration_readiness_matches_current_head(self):
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = [("head_revision",)]
        session.execute.return_value = result

        with patch(
            "src.services.health.health.get_expected_migration_heads",
            return_value=("head_revision",),
        ):
            healthy = await check_migration_readiness(session)

        assert healthy.status == "healthy"
        assert healthy.code == "ok"

        result.all.return_value = [("old_revision",)]
        with patch(
            "src.services.health.health.get_expected_migration_heads",
            return_value=("head_revision",),
        ):
            stale = await check_migration_readiness(session)

        assert stale.status == "unhealthy"
        assert stale.code == "migration_head_mismatch"

    def test_core_config_readiness_checks_production_https(self):
        with patch(
            "src.services.health.health.get_learnhouse_config",
            return_value=_config(production=True),
        ):
            assert check_core_config_readiness().status == "healthy"

        invalid = _config(production=True)
        invalid.hosting_config.ssl = False
        with patch(
            "src.services.health.health.get_learnhouse_config",
            return_value=invalid,
        ):
            result = check_core_config_readiness()
        assert result.status == "unhealthy"
        assert result.code == "core_config_invalid"

    @pytest.mark.asyncio
    async def test_readiness_uses_503_only_for_required_checks(self):
        with patch(
            "src.services.health.health.check_database_readiness",
            new=AsyncMock(return_value=_healthy()),
        ), patch(
            "src.services.health.health.check_redis_readiness",
            new=AsyncMock(return_value=_healthy()),
        ), patch(
            "src.services.health.health.check_migration_readiness",
            new=AsyncMock(return_value=_healthy()),
        ), patch(
            "src.services.health.health.check_core_config_readiness",
            return_value=_healthy(),
        ), patch(
            "src.services.health.health._optional_integration_statuses",
            return_value={
                "email": HealthCheckResult(
                    status="degraded",
                    required=False,
                    code="email_config_incomplete",
                )
            },
        ):
            degraded = await check_readiness(AsyncMock())

        assert degraded.ready is True
        assert degraded.status == "degraded"

        with patch(
            "src.services.health.health.check_database_readiness",
            new=AsyncMock(
                return_value=HealthCheckResult(
                    status="unhealthy",
                    required=True,
                    code="database_unavailable",
                )
            ),
        ), patch(
            "src.services.health.health.check_redis_readiness",
            new=AsyncMock(return_value=_healthy()),
        ), patch(
            "src.services.health.health.check_core_config_readiness",
            return_value=_healthy(),
        ), patch(
            "src.services.health.health._optional_integration_statuses",
            return_value={},
        ):
            not_ready = await check_readiness(AsyncMock())

        assert not_ready.ready is False
        assert not_ready.status == "not_ready"
        assert not_ready.checks["migration"].code == "database_dependency_unavailable"

    def test_liveness_is_dependency_free_and_structured(self):
        report = get_liveness()
        assert report.status == "healthy"
        assert report.service == "learnhouse-api"
        assert report.timestamp
