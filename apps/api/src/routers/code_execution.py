import asyncio
import base64
import io
import os
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, Form
from pydantic import BaseModel, Field

from typing import Optional, Union

from config.config import get_learnhouse_config
from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.events.database import get_db_session
from src.db.users import APITokenUser, PublicUser
from src.security.auth import get_authenticated_user
from src.security.rbac.rbac import authorization_verify_based_on_roles_and_authorship
from src.services.code_language_capabilities import (
    API_EXECUTION_LANGUAGE_IDS,
    API_LANGUAGE_ADAPTERS,
    HTML_PREVIEW_LANGUAGE_ID,
    PYTHON3_LANGUAGE_ID,
    SQL_LANGUAGE_ID,
)
from src.services.judge0 import Judge0ServiceError, submit_judge0
from src.services.utils.upload_content import upload_file

router = APIRouter()

def _validate_execution_language(language_id: int) -> None:
    """Reject preview-only and unknown ids before any executor request."""
    if language_id == HTML_PREVIEW_LANGUAGE_ID:
        raise HTTPException(
            status_code=400,
            detail="HTML/CSS/JS exercises render in the browser and cannot be executed on the server",
        )
    if language_id not in API_EXECUTION_LANGUAGE_IDS:
        raise HTTPException(status_code=422, detail="unsupported language_id")


def _validate_adapter_prerequisites(
    language_id: int,
    sqlite_db_path: str | None,
) -> None:
    adapter = API_LANGUAGE_ADAPTERS.get(language_id)
    if not adapter:
        return
    if "sqlite_db_path" in adapter.get("requires", []) and not sqlite_db_path:
        raise HTTPException(
            status_code=400,
            detail="執行 SQL 前請先上傳 SQLite 資料庫檔案。",
        )


class AdditionalFile(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=1_000_000)


class ExecuteRequest(BaseModel):
    language_id: int = Field(ge=1)
    source_code: str = Field(min_length=1, max_length=200_000)
    stdin: str = Field(default="", max_length=100_000)
    sqlite_db_path: Optional[str] = None
    additional_files: Optional[list[AdditionalFile]] = Field(default=None, max_length=20)


class TestCase(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    stdin: str = Field(max_length=100_000)
    expected_stdout: str = Field(max_length=100_000)


class ExecuteBatchRequest(BaseModel):
    language_id: int = Field(ge=1)
    source_code: str = Field(min_length=1, max_length=200_000)
    test_cases: list[TestCase] = Field(min_length=1, max_length=50)
    sqlite_db_path: Optional[str] = None
    additional_files: Optional[list[AdditionalFile]] = Field(default=None, max_length=20)


def _get_judge0_config():
    config = get_learnhouse_config()
    if not config.judge0_config:
        raise HTTPException(
            status_code=503,
            detail="Code execution is not configured. Set LEARNHOUSE_JUDGE0_API_URL.",
        )
    return config.judge0_config


def _wrap_sql_in_python(sql: str) -> str:
    """Wrap raw SQL in a Python script that executes it against db.sqlite3.

    The SQL is base64-encoded so no user input is interpolated into the
    generated source code, eliminating the triple-quote injection vector.
    """
    import base64
    encoded = base64.b64encode(sql.encode()).decode()
    return f"""import sqlite3, base64

sql = base64.b64decode('{encoded}').decode()
conn = sqlite3.connect('db.sqlite3')
cursor = conn.cursor()

for statement in sql.strip().split(';'):
    statement = statement.strip()
    if not statement:
        continue
    cursor.execute(statement)
    if cursor.description:
        cols = [d[0] for d in cursor.description]
        print('|'.join(cols))
        for row in cursor.fetchall():
            print('|'.join(str(v) for v in row))

conn.close()
"""


def _validate_storage_path(file_path: str) -> str:
    """Validate and sanitize a storage file path to prevent path traversal."""
    if '..' in file_path or file_path.startswith('/') or '\x00' in file_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    normalized = file_path.replace('\\', '/')
    if '..' in normalized:
        raise HTTPException(status_code=400, detail="Invalid file path")
    full_path = f"content/{normalized}"
    # Resolve to absolute and verify containment within content/
    base_real = os.path.realpath("content")
    full_real = os.path.realpath(full_path)
    if not full_real.startswith(base_real + os.sep) and full_real != base_real:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return full_path


def _course_uuid_from_sqlite_path(file_path: str) -> str:
    """
    Extract the course_uuid from a sqlite storage path so we can run an RBAC
    check before the file is read into the Judge0 sandbox.

    Expected layout: ``orgs/{org_uuid}/courses/{course_uuid}/activities/...``
    """
    parts = file_path.replace('\\', '/').strip('/').split('/')
    if len(parts) < 4 or parts[0] != 'orgs' or parts[2] != 'courses':
        raise HTTPException(
            status_code=400,
            detail="sqlite_db_path must reference a course under orgs/{org_uuid}/courses/{course_uuid}/...",
        )
    course_uuid = parts[3]
    if not course_uuid.startswith('course_'):
        raise HTTPException(status_code=400, detail="Invalid course_uuid in sqlite_db_path")
    return course_uuid


async def _require_course_access(
    request: Request,
    current_user: Union[PublicUser, APITokenUser],
    course_uuid: str,
    action: str,
    db_session: AsyncSession,
) -> None:
    """
    Verify the caller has the requested RBAC action on the given course.

    API tokens are not permitted here — code execution / sqlite upload are
    interactive features tied to an authenticated user's course access.
    """
    if isinstance(current_user, APITokenUser):
        raise HTTPException(
            status_code=403,
            detail="API tokens cannot use code execution or playground uploads",
        )
    await authorization_verify_based_on_roles_and_authorship(
        request, current_user.id, action, course_uuid, db_session
    )


def _read_storage_file(file_path: str) -> bytes:
    """Read a file from storage (filesystem or S3)."""
    config = get_learnhouse_config()
    content_delivery = config.hosting_config.content_delivery.type
    safe_path = _validate_storage_path(file_path)

    if content_delivery == "filesystem":
        # Resolve to canonical absolute path and re-verify containment (CodeQL requires
        # the realpath check to be visible immediately before the file operation).
        base_real = os.path.realpath("content")
        resolved = os.path.realpath(safe_path)  # noqa: S108
        if not resolved.startswith(base_real + os.sep):
            raise HTTPException(status_code=400, detail="Invalid file path")
        if not os.path.isfile(resolved):
            raise HTTPException(status_code=404, detail="SQLite database file not found")
        with open(resolved, "rb") as f:
            return f.read()
    elif content_delivery == "s3api":
        import boto3
        from botocore.exceptions import ClientError

        s3 = boto3.client(
            "s3",
            endpoint_url=config.hosting_config.content_delivery.s3api.endpoint_url,
        )
        bucket = config.hosting_config.content_delivery.s3api.bucket_name or "learnhouse-media"
        try:
            response = s3.get_object(Bucket=bucket, Key=safe_path)
            return response["Body"].read()
        except ClientError:
            raise HTTPException(status_code=404, detail="SQLite database file not found")
    else:
        raise HTTPException(status_code=500, detail="Unknown storage backend")


def _make_additional_files_zip(
    db_bytes: bytes | None = None,
    text_files: list[dict] | None = None,
) -> str:
    """Create a base64-encoded zip containing optional SQLite db and/or text files."""
    buf = io.BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_bytes:
            zf.writestr("db.sqlite3", db_bytes)
            seen_names.add("db.sqlite3")
        if text_files:
            for f in text_files:
                normalized = f["name"].replace("\\", "/")
                path = PurePosixPath(normalized)
                if (
                    path.is_absolute()
                    or not path.parts
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or "\x00" in normalized
                    or normalized in seen_names
                    or normalized == "db.sqlite3"
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="附加檔案名稱無效或重複。",
                    )
                seen_names.add(normalized)
                zf.writestr(normalized, f["content"])
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _judge0_http_exception(error: Judge0ServiceError) -> HTTPException:
    messages = {
        "judge0_request_too_large": "程式或測試資料超出允許大小，請縮短後再試。",
        "judge0_submission_rejected": "程式執行請求格式不受支援，請檢查語言和附加檔案。",
        "judge0_timeout": "程式執行服務逾時，請稍後再試。",
        "judge0_rate_limited": "程式執行服務目前繁忙，請稍後再試。",
        "judge0_auth_failed": "程式執行服務暫時不可用，請通知管理員。",
        "judge0_invalid_response": "程式執行服務回應異常，請稍後再試。",
        "judge0_unavailable": "程式執行服務暫時不可用，請稍後再試。",
    }
    return HTTPException(
        status_code=error.http_status,
        detail={
            "code": error.code,
            "message": messages.get(error.code, messages["judge0_unavailable"]),
            "retryable": error.retryable,
        },
    )


async def _submit_single(
    judge0_cfg,
    language_id: int,
    source_code: str,
    stdin: str,
    additional_files: Optional[str] = None,
) -> dict:
    try:
        return await submit_judge0(
            judge0_cfg,
            language_id=language_id,
            source_code=source_code,
            stdin=stdin,
            additional_files=additional_files,
        )
    except Judge0ServiceError as exc:
        raise _judge0_http_exception(exc) from exc


@router.post(
    "/execute",
    summary="Execute a code snippet",
    description="Submit a single code snippet to the Judge0 sandbox and return its result. Supports SQL execution via a bundled SQLite database; API tokens are not permitted for this endpoint.",
    responses={
        200: {"description": "Judge0 submission result for the executed code."},
        400: {"description": "Invalid sqlite_db_path or file path"},
        401: {"description": "Authentication required"},
        403: {"description": "API tokens not permitted, or no access to the referenced course"},
        404: {"description": "SQLite database file not found"},
        503: {"description": "Code execution is not configured on this instance"},
    },
)
async def execute_code(
    request: Request,
    body: ExecuteRequest,
    current_user: Union[PublicUser, APITokenUser] = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    _validate_execution_language(body.language_id)
    _validate_adapter_prerequisites(body.language_id, body.sqlite_db_path)
    judge0_cfg = _get_judge0_config()

    language_id = body.language_id
    source_code = body.source_code
    additional_files_b64 = None

    zip_files = None
    if body.additional_files:
        zip_files = [{"name": f.name, "content": f.content} for f in body.additional_files]

    if language_id == SQL_LANGUAGE_ID and body.sqlite_db_path:
        # Authorize: caller must have read access to the course that owns the sqlite file.
        course_uuid = _course_uuid_from_sqlite_path(body.sqlite_db_path)
        await _require_course_access(request, current_user, course_uuid, "read", db_session)

        db_bytes = _read_storage_file(body.sqlite_db_path)
        language_id = PYTHON3_LANGUAGE_ID
        source_code = _wrap_sql_in_python(body.source_code)
        additional_files_b64 = _make_additional_files_zip(db_bytes=db_bytes, text_files=zip_files)
    elif zip_files:
        additional_files_b64 = _make_additional_files_zip(text_files=zip_files)

    result = await _submit_single(
        judge0_cfg, language_id, source_code, body.stdin, additional_files_b64
    )
    return result


@router.post(
    "/execute-batch",
    summary="Execute code against a batch of test cases",
    description="Run a code snippet against multiple test cases in parallel and return per-test pass/fail results. Supports SQL execution via a bundled SQLite database; API tokens are not permitted.",
    responses={
        200: {"description": "Per-test execution results for the submitted code."},
        400: {"description": "Invalid sqlite_db_path or file path"},
        401: {"description": "Authentication required"},
        403: {"description": "API tokens not permitted, or no access to the referenced course"},
        404: {"description": "SQLite database file not found"},
        503: {"description": "Code execution is not configured on this instance"},
    },
)
async def execute_batch(
    request: Request,
    body: ExecuteBatchRequest,
    current_user: Union[PublicUser, APITokenUser] = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    _validate_execution_language(body.language_id)
    _validate_adapter_prerequisites(body.language_id, body.sqlite_db_path)
    judge0_cfg = _get_judge0_config()

    language_id = body.language_id
    source_code = body.source_code
    additional_files_b64 = None

    zip_files = None
    if body.additional_files:
        zip_files = [{"name": f.name, "content": f.content} for f in body.additional_files]

    if language_id == SQL_LANGUAGE_ID and body.sqlite_db_path:
        # Authorize: caller must have read access to the course that owns the sqlite file.
        course_uuid = _course_uuid_from_sqlite_path(body.sqlite_db_path)
        await _require_course_access(request, current_user, course_uuid, "read", db_session)

        db_bytes = _read_storage_file(body.sqlite_db_path)
        language_id = PYTHON3_LANGUAGE_ID
        source_code = _wrap_sql_in_python(body.source_code)
        additional_files_b64 = _make_additional_files_zip(db_bytes=db_bytes, text_files=zip_files)
    elif zip_files:
        additional_files_b64 = _make_additional_files_zip(text_files=zip_files)

    async def run_test(tc: TestCase) -> dict:
        r = await _submit_single(
            judge0_cfg, language_id, source_code, tc.stdin, additional_files_b64
        )
        status = r.get("status", {})
        # Normalize both outputs before comparing:
        # - splitlines handles \n, \r\n, and \r equivalently
        # - rstrip each line removes accidental trailing spaces / tabs /
        #   carriage returns (a very common source of false failures when a
        #   teacher types the expected value in a textarea)
        # - drop trailing empty lines so `print("x")` (outputs "x\n") matches
        #   `x` with or without a trailing newline in the expected field
        # We intentionally preserve leading whitespace on each line so that
        # indentation-sensitive tests (trees, tables, Python REPL output)
        # still work correctly.
        def normalize_output(s: str | None) -> str:
            if not s:
                return ""
            lines = [line.rstrip() for line in s.splitlines()]
            while lines and lines[-1] == "":
                lines.pop()
            return "\n".join(lines)

        actual = normalize_output(r.get("stdout"))
        expected = normalize_output(tc.expected_stdout)
        passed = status.get("id") == 3 and actual == expected
        return {
            "id": tc.id,
            "label": tc.label,
            "passed": passed,
            "actual_stdout": r.get("stdout"),
            "expected_stdout": tc.expected_stdout,
            "stderr": r.get("stderr"),
            "compile_output": r.get("compile_output"),
            "status": status,
            "time": r.get("time"),
            "memory": r.get("memory"),
        }

    results = await asyncio.gather(*[run_test(tc) for tc in body.test_cases])
    return {"results": list(results)}


@router.post(
    "/upload-sqlite",
    summary="Upload a SQLite database for a code playground block",
    description="Upload a SQLite database file for a code playground block. Requires an authenticated user with update permission on the target course; API tokens are not permitted.",
    responses={
        200: {"description": "Uploaded file path and original filename."},
        400: {"description": "Invalid course_uuid or file path"},
        401: {"description": "Authentication required"},
        403: {"description": "API tokens not permitted, or no update access to the course"},
    },
)
async def upload_sqlite_db(
    request: Request,
    file_object: UploadFile,
    activity_uuid: str = Form(),
    block_id: str = Form(),
    org_uuid: str = Form(),
    course_uuid: str = Form(),
    current_user: Union[PublicUser, APITokenUser] = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Upload a SQLite database file for a code playground block.

    SECURITY: Requires authentication and update permission on the target
    course — otherwise an attacker could overwrite any course's playground DB.
    """
    if not course_uuid.startswith("course_"):
        raise HTTPException(status_code=400, detail="Invalid course_uuid")

    await _require_course_access(request, current_user, course_uuid, "update", db_session)

    directory = f"courses/{course_uuid}/activities/{activity_uuid}/dynamic/blocks/codePlayground/{block_id}"

    filename = await upload_file(
        file=file_object,
        directory=directory,
        type_of_dir="orgs",
        uuid=org_uuid,
        allowed_types=["database"],
        filename_prefix="sqlite_db",
    )

    file_path = f"orgs/{org_uuid}/{directory}/{filename}"

    return {
        "file_path": file_path,
        "file_name": file_object.filename,
    }
