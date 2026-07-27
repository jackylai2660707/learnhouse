#!/usr/bin/env python3
"""Run Alembic safely before LearnHouse production services start."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, URL, make_url
from dotenv import dotenv_values
from sqlmodel import SQLModel


API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
MIGRATION_LOCK_ID = 4_836_929_740_117_283_411
FRESH_RECONCILIATION_BASELINE = "bc3d4e5f6a7b"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


class MigrationError(RuntimeError):
    """Safe operator-facing migration error."""


class DatabaseState(str, Enum):
    FRESH = "fresh"
    VERSIONED = "versioned"
    LEGACY_UNVERSIONED = "legacy_unversioned"


def load_model_metadata():
    model_roots = (
        (API_ROOT / "src" / "db", "src.db"),
        (API_ROOT / "ee" / "db", "ee.db"),
    )
    for base_dir, base_module in model_roots:
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            relative_module = path.relative_to(base_dir).with_suffix("")
            module_name = ".".join((base_module, *relative_module.parts))
            importlib.import_module(module_name)
    return SQLModel.metadata


def bootstrap_fresh_database(connection: Connection) -> None:
    try:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:
        raise MigrationError(
            f"Fresh database extension bootstrap failed ({type(exc).__name__})."
        ) from exc

    try:
        metadata = load_model_metadata()
    except Exception as exc:
        raise MigrationError(
            f"Fresh database model loading failed ({type(exc).__name__})."
        ) from exc

    try:
        metadata.create_all(connection)
    except Exception as exc:
        raise MigrationError(
            f"Fresh database schema creation failed ({type(exc).__name__})."
        ) from exc


def build_alembic_config(connection: Connection | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def to_sync_postgresql_url(raw_url: str) -> URL:
    if not raw_url.strip():
        raise MigrationError("LEARNHOUSE_SQL_CONNECTION_STRING is required.")
    try:
        parsed = make_url(raw_url)
    except Exception as exc:
        raise MigrationError(
            "LEARNHOUSE_SQL_CONNECTION_STRING is not a valid database URL."
        ) from exc
    if parsed.get_backend_name() != "postgresql":
        raise MigrationError("Production migrations require a PostgreSQL database.")
    return parsed.set(drivername="postgresql+psycopg2")


def classify_database(
    connection: Connection,
    *,
    inspector_factory: Callable[[Connection], object] = inspect,
) -> DatabaseState:
    inspector = inspector_factory(connection)
    tables = set(inspector.get_table_names())  # type: ignore[attr-defined]
    if "alembic_version" in tables:
        return DatabaseState.VERSIONED
    if not tables:
        return DatabaseState.FRESH
    return DatabaseState.LEGACY_UNVERSIONED


def validate_baseline(baseline: str, config: Config) -> str:
    candidate = baseline.strip()
    if not candidate:
        raise MigrationError(
            "This database has application tables but no alembic_version table. "
            "Set LEARNHOUSE_ALEMBIC_BASELINE to a verified deployed revision."
        )

    script = ScriptDirectory.from_config(config)
    heads = list(script.get_heads())
    if len(heads) != 1:
        raise MigrationError("The migration graph must have exactly one head.")

    try:
        revision = script.get_revision(candidate)
    except CommandError as exc:
        raise MigrationError(
            "LEARNHOUSE_ALEMBIC_BASELINE is not a known revision."
        ) from exc
    if revision is None:
        raise MigrationError("LEARNHOUSE_ALEMBIC_BASELINE is not a known revision.")
    if revision.revision != candidate:
        raise MigrationError(
            "LEARNHOUSE_ALEMBIC_BASELINE must use the complete revision identifier."
        )

    ancestry = {item.revision for item in script.iterate_revisions(heads[0], "base")}
    if revision.revision not in ancestry:
        raise MigrationError(
            "LEARNHOUSE_ALEMBIC_BASELINE is not an ancestor of the current head."
        )
    return revision.revision


def run_migrations_on_connection(
    connection: Connection,
    env: Mapping[str, str],
    *,
    command_api: object = command,
    state_resolver: Callable[[Connection], DatabaseState] = classify_database,
    fresh_bootstrapper: Callable[[Connection], None] = bootstrap_fresh_database,
) -> DatabaseState:
    config = build_alembic_config(connection)
    state = state_resolver(connection)

    if state is DatabaseState.FRESH:
        fresh_bootstrapper(connection)
        baseline = validate_baseline(FRESH_RECONCILIATION_BASELINE, config)
        command_api.stamp(config, baseline)  # type: ignore[attr-defined]
        command_api.upgrade(config, "head")  # type: ignore[attr-defined]
        return state

    if state is DatabaseState.LEGACY_UNVERSIONED:
        baseline = validate_baseline(env.get("LEARNHOUSE_ALEMBIC_BASELINE", ""), config)
        command_api.stamp(config, baseline)  # type: ignore[attr-defined]

    command_api.upgrade(config, "head")  # type: ignore[attr-defined]
    return state


def run_locked_migrations(
    connection: Connection,
    env: Mapping[str, str],
    *,
    command_api: object = command,
    state_resolver: Callable[[Connection], DatabaseState] = classify_database,
    fresh_bootstrapper: Callable[[Connection], None] = bootstrap_fresh_database,
) -> DatabaseState:
    lock_statement = text("SELECT pg_advisory_lock(:lock_id)")
    unlock_statement = text("SELECT pg_advisory_unlock(:lock_id)")
    connection.execute(lock_statement, {"lock_id": MIGRATION_LOCK_ID})
    connection.commit()
    try:
        state = run_migrations_on_connection(
            connection,
            env,
            command_api=command_api,
            state_resolver=state_resolver,
            fresh_bootstrapper=fresh_bootstrapper,
        )
        if connection.in_transaction():
            connection.commit()
        return state
    except Exception:
        if connection.in_transaction():
            connection.rollback()
        raise
    finally:
        connection.execute(unlock_statement, {"lock_id": MIGRATION_LOCK_ID})
        connection.commit()


def migrate(env: Mapping[str, str]) -> DatabaseState:
    database_url = to_sync_postgresql_url(
        env.get("LEARNHOUSE_SQL_CONNECTION_STRING", "")
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return run_locked_migrations(connection, env)
    finally:
        engine.dispose()


def _load_environment(env_file: str | None) -> Mapping[str, str]:
    if not env_file:
        return os.environ
    file_values = {key: value or "" for key, value in dotenv_values(env_file).items()}
    file_values.update(os.environ)
    return file_values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        help="Optional dotenv file for an operator-run migration.",
    )
    args = parser.parse_args(argv)
    try:
        state = migrate(_load_environment(args.env_file))
    except MigrationError as exc:
        print(f"Production migration blocked: {exc}")
        return 1
    except Exception:
        print(
            "Production migration failed. Check database connectivity and migration "
            "logs; credentials are intentionally omitted."
        )
        return 1

    print(f"Production migration completed from database state: {state.value}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
