"""Opt-in PostgreSQL regressions for the RAG indexed-content lock contract.

Set both ``LEARNHOUSE_RUN_PG_LOCK_INTEGRATION=1`` and
``LEARNHOUSE_PG_LOCK_TEST_URL`` to run this module.  The URL must target a
caller-created database named ``learnhouse_lock_test*`` or
``learnhouse_lock_integration*``.  The tests never use the application's
configured database URL, migrations, or persistent application tables.
"""

import asyncio
import ipaddress
import os
import re
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import (
    Activity,
    ActivitySubTypeEnum,
    ActivityTypeEnum,
)
from src.services.ai.rag import index_lock


_ENABLE_ENV = "LEARNHOUSE_RUN_PG_LOCK_INTEGRATION"
_URL_ENV = "LEARNHOUSE_PG_LOCK_TEST_URL"
_ISOLATED_DATABASE_RE = re.compile(
    r"^learnhouse_lock_(?:test|integration)(?:_[a-z0-9]+)*$"
)
_LOCK_WAIT_SECONDS = 3


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
    """Return the explicit test URL, refusing ordinary application databases."""
    if os.getenv(_ENABLE_ENV, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"set {_ENABLE_ENV}=1 to run PostgreSQL lock integration")

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
            f"{_URL_ENV} must name an isolated learnhouse_lock_test* or "
            "learnhouse_lock_integration* database"
        )

    # Preserve the password inside SQLAlchemy's URL object. ``str(url)`` masks
    # it and would make a password-authenticated isolated database unusable.
    return url.set(drivername="postgresql+asyncpg")


def test_postgres_integration_guard_rejects_remote_endpoint(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@db.invalid:5432/learnhouse_lock_test_guard",
    )

    with pytest.raises(pytest.UsageError, match="loopback"):
        _isolated_postgres_url()


def test_postgres_integration_guard_preserves_test_password(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@127.0.0.1:15432/learnhouse_lock_test_guard",
    )

    url = _isolated_postgres_url()

    assert isinstance(url, URL)
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "test-only"


def test_postgres_integration_guard_rejects_query_routing_override(monkeypatch):
    monkeypatch.setenv(_ENABLE_ENV, "1")
    monkeypatch.setenv(
        _URL_ENV,
        "postgresql://test:test-only@127.0.0.1:15432/"
        "learnhouse_lock_test_guard?host=db.invalid",
    )

    with pytest.raises(pytest.UsageError, match="query parameters"):
        _isolated_postgres_url()


@pytest.fixture
async def postgres_lock_engine():
    """A short-lived engine for the caller-supplied isolated database only."""
    url = _isolated_postgres_url()
    engine = create_async_engine(
        url,
        pool_size=4,
        max_overflow=0,
        connect_args={
            "server_settings": {"lock_timeout": f"{_LOCK_WAIT_SECONDS}s"}
        },
    )
    try:
        async with engine.connect() as connection:
            database_name = (
                await connection.execute(text("SELECT current_database()"))
            ).scalar_one()
        if database_name != make_url(url).database:
            pytest.fail("PostgreSQL connection did not select the supplied test database")
        yield engine
    finally:
        await engine.dispose()


def _course_ids(count: int) -> tuple[int, ...]:
    """Use positive, test-only advisory-lock ids without touching course rows."""
    start = 1_000_000_000 + secrets.randbelow(900_000_000)
    return tuple(start + offset for offset in range(count))


def _session_factory(engine):
    index_lock.install_indexed_content_write_lock()
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _finish_tasks(*tasks: asyncio.Task) -> None:
    """Release integration tasks without allowing a stuck lock to hang CI."""
    _done, pending = await asyncio.wait(tasks, timeout=_LOCK_WAIT_SECONDS)
    if pending:
        for task in pending:
            task.cancel()
        _done, pending = await asyncio.wait(pending, timeout=_LOCK_WAIT_SECONDS)
    assert not pending, "PostgreSQL lock integration task did not terminate"
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_reverse_write_sets_complete_without_postgresql_deadlock(
    postgres_lock_engine,
):
    """Concurrent A->B and B->A callers serialize on the same sorted locks."""
    first_course, second_course = _course_ids(2)
    factory = _session_factory(postgres_lock_engine)
    first_has_locks = asyncio.Event()
    second_has_locks = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def lock_write_set(course_ids, acquired, release):
        async with factory() as session:
            declared = await index_lock.declare_course_index_write_set(
                course_ids,
                session,
            )
            acquired.set()
            await release.wait()
            await session.commit()
            return declared

    first = asyncio.create_task(
        lock_write_set(
            [second_course, first_course],
            first_has_locks,
            release_first,
        )
    )
    await asyncio.wait_for(first_has_locks.wait(), timeout=_LOCK_WAIT_SECONDS)

    second = asyncio.create_task(
        lock_write_set(
            [first_course, second_course],
            second_has_locks,
            release_second,
        )
    )
    try:
        await asyncio.sleep(0.1)
        assert second.done() is False

        release_first.set()
        await asyncio.wait_for(second_has_locks.wait(), timeout=_LOCK_WAIT_SECONDS)
        release_second.set()
        assert await asyncio.wait_for(first, timeout=_LOCK_WAIT_SECONDS) == (
            first_course,
            second_course,
        )
        assert await asyncio.wait_for(second, timeout=_LOCK_WAIT_SECONDS) == (
            first_course,
            second_course,
        )
    finally:
        release_first.set()
        release_second.set()
        await _finish_tasks(first, second)


async def test_reverse_input_attempts_lower_postgresql_lock_first(
    postgres_lock_engine,
):
    """A reverse caller blocks on the lower key before it can hold the higher key."""
    lower_course, higher_course = _course_ids(2)
    factory = _session_factory(postgres_lock_engine)

    async with (
        factory() as blocker_session,
        factory() as contender_session,
        factory() as observer_session,
    ):
        await index_lock.acquire_course_index_lock(lower_course, blocker_session)
        contender = asyncio.create_task(
            index_lock.declare_course_index_write_set(
                [higher_course, lower_course],
                contender_session,
            )
        )
        try:
            await asyncio.sleep(0.1)
            assert contender.done() is False

            higher_lock_available = (
                await observer_session.execute(
                    text(
                        "SELECT pg_try_advisory_xact_lock("
                        "CAST(:namespace AS INTEGER), CAST(:course_id AS INTEGER))"
                    ),
                    {
                        "namespace": index_lock.COURSE_INDEX_LOCK_NAMESPACE,
                        "course_id": higher_course,
                    },
                )
            ).scalar_one()
            assert higher_lock_available is True
            await observer_session.rollback()

            await blocker_session.commit()
            assert await asyncio.wait_for(
                asyncio.shield(contender), timeout=_LOCK_WAIT_SECONDS
            ) == (lower_course, higher_course)
            await contender_session.commit()
        finally:
            await blocker_session.rollback()
            await observer_session.rollback()
            await _finish_tasks(contender)


async def test_writer_waits_while_reindex_lock_is_held(postgres_lock_engine):
    """A separate writer session cannot enter a reindex transaction's window."""
    (course_id,) = _course_ids(1)
    factory = _session_factory(postgres_lock_engine)

    async with factory() as index_session, factory() as writer_session:
        await index_lock.acquire_course_index_lock(course_id, index_session)
        writer = asyncio.create_task(
            index_lock.acquire_course_index_lock(course_id, writer_session)
        )

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(writer), timeout=0.15)

        await index_session.commit()
        await asyncio.wait_for(writer, timeout=_LOCK_WAIT_SECONDS)
        await writer_session.commit()


async def test_course_move_locks_old_and_new_postgresql_keys(
    postgres_lock_engine,
):
    """An ORM course move holds A and B, including the old foreign-key value."""
    old_course_id, new_course_id = _course_ids(2)
    factory = _session_factory(postgres_lock_engine)

    async with postgres_lock_engine.connect() as connection:
        # This connection-local table shadows no persistent data and disappears
        # when the engine is disposed.  It is only enough schema for Activity's
        # ORM UPDATE to invoke the real before_flush listener.
        writer_session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            await writer_session.execute(
                text(
                    """
                    CREATE TEMPORARY TABLE activity (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        activity_type VARCHAR NOT NULL,
                        activity_sub_type VARCHAR NOT NULL,
                        content JSON NOT NULL,
                        details JSON,
                        published BOOLEAN NOT NULL,
                        lock_type VARCHAR NOT NULL,
                        org_id INTEGER NOT NULL,
                        course_id INTEGER NOT NULL,
                        activity_uuid TEXT NOT NULL,
                        creation_date TEXT NOT NULL,
                        update_date TEXT NOT NULL,
                        extra_metadata JSONB,
                        current_version INTEGER NOT NULL,
                        last_modified_by_id INTEGER
                    ) ON COMMIT PRESERVE ROWS
                    """
                )
            )
            await writer_session.commit()

            # Seed through raw SQL so this isolated database needs no persistent
            # application enum types. The actual course move below still loads
            # and flushes the real Activity mapper and before_flush listener.
            await writer_session.execute(
                text(
                    """
                    INSERT INTO activity (
                        id, name, activity_type, activity_sub_type, content,
                        details, published, lock_type, org_id, course_id,
                        activity_uuid, creation_date, update_date,
                        extra_metadata, current_version, last_modified_by_id
                    ) VALUES (
                        1, 'PostgreSQL lock integration fixture',
                        :activity_type, :activity_sub_type,
                        '{"type":"doc","content":[]}'::json, 'null'::json,
                        FALSE, 'PUBLIC', 1, :course_id,
                        'pg_lock_integration', '', '', 'null'::jsonb, 1, NULL
                    )
                    """
                ),
                {
                    "activity_type": ActivityTypeEnum.TYPE_DYNAMIC.value,
                    "activity_sub_type": ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE.value,
                    "course_id": old_course_id,
                },
            )
            await writer_session.commit()

            activity = await writer_session.get(Activity, 1)
            assert activity is not None
            activity.course_id = new_course_id
            await writer_session.flush()

            async with factory() as observer_session:
                for course_id in (old_course_id, new_course_id):
                    acquired = (
                        await observer_session.execute(
                            text(
                                "SELECT pg_try_advisory_xact_lock("
                                "CAST(:namespace AS INTEGER), "
                                "CAST(:course_id AS INTEGER))"
                            ),
                            {
                                "namespace": index_lock.COURSE_INDEX_LOCK_NAMESPACE,
                                "course_id": course_id,
                            },
                        )
                    ).scalar_one()
                    assert acquired is False
                await observer_session.rollback()
        finally:
            await writer_session.rollback()
            await writer_session.close()


async def test_later_course_declaration_fails_before_acquiring_a_new_lock(
    postgres_lock_engine,
):
    """A later undeclared course never obtains a PostgreSQL advisory lock."""
    declared_course, undeclared_course = _course_ids(2)
    factory = _session_factory(postgres_lock_engine)

    async with factory() as writer_session, factory() as observer_session:
        await index_lock.declare_course_index_write_set(
            [declared_course],
            writer_session,
        )
        with pytest.raises(RuntimeError, match="cannot expand their course set"):
            await index_lock.declare_course_index_write_set(
                [undeclared_course],
                writer_session,
            )

        acquired = (
            await observer_session.execute(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "CAST(:namespace AS INTEGER), CAST(:course_id AS INTEGER))"
                ),
                {
                    "namespace": index_lock.COURSE_INDEX_LOCK_NAMESPACE,
                    "course_id": undeclared_course,
                },
            )
        ).scalar_one()
        assert acquired is True
        await observer_session.rollback()
        await writer_session.rollback()


async def test_root_and_nested_transactions_clear_lock_state_correctly(
    postgres_lock_engine,
):
    """Nested completion preserves state; each completed root transaction clears it."""
    first_course, second_course = _course_ids(2)
    factory = _session_factory(postgres_lock_engine)

    async with factory() as session:
        await index_lock.declare_course_index_write_set([first_course], session)
        assert session.sync_session.info[index_lock._DECLARED_COURSE_IDS_KEY] == frozenset(
            {first_course}
        )

        async with session.begin_nested():
            assert session.sync_session.info[
                index_lock._DECLARED_COURSE_IDS_KEY
            ] == frozenset({first_course})

        assert session.sync_session.info[index_lock._DECLARED_COURSE_IDS_KEY] == frozenset(
            {first_course}
        )
        await session.rollback()
        assert index_lock._DECLARED_COURSE_IDS_KEY not in session.sync_session.info
        assert index_lock._ACQUIRED_COURSE_IDS_KEY not in session.sync_session.info

        await index_lock.declare_course_index_write_set([second_course], session)
        await session.commit()
        assert index_lock._DECLARED_COURSE_IDS_KEY not in session.sync_session.info
        assert index_lock._ACQUIRED_COURSE_IDS_KEY not in session.sync_session.info
