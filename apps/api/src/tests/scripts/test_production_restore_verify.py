import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import production_restore_verify


def _write_content_archive(path: Path, *, unsafe: bool = False) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        payload = b"course content"
        info = tarfile.TarInfo("../escape.txt" if unsafe else "course/file.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def _write_valid_backup(tmp_path: Path) -> tuple[Path, dict]:
    backup_dir = tmp_path / "20260712T100000Z"
    backup_dir.mkdir(mode=0o700)
    (backup_dir / "database.dump").write_bytes(b"synthetic database dump")
    _write_content_archive(backup_dir / "content.tar.gz")
    (backup_dir / "redis.rdb").write_bytes(b"REDIS0011synthetic")

    artifacts = []
    for name in ("database.dump", "content.tar.gz", "redis.rdb"):
        path = backup_dir / name
        path.chmod(0o600)
        artifacts.append(
            {
                "name": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )

    manifest = {
        "artifacts": artifacts,
        "backup_id": backup_dir.name,
        "database": {
            "alembic_current": ["head123"],
            "alembic_expected_heads": ["head123"],
            "at_expected_head": True,
        },
        "format_version": 1,
        "redis": {"status": "included"},
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)

    checksum_names = ["database.dump", "content.tar.gz", "redis.rdb", "manifest.json"]
    checksum_lines = [
        f"{hashlib.sha256((backup_dir / name).read_bytes()).hexdigest()}  {name}"
        for name in checksum_names
    ]
    checksums_path = backup_dir / "SHA256SUMS"
    checksums_path.write_text("\n".join(checksum_lines) + "\n")
    checksums_path.chmod(0o600)
    success_path = backup_dir / "SUCCESS"
    success_path.write_text("ok\n")
    success_path.chmod(0o600)
    return backup_dir, manifest


def _refresh_content_metadata(backup_dir: Path, manifest: dict) -> None:
    content_path = backup_dir / "content.tar.gz"
    content = next(
        artifact for artifact in manifest["artifacts"] if artifact["name"] == "content.tar.gz"
    )
    content["sha256"] = hashlib.sha256(content_path.read_bytes()).hexdigest()
    content["size_bytes"] = content_path.stat().st_size
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    lines = []
    for name in ("database.dump", "content.tar.gz", "redis.rdb", "manifest.json"):
        lines.append(
            f"{hashlib.sha256((backup_dir / name).read_bytes()).hexdigest()}  {name}"
        )
    (backup_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def test_verify_backup_files_checks_manifest_archives_and_redis(tmp_path):
    backup_dir, manifest = _write_valid_backup(tmp_path)

    result_manifest, result = production_restore_verify.verify_backup_files(backup_dir)

    assert result_manifest == manifest
    assert result["content"]["regular_file_count"] == 1
    assert result["content"]["uncompressed_size_bytes"] == len(b"course content")
    assert result["redis"]["format"] == "REDIS0011"


def test_verify_backup_files_rejects_checksum_mismatch(tmp_path):
    backup_dir, _manifest = _write_valid_backup(tmp_path)
    (backup_dir / "database.dump").write_bytes(b"tampered")

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.verify_backup_files(backup_dir)

    assert exc_info.value.code == "checksum_mismatch"


def test_verify_backup_files_rejects_unsafe_content_path(tmp_path):
    backup_dir, manifest = _write_valid_backup(tmp_path)
    _write_content_archive(backup_dir / "content.tar.gz", unsafe=True)
    (backup_dir / "content.tar.gz").chmod(0o600)
    _refresh_content_metadata(backup_dir, manifest)

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.verify_backup_files(backup_dir)

    assert exc_info.value.code == "content_archive_unsafe"


def test_verify_backup_files_rejects_world_readable_artifact(tmp_path):
    backup_dir, _manifest = _write_valid_backup(tmp_path)
    (backup_dir / "database.dump").chmod(0o644)

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.verify_backup_files(backup_dir)

    assert exc_info.value.code == "backup_permissions_unsafe"


def test_verify_backup_files_rejects_symlinked_artifact(tmp_path):
    backup_dir, _manifest = _write_valid_backup(tmp_path)
    database_path = backup_dir / "database.dump"
    external_path = tmp_path / "external.dump"
    external_path.write_bytes(database_path.read_bytes())
    database_path.unlink()
    database_path.symlink_to(external_path)

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.verify_backup_files(backup_dir)

    assert exc_info.value.code == "checksum_mismatch"


def test_restore_database_checks_head_schema_and_aggregates(monkeypatch, tmp_path):
    backup_dir, manifest = _write_valid_backup(tmp_path)
    monkeypatch.setattr(
        production_restore_verify,
        "_start_container",
        lambda image, tmpfs_size_mb: "verify-container",
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_wait_for_postgres",
        lambda container_name: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_quiet",
        lambda command, code: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_capture",
        lambda command, code: b"archive entry\n",
    )

    def psql(container_name, sql, code):
        if "version_num" in sql:
            return ["head123"]
        if "information_schema.tables" in sql:
            return [
                "alembic_version",
                "organization",
                "user",
                "course",
                "assignment",
                "assignmentsubmission",
            ]
        return ["3"]

    monkeypatch.setattr(production_restore_verify, "_psql", psql)
    cleanup_calls = []
    monkeypatch.setattr(
        production_restore_verify.subprocess,
        "run",
        lambda command, **kwargs: cleanup_calls.append(command)
        or SimpleNamespace(returncode=0),
    )

    result = production_restore_verify.restore_database(
        backup_dir,
        manifest,
        image="pgvector/pgvector:pg16",
        tmpfs_size_mb=512,
    )

    assert result["alembic_current"] == ["head123"]
    assert result["alembic_expected_heads"] == ["head123"]
    assert result["at_expected_head"] is True
    assert result["migration_head_policy"] == "current_head_required"
    assert result["table_count"] == 6
    assert result["aggregate_counts"]["organization"] == 3
    assert cleanup_calls[-1] == ["docker", "rm", "--force", "verify-container"]


@pytest.mark.parametrize("allow_mismatch", [False, True])
def test_restore_database_never_accepts_revision_different_from_manifest_current(
    monkeypatch, tmp_path, allow_mismatch
):
    backup_dir, manifest = _write_valid_backup(tmp_path)
    monkeypatch.setattr(
        production_restore_verify,
        "_start_container",
        lambda image, tmpfs_size_mb: "verify-container",
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_wait_for_postgres",
        lambda container_name: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_quiet",
        lambda command, code: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_capture",
        lambda command, code: b"archive entry\n",
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_psql",
        lambda container_name, sql, code: ["different123"],
    )
    monkeypatch.setattr(
        production_restore_verify.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.restore_database(
            backup_dir,
            manifest,
            image="pgvector/pgvector:pg16",
            tmpfs_size_mb=512,
            allow_migration_head_mismatch=allow_mismatch,
        )

    assert exc_info.value.code == "restore_migration_head_mismatch"


def test_restore_database_requires_explicit_opt_in_for_pre_migration_backup(
    monkeypatch, tmp_path
):
    backup_dir, manifest = _write_valid_backup(tmp_path)
    manifest["database"] = {
        "alembic_current": ["old123"],
        "alembic_expected_heads": ["head123"],
        "at_expected_head": False,
    }
    monkeypatch.setattr(
        production_restore_verify,
        "_start_container",
        lambda image, tmpfs_size_mb: "verify-container",
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_wait_for_postgres",
        lambda container_name: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_quiet",
        lambda command, code: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_capture",
        lambda command, code: b"archive entry\n",
    )

    def psql(container_name, sql, code):
        if "version_num" in sql:
            return ["old123"]
        if "information_schema.tables" in sql:
            return ["alembic_version", "organization", "user", "course", "assignment"]
        return ["0"]

    monkeypatch.setattr(production_restore_verify, "_psql", psql)
    monkeypatch.setattr(
        production_restore_verify.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.restore_database(
            backup_dir,
            manifest,
            image="pgvector/pgvector:pg16",
            tmpfs_size_mb=512,
        )
    assert exc_info.value.code == "restore_migration_head_mismatch"

    result = production_restore_verify.restore_database(
        backup_dir,
        manifest,
        image="pgvector/pgvector:pg16",
        tmpfs_size_mb=512,
        allow_migration_head_mismatch=True,
    )

    assert result["alembic_current"] == ["old123"]
    assert result["alembic_expected_heads"] == ["head123"]
    assert result["at_expected_head"] is False
    assert result["migration_head_policy"] == "pre_migration_mismatch_allowed"


@pytest.mark.parametrize(
    ("current", "expected", "at_expected"),
    [
        ([], ["head123"], False),
        (["old123"], [], False),
        (["old123", "old123"], ["head123"], False),
        (["old123"], ["head123", "head123"], False),
        (["old123"], ["head123"], True),
        (["head123"], ["head123"], False),
    ],
)
def test_restore_database_rejects_invalid_or_inconsistent_revision_metadata(
    monkeypatch, tmp_path, current, expected, at_expected
):
    backup_dir, manifest = _write_valid_backup(tmp_path)
    manifest["database"] = {
        "alembic_current": current,
        "alembic_expected_heads": expected,
        "at_expected_head": at_expected,
    }
    monkeypatch.setattr(
        production_restore_verify,
        "_start_container",
        lambda image, tmpfs_size_mb: "verify-container",
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_wait_for_postgres",
        lambda container_name: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_quiet",
        lambda command, code: None,
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_run_capture",
        lambda command, code: b"archive entry\n",
    )
    restored = current if current else ["old123"]
    monkeypatch.setattr(
        production_restore_verify,
        "_psql",
        lambda container_name, sql, code: restored,
    )
    monkeypatch.setattr(
        production_restore_verify.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.restore_database(
            backup_dir,
            manifest,
            image="pgvector/pgvector:pg16",
            tmpfs_size_mb=512,
            allow_migration_head_mismatch=True,
        )

    assert exc_info.value.code == "restore_migration_head_mismatch"


def test_main_propagates_pre_migration_mode_and_discloses_policy(
    monkeypatch, tmp_path, capsys
):
    backup_dir, _manifest = _write_valid_backup(tmp_path)
    report_path = tmp_path / "pre-migration-report.json"
    captured = {}

    def verify_restore(path, *, image, tmpfs_size_mb, allow_migration_head_mismatch):
        captured["allow_migration_head_mismatch"] = allow_migration_head_mismatch
        return {
            "backup_id": path.name,
            "status": "succeeded",
            "database": {
                "alembic_current": ["old123"],
                "alembic_expected_heads": ["head123"],
                "at_expected_head": False,
                "migration_head_policy": "pre_migration_mismatch_allowed",
                "table_count": 59,
            },
        }

    monkeypatch.setattr(production_restore_verify, "verify_restore", verify_restore)

    exit_code = production_restore_verify.main(
        [
            str(backup_dir),
            "--allow-migration-head-mismatch",
            "--report",
            str(report_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    report = json.loads(report_path.read_text())
    assert exit_code == 0
    assert captured["allow_migration_head_mismatch"] is True
    assert output["alembic_current"] == ["old123"]
    assert output["alembic_expected_heads"] == ["head123"]
    assert output["at_expected_head"] is False
    assert output["migration_head_policy"] == "pre_migration_mismatch_allowed"
    assert report["database"]["alembic_current"] == ["old123"]
    assert report["database"]["alembic_expected_heads"] == ["head123"]
    assert report["database"]["at_expected_head"] is False
    assert report["database"]["migration_head_policy"] == (
        "pre_migration_mismatch_allowed"
    )


def test_restore_database_cleans_container_after_restore_failure(monkeypatch, tmp_path):
    backup_dir, manifest = _write_valid_backup(tmp_path)
    monkeypatch.setattr(
        production_restore_verify,
        "_start_container",
        lambda image, tmpfs_size_mb: "verify-container",
    )
    monkeypatch.setattr(
        production_restore_verify,
        "_wait_for_postgres",
        lambda container_name: None,
    )

    def fail_restore(command, code):
        raise production_restore_verify.RestoreVerificationError("restore_database_failed")

    monkeypatch.setattr(production_restore_verify, "_run_quiet", fail_restore)
    cleanup_calls = []
    monkeypatch.setattr(
        production_restore_verify.subprocess,
        "run",
        lambda command, **kwargs: cleanup_calls.append(command)
        or SimpleNamespace(returncode=0),
    )

    with pytest.raises(production_restore_verify.RestoreVerificationError) as exc_info:
        production_restore_verify.restore_database(
            backup_dir,
            manifest,
            image="pgvector/pgvector:pg16",
            tmpfs_size_mb=512,
        )

    assert exc_info.value.code == "restore_database_failed"
    assert cleanup_calls[-1] == ["docker", "rm", "--force", "verify-container"]


def test_latest_successful_backup_ignores_incomplete_and_non_backup_dirs(tmp_path):
    older, _manifest = _write_valid_backup(tmp_path)
    newer = tmp_path / "20260713T100000Z"
    newer.mkdir()
    (newer / "SUCCESS").write_text("ok\n")
    incomplete = tmp_path / "20260714T100000Z"
    incomplete.mkdir()
    (tmp_path / "notes").mkdir()

    result = production_restore_verify.latest_successful_backup(tmp_path)

    assert result == newer
    assert result != older
