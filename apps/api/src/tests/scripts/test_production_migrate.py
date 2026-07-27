from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import production_migrate


REPO_ROOT = Path(__file__).resolve().parents[5]


class FakeInspector:
    def __init__(self, tables: list[str]):
        self.tables = tables

    def get_table_names(self) -> list[str]:
        return self.tables


class FakeConnection:
    def __init__(self):
        self.statements: list[str] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.transaction_active = False

    def execute(self, statement, parameters):
        self.statements.append(str(statement))
        assert parameters == {"lock_id": production_migrate.MIGRATION_LOCK_ID}
        self.transaction_active = True

    def commit(self):
        self.commit_count += 1
        self.transaction_active = False

    def rollback(self):
        self.rollback_count += 1
        self.transaction_active = False

    def in_transaction(self):
        return self.transaction_active


class CommandRecorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def stamp(self, config, revision: str):
        assert config.attributes["connection"] is not None
        config.attributes["connection"].transaction_active = True
        self.calls.append(("stamp", revision))

    def upgrade(self, config, revision: str):
        assert config.attributes["connection"] is not None
        config.attributes["connection"].transaction_active = True
        self.calls.append(("upgrade", revision))


@pytest.mark.parametrize(
    ("tables", "expected"),
    [
        ([], production_migrate.DatabaseState.FRESH),
        (["alembic_version"], production_migrate.DatabaseState.VERSIONED),
        (["organization", "user"], production_migrate.DatabaseState.LEGACY_UNVERSIONED),
    ],
)
def test_classify_database(tables, expected):
    state = production_migrate.classify_database(
        SimpleNamespace(),
        inspector_factory=lambda connection: FakeInspector(tables),
    )

    assert state is expected


def test_fresh_database_bootstraps_current_schema_under_advisory_lock():
    connection = FakeConnection()
    commands = CommandRecorder()
    bootstrapped = []

    state = production_migrate.run_locked_migrations(
        connection,
        {},
        command_api=commands,
        state_resolver=lambda _: production_migrate.DatabaseState.FRESH,
        fresh_bootstrapper=lambda current_connection: bootstrapped.append(
            current_connection
        ),
    )

    assert state is production_migrate.DatabaseState.FRESH
    assert bootstrapped == [connection]
    assert commands.calls == [
        ("stamp", production_migrate.FRESH_RECONCILIATION_BASELINE),
        ("upgrade", "head"),
    ]
    assert "pg_advisory_lock" in connection.statements[0]
    assert "pg_advisory_unlock" in connection.statements[-1]
    assert connection.commit_count == 3
    assert connection.rollback_count == 0


def test_loaded_model_index_names_fit_postgresql_identifier_limit():
    metadata = production_migrate.load_model_metadata()
    offenders = sorted(
        index.name
        for table in metadata.tables.values()
        for index in table.indexes
        if index.name and len(index.name) > 63
    )

    assert offenders == []


def test_versioned_database_upgrades_without_stamping():
    commands = CommandRecorder()

    state = production_migrate.run_migrations_on_connection(
        FakeConnection(),
        {},
        command_api=commands,
        state_resolver=lambda _: production_migrate.DatabaseState.VERSIONED,
    )

    assert state is production_migrate.DatabaseState.VERSIONED
    assert commands.calls == [("upgrade", "head")]


def test_legacy_database_without_baseline_fails_closed():
    commands = CommandRecorder()

    with pytest.raises(production_migrate.MigrationError, match="ALEMBIC_BASELINE"):
        production_migrate.run_migrations_on_connection(
            FakeConnection(),
            {},
            command_api=commands,
            state_resolver=lambda _: (
                production_migrate.DatabaseState.LEGACY_UNVERSIONED
            ),
        )

    assert commands.calls == []


def test_legacy_database_stamps_verified_ancestor_then_upgrades():
    commands = CommandRecorder()
    baseline = "bc3d4e5f6a7b"

    state = production_migrate.run_migrations_on_connection(
        FakeConnection(),
        {"LEARNHOUSE_ALEMBIC_BASELINE": baseline},
        command_api=commands,
        state_resolver=lambda _: production_migrate.DatabaseState.LEGACY_UNVERSIONED,
    )

    assert state is production_migrate.DatabaseState.LEGACY_UNVERSIONED
    assert commands.calls == [("stamp", baseline), ("upgrade", "head")]


def test_unknown_baseline_is_rejected_before_stamp():
    commands = CommandRecorder()

    with pytest.raises(production_migrate.MigrationError, match="known revision"):
        production_migrate.run_migrations_on_connection(
            FakeConnection(),
            {"LEARNHOUSE_ALEMBIC_BASELINE": "not-a-revision"},
            command_api=commands,
            state_resolver=lambda _: (
                production_migrate.DatabaseState.LEGACY_UNVERSIONED
            ),
        )

    assert commands.calls == []


def test_partial_baseline_identifier_is_rejected():
    commands = CommandRecorder()

    with pytest.raises(production_migrate.MigrationError, match="complete revision"):
        production_migrate.run_migrations_on_connection(
            FakeConnection(),
            {"LEARNHOUSE_ALEMBIC_BASELINE": "bc3d"},
            command_api=commands,
            state_resolver=lambda _: (
                production_migrate.DatabaseState.LEGACY_UNVERSIONED
            ),
        )

    assert commands.calls == []


def test_advisory_lock_is_released_when_upgrade_fails():
    connection = FakeConnection()

    class FailingCommands(CommandRecorder):
        def upgrade(self, config, revision: str):
            config.attributes["connection"].transaction_active = True
            raise RuntimeError("upgrade failed")

    with pytest.raises(RuntimeError, match="upgrade failed"):
        production_migrate.run_locked_migrations(
            connection,
            {},
            command_api=FailingCommands(),
            state_resolver=lambda _: production_migrate.DatabaseState.VERSIONED,
        )

    assert "pg_advisory_unlock" in connection.statements[-1]
    assert connection.commit_count == 2
    assert connection.rollback_count == 1


def test_database_url_is_converted_to_sync_psycopg2_driver():
    result = production_migrate.to_sync_postgresql_url(
        "postgresql+asyncpg://user:password@db:5432/learnhouse"
    )

    assert result.drivername == "postgresql+psycopg2"
    assert result.host == "db"


def test_main_does_not_echo_credentials_on_unexpected_failure(monkeypatch, capsys):
    leaked_value = "postgresql://user:secret-password@db:5432/learnhouse"

    def fail(_env):
        raise RuntimeError(leaked_value)

    monkeypatch.setattr(production_migrate, "migrate", fail)

    assert production_migrate.main([]) == 1
    assert leaked_value not in capsys.readouterr().out


def test_env_file_supports_safe_operator_run_with_process_override(
    tmp_path, monkeypatch
):
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "LEARNHOUSE_SQL_CONNECTION_STRING=postgresql://file-host/db\n"
        "LEARNHOUSE_ALEMBIC_BASELINE=bc3d4e5f6a7b\n"
    )
    monkeypatch.setenv(
        "LEARNHOUSE_SQL_CONNECTION_STRING",
        "postgresql://override-host/db",
    )

    loaded = production_migrate._load_environment(str(env_file))

    assert loaded["LEARNHOUSE_SQL_CONNECTION_STRING"] == (
        "postgresql://override-host/db"
    )
    assert loaded["LEARNHOUSE_ALEMBIC_BASELINE"] == "bc3d4e5f6a7b"


def test_production_startup_gate_runs_before_any_service_process():
    startup = (REPO_ROOT / "docker" / "start.sh").read_text()

    assert "set -eu" in startup
    assert startup.index("production_preflight.py") < startup.index(
        "production_migrate.py"
    )
    assert startup.index("production_migrate.py") < startup.index("pm2 start")
    assert startup.index("pm2 start") < startup.index("nginx -g")
    assert 'case "${LEARNHOUSE_ENV:-}"' in startup
