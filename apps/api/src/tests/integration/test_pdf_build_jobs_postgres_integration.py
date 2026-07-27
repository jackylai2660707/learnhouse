"""Opt-in PostgreSQL claim-query concurrency and eligibility regressions.

Set both ``LEARNHOUSE_RUN_PDF_BUILD_PG_INTEGRATION=1`` and
``LEARNHOUSE_PDF_BUILD_PG_TEST_URL`` to run the real-service tests. The URL
must target a caller-created, loopback-only database named
``learnhouse_pdf_build_test*`` or ``learnhouse_pdf_build_integration*``.

The suite never reads the application's configured database URL or runs
migrations. It creates one randomly named scratch schema containing only the
ledger-shaped table needed by the production claim service, then drops that
exact schema during cleanup.

This deliberately hand-built table proves only the production claim query's
behavior on real PostgreSQL. It is not evidence that the h9 migration or full
application schema can be applied successfully; migration tests own that proof.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.pdf_course_build_jobs import (
    PDFBuildCreditState,
    PDFBuildStage,
    PDFCourseBuildJob,
    utc_now,
)
from src.services.ai import pdf_build_jobs


_ENABLE_ENV = "LEARNHOUSE_RUN_PDF_BUILD_PG_INTEGRATION"
_URL_ENV = "LEARNHOUSE_PDF_BUILD_PG_TEST_URL"
_ISOLATED_DATABASE_RE = re.compile(
    r"^learnhouse_pdf_build_(?:test|integration)(?:_[a-z0-9]+)*$"
)
_SCHEMA_RE = re.compile(r"^pdf_build_claim_it_[a-f0-9]{32}$")
_WAIT_SECONDS = 5


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _isolated_postgres_url() -> URL:
    """Return only an explicitly opted-in loopback scratch database URL."""
    if os.getenv(_ENABLE_ENV, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"set {_ENABLE_ENV}=1 to run PDF PostgreSQL integration")

    raw_url = os.getenv(_URL_ENV)
    if not raw_url:
        pytest.skip(f"set {_URL_ENV} to an isolated PostgreSQL test database")

    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(f"{_URL_ENV} must use PostgreSQL")
    if not _is_loopback_host(url.host):
        raise pytest.UsageError(f"{_URL_ENV} must target a loopback host")
    if url.port is None:
        raise pytest.UsageError(f"{_URL_ENV} must include an explicit port")
    if url.query:
        raise pytest.UsageError(f"{_URL_ENV} must not contain query parameters")
    if not url.database or not _ISOLATED_DATABASE_RE.fullmatch(url.database):
        raise pytest.UsageError(
            f"{_URL_ENV} must name an isolated learnhouse_pdf_build_test* or "
            "learnhouse_pdf_build_integration* database"
        )

    # SQLAlchemy masks passwords when a URL is stringified, so preserve the
    # parsed URL object and change only its async driver.
    return url.set(drivername="postgresql+asyncpg")


def test_pdf_postgres_guard_rejects_remote_endpoint(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@db.invalid:5432/"
        "learnhouse_pdf_build_test_guard",
    )

    with pytest.raises(pytest.UsageError, match="loopback"):
        _isolated_postgres_url()


def test_pdf_postgres_guard_rejects_ordinary_database(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@127.0.0.1:15432/learnhouse",
    )

    with pytest.raises(pytest.UsageError, match="isolated"):
        _isolated_postgres_url()


def test_pdf_postgres_guard_rejects_query_routing_override(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@127.0.0.1:15432/"
        "learnhouse_pdf_build_test_guard?host=db.invalid",
    )

    with pytest.raises(pytest.UsageError, match="query parameters"):
        _isolated_postgres_url()


def test_pdf_postgres_guard_preserves_test_password(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@127.0.0.1:15432/"
        "learnhouse_pdf_build_test_guard",
    )

    url = _isolated_postgres_url()

    assert isinstance(url, URL)
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "test-only"


def _claim_query_test_table_ddl(schema: str) -> str:
    """Return only the table shape needed for claim-query behavior tests."""
    if not _SCHEMA_RE.fullmatch(schema):
        raise ValueError("unsafe PDF claim integration schema name")
    return f"""
        CREATE TABLE \"{schema}\".pdf_course_build_job (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            job_uuid VARCHAR(80) NOT NULL UNIQUE,
            org_id INTEGER NOT NULL,
            creator_user_id INTEGER NOT NULL,
            idempotency_key VARCHAR(255) NOT NULL,
            request_fingerprint VARCHAR(64) NOT NULL,
            stage VARCHAR(24) NOT NULL,
            progress_current INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER NOT NULL DEFAULT 0,
            options JSONB NOT NULL,
            plan_checkpoint JSONB,
            course_id INTEGER,
            chapters_created INTEGER NOT NULL DEFAULT 0,
            activities_created INTEGER NOT NULL DEFAULT 0,
            source_documents_created INTEGER NOT NULL DEFAULT 0,
            indexing_status VARCHAR(24),
            indexing_code VARCHAR(80),
            chunks_indexed INTEGER NOT NULL DEFAULT 0,
            warning_code VARCHAR(80),
            warning_message TEXT,
            error_code VARCHAR(80),
            error_message TEXT,
            error_retryable BOOLEAN NOT NULL DEFAULT FALSE,
            credit_amount INTEGER NOT NULL DEFAULT 8,
            credit_period_token VARCHAR(80) NOT NULL DEFAULT '0',
            credit_state VARCHAR(24) NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ,
            lease_owner VARCHAR(160),
            lease_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            staging_cleaned_at TIMESTAMPTZ,
            UNIQUE (org_id, creator_user_id, idempotency_key),
            CHECK (stage IN (
                'extracting', 'planning', 'creating', 'indexing', 'done', 'failed'
            )),
            CHECK (credit_state IN ('pending', 'reserved', 'consumed', 'refunded')),
            CHECK (attempts >= 0),
            CHECK (progress_current >= 0),
            CHECK (progress_total >= 0)
        )
    """


@pytest.fixture
async def pdf_claim_postgres() -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
]:
    """Create and exactly remove one ledger-only scratch schema."""
    url = _isolated_postgres_url()
    schema = f"pdf_build_claim_it_{uuid4().hex}"
    bootstrap_engine = create_async_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "server_settings": {
                "application_name": "learnhouse_pdf_claim_integration_bootstrap",
                "statement_timeout": f"{_WAIT_SECONDS}s",
            }
        },
    )
    worker_engine: AsyncEngine | None = None
    schema_created = False
    try:
        async with bootstrap_engine.begin() as connection:
            database_name = (
                await connection.execute(text("SELECT current_database()"))
            ).scalar_one()
            if database_name != url.database:
                pytest.fail(
                    "PostgreSQL connection did not select the supplied test database"
                )
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
            await connection.execute(text(_claim_query_test_table_ddl(schema)))

        worker_engine = create_async_engine(
            url,
            pool_size=4,
            max_overflow=0,
            connect_args={
                "server_settings": {
                    "application_name": "learnhouse_pdf_claim_integration",
                    "search_path": schema,
                    "lock_timeout": f"{_WAIT_SECONDS}s",
                    "statement_timeout": f"{_WAIT_SECONDS}s",
                }
            },
        )
        factory = async_sessionmaker(
            worker_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with worker_engine.connect() as connection:
            selected_database, selected_schema, ledger_table = (
                await connection.execute(
                    text(
                        "SELECT current_database(), current_schema(), "
                        "to_regclass('pdf_course_build_job')::text"
                    )
                )
            ).one()
        if selected_database != url.database or selected_schema != schema:
            pytest.fail("PDF claim test engine escaped its isolated database/schema")
        if ledger_table != "pdf_course_build_job":
            pytest.fail("isolated PDF claim ledger table is unavailable")

        yield worker_engine, factory
    finally:
        if worker_engine is not None:
            await worker_engine.dispose()
        if schema_created:
            if not _SCHEMA_RE.fullmatch(schema):
                raise RuntimeError("refusing unsafe PDF claim schema cleanup")
            async with bootstrap_engine.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                )
        await bootstrap_engine.dispose()


async def _seed_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
    age_seconds: int,
    attempts: int = 0,
    stage: str = PDFBuildStage.EXTRACTING.value,
    next_attempt_delta_seconds: int | None = None,
    lease_owner: str | None = None,
    lease_expires_delta_seconds: int | None = None,
) -> PDFCourseBuildJob:
    now = utc_now()
    next_attempt_at = None
    if next_attempt_delta_seconds is not None:
        next_attempt_at = now + timedelta(seconds=next_attempt_delta_seconds)
    lease_expires_at = None
    if lease_expires_delta_seconds is not None:
        lease_expires_at = now + timedelta(seconds=lease_expires_delta_seconds)
    job = PDFCourseBuildJob(
        job_uuid=f"pdfbuild_pg_{suffix}_{uuid4().hex}",
        org_id=1,
        creator_user_id=1,
        idempotency_key=f"pg-{suffix}-{uuid4().hex}",
        request_fingerprint="a" * 64,
        stage=stage,
        options={},
        credit_state=PDFBuildCreditState.RESERVED.value,
        attempts=attempts,
        next_attempt_at=next_attempt_at,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        created_at=now - timedelta(seconds=age_seconds),
        updated_at=now - timedelta(seconds=age_seconds),
        started_at=(now - timedelta(seconds=age_seconds)) if attempts else None,
    )
    async with factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)
    return job


class _CommitBarrierSession(AsyncSession):
    """Hold the claimed row lock immediately before the production commit."""

    def __init__(
        self,
        *args,
        commit_started: asyncio.Event,
        release_commit: asyncio.Event,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._commit_started = commit_started
        self._release_commit = release_commit

    async def commit(self) -> None:
        self._commit_started.set()
        await self._release_commit.wait()
        await super().commit()


async def _barrier_claim(
    engine: AsyncEngine,
    *,
    worker_id: str,
    commit_started: asyncio.Event,
    release_commit: asyncio.Event,
) -> PDFCourseBuildJob | None:
    async with _CommitBarrierSession(
        bind=engine,
        expire_on_commit=False,
        commit_started=commit_started,
        release_commit=release_commit,
    ) as session:
        return await pdf_build_jobs.claim_next_pdf_build_job(
            session,
            worker_id=worker_id,
            lease_seconds=60,
        )


async def _finish_task(task: asyncio.Task) -> None:
    """Bound cleanup so a failed lock assertion cannot hang the test process."""
    if not task.done():
        task.cancel()
    _done, pending = await asyncio.wait({task}, timeout=_WAIT_SECONDS)
    assert not pending, "PDF claim integration task did not terminate"
    await asyncio.gather(task, return_exceptions=True)


async def test_two_workers_cannot_claim_the_same_postgresql_job(
    pdf_claim_postgres,
):
    """A locked sole row is skipped, never returned to a second worker."""
    engine, factory = pdf_claim_postgres
    seeded = await _seed_job(factory, suffix="sole", age_seconds=30)
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    first_claim = asyncio.create_task(
        _barrier_claim(
            engine,
            worker_id="worker-a",
            commit_started=commit_started,
            release_commit=release_commit,
        )
    )
    try:
        await asyncio.wait_for(commit_started.wait(), timeout=_WAIT_SECONDS)
        async with factory() as second_session:
            second = await asyncio.wait_for(
                pdf_build_jobs.claim_next_pdf_build_job(
                    second_session,
                    worker_id="worker-b",
                    lease_seconds=60,
                ),
                timeout=_WAIT_SECONDS,
            )
        assert second is None

        release_commit.set()
        first = await asyncio.wait_for(first_claim, timeout=_WAIT_SECONDS)
        assert first is not None
        assert first.id == seeded.id
        assert first.lease_owner == "worker-a"
        assert first.attempts == 1
    finally:
        release_commit.set()
        await _finish_task(first_claim)


async def test_two_workers_claim_separate_postgresql_jobs(
    pdf_claim_postgres,
):
    """SKIP LOCKED lets another worker claim the next eligible ledger row."""
    engine, factory = pdf_claim_postgres
    oldest = await _seed_job(factory, suffix="oldest", age_seconds=60)
    next_job = await _seed_job(factory, suffix="next", age_seconds=30)
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    first_claim = asyncio.create_task(
        _barrier_claim(
            engine,
            worker_id="worker-a",
            commit_started=commit_started,
            release_commit=release_commit,
        )
    )
    try:
        await asyncio.wait_for(commit_started.wait(), timeout=_WAIT_SECONDS)
        async with factory() as second_session:
            second = await asyncio.wait_for(
                pdf_build_jobs.claim_next_pdf_build_job(
                    second_session,
                    worker_id="worker-b",
                    lease_seconds=60,
                ),
                timeout=_WAIT_SECONDS,
            )
        assert second is not None
        assert second.id == next_job.id
        assert second.lease_owner == "worker-b"

        release_commit.set()
        first = await asyncio.wait_for(first_claim, timeout=_WAIT_SECONDS)
        assert first is not None
        assert first.id == oldest.id
        assert first.lease_owner == "worker-a"
        assert {first.id, second.id} == {oldest.id, next_job.id}
    finally:
        release_commit.set()
        await _finish_task(first_claim)


def _claim_mutation_state(job: PDFCourseBuildJob) -> tuple:
    """Fields that the claim service is permitted to mutate on one chosen row."""
    return (
        job.stage,
        job.attempts,
        job.next_attempt_at,
        job.lease_owner,
        job.lease_expires_at,
        job.started_at,
        job.updated_at,
    )


async def test_postgresql_claim_eligibility_matrix_and_untouched_rows(
    pdf_claim_postgres,
):
    """Only a due active row is claimed; every excluded/extra row is unchanged."""
    _engine, factory = pdf_claim_postgres
    rows = [
        await _seed_job(
            factory,
            suffix="terminal-done",
            age_seconds=500,
            attempts=2,
            stage=PDFBuildStage.DONE.value,
        ),
        await _seed_job(
            factory,
            suffix="terminal-failed",
            age_seconds=450,
            attempts=2,
            stage=PDFBuildStage.FAILED.value,
        ),
        await _seed_job(
            factory,
            suffix="unexpired-lease",
            age_seconds=400,
            attempts=1,
            lease_owner="active-worker",
            lease_expires_delta_seconds=60,
        ),
        await _seed_job(
            factory,
            suffix="future-retry",
            age_seconds=350,
            attempts=1,
            next_attempt_delta_seconds=60,
        ),
        await _seed_job(
            factory,
            suffix="past-retry",
            age_seconds=300,
            attempts=1,
            next_attempt_delta_seconds=-60,
        ),
        await _seed_job(
            factory,
            suffix="later-eligible",
            age_seconds=100,
        ),
    ]
    past_retry = rows[4]
    untouched_before = {
        job.id: _claim_mutation_state(job)
        for job in rows
        if job.id != past_retry.id
    }

    async with factory() as session:
        claimed = await pdf_build_jobs.claim_next_pdf_build_job(
            session,
            worker_id="eligibility-worker",
            lease_seconds=60,
        )

    assert claimed is not None
    assert claimed.id == past_retry.id
    assert claimed.next_attempt_at is None
    assert claimed.lease_owner == "eligibility-worker"
    assert claimed.attempts == 2

    async with factory() as observer:
        for job_id, expected_state in untouched_before.items():
            untouched = await observer.get(PDFCourseBuildJob, job_id)
            assert untouched is not None
            assert _claim_mutation_state(untouched) == expected_state


async def test_expired_postgresql_lease_is_reclaimed(
    pdf_claim_postgres,
):
    """An expired owner is replaced and the durable attempt counter advances."""
    _engine, factory = pdf_claim_postgres
    expired = await _seed_job(
        factory,
        suffix="expired",
        age_seconds=90,
        attempts=1,
        lease_owner="expired-worker",
        lease_expires_delta_seconds=-30,
    )

    async with factory() as session:
        reclaimed = await pdf_build_jobs.claim_next_pdf_build_job(
            session,
            worker_id="recovery-worker",
            lease_seconds=60,
        )

    assert reclaimed is not None
    assert reclaimed.id == expired.id
    assert reclaimed.lease_owner == "recovery-worker"
    assert reclaimed.attempts == 2
    assert reclaimed.lease_expires_at is not None
    assert reclaimed.lease_expires_at > utc_now()
