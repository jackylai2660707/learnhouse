"""Opt-in PostgreSQL regression for fresh metadata bootstrap reconciliation.

Set both ``LEARNHOUSE_RUN_FRESH_MIGRATION_PG_INTEGRATION=1`` and
``LEARNHOUSE_FRESH_MIGRATION_PG_TEST_URL`` to run this test.  The URL must
target a caller-created, loopback-only database named
``learnhouse_fresh_migration_test*`` or ``learnhouse_fresh_migration_integration*``.

The test creates and removes one random schema.  It never reads the
application database URL or uses an application schema.
"""

from __future__ import annotations

import ipaddress
import os
import re
from uuid import uuid4

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL, make_url

from scripts import production_migrate


_ENABLE_ENV = "LEARNHOUSE_RUN_FRESH_MIGRATION_PG_INTEGRATION"
_URL_ENV = "LEARNHOUSE_FRESH_MIGRATION_PG_TEST_URL"
_DATABASE_RE = re.compile(
    r"^learnhouse_fresh_migration_(?:test|integration)(?:_[a-z0-9]+)*$"
)
_SCHEMA_RE = re.compile(r"^fresh_migration_h9_[a-f0-9]{32}$")


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
    if os.getenv(_ENABLE_ENV, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"set {_ENABLE_ENV}=1 to run fresh migration integration")

    raw_url = os.getenv(_URL_ENV)
    if not raw_url:
        pytest.skip(f"set {_URL_ENV} to an isolated PostgreSQL test database")

    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(f"{_URL_ENV} must use PostgreSQL")
    if not _is_loopback_host(url.host) or url.port is None:
        raise pytest.UsageError(f"{_URL_ENV} must target loopback with an explicit port")
    if url.query or not url.database or not _DATABASE_RE.fullmatch(url.database):
        raise pytest.UsageError(
            f"{_URL_ENV} must name an isolated learnhouse_fresh_migration_test* database"
        )
    return url.set(drivername="postgresql+psycopg2")


@pytest.fixture
def fresh_migration_connection() -> Connection:
    """Yield one empty, disposable schema on an explicitly opted-in database."""
    url = _isolated_postgres_url()
    schema = f"fresh_migration_h9_{uuid4().hex}"
    schema_created = False
    bootstrap_engine = create_engine(url, pool_pre_ping=True)
    engine = None
    connection: Connection | None = None
    try:
        with bootstrap_engine.begin() as bootstrap_connection:
            selected_database = bootstrap_connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if selected_database != url.database:
                pytest.fail("PostgreSQL connection did not select the supplied test database")
            bootstrap_connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True

        # Set the search path while the migration engine is established so
        # SQLAlchemy inspection classifies this disposable schema, not public.
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={schema},public"},
        )
        connection = engine.connect()
        assert connection.execute(text("SELECT current_schema()")).scalar_one() == schema
        yield connection
    finally:
        if connection is not None:
            if connection.in_transaction():
                connection.rollback()
            connection.close()
        if schema_created:
            if not _SCHEMA_RE.fullmatch(schema):
                raise RuntimeError("refusing unsafe fresh migration schema cleanup")
            with bootstrap_engine.begin() as bootstrap_connection:
                bootstrap_connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                )
        if engine is not None:
            engine.dispose()
        bootstrap_engine.dispose()


def test_fresh_metadata_bootstrap_reconciles_to_single_head_without_h9_conflict(
    fresh_migration_connection,
):
    connection = fresh_migration_connection

    state = production_migrate.run_locked_migrations(connection, {})

    config = production_migrate.build_alembic_config()
    assert state is production_migrate.DatabaseState.FRESH
    assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == list(
        ScriptDirectory.from_config(config).get_heads()
    )
    assert connection.execute(
        text("SELECT to_regclass('pdf_course_build_job')::text")
    ).scalar_one() == "pdf_course_build_job"
