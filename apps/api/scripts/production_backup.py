#!/usr/bin/env python3
"""Create a non-interactive, checksummed LearnHouse production backup set."""

from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Sequence


BACKUP_FORMAT_VERSION = 1
SUCCESS_MARKER = "SUCCESS"
MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
LOCK_NAME = ".backup.lock"
LAST_FAILURE_NAME = "LAST_FAILURE.json"
BACKUP_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")
SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class BackupError(RuntimeError):
    def __init__(self, code: str, *, exit_code: int = 1) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class BackupConfig:
    project_root: Path
    compose_file: Path
    output_root: Path
    migrations_dir: Path
    db_service: str
    app_service: str
    redis_service: str
    content_path: str
    redis_mode: str
    retention_days: int
    minimum_backups: int
    offsite_hook: Path | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _backup_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _validate_service_name(value: str) -> str:
    if not SERVICE_NAME_RE.fullmatch(value):
        raise BackupError("invalid_service_name", exit_code=2)
    return value


def _safe_chmod(path: Path, mode: int) -> None:
    path.chmod(mode)


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    _safe_chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    _atomic_write(path, serialized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_metadata(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _write_checksums(backup_dir: Path, artifact_names: Sequence[str]) -> None:
    lines = [f"{_sha256(backup_dir / name)}  {name}" for name in artifact_names]
    _atomic_write(
        backup_dir / CHECKSUMS_NAME,
        ("\n".join(lines) + "\n").encode("ascii"),
    )


def _run_capture(command: Sequence[str], *, code: str) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise BackupError(code) from exc
    if result.returncode != 0:
        raise BackupError(code)
    return result.stdout


def _run_to_file(command: Sequence[str], destination: Path, *, code: str) -> None:
    try:
        with destination.open("wb") as handle:
            result = subprocess.run(
                list(command),
                check=False,
                stdout=handle,
                stderr=subprocess.PIPE,
            )
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise BackupError(code) from exc
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise BackupError(code)
    _safe_chmod(destination, stat.S_IRUSR | stat.S_IWUSR)


def _run_quiet(command: Sequence[str], *, code: str) -> None:
    _run_capture(command, code=code)


def _compose_command(config: BackupConfig, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(config.project_root),
        "-f",
        str(config.compose_file),
        *arguments,
    ]


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                target_name = target.id
                value = node.value
        if target_name == name and value is not None:
            return ast.literal_eval(value)
    raise BackupError("migration_metadata_invalid")


def discover_migration_heads(migrations_dir: Path) -> list[str]:
    revisions: set[str] = set()
    parent_revisions: set[str] = set()
    for migration_path in sorted(migrations_dir.glob("*.py")):
        if migration_path.name.startswith("__"):
            continue
        try:
            tree = ast.parse(migration_path.read_text(encoding="utf-8"))
            revision = _literal_assignment(tree, "revision")
            down_revision = _literal_assignment(tree, "down_revision")
        except (OSError, SyntaxError, ValueError, TypeError) as exc:
            raise BackupError("migration_metadata_invalid") from exc
        if not isinstance(revision, str) or not revision or revision in revisions:
            raise BackupError("migration_metadata_invalid")
        revisions.add(revision)
        if isinstance(down_revision, str):
            parent_revisions.add(down_revision)
        elif isinstance(down_revision, (tuple, list)):
            if not all(isinstance(item, str) for item in down_revision):
                raise BackupError("migration_metadata_invalid")
            parent_revisions.update(down_revision)
        elif down_revision is not None:
            raise BackupError("migration_metadata_invalid")

    heads = sorted(revisions - parent_revisions)
    if not revisions or not heads:
        raise BackupError("migration_metadata_invalid")
    return heads


def _database_current_revisions(config: BackupConfig) -> list[str]:
    command = _compose_command(
        config,
        "exec",
        "-T",
        config.db_service,
        "sh",
        "-eu",
        "-c",
        (
            'exec psql --tuples-only --no-align --username="$POSTGRES_USER" '
            '--dbname="$POSTGRES_DB" '
            '-c "SELECT version_num FROM alembic_version ORDER BY version_num"'
        ),
    )
    output = _run_capture(command, code="database_revision_read_failed")
    revisions = sorted(
        line.strip() for line in output.decode("utf-8", errors="replace").splitlines() if line.strip()
    )
    if not revisions or not all(re.fullmatch(r"[A-Za-z0-9_-]+", item) for item in revisions):
        raise BackupError("database_revision_read_failed")
    return revisions


def _database_tool_version(config: BackupConfig) -> str:
    output = _run_capture(
        _compose_command(
            config,
            "exec",
            "-T",
            config.db_service,
            "pg_dump",
            "--version",
        ),
        code="database_tool_check_failed",
    )
    return output.decode("utf-8", errors="replace").strip()[:200]


def _create_database_dump(config: BackupConfig, destination: Path) -> None:
    command = _compose_command(
        config,
        "exec",
        "-T",
        config.db_service,
        "sh",
        "-eu",
        "-c",
        (
            'exec pg_dump --format=custom --no-owner --no-privileges '
            '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
        ),
    )
    _run_to_file(command, destination, code="database_dump_failed")


def _create_content_archive(config: BackupConfig, destination: Path) -> None:
    command = _compose_command(
        config,
        "exec",
        "-T",
        config.app_service,
        "tar",
        "-C",
        config.content_path,
        "-czf",
        "-",
        ".",
    )
    _run_to_file(command, destination, code="content_archive_failed")


def _create_redis_snapshot(config: BackupConfig, destination: Path, backup_id: str) -> None:
    remote_path = f"/tmp/learnhouse-backup-{backup_id}.rdb"
    try:
        _run_quiet(
            _compose_command(
                config,
                "exec",
                "-T",
                config.redis_service,
                "redis-cli",
                "--rdb",
                remote_path,
            ),
            code="redis_snapshot_failed",
        )
        _run_quiet(
            _compose_command(
                config,
                "cp",
                f"{config.redis_service}:{remote_path}",
                str(destination),
            ),
            code="redis_snapshot_copy_failed",
        )
        if not destination.exists() or destination.stat().st_size < 9:
            raise BackupError("redis_snapshot_invalid")
        with destination.open("rb") as handle:
            if not handle.read(5).startswith(b"REDIS"):
                raise BackupError("redis_snapshot_invalid")
        _safe_chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        try:
            subprocess.run(
                _compose_command(
                    config,
                    "exec",
                    "-T",
                    config.redis_service,
                    "rm",
                    "-f",
                    remote_path,
                ),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


def _successful_backup_dirs(output_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in output_root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and BACKUP_ID_RE.fullmatch(path.name)
            and (path / SUCCESS_MARKER).is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def apply_retention(
    output_root: Path,
    *,
    retention_days: int,
    minimum_backups: int,
    now: datetime,
) -> list[str]:
    cutoff = now - timedelta(days=retention_days)
    removed: list[str] = []
    for index, backup_dir in enumerate(_successful_backup_dirs(output_root)):
        if index < minimum_backups:
            continue
        created = datetime.strptime(backup_dir.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if created >= cutoff:
            continue
        shutil.rmtree(backup_dir)
        removed.append(backup_dir.name)
    return removed


def backup_status(
    output_root: Path,
    *,
    max_age_hours: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], int]:
    checked_at = now or _utc_now()
    if not output_root.is_dir():
        return (
            {
                "checked_at": _isoformat(checked_at),
                "code": "backup_root_missing",
                "status": "unhealthy",
            },
            2,
        )
    backups = _successful_backup_dirs(output_root)
    if not backups:
        return (
            {
                "checked_at": _isoformat(checked_at),
                "code": "backup_not_found",
                "status": "unhealthy",
            },
            2,
        )

    latest = backups[0]
    try:
        created_at = datetime.strptime(latest.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        manifest = json.loads((latest / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return (
            {
                "backup_id": latest.name,
                "checked_at": _isoformat(checked_at),
                "code": "backup_metadata_invalid",
                "status": "unhealthy",
            },
            2,
        )

    age_hours = round(max(0.0, (checked_at - created_at).total_seconds() / 3600), 3)
    offsite_status = manifest.get("offsite", {}).get("status", "unknown")
    redis_status = manifest.get("redis", {}).get("status", "unknown")
    at_expected_head = manifest.get("database", {}).get("at_expected_head") is True
    status = "healthy"
    code = "backup_current"
    exit_code = 0
    if age_hours > max_age_hours:
        status = "unhealthy"
        code = "backup_stale"
        exit_code = 2
    elif offsite_status != "succeeded" or redis_status not in {"included", "disabled"} or not at_expected_head:
        status = "degraded"
        code = "backup_attention_required"
        exit_code = 1

    return (
        {
            "age_hours": age_hours,
            "at_expected_head": at_expected_head,
            "backup_id": latest.name,
            "checked_at": _isoformat(checked_at),
            "code": code,
            "offsite_status": offsite_status,
            "redis_status": redis_status,
            "status": status,
        },
        exit_code,
    )


def _record_failure(output_root: Path, *, code: str, backup_id: str | None) -> None:
    _write_json(
        output_root / LAST_FAILURE_NAME,
        {
            "backup_id": backup_id,
            "code": code,
            "failed_at": _isoformat(_utc_now()),
        },
    )


def _offsite_hook(config: BackupConfig, backup_dir: Path) -> str:
    hook = config.offsite_hook
    if hook is None:
        return "unconfigured"
    if not hook.is_absolute() or not hook.is_file() or not os.access(hook, os.X_OK):
        raise BackupError("offsite_hook_invalid", exit_code=4)
    try:
        result = subprocess.run(
            [str(hook), str(backup_dir)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LEARNHOUSE_BACKUP_ID": backup_dir.name},
        )
    except OSError as exc:
        raise BackupError("offsite_upload_failed", exit_code=4) from exc
    if result.returncode != 0:
        raise BackupError("offsite_upload_failed", exit_code=4)
    return "succeeded"


def _notify_failure(code: str) -> None:
    value = os.environ.get("LEARNHOUSE_BACKUP_ALERT_HOOK")
    if not value:
        return
    hook = Path(value).expanduser().resolve()
    if not hook.is_absolute() or not hook.is_file() or not os.access(hook, os.X_OK):
        return
    try:
        subprocess.run(
            [str(hook)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LEARNHOUSE_OPERATION_CODE": code},
        )
    except OSError:
        pass


def _refresh_manifest_and_checksums(
    backup_dir: Path,
    manifest: dict[str, Any],
    artifact_names: Sequence[str],
) -> None:
    _write_json(backup_dir / MANIFEST_NAME, manifest)
    _write_checksums(backup_dir, [*artifact_names, MANIFEST_NAME])


def create_backup(config: BackupConfig, *, now: datetime | None = None) -> Path:
    started_at = time.monotonic()
    current_time = now or _utc_now()
    backup_id = _backup_id(current_time)
    staging_dir = config.output_root / f".{backup_id}.incomplete-{os.getpid()}"
    final_dir = config.output_root / backup_id

    config.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_chmod(config.output_root, 0o700)
    if staging_dir.exists() or final_dir.exists():
        raise BackupError("backup_id_conflict")

    lock_path = config.output_root / LOCK_NAME
    lock_handle: BinaryIO | None = None
    try:
        lock_handle = lock_path.open("a+b")
        _safe_chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("backup_already_running", exit_code=3) from exc

        staging_dir.mkdir(mode=0o700)
        expected_heads = discover_migration_heads(config.migrations_dir)
        current_revisions = _database_current_revisions(config)
        database_tool_version = _database_tool_version(config)

        artifact_names = ["database.dump", "content.tar.gz"]
        _create_database_dump(config, staging_dir / "database.dump")
        _create_content_archive(config, staging_dir / "content.tar.gz")

        redis_status = "disabled"
        if config.redis_mode != "disabled":
            try:
                _create_redis_snapshot(
                    config,
                    staging_dir / "redis.rdb",
                    backup_id,
                )
                artifact_names.append("redis.rdb")
                redis_status = "included"
            except BackupError:
                if config.redis_mode == "required":
                    raise
                redis_status = "unavailable"

        for artifact_name in artifact_names:
            _safe_chmod(staging_dir / artifact_name, 0o600)

        completed_at = _utc_now()
        manifest: dict[str, Any] = {
            "artifacts": [
                _artifact_metadata(staging_dir / artifact_name)
                for artifact_name in artifact_names
            ],
            "backup_id": backup_id,
            "completed_at": _isoformat(completed_at),
            "content": {"format": "tar.gz"},
            "database": {
                "alembic_current": current_revisions,
                "alembic_expected_heads": expected_heads,
                "at_expected_head": current_revisions == expected_heads,
                "format": "postgresql_custom",
                "tool_version": database_tool_version,
            },
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "format_version": BACKUP_FORMAT_VERSION,
            "offsite": {
                "configured": config.offsite_hook is not None,
                "status": "pending" if config.offsite_hook is not None else "unconfigured",
            },
            "redis": {
                "mode": config.redis_mode,
                "status": redis_status,
            },
            "retention": {
                "days": config.retention_days,
                "minimum_backups": config.minimum_backups,
            },
            "started_at": _isoformat(current_time),
        }
        _refresh_manifest_and_checksums(staging_dir, manifest, artifact_names)
        _atomic_write(staging_dir / SUCCESS_MARKER, b"ok\n")
        os.replace(staging_dir, final_dir)

        try:
            manifest["offsite"]["status"] = _offsite_hook(config, final_dir)
            _refresh_manifest_and_checksums(final_dir, manifest, artifact_names)
        except BackupError as exc:
            manifest["offsite"]["status"] = "failed"
            _refresh_manifest_and_checksums(final_dir, manifest, artifact_names)
            _record_failure(config.output_root, code=exc.code, backup_id=backup_id)
            raise

        apply_retention(
            config.output_root,
            retention_days=config.retention_days,
            minimum_backups=config.minimum_backups,
            now=completed_at,
        )
        (config.output_root / LAST_FAILURE_NAME).unlink(missing_ok=True)
        return final_dir
    except BackupError as exc:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if exc.code not in {"backup_already_running", "offsite_upload_failed"}:
            _record_failure(config.output_root, code=exc.code, backup_id=backup_id)
        raise
    except Exception as exc:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        _record_failure(
            config.output_root,
            code="backup_operation_failed",
            backup_id=backup_id,
        )
        raise BackupError("backup_operation_failed") from exc
    finally:
        if lock_handle is not None:
            lock_handle.close()


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_root = _default_project_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(os.environ.get("LEARNHOUSE_BACKUP_PROJECT_ROOT", project_root)),
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path(os.environ.get("LEARNHOUSE_BACKUP_COMPOSE_FILE", "docker-compose.yml")),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("LEARNHOUSE_BACKUP_OUTPUT_DIR", "/var/backups/learnhouse")),
    )
    parser.add_argument(
        "--retention-days",
        type=_positive_int,
        default=_positive_int(os.environ.get("LEARNHOUSE_BACKUP_RETENTION_DAYS", "14")),
    )
    parser.add_argument(
        "--minimum-backups",
        type=_non_negative_int,
        default=_non_negative_int(os.environ.get("LEARNHOUSE_BACKUP_MINIMUM_BACKUPS", "7")),
    )
    parser.add_argument(
        "--redis-mode",
        choices=("required", "optional", "disabled"),
        default=os.environ.get("LEARNHOUSE_BACKUP_REDIS_MODE", "required"),
    )
    parser.add_argument(
        "--offsite-hook",
        type=Path,
        default=(
            Path(value)
            if (
                value := os.environ.get("LEARNHOUSE_BACKUP_OFFSITE_HOOK")
                or os.environ.get("LEARNHOUSE_BACKUP_OFFSITE_COMMAND")
            )
            else None
        ),
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "--max-age-hours",
        type=_positive_int,
        default=_positive_int(os.environ.get("LEARNHOUSE_BACKUP_MAX_AGE_HOURS", "36")),
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> BackupConfig:
    project_root = args.project_root.expanduser().resolve()
    compose_file = args.compose_file.expanduser()
    if not compose_file.is_absolute():
        compose_file = project_root / compose_file
    compose_file = compose_file.resolve()
    output_root = args.output_dir.expanduser().resolve()
    migrations_dir = project_root / "apps" / "api" / "migrations" / "versions"
    if (
        not project_root.is_dir()
        or not compose_file.is_file()
        or not migrations_dir.is_dir()
        or output_root == Path("/")
        or output_root == project_root
    ):
        raise BackupError("backup_paths_invalid", exit_code=2)

    content_path = os.environ.get(
        "LEARNHOUSE_BACKUP_CONTENT_PATH", "/app/api/content"
    )
    if not content_path.startswith("/") or "\x00" in content_path:
        raise BackupError("backup_paths_invalid", exit_code=2)

    return BackupConfig(
        project_root=project_root,
        compose_file=compose_file,
        output_root=output_root,
        migrations_dir=migrations_dir,
        db_service=_validate_service_name(
            os.environ.get("LEARNHOUSE_BACKUP_DB_SERVICE", "db")
        ),
        app_service=_validate_service_name(
            os.environ.get("LEARNHOUSE_BACKUP_APP_SERVICE", "learnhouse-app")
        ),
        redis_service=_validate_service_name(
            os.environ.get("LEARNHOUSE_BACKUP_REDIS_SERVICE", "redis")
        ),
        content_path=content_path,
        redis_mode=args.redis_mode,
        retention_days=args.retention_days,
        minimum_backups=args.minimum_backups,
        offsite_hook=args.offsite_hook.expanduser().resolve()
        if args.offsite_hook
        else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    try:
        args = parse_args(argv)
        config = config_from_args(args)
        if args.status:
            result, exit_code = backup_status(
                config.output_root,
                max_age_hours=args.max_age_hours,
            )
            print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
            return exit_code
        backup_dir = create_backup(config)
    except BackupError as exc:
        _notify_failure(exc.code)
        print(
            json.dumps(
                {"code": exc.code, "status": "failed"},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return exc.exit_code
    except Exception:
        _notify_failure("backup_operation_failed")
        print(
            json.dumps(
                {"code": "backup_operation_failed", "status": "failed"},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {"backup_id": backup_dir.name, "status": "succeeded"},
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
