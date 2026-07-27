import base64
import io
import zipfile
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.routers.code_execution import (
    ExecuteBatchRequest,
    ExecuteRequest,
    execute_batch,
    execute_code,
    _validate_adapter_prerequisites,
    _judge0_http_exception,
    _make_additional_files_zip,
    _validate_execution_language,
)
from src.services.code_language_capabilities import (
    API_EXECUTION_LANGUAGE_IDS,
    API_LANGUAGE_ADAPTERS,
    EXECUTOR_LANGUAGE_IDS,
    HTML_PREVIEW_LANGUAGE_ID,
    PYTHON3_LANGUAGE_ID,
    SQL_LANGUAGE_ID,
)
from src.services.judge0 import Judge0ServiceError


def _zip_names(encoded: str) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
        return archive.namelist()


def test_machine_readable_language_capabilities_include_sql_api_adapter():
    assert EXECUTOR_LANGUAGE_IDS == {50, 54, 62, 63, 71, 74}
    assert API_EXECUTION_LANGUAGE_IDS == {50, 54, 62, 63, 71, 74, 82}
    assert API_LANGUAGE_ADAPTERS[82] == {
        "language_id": 82,
        "name": "SQL (SQLite)",
        "executor_language_id": 71,
        "requires": ["sqlite_db_path"],
    }
    assert SQL_LANGUAGE_ID == 82
    assert PYTHON3_LANGUAGE_ID == 71
    assert HTML_PREVIEW_LANGUAGE_ID == 1000


def test_execution_language_validation_rejects_unknown_id_before_executor():
    with pytest.raises(HTTPException) as exc_info:
        _validate_execution_language(73)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "unsupported language_id"


def test_execution_language_validation_keeps_preview_specific_error():
    with pytest.raises(HTTPException) as exc_info:
        _validate_execution_language(HTML_PREVIEW_LANGUAGE_ID)

    assert exc_info.value.status_code == 400
    assert "browser" in exc_info.value.detail


def test_sql_adapter_prerequisite_has_safe_actionable_traditional_chinese_error():
    with pytest.raises(HTTPException) as exc_info:
        _validate_adapter_prerequisites(SQL_LANGUAGE_ID, None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "執行 SQL 前請先上傳 SQLite 資料庫檔案。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "body"),
    [
        (
            execute_code,
            ExecuteRequest(language_id=82, source_code="SELECT 1"),
        ),
        (
            execute_batch,
            ExecuteBatchRequest(
                language_id=82,
                source_code="SELECT 1",
                test_cases=[
                    {
                        "id": "sql",
                        "label": "SQL",
                        "stdin": "",
                        "expected_stdout": "1",
                    }
                ],
            ),
        ),
    ],
)
async def test_sql_without_database_fails_before_executor_configuration(handler, body):
    with patch(
        "src.routers.code_execution._get_judge0_config",
        side_effect=AssertionError("executor configuration must not be reached"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await handler(None, body, None, None)

    assert exc_info.value.status_code == 400
    assert "SQLite" in exc_info.value.detail


def test_additional_files_zip_allows_safe_relative_paths():
    encoded = _make_additional_files_zip(
        text_files=[
            {"name": "src/main.py", "content": "print('ok')"},
            {"name": "input.txt", "content": "hello"},
        ]
    )

    assert _zip_names(encoded) == ["src/main.py", "input.txt"]


@pytest.mark.parametrize(
    "name",
    [
        "../escape.py",
        "/absolute.py",
        "src/../../escape.py",
        "db.sqlite3",
        "bad\x00name.py",
    ],
)
def test_additional_files_zip_rejects_unsafe_or_reserved_names(name):
    with pytest.raises(HTTPException) as exc_info:
        _make_additional_files_zip(text_files=[{"name": name, "content": "x"}])

    assert exc_info.value.status_code == 400
    assert "附加檔案名稱" in str(exc_info.value.detail)


def test_additional_files_zip_rejects_duplicate_names():
    with pytest.raises(HTTPException) as exc_info:
        _make_additional_files_zip(
            text_files=[
                {"name": "main.py", "content": "first"},
                {"name": "main.py", "content": "second"},
            ]
        )

    assert exc_info.value.status_code == 400


def test_execute_request_and_batch_enforce_cost_bounds():
    with pytest.raises(ValidationError):
        ExecuteRequest(language_id=71, source_code="", stdin="")
    with pytest.raises(ValidationError):
        ExecuteRequest(language_id=71, source_code="x", stdin="x" * 100_001)
    with pytest.raises(ValidationError):
        ExecuteBatchRequest(
            language_id=71,
            source_code="x",
            test_cases=[
                {
                    "id": str(index),
                    "label": "case",
                    "stdin": "",
                    "expected_stdout": "",
                }
                for index in range(51)
            ],
        )


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        ("judge0_submission_rejected", 400, False),
        ("judge0_timeout", 503, True),
        ("judge0_invalid_response", 503, True),
    ],
)
def test_judge0_http_error_is_stable_traditional_chinese(code, status, retryable):
    error = _judge0_http_exception(
        Judge0ServiceError(code=code, http_status=status, retryable=retryable)
    )

    assert error.status_code == status
    assert error.detail["code"] == code
    assert error.detail["retryable"] is retryable
    assert isinstance(error.detail["message"], str)
    assert "provider" not in error.detail["message"].lower()
