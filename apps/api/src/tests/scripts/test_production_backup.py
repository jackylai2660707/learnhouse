import fcntl
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import production_backup


REPO_ROOT = Path(__file__).resolve().parents[5]


def _config(tmp_path: Path, *, redis_mode: str = "required") -> production_backup.BackupConfig:
    return production_backup.BackupConfig(
        project_root=REPO_ROOT,
        compose_file=REPO_ROOT / "docker-compose.yml",
        output_root=tmp_path / "backups",
        migrations_dir=REPO_ROOT / "apps" / "api" / "migrations" / "versions",
        db_service="db",
        app_service="learnhouse-app",
        redis_service="redis",
        content_path="/app/api/content",
        redis_mode=redis_mode,
        retention_days=14,
        minimum_backups=2,
        offsite_hook=None,
    )


def _install_success_fakes(monkeypatch, config):
    heads = production_backup.discover_migration_heads(config.migrations_dir)
    monkeypatch.setattr(
        production_backup,
        "_database_current_revisions",
        lambda _config: heads,
    )
    monkeypatch.setattr(
        production_backup,
        "_database_tool_version",
        lambda _config: "pg_dump synthetic",
    )
    monkeypatch.setattr(
        production_backup,
        "_create_database_dump",
        lambda _config, destination: destination.write_bytes(b"synthetic-dump"),
    )
    monkeypatch.setattr(
        production_backup,
        "_create_content_archive",
        lambda _config, destination: destination.write_bytes(b"synthetic-content"),
    )
    monkeypatch.setattr(
        production_backup,
        "_create_redis_snapshot",
        lambda _config, destination, backup_id: destination.write_bytes(
            b"REDIS0011synthetic"
        ),
    )
    return heads


def test_discover_migration_heads_matches_current_graph():
    migrations = REPO_ROOT / "apps" / "api" / "migrations" / "versions"

    heads = production_backup.discover_migration_heads(migrations)

    assert heads
    assert all(head.replace("_", "").isalnum() for head in heads)


def test_create_backup_writes_private_checksummed_success_set(monkeypatch, tmp_path):
    config = _config(tmp_path)
    heads = _install_success_fakes(monkeypatch, config)
    now = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)

    backup_dir = production_backup.create_backup(config, now=now)

    assert backup_dir.name == "20260712T093000Z"
    assert (backup_dir / "SUCCESS").read_text() == "ok\n"
    assert not list(config.output_root.glob("*.incomplete-*"))
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert manifest["format_version"] == 1
    assert manifest["database"]["alembic_current"] == heads
    assert manifest["database"]["at_expected_head"] is True
    assert manifest["redis"]["status"] == "included"
    assert manifest["offsite"]["status"] == "unconfigured"
    assert {artifact["name"] for artifact in manifest["artifacts"]} == {
        "database.dump",
        "content.tar.gz",
        "redis.rdb",
    }

    checksum_lines = (backup_dir / "SHA256SUMS").read_text().splitlines()
    for line in checksum_lines:
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((backup_dir / name).read_bytes()).hexdigest() == expected

    assert config.output_root.stat().st_mode & 0o777 == 0o700
    for name in (
        "database.dump",
        "content.tar.gz",
        "redis.rdb",
        "manifest.json",
        "SHA256SUMS",
        "SUCCESS",
    ):
        assert (backup_dir / name).stat().st_mode & 0o777 == 0o600


def test_backup_lock_rejects_concurrent_runner(monkeypatch, tmp_path):
    config = _config(tmp_path)
    _install_success_fakes(monkeypatch, config)
    config.output_root.mkdir(parents=True)
    lock_path = config.output_root / production_backup.LOCK_NAME

    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(production_backup.BackupError) as exc_info:
            production_backup.create_backup(
                config,
                now=datetime(2026, 7, 12, 9, 31, tzinfo=UTC),
            )

    assert exc_info.value.code == "backup_already_running"
    assert not list(config.output_root.glob("*.incomplete-*"))


def test_failed_backup_preserves_previous_success(monkeypatch, tmp_path):
    config = _config(tmp_path)
    heads = production_backup.discover_migration_heads(config.migrations_dir)
    previous = config.output_root / "20260711T093000Z"
    previous.mkdir(parents=True)
    (previous / "SUCCESS").write_text("ok\n")

    monkeypatch.setattr(
        production_backup,
        "_database_current_revisions",
        lambda _config: heads,
    )
    monkeypatch.setattr(
        production_backup,
        "_database_tool_version",
        lambda _config: "pg_dump synthetic",
    )

    def fail_dump(_config, destination):
        raise production_backup.BackupError("database_dump_failed")

    monkeypatch.setattr(production_backup, "_create_database_dump", fail_dump)

    with pytest.raises(production_backup.BackupError) as exc_info:
        production_backup.create_backup(
            config,
            now=datetime(2026, 7, 12, 9, 32, tzinfo=UTC),
        )

    assert exc_info.value.code == "database_dump_failed"
    assert (previous / "SUCCESS").is_file()
    failure = json.loads(
        (config.output_root / production_backup.LAST_FAILURE_NAME).read_text()
    )
    assert failure["code"] == "database_dump_failed"
    assert not list(config.output_root.glob("*.incomplete-*"))


def test_offsite_failure_keeps_local_success_and_skips_retention(monkeypatch, tmp_path):
    hook = tmp_path / "hook"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o700)
    base = _config(tmp_path)
    config = production_backup.BackupConfig(**{**base.__dict__, "offsite_hook": hook})
    _install_success_fakes(monkeypatch, config)
    retention_called = False

    def retention(*args, **kwargs):
        nonlocal retention_called
        retention_called = True
        return []

    monkeypatch.setattr(production_backup, "apply_retention", retention)

    with pytest.raises(production_backup.BackupError) as exc_info:
        production_backup.create_backup(
            config,
            now=datetime(2026, 7, 12, 9, 33, tzinfo=UTC),
        )

    backup_dir = config.output_root / "20260712T093300Z"
    assert exc_info.value.code == "offsite_upload_failed"
    assert (backup_dir / "SUCCESS").is_file()
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert manifest["offsite"]["status"] == "failed"
    assert retention_called is False


def test_retention_keeps_minimum_and_recent_backups(tmp_path):
    output_root = tmp_path / "backups"
    output_root.mkdir()
    for backup_id in (
        "20260712T000000Z",
        "20260711T000000Z",
        "20260701T000000Z",
        "20260601T000000Z",
    ):
        backup_dir = output_root / backup_id
        backup_dir.mkdir()
        (backup_dir / "SUCCESS").write_text("ok\n")

    removed = production_backup.apply_retention(
        output_root,
        retention_days=7,
        minimum_backups=2,
        now=datetime(2026, 7, 12, tzinfo=UTC),
    )

    assert removed == ["20260701T000000Z", "20260601T000000Z"]
    assert (output_root / "20260712T000000Z").is_dir()
    assert (output_root / "20260711T000000Z").is_dir()


def test_backup_status_reports_offsite_degraded_and_stale(tmp_path):
    output_root = tmp_path / "backups"
    backup_dir = output_root / "20260712T000000Z"
    backup_dir.mkdir(parents=True)
    (backup_dir / "SUCCESS").write_text("ok\n")
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "database": {"at_expected_head": True},
                "offsite": {"status": "unconfigured"},
                "redis": {"status": "included"},
            }
        )
    )

    degraded, degraded_exit = production_backup.backup_status(
        output_root,
        max_age_hours=36,
        now=datetime(2026, 7, 12, 12, tzinfo=UTC),
    )
    stale, stale_exit = production_backup.backup_status(
        output_root,
        max_age_hours=36,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert degraded["status"] == "degraded"
    assert degraded["offsite_status"] == "unconfigured"
    assert degraded_exit == 1
    assert stale["status"] == "unhealthy"
    assert stale["code"] == "backup_stale"
    assert stale_exit == 2
