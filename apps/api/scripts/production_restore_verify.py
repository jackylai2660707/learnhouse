#!/usr/bin/env python3
"""Verify and restore a LearnHouse backup into an isolated PostgreSQL container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


BACKUP_FORMAT_VERSION = 1
SUCCESS_MARKER = "SUCCESS"
MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
REQUIRED_ARTIFACTS = {"database.dump", "content.tar.gz"}
OPTIONAL_ARTIFACTS = {"redis.rdb"}
SAFE_ARTIFACTS = REQUIRED_ARTIFACTS | OPTIONAL_ARTIFACTS | {MANIFEST_NAME}
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
BACKUP_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")
REVISION_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CORE_TABLES = (
    "organization",
    "user",
    "course",
    "assignment",
    "assignmentsubmission",
    "questionbankitem",
    "selftestattempt",
)
REQUIRED_CORE_TABLES = {"organization", "user", "course", "assignment"}


class RestoreVerificationError(RuntimeError):
    def __init__(self, code: str, *, exit_code: int = 1) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)


def _read_manifest(backup_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((backup_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreVerificationError("manifest_invalid") from exc
    if not isinstance(payload, dict) or payload.get("format_version") != BACKUP_FORMAT_VERSION:
        raise RestoreVerificationError("manifest_invalid")
    return payload


def _safe_checksum_name(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or len(path.parts) != 1
        or value not in SAFE_ARTIFACTS
    ):
        raise RestoreVerificationError("checksum_manifest_invalid")
    return value


def verify_checksums(backup_dir: Path) -> dict[str, str]:
    try:
        lines = (backup_dir / CHECKSUMS_NAME).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise RestoreVerificationError("checksum_manifest_invalid") from exc

    checksums: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise RestoreVerificationError("checksum_manifest_invalid")
        name = _safe_checksum_name(parts[1])
        if name in checksums:
            raise RestoreVerificationError("checksum_manifest_invalid")
        checksums[name] = parts[0]

    if not REQUIRED_ARTIFACTS | {MANIFEST_NAME} <= checksums.keys():
        raise RestoreVerificationError("checksum_manifest_invalid")
    for name, expected in checksums.items():
        path = backup_dir / name
        if path.is_symlink() or not path.is_file() or _sha256(path) != expected:
            raise RestoreVerificationError("checksum_mismatch")
    return checksums


def _verify_manifest_artifacts(
    backup_dir: Path,
    manifest: dict[str, Any],
    checksums: dict[str, str],
) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise RestoreVerificationError("manifest_invalid")

    manifest_names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RestoreVerificationError("manifest_invalid")
        name = artifact.get("name")
        checksum = artifact.get("sha256")
        size_bytes = artifact.get("size_bytes")
        if (
            not isinstance(name, str)
            or name not in REQUIRED_ARTIFACTS | OPTIONAL_ARTIFACTS
            or name in manifest_names
            or not isinstance(checksum, str)
            or not isinstance(size_bytes, int)
        ):
            raise RestoreVerificationError("manifest_invalid")
        path = backup_dir / name
        if (
            name not in checksums
            or checksums[name] != checksum
            or not path.is_file()
            or path.stat().st_size != size_bytes
        ):
            raise RestoreVerificationError("manifest_artifact_mismatch")
        manifest_names.add(name)

    if not REQUIRED_ARTIFACTS <= manifest_names:
        raise RestoreVerificationError("manifest_invalid")
    redis_status = manifest.get("redis", {}).get("status")
    if redis_status == "included" and "redis.rdb" not in manifest_names:
        raise RestoreVerificationError("manifest_invalid")


def verify_content_archive(path: Path) -> dict[str, int]:
    member_count = 0
    regular_file_count = 0
    uncompressed_size_bytes = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                member_count += 1
                member_path = PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise RestoreVerificationError("content_archive_unsafe")
                if member.isfile():
                    regular_file_count += 1
                    uncompressed_size_bytes += member.size
    except (tarfile.TarError, OSError) as exc:
        raise RestoreVerificationError("content_archive_invalid") from exc
    if member_count == 0:
        raise RestoreVerificationError("content_archive_invalid")
    return {
        "member_count": member_count,
        "regular_file_count": regular_file_count,
        "uncompressed_size_bytes": uncompressed_size_bytes,
    }


def verify_redis_snapshot(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            header = handle.read(9)
    except OSError as exc:
        raise RestoreVerificationError("redis_snapshot_invalid") from exc
    if len(header) != 9 or not re.fullmatch(rb"REDIS\d{4}", header):
        raise RestoreVerificationError("redis_snapshot_invalid")
    return {"format": header.decode("ascii"), "size_bytes": path.stat().st_size}


def verify_backup_files(backup_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        backup_dir.is_symlink()
        or not backup_dir.is_dir()
        or backup_dir.stat().st_mode & 0o077
        or not (backup_dir / SUCCESS_MARKER).is_file()
    ):
        raise RestoreVerificationError("backup_incomplete", exit_code=2)
    for name in (MANIFEST_NAME, CHECKSUMS_NAME, SUCCESS_MARKER):
        if (backup_dir / name).is_symlink():
            raise RestoreVerificationError("backup_permissions_unsafe")
    manifest = _read_manifest(backup_dir)
    if manifest.get("backup_id") != backup_dir.name:
        raise RestoreVerificationError("manifest_invalid")
    checksums = verify_checksums(backup_dir)
    _verify_manifest_artifacts(backup_dir, manifest, checksums)
    for name in (*checksums, CHECKSUMS_NAME, SUCCESS_MARKER):
        path = backup_dir / name
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise RestoreVerificationError("backup_permissions_unsafe")
    content_result = verify_content_archive(backup_dir / "content.tar.gz")

    redis_result: dict[str, Any] = {"status": "not_included"}
    if "redis.rdb" in checksums:
        redis_result = {
            "status": "valid",
            **verify_redis_snapshot(backup_dir / "redis.rdb"),
        }
    return manifest, {"content": content_result, "redis": redis_result}


def _run_capture(command: Sequence[str], *, code: str) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise RestoreVerificationError(code) from exc
    if result.returncode != 0:
        raise RestoreVerificationError(code)
    return result.stdout


def _run_quiet(command: Sequence[str], *, code: str) -> None:
    _run_capture(command, code=code)


def _start_container(image: str, *, tmpfs_size_mb: int) -> str:
    if not IMAGE_RE.fullmatch(image):
        raise RestoreVerificationError("restore_image_invalid", exit_code=2)
    container_name = f"learnhouse-restore-verify-{secrets.token_hex(6)}"
    _run_quiet(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--label",
            "learnhouse.restore-verifier=true",
            "--tmpfs",
            f"/var/lib/postgresql/data:rw,noexec,nosuid,size={tmpfs_size_mb}m",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env",
            "POSTGRES_USER=learnhouse_verify",
            "--env",
            "POSTGRES_DB=learnhouse_verify",
            image,
        ],
        code="restore_container_start_failed",
    )
    return container_name


def _wait_for_postgres(container_name: str, *, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    command = [
        "docker",
        "exec",
        container_name,
        "pg_isready",
        "--username=learnhouse_verify",
        "--dbname=learnhouse_verify",
    ]
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RestoreVerificationError("restore_container_unavailable") from exc
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RestoreVerificationError("restore_container_timeout")


def _psql(container_name: str, sql: str, *, code: str) -> list[str]:
    output = _run_capture(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--username=learnhouse_verify",
            "--dbname=learnhouse_verify",
            "--command",
            sql,
        ],
        code=code,
    )
    return [
        line.strip()
        for line in output.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]


def restore_database(
    backup_dir: Path,
    manifest: dict[str, Any],
    *,
    image: str,
    tmpfs_size_mb: int,
    allow_migration_head_mismatch: bool = False,
) -> dict[str, Any]:
    container_name: str | None = None
    try:
        container_name = _start_container(image, tmpfs_size_mb=tmpfs_size_mb)
        _wait_for_postgres(container_name)
        _run_quiet(
            [
                "docker",
                "cp",
                str(backup_dir / "database.dump"),
                f"{container_name}:/tmp/database.dump",
            ],
            code="restore_dump_copy_failed",
        )
        archive_listing = _run_capture(
            [
                "docker",
                "exec",
                container_name,
                "pg_restore",
                "--list",
                "/tmp/database.dump",
            ],
            code="restore_dump_invalid",
        )
        if not archive_listing.strip():
            raise RestoreVerificationError("restore_dump_invalid")
        _run_quiet(
            [
                "docker",
                "exec",
                container_name,
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--username=learnhouse_verify",
                "--dbname=learnhouse_verify",
                "/tmp/database.dump",
            ],
            code="restore_database_failed",
        )

        revisions = sorted(
            _psql(
                container_name,
                "SELECT version_num FROM alembic_version ORDER BY version_num",
                code="restore_revision_check_failed",
            )
        )
        database_manifest = manifest.get("database")
        if not isinstance(database_manifest, dict):
            raise RestoreVerificationError("manifest_invalid")
        manifest_current = database_manifest.get("alembic_current")
        expected_heads = database_manifest.get("alembic_expected_heads")
        manifest_at_expected_head = database_manifest.get("at_expected_head")
        if (
            not isinstance(manifest_current, list)
            or not isinstance(expected_heads, list)
            or not isinstance(manifest_at_expected_head, bool)
            or not revisions
            or not manifest_current
            or not expected_heads
            or not all(isinstance(item, str) and REVISION_RE.fullmatch(item) for item in revisions)
            or not all(
                isinstance(item, str) and REVISION_RE.fullmatch(item)
                for item in manifest_current
            )
            or not all(
                isinstance(item, str) and REVISION_RE.fullmatch(item)
                for item in expected_heads
            )
            or len(revisions) != len(set(revisions))
            or len(manifest_current) != len(set(manifest_current))
            or len(expected_heads) != len(set(expected_heads))
            or revisions != sorted(manifest_current)
            or manifest_at_expected_head
            != (sorted(manifest_current) == sorted(expected_heads))
        ):
            raise RestoreVerificationError("restore_migration_head_mismatch")
        at_expected_head = revisions == sorted(expected_heads)
        if not at_expected_head and not allow_migration_head_mismatch:
            raise RestoreVerificationError("restore_migration_head_mismatch")
        migration_head_policy = (
            "current_head_required"
            if at_expected_head
            else "pre_migration_mismatch_allowed"
        )

        table_names = _psql(
            container_name,
            (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            ),
            code="restore_schema_check_failed",
        )
        table_name_set = set(table_names)
        if (
            "alembic_version" not in table_name_set
            or not REQUIRED_CORE_TABLES <= table_name_set
        ):
            raise RestoreVerificationError("restore_schema_check_failed")

        aggregate_counts: dict[str, int] = {}
        for table_name in CORE_TABLES:
            if table_name not in table_name_set:
                continue
            values = _psql(
                container_name,
                f'SELECT count(*) FROM "{table_name}"',
                code="restore_aggregate_check_failed",
            )
            if len(values) != 1 or not values[0].isdigit():
                raise RestoreVerificationError("restore_aggregate_check_failed")
            aggregate_counts[table_name] = int(values[0])

        return {
            "aggregate_counts": aggregate_counts,
            "alembic_current": revisions,
            "alembic_expected_heads": sorted(expected_heads),
            "archive_entry_count": len(archive_listing.splitlines()),
            "at_expected_head": at_expected_head,
            "migration_head_policy": migration_head_policy,
            "table_count": len(table_names),
        }
    finally:
        if container_name is not None:
            try:
                subprocess.run(
                    ["docker", "rm", "--force", container_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass


def verify_restore(
    backup_dir: Path,
    *,
    image: str,
    tmpfs_size_mb: int,
    allow_migration_head_mismatch: bool = False,
) -> dict[str, Any]:
    started_at = time.monotonic()
    manifest, file_results = verify_backup_files(backup_dir)
    database_result = restore_database(
        backup_dir,
        manifest,
        image=image,
        tmpfs_size_mb=tmpfs_size_mb,
        allow_migration_head_mismatch=allow_migration_head_mismatch,
    )
    return {
        "backup_id": manifest.get("backup_id"),
        "checked_at": _iso_now(),
        "content": file_results["content"],
        "database": database_result,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "format_version": BACKUP_FORMAT_VERSION,
        "redis": file_results["redis"],
        "status": "succeeded",
    }


def latest_successful_backup(output_root: Path) -> Path:
    if not output_root.is_dir():
        raise RestoreVerificationError("backup_root_invalid", exit_code=2)
    candidates = sorted(
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
    if not candidates:
        raise RestoreVerificationError("backup_not_found", exit_code=2)
    return candidates[0]


def _notify_failure(code: str) -> None:
    value = os.environ.get("LEARNHOUSE_RESTORE_ALERT_HOOK")
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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("backup_dir", type=Path, nargs="?")
    source.add_argument("--latest-from", type=Path)
    parser.add_argument(
        "--image",
        default=os.environ.get(
            "LEARNHOUSE_RESTORE_VERIFY_IMAGE", "pgvector/pgvector:pg16"
        ),
    )
    parser.add_argument(
        "--tmpfs-size-mb",
        type=_positive_int,
        default=_positive_int(
            os.environ.get("LEARNHOUSE_RESTORE_VERIFY_TMPFS_SIZE_MB", "4096")
        ),
    )
    parser.add_argument(
        "--allow-migration-head-mismatch",
        action="store_true",
        help=(
            "Verify a pre-migration backup whose restored revision matches the "
            "manifest's recorded current revision but not its recorded expected "
            "heads. The default remains strict."
        ),
    )
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = parse_args(argv)
    try:
        backup_dir = (
            latest_successful_backup(args.latest_from.expanduser().resolve())
            if args.latest_from
            else args.backup_dir.expanduser().resolve()
        )
        result = verify_restore(
            backup_dir,
            image=args.image,
            tmpfs_size_mb=args.tmpfs_size_mb,
            allow_migration_head_mismatch=args.allow_migration_head_mismatch,
        )
        if args.report:
            report_path = args.report.expanduser().resolve()
            if report_path == backup_dir or report_path.is_relative_to(backup_dir):
                raise RestoreVerificationError("report_path_invalid", exit_code=2)
            _atomic_write_json(report_path, result)
    except RestoreVerificationError as exc:
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
        _notify_failure("restore_verification_failed")
        print(
            json.dumps(
                {"code": "restore_verification_failed", "status": "failed"},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "backup_id": result["backup_id"],
                "status": result["status"],
                "alembic_current": result["database"]["alembic_current"],
                "alembic_expected_heads": result["database"][
                    "alembic_expected_heads"
                ],
                "at_expected_head": result["database"]["at_expected_head"],
                "migration_head_policy": result["database"][
                    "migration_head_policy"
                ],
                "table_count": result["database"]["table_count"],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
