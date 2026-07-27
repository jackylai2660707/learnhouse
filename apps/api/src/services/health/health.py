import asyncio
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Literal

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.core.redis import get_redis_client
from src.services.ai.assignment_config import assignment_ai_configuration_error
from src.services.email.utils import email_configuration_status


SERVICE_NAME = "learnhouse-api"
SERVICE_VERSION = "1.2.2"
DEFAULT_CHECK_TIMEOUT_SECONDS = 3.0

HealthStatus = Literal["healthy", "unhealthy", "disabled", "configured", "degraded"]


class HealthCheckResult(BaseModel):
    status: HealthStatus
    required: bool
    code: str
    latency_ms: float | None = None


class LivenessReport(BaseModel):
    status: Literal["healthy"] = "healthy"
    service: str = SERVICE_NAME
    version: str = SERVICE_VERSION
    timestamp: str


class ReadinessReport(BaseModel):
    status: Literal["ready", "degraded", "not_ready"]
    service: str = SERVICE_NAME
    version: str = SERVICE_VERSION
    timestamp: str
    checks: dict[str, HealthCheckResult]
    integrations: dict[str, HealthCheckResult]

    @property
    def ready(self) -> bool:
        return self.status != "not_ready"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)


def get_liveness() -> LivenessReport:
    return LivenessReport(timestamp=_utc_timestamp())


async def check_database_health(db_session: AsyncSession) -> bool:
    result = await db_session.execute(text("SELECT 1"))
    return result is not None


async def check_database_readiness(
    db_session: AsyncSession,
    timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> HealthCheckResult:
    started_at = perf_counter()
    try:
        healthy = await asyncio.wait_for(
            check_database_health(db_session),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="database_timeout",
            latency_ms=_elapsed_ms(started_at),
        )
    except Exception:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="database_unavailable",
            latency_ms=_elapsed_ms(started_at),
        )

    return HealthCheckResult(
        status="healthy" if healthy else "unhealthy",
        required=True,
        code="ok" if healthy else "database_unavailable",
        latency_ms=_elapsed_ms(started_at),
    )


async def check_redis_readiness(
    timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> HealthCheckResult:
    started_at = perf_counter()
    client = get_redis_client()
    if client is None:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="redis_not_configured",
            latency_ms=_elapsed_ms(started_at),
        )

    try:
        pong = await asyncio.wait_for(
            asyncio.to_thread(client.ping),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="redis_timeout",
            latency_ms=_elapsed_ms(started_at),
        )
    except Exception:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="redis_unavailable",
            latency_ms=_elapsed_ms(started_at),
        )

    return HealthCheckResult(
        status="healthy" if pong else "unhealthy",
        required=True,
        code="ok" if pong else "redis_unavailable",
        latency_ms=_elapsed_ms(started_at),
    )


@lru_cache(maxsize=1)
def get_expected_migration_heads() -> tuple[str, ...]:
    api_dir = Path(__file__).resolve().parents[3]
    alembic_config = AlembicConfig(str(api_dir / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(api_dir / "migrations"))
    return tuple(sorted(ScriptDirectory.from_config(alembic_config).get_heads()))


async def check_migration_readiness(
    db_session: AsyncSession,
    timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> HealthCheckResult:
    started_at = perf_counter()
    try:
        result = await asyncio.wait_for(
            db_session.execute(text("SELECT version_num FROM alembic_version")),
            timeout=timeout_seconds,
        )
        current_heads = tuple(sorted(str(row[0]) for row in result.all()))
        expected_heads = get_expected_migration_heads()
    except TimeoutError:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="migration_check_timeout",
            latency_ms=_elapsed_ms(started_at),
        )
    except Exception:
        return HealthCheckResult(
            status="unhealthy",
            required=True,
            code="migration_state_unavailable",
            latency_ms=_elapsed_ms(started_at),
        )

    healthy = bool(current_heads) and current_heads == expected_heads
    return HealthCheckResult(
        status="healthy" if healthy else "unhealthy",
        required=True,
        code="ok" if healthy else "migration_head_mismatch",
        latency_ms=_elapsed_ms(started_at),
    )


def check_core_config_readiness() -> HealthCheckResult:
    try:
        config = get_learnhouse_config()
        production = config.general_config.env == "production"
        valid = bool(
            config.database_config.sql_connection_string
            and config.redis_config.redis_connection_string
            and len(config.security_config.auth_jwt_secret_key or "") >= 32
            and (
                not production
                or (
                    config.hosting_config.ssl
                    and bool(config.hosting_config.domain)
                    and "localhost" not in config.hosting_config.domain.lower()
                )
            )
        )
    except Exception:
        valid = False

    return HealthCheckResult(
        status="healthy" if valid else "unhealthy",
        required=True,
        code="ok" if valid else "core_config_invalid",
    )


def _optional_integration_statuses() -> dict[str, HealthCheckResult]:
    try:
        config = get_learnhouse_config()
    except Exception:
        return {
            "configuration": HealthCheckResult(
                status="degraded",
                required=False,
                code="config_unavailable",
            )
        }

    ai_enabled = bool(config.ai_config.is_ai_enabled)
    ai_error = assignment_ai_configuration_error(config) if ai_enabled else None
    ai_status = HealthCheckResult(
        status=("disabled" if not ai_enabled else "configured" if ai_error is None else "degraded"),
        required=False,
        code=("disabled" if not ai_enabled else "configured" if ai_error is None else "ai_config_incomplete"),
    )

    judge0_status = HealthCheckResult(
        status="configured" if config.judge0_config else "disabled",
        required=False,
        code="configured" if config.judge0_config else "disabled",
    )

    mail = config.mailing_config
    mail_status = email_configuration_status(mail)
    email_status = HealthCheckResult(
        status="configured" if mail_status.configured else "degraded",
        required=False,
        code=mail_status.code,
    )

    sentry_configured = bool(config.general_config.sentry_config.dsn)
    sentry_status = HealthCheckResult(
        status="configured" if sentry_configured else "disabled",
        required=False,
        code="configured" if sentry_configured else "disabled",
    )

    content = config.hosting_config.content_delivery
    if content.type == "s3api":
        content_configured = bool(content.s3api.bucket_name and content.s3api.endpoint_url)
        content_status = HealthCheckResult(
            status="configured" if content_configured else "degraded",
            required=False,
            code="configured" if content_configured else "content_config_incomplete",
        )
    else:
        offsite_configured = bool(
            os.environ.get("LEARNHOUSE_BACKUP_OFFSITE_HOOK")
            or os.environ.get("LEARNHOUSE_BACKUP_OFFSITE_COMMAND")
        )
        content_status = HealthCheckResult(
            status="configured" if offsite_configured else "degraded",
            required=False,
            code="configured" if offsite_configured else "offsite_backup_unconfigured",
        )

    # RAG embeddings are tracked separately from "ai" because they fail
    # independently: this deployment's chat provider works fine while
    # answering 404 on /embeddings. Without a dedicated backend, RAG either
    # errors or silently degrades to a lexical hash, so neither state may
    # report "configured".
    from src.services.ai.rag.embedding_service import embedding_status

    embeddings = embedding_status()
    if embeddings["degraded"]:
        embedding_code = f"embeddings_degraded:{embeddings['degraded_reason']}"
        embedding_state = "degraded"
    elif not embeddings["dedicated_endpoint_configured"]:
        embedding_code = "embeddings_no_dedicated_endpoint"
        embedding_state = "degraded"
    else:
        embedding_code = "configured"
        embedding_state = "configured"
    embeddings_status = HealthCheckResult(
        status=embedding_state,
        required=False,
        code=embedding_code,
    )

    return {
        "ai": ai_status,
        "embeddings": embeddings_status,
        "judge0": judge0_status,
        "email": email_status,
        "sentry": sentry_status,
        "content_backup": content_status,
    }


async def check_readiness(db_session: AsyncSession) -> ReadinessReport:
    database = await check_database_readiness(db_session)
    redis = await check_redis_readiness()
    migration = (
        await check_migration_readiness(db_session)
        if database.status == "healthy"
        else HealthCheckResult(
            status="unhealthy",
            required=True,
            code="database_dependency_unavailable",
        )
    )
    checks = {
        "database": database,
        "redis": redis,
        "migration": migration,
        "configuration": check_core_config_readiness(),
    }
    integrations = _optional_integration_statuses()

    required_healthy = all(check.status == "healthy" for check in checks.values())
    optional_degraded = any(check.status == "degraded" for check in integrations.values())
    status: Literal["ready", "degraded", "not_ready"] = (
        "not_ready" if not required_healthy else "degraded" if optional_degraded else "ready"
    )
    return ReadinessReport(
        status=status,
        timestamp=_utc_timestamp(),
        checks=checks,
        integrations=integrations,
    )


async def check_health(db_session: AsyncSession) -> bool:
    database_healthy = await check_database_health(db_session)
    if not database_healthy:
        raise HTTPException(status_code=503, detail="Database is not healthy")
    return True
