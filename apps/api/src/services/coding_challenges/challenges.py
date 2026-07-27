import asyncio
import copy
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.coding_challenges import (
    CodingChallenge,
    CodingChallengeProgress,
    CodingChallengeSolutionVisibility,
    CodingChallengeSubmission,
    CodingChallengeTest,
    CodingChallengeTestVisibility,
)
from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.roles import Role
from src.db.resource_authors import ResourceAuthor, ResourceAuthorshipStatusEnum
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.user_organizations import UserOrganization
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.users import PublicUser, User
from src.routers.code_execution import (
    _course_uuid_from_sqlite_path,
    _get_judge0_config,
    _make_additional_files_zip,
    _read_storage_file,
    _submit_single,
    _wrap_sql_in_python,
)
from src.services.code_language_capabilities import (
    API_EXECUTION_LANGUAGE_IDS,
    HTML_PREVIEW_LANGUAGE_ID,
    PREVIEW_LANGUAGE_IDS,
    PYTHON3_LANGUAGE_ID,
    SQL_LANGUAGE_ID,
)
from src.security.rbac import AccessAction, ResourceAccessChecker, check_resource_access
from src.security.rbac.constants import ADMIN_OR_MAINTAINER_ROLE_IDS
from src.services.security.rate_limiting import check_rate_limit
from src.services.trail.trail import add_activity_to_trail


# Durable challenges may preserve every declared raw executor runtime, API
# adapter, and preview-only id. Preview ids round-trip but never reach the
# executor (see _run_test_suite).
SUPPORTED_LANGUAGE_IDS = frozenset(
    (*API_EXECUTION_LANGUAGE_IDS, *PREVIEW_LANGUAGE_IDS)
)
MAX_SOURCE_CODE_BYTES = 100_000
MAX_CHALLENGE_TESTS = 50
MAX_TEST_VALUE_BYTES = 64_000
MAX_PARALLEL_TESTS = 4
MAX_ADDITIONAL_FILES = 10
MAX_ADDITIONAL_FILE_BYTES = 64_000
MAX_ADDITIONAL_FILES_TOTAL_BYTES = 256_000
MAX_ADDITIONAL_FILE_NAME_BYTES = 255


def _now() -> str:
    return datetime.utcnow().isoformat()


def _ensure_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _ensure_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_solution_visibility(value: Any) -> CodingChallengeSolutionVisibility:
    try:
        return CodingChallengeSolutionVisibility(value or CodingChallengeSolutionVisibility.AFTER_PASS.value)
    except ValueError:
        return CodingChallengeSolutionVisibility.AFTER_PASS


def _normalize_test_visibility(value: Any) -> CodingChallengeTestVisibility:
    if value == CodingChallengeTestVisibility.HIDDEN.value or value is True:
        return CodingChallengeTestVisibility.HIDDEN
    return CodingChallengeTestVisibility.VISIBLE


def _normalize_output(s: str | None) -> str:
    if not s:
        return ""
    lines = [line.rstrip() for line in s.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _walk_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for child in _ensure_list(node.get("content")):
            yield from _walk_nodes(child)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_nodes(item)


def sanitize_coding_challenge_content(content: Any) -> Any:
    """Return activity content that cannot disclose challenge-only answers."""
    sanitized = copy.deepcopy(content)
    for node in _walk_nodes(sanitized):
        if node.get("type") != "blockCode":
            continue
        attrs = _ensure_dict(node.get("attrs"))
        attrs.pop("solutionCode", None)
        attrs.pop("solution_code", None)
        attrs.pop("hiddenTestCases", None)
        attrs["testCases"] = [
            test
            for test in _ensure_list(attrs.get("testCases"))
            if not bool(_ensure_dict(test).get("hidden"))
            and _ensure_dict(test).get("visibility") != CodingChallengeTestVisibility.HIDDEN.value
        ]
    return sanitized


def remap_coding_challenge_identifiers(content: Any) -> Any:
    """Give imported coding blocks fresh ownership-scoped identifiers."""
    remapped = copy.deepcopy(content)
    for node in _walk_nodes(remapped):
        if node.get("type") != "blockCode":
            continue
        attrs = _ensure_dict(node.setdefault("attrs", {}))
        attrs["id"] = f"block_{uuid4()}"
        attrs["challengeUuid"] = f"challenge_{uuid4()}"
    return remapped


def _validate_source_code(source_code: str) -> None:
    if len(source_code.encode("utf-8")) > MAX_SOURCE_CODE_BYTES:
        raise HTTPException(status_code=413, detail="Source code is too large")


def _validate_test_value(value: Any, field_name: str) -> None:
    if len(str(value or "").encode("utf-8")) > MAX_TEST_VALUE_BYTES:
        raise HTTPException(status_code=400, detail=f"Challenge {field_name} is too large")


def _validate_additional_files(value: Any) -> list[dict]:
    files = _ensure_list(value)
    if len(files) > MAX_ADDITIONAL_FILES:
        raise HTTPException(status_code=400, detail="Challenge has too many additional files")
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="Invalid additional file")
        name = item.get("name")
        content = item.get("content")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="Invalid additional file name")
        if (
            name in {".", ".."}
            or "/" in name
            or "\\" in name
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or len(name.encode("utf-8")) > MAX_ADDITIONAL_FILE_NAME_BYTES
        ):
            raise HTTPException(status_code=400, detail="Unsafe additional file name")
        if not isinstance(content, str) or not content:
            raise HTTPException(status_code=400, detail="Invalid additional file content")
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_ADDITIONAL_FILE_BYTES:
            raise HTTPException(status_code=400, detail="Challenge additional file is too large")
        total_bytes += content_bytes
    if total_bytes > MAX_ADDITIONAL_FILES_TOTAL_BYTES:
        raise HTTPException(status_code=400, detail="Challenge additional files are too large")
    return files


def _challenge_revision(
    challenge: CodingChallenge,
    tests: list[CodingChallengeTest],
) -> str:
    payload = {
        "updated_at": challenge.updated_at,
        "language_id": challenge.language_id,
        "sqlite_db_path": challenge.sqlite_db_path,
        "additional_files": challenge.additional_files,
        "tests": [
            {
                "uuid": test.test_uuid,
                "stdin": test.stdin,
                "expected": test.expected_stdout,
                "visibility": test.visibility.value,
                "order": test.order,
                "updated_at": test.updated_at,
            }
            for test in tests
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _test_from_attrs(raw: dict, idx: int, default_visibility: CodingChallengeTestVisibility) -> dict:
    visibility = _normalize_test_visibility(raw.get("visibility", raw.get("hidden", default_visibility.value)))
    return {
        "test_uuid": raw.get("testUuid") or raw.get("test_uuid") or raw.get("id") or f"test_{uuid4()}",
        "label": raw.get("label") or f"Test {idx + 1}",
        "stdin": raw.get("stdin") or "",
        "expected_stdout": raw.get("expectedStdout") or raw.get("expected_stdout") or "",
        "visibility": visibility,
        "order": idx,
    }


async def sync_coding_challenges_for_activity(
    activity: Activity,
    course: Course,
    content: dict,
    db_session: AsyncSession,
) -> dict:
    """Sync blockCode attrs to durable challenge rows and strip hidden tests.

    The returned content is safe to store in Activity.content. Hidden tests may
    pass through this function from the teacher editor save request, but they
    are persisted only in coding_challenge_test rows.
    """
    if not isinstance(content, dict):
        return content

    seen_challenge_ids: set[int] = set()

    for node in _walk_nodes(content):
        if node.get("type") != "blockCode":
            continue
        attrs = _ensure_dict(node.setdefault("attrs", {}))
        block_id = attrs.get("id") or attrs.get("blockId") or f"block_{uuid4()}"
        attrs["id"] = block_id

        challenge_uuid = attrs.get("challengeUuid") or attrs.get("challenge_uuid") or f"challenge_{uuid4()}"
        attrs["challengeUuid"] = challenge_uuid

        challenge = (
            await db_session.execute(
                select(CodingChallenge).where(CodingChallenge.challenge_uuid == challenge_uuid)
            )
        ).scalars().first()
        if challenge is None:
            challenge = CodingChallenge(
                challenge_uuid=challenge_uuid,
                org_id=activity.org_id,
                course_id=activity.course_id,
                activity_id=activity.id or 0,
                block_id=block_id,
            )
        elif (
            challenge.org_id != activity.org_id
            or challenge.course_id != activity.course_id
            or challenge.activity_id != (activity.id or 0)
            or challenge.block_id != block_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Coding challenge identifier already belongs to another activity",
            )

        language_id = _coerce_int(attrs.get("languageId") or attrs.get("language_id"), 71)
        if language_id not in SUPPORTED_LANGUAGE_IDS:
            raise HTTPException(
                status_code=400,
                detail="這個程式語言目前不支援執行，請選擇可用語言後再儲存。",
            )

        challenge.org_id = activity.org_id
        challenge.course_id = activity.course_id
        challenge.activity_id = activity.id or 0
        challenge.block_id = block_id
        challenge.required = bool(attrs.get("required", attrs.get("challengeRequired", False)))
        challenge.archived = False
        challenge.language_id = language_id
        challenge.title = attrs.get("title") or attrs.get("languageName") or ""
        challenge.description = attrs.get("description") or ""
        challenge.starter_code = attrs.get("starterCode") or attrs.get("starter_code") or ""
        if "solutionCode" in attrs or "solution_code" in attrs:
            challenge.solution_code = attrs.get("solutionCode") or attrs.get("solution_code") or ""
        challenge.solution_visibility = _normalize_solution_visibility(attrs.get("solutionVisibility"))
        challenge.hints = _ensure_list(attrs.get("hints"))
        challenge.difficulty = attrs.get("difficulty") or "medium"
        challenge.time_limit_ms = _coerce_int(attrs.get("timeLimitMs") or attrs.get("time_limit_ms"), 10000)
        sqlite_db_path = attrs.get("sqliteDbPath") or ""
        if language_id == SQL_LANGUAGE_ID and not sqlite_db_path:
            raise HTTPException(
                status_code=400,
                detail="SQL 題目必須先上傳 SQLite 資料庫檔案。",
            )
        if sqlite_db_path and _course_uuid_from_sqlite_path(sqlite_db_path) != course.course_uuid:
            raise HTTPException(
                status_code=400,
                detail="SQLite database must belong to the challenge course",
            )
        challenge.sqlite_db_path = sqlite_db_path
        challenge.additional_files = _validate_additional_files(attrs.get("additionalFiles"))
        challenge.external_id = attrs.get("externalId") or attrs.get("external_id")
        challenge.source_template_uuid = attrs.get("sourceTemplateUuid") or attrs.get("source_template_uuid")
        challenge.tags = _ensure_list(attrs.get("tags"))
        challenge.extra_metadata = _ensure_dict(attrs.get("metadata") or attrs.get("extraMetadata"))
        challenge.updated_at = _now()
        db_session.add(challenge)
        await db_session.flush()
        seen_challenge_ids.add(challenge.id or 0)

        old_tests = (
            await db_session.execute(
                select(CodingChallengeTest).where(CodingChallengeTest.challenge_id == challenge.id)
            )
        ).scalars().all()

        raw_tests = _ensure_list(attrs.get("testCases"))
        if "hiddenTestCases" in attrs:
            raw_hidden_tests = _ensure_list(attrs.get("hiddenTestCases"))
        else:
            raw_hidden_tests = [
                {
                    "testUuid": test.test_uuid,
                    "label": test.label,
                    "stdin": test.stdin,
                    "expectedStdout": test.expected_stdout,
                    "visibility": CodingChallengeTestVisibility.HIDDEN.value,
                }
                for test in old_tests
                if test.visibility == CodingChallengeTestVisibility.HIDDEN
            ]
        normalized_tests = [
            _test_from_attrs(raw, idx, CodingChallengeTestVisibility.VISIBLE)
            for idx, raw in enumerate(raw_tests)
        ] + [
            _test_from_attrs(raw, len(raw_tests) + idx, CodingChallengeTestVisibility.HIDDEN)
            for idx, raw in enumerate(raw_hidden_tests)
        ]
        if len(normalized_tests) > MAX_CHALLENGE_TESTS:
            raise HTTPException(status_code=400, detail="Challenge has too many tests")
        for test_data in normalized_tests:
            _validate_test_value(test_data["stdin"], "input")
            _validate_test_value(test_data["expected_stdout"], "expected output")

        old_by_uuid = {t.test_uuid: t for t in old_tests}
        kept_uuids: set[str] = set()
        for test_data in normalized_tests:
            test = old_by_uuid.get(test_data["test_uuid"])
            if test is None:
                test = CodingChallengeTest(
                    challenge_id=challenge.id or 0,
                    test_uuid=test_data["test_uuid"],
                )
            test.label = test_data["label"]
            test.stdin = test_data["stdin"]
            test.expected_stdout = test_data["expected_stdout"]
            test.visibility = test_data["visibility"]
            test.order = test_data["order"]
            test.updated_at = _now()
            db_session.add(test)
            kept_uuids.add(test.test_uuid)

        for test in old_tests:
            if test.test_uuid not in kept_uuids:
                await db_session.delete(test)

        visible_tests_for_content = []
        for test_data in normalized_tests:
            if test_data["visibility"] != CodingChallengeTestVisibility.VISIBLE:
                continue
            visible_tests_for_content.append({
                "id": test_data["test_uuid"],
                "testUuid": test_data["test_uuid"],
                "label": test_data["label"],
                "stdin": test_data["stdin"],
                "expectedStdout": test_data["expected_stdout"],
                "hidden": False,
            })
        attrs["testCases"] = visible_tests_for_content
        attrs.pop("hiddenTestCases", None)
        attrs.pop("solutionCode", None)
        attrs.pop("solution_code", None)

    existing = (
        await db_session.execute(
            select(CodingChallenge).where(CodingChallenge.activity_id == activity.id)
        )
    ).scalars().all()
    for challenge in existing:
        if challenge.id not in seen_challenge_ids:
            challenge.archived = True
            challenge.updated_at = _now()
            db_session.add(challenge)

    return content


async def _load_challenge(
    challenge_uuid: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> tuple[CodingChallenge, Course]:
    row = (
        await db_session.execute(
            select(CodingChallenge, Course)
            .join(Course, Course.id == CodingChallenge.course_id)
            .where(
                CodingChallenge.challenge_uuid == challenge_uuid,
                CodingChallenge.org_id == Course.org_id,
            )
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Coding challenge not found")
    challenge, course = row
    if challenge.archived:
        raise HTTPException(status_code=404, detail="Coding challenge not found")
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    return challenge, course


def _reject_preview_challenge(challenge: CodingChallenge) -> None:
    """HTML/CSS/JS challenges render in the learner's browser, not on the executor."""
    if challenge.language_id == HTML_PREVIEW_LANGUAGE_ID:
        raise HTTPException(
            status_code=400,
            detail="HTML/CSS/JS exercises render in the browser and cannot be executed on the server",
        )


def _enforce_challenge_rate_limit(user_id: int, challenge_uuid: str, action: str) -> None:
    seconds = 2 if action == "run" else 10
    allowed, _count, retry_after = check_rate_limit(
        key=f"coding_challenge:{action}:{user_id}:{challenge_uuid}",
        max_attempts=1,
        window_seconds=seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many code execution requests. Please wait before trying again.",
            headers={"Retry-After": str(retry_after)},
        )


async def _run_test_suite(
    challenge: CodingChallenge,
    tests: list[CodingChallengeTest],
    source_code: str,
) -> list[dict]:
    if challenge.language_id == SQL_LANGUAGE_ID and not challenge.sqlite_db_path:
        raise HTTPException(
            status_code=400,
            detail="SQL 題目尚未設定 SQLite 資料庫檔案，請聯絡老師。",
        )
    judge0_cfg = _get_judge0_config()
    language_id = challenge.language_id
    effective_source = source_code
    additional_files_b64 = None
    zip_files = [{"name": f.get("name"), "content": f.get("content", "")} for f in challenge.additional_files if f.get("name")]

    if language_id == SQL_LANGUAGE_ID and challenge.sqlite_db_path:
        _course_uuid_from_sqlite_path(challenge.sqlite_db_path)
        db_bytes = _read_storage_file(challenge.sqlite_db_path)
        language_id = PYTHON3_LANGUAGE_ID
        effective_source = _wrap_sql_in_python(source_code)
        additional_files_b64 = _make_additional_files_zip(db_bytes=db_bytes, text_files=zip_files)
    elif zip_files:
        additional_files_b64 = _make_additional_files_zip(text_files=zip_files)

    semaphore = asyncio.Semaphore(MAX_PARALLEL_TESTS)

    async def run_one(test: CodingChallengeTest) -> dict:
        async with semaphore:
            result = await _submit_single(
                judge0_cfg,
                language_id,
                effective_source,
                test.stdin,
                additional_files_b64,
            )
        status_obj = result.get("status", {})
        actual = _normalize_output(result.get("stdout"))
        expected = _normalize_output(test.expected_stdout)
        passed = status_obj.get("id") == 3 and actual == expected
        hidden = test.visibility == CodingChallengeTestVisibility.HIDDEN
        return {
            "id": f"hidden_{test.order}" if hidden else test.test_uuid,
            "label": "Hidden test" if hidden else test.label,
            "visibility": test.visibility.value,
            "hidden": hidden,
            "passed": passed,
            "actual_stdout": None if hidden else result.get("stdout"),
            "expected_stdout": None if hidden else test.expected_stdout,
            "stderr": None if hidden else result.get("stderr"),
            "compile_output": None if hidden else result.get("compile_output"),
            "status": status_obj,
            "time": result.get("time"),
            "memory": result.get("memory"),
        }

    return list(await asyncio.gather(*[run_one(test) for test in tests]))


async def _ensure_execution_revision_unchanged(
    challenge: CodingChallenge,
    revision: str,
    db_session: AsyncSession,
) -> None:
    await db_session.execute(
        select(CodingChallenge.id)
        .where(CodingChallenge.id == challenge.id)
        .with_for_update()
    )
    refreshed_challenge = (
        await db_session.execute(
            select(CodingChallenge)
            .where(CodingChallenge.id == challenge.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().one()
    refreshed_tests = list(
        (
            await db_session.execute(
                select(CodingChallengeTest)
                .where(CodingChallengeTest.challenge_id == challenge.id)
                .order_by(CodingChallengeTest.order)
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
    )
    _validate_additional_files(refreshed_challenge.additional_files)
    if _challenge_revision(refreshed_challenge, refreshed_tests) != revision:
        await db_session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Challenge changed during execution. Please retry.",
        )


async def run_visible_tests(
    challenge_uuid: str,
    source_code: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, course = await _load_challenge(challenge_uuid, request, current_user, db_session)
    _reject_preview_challenge(challenge)
    _validate_source_code(source_code)
    if (
        challenge.sqlite_db_path
        and _course_uuid_from_sqlite_path(challenge.sqlite_db_path) != course.course_uuid
    ):
        raise HTTPException(status_code=400, detail="SQLite database does not belong to this course")
    _enforce_challenge_rate_limit(current_user.id, challenge_uuid, "run")
    tests = (
        await db_session.execute(
            select(CodingChallengeTest)
            .where(
                CodingChallengeTest.challenge_id == challenge.id,
                CodingChallengeTest.visibility == CodingChallengeTestVisibility.VISIBLE,
            )
            .order_by(CodingChallengeTest.order)
        )
    ).scalars().all()
    _validate_additional_files(challenge.additional_files)
    revision_tests = list(
        (
            await db_session.execute(
                select(CodingChallengeTest)
                .where(CodingChallengeTest.challenge_id == challenge.id)
                .order_by(CodingChallengeTest.order)
            )
        ).scalars().all()
    )
    revision = _challenge_revision(challenge, revision_tests)
    results = await _run_test_suite(challenge, list(tests), source_code)
    await _ensure_execution_revision_unchanged(challenge, revision, db_session)
    return {
        "results": results,
        "passed": bool(results) and all(r["passed"] for r in results),
        "total_tests": len(results),
        "passed_tests": len([r for r in results if r["passed"]]),
    }


async def get_challenge_editor_payload(
    challenge_uuid: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, course = await _load_challenge(challenge_uuid, request, current_user, db_session)
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)
    tests = (
        await db_session.execute(
            select(CodingChallengeTest)
            .where(CodingChallengeTest.challenge_id == challenge.id)
            .order_by(CodingChallengeTest.order)
        )
    ).scalars().all()

    def test_payload(test: CodingChallengeTest) -> dict:
        return {
            "id": test.test_uuid,
            "testUuid": test.test_uuid,
            "label": test.label,
            "stdin": test.stdin,
            "expectedStdout": test.expected_stdout,
            "hidden": test.visibility == CodingChallengeTestVisibility.HIDDEN,
            "visibility": test.visibility.value,
        }

    return {
        "challengeUuid": challenge.challenge_uuid,
        "blockId": challenge.block_id,
        "required": challenge.required,
        "languageId": challenge.language_id,
        "title": challenge.title,
        "description": challenge.description,
        "starterCode": challenge.starter_code,
        "solutionCode": challenge.solution_code,
        "solutionVisibility": challenge.solution_visibility.value,
        "hints": challenge.hints,
        "difficulty": challenge.difficulty,
        "timeLimitMs": challenge.time_limit_ms,
        "sqliteDbPath": challenge.sqlite_db_path,
        "additionalFiles": challenge.additional_files,
        "tags": challenge.tags,
        "extraMetadata": challenge.extra_metadata,
        "testCases": [
            test_payload(test)
            for test in tests
            if test.visibility == CodingChallengeTestVisibility.VISIBLE
        ],
        "hiddenTestCases": [
            test_payload(test)
            for test in tests
            if test.visibility == CodingChallengeTestVisibility.HIDDEN
        ],
    }


async def submit_challenge(
    challenge_uuid: str,
    source_code: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, course = await _load_challenge(challenge_uuid, request, current_user, db_session)
    _reject_preview_challenge(challenge)
    _validate_source_code(source_code)
    if (
        challenge.sqlite_db_path
        and _course_uuid_from_sqlite_path(challenge.sqlite_db_path) != course.course_uuid
    ):
        raise HTTPException(status_code=400, detail="SQLite database does not belong to this course")
    _enforce_challenge_rate_limit(current_user.id, challenge_uuid, "submit")
    tests = (
        await db_session.execute(
            select(CodingChallengeTest)
            .where(CodingChallengeTest.challenge_id == challenge.id)
            .order_by(CodingChallengeTest.order)
        )
    ).scalars().all()
    if not tests:
        raise HTTPException(status_code=400, detail="Challenge has no tests")

    _validate_additional_files(challenge.additional_files)
    revision = _challenge_revision(challenge, list(tests))
    results = await _run_test_suite(challenge, list(tests), source_code)
    await _ensure_execution_revision_unchanged(challenge, revision, db_session)
    passed_tests = len([r for r in results if r["passed"]])
    visible_results = [r for r in results if r["visibility"] == CodingChallengeTestVisibility.VISIBLE.value]
    hidden_results = [r for r in results if r["visibility"] == CodingChallengeTestVisibility.HIDDEN.value]
    passed = passed_tests == len(results)

    attempt_number = (
        await db_session.execute(
            select(func.count(CodingChallengeSubmission.id)).where(
                CodingChallengeSubmission.challenge_id == challenge.id,
                CodingChallengeSubmission.user_id == current_user.id,
            )
        )
    ).scalar_one() + 1

    execution_times = []
    for result in results:
        try:
            if result.get("time") is not None:
                execution_times.append(float(result["time"]) * 1000)
        except (TypeError, ValueError):
            pass

    submission = CodingChallengeSubmission(
        submission_uuid=f"ccsub_{uuid4()}",
        challenge_id=challenge.id or 0,
        user_id=current_user.id,
        activity_id=challenge.activity_id,
        course_id=challenge.course_id,
        org_id=challenge.org_id,
        attempt_number=attempt_number,
        language_id=challenge.language_id,
        source_code=source_code,
        passed=passed,
        total_tests=len(results),
        passed_tests=passed_tests,
        visible_total_tests=len(visible_results),
        visible_passed_tests=len([r for r in visible_results if r["passed"]]),
        hidden_total_tests=len(hidden_results),
        hidden_passed_tests=len([r for r in hidden_results if r["passed"]]),
        results={"items": results},
        execution_time_ms=round(max(execution_times)) if execution_times else None,
    )
    db_session.add(submission)
    await db_session.flush()

    progress = (
        await db_session.execute(
            select(CodingChallengeProgress).where(
                CodingChallengeProgress.challenge_id == challenge.id,
                CodingChallengeProgress.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if progress is None:
        progress = CodingChallengeProgress(
            challenge_id=challenge.id or 0,
            user_id=current_user.id,
            activity_id=challenge.activity_id,
            course_id=challenge.course_id,
            org_id=challenge.org_id,
        )
    progress.attempt_count = attempt_number
    progress.latest_submission_id = submission.id
    if passed and not progress.passed:
        progress.passed = True
        progress.first_passed_at = _now()
    progress.updated_at = _now()
    db_session.add(progress)
    await db_session.commit()
    await db_session.refresh(submission)

    activity_completed = False
    if passed:
        activity_completed = await _complete_activity_if_required_challenges_passed(
            challenge.activity_id,
            request,
            current_user,
            db_session,
        )

    return {
        "submission_uuid": submission.submission_uuid,
        "attempt_number": attempt_number,
        "passed": passed,
        "progress_passed": progress.passed,
        "solution_available": progress.passed
        and challenge.solution_visibility != CodingChallengeSolutionVisibility.NEVER
        and bool(challenge.solution_code),
        "activity_completed": activity_completed,
        "total_tests": len(results),
        "passed_tests": passed_tests,
        "visible_total_tests": len(visible_results),
        "visible_passed_tests": len([r for r in visible_results if r["passed"]]),
        "hidden_total_tests": len(hidden_results),
        "hidden_passed_tests": len([r for r in hidden_results if r["passed"]]),
        "results": results,
    }


async def get_challenge_state(
    challenge_uuid: str,
    page: int,
    limit: int,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, _course = await _load_challenge(
        challenge_uuid, request, current_user, db_session
    )
    page = max(page, 1)
    limit = min(max(limit, 1), 50)
    progress = (
        await db_session.execute(
            select(CodingChallengeProgress).where(
                CodingChallengeProgress.challenge_id == challenge.id,
                CodingChallengeProgress.user_id == current_user.id,
                CodingChallengeProgress.org_id == challenge.org_id,
            )
        )
    ).scalars().first()
    base_filters = (
        CodingChallengeSubmission.challenge_id == challenge.id,
        CodingChallengeSubmission.user_id == current_user.id,
        CodingChallengeSubmission.org_id == challenge.org_id,
    )
    submissions = (
        await db_session.execute(
            select(CodingChallengeSubmission)
            .where(*base_filters)
            .order_by(col(CodingChallengeSubmission.id).desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars().all()
    total = (
        await db_session.execute(
            select(func.count(CodingChallengeSubmission.id)).where(*base_filters)
        )
    ).scalar_one()
    passed = bool(progress and progress.passed)
    return {
        "passed": passed,
        "attempt_count": progress.attempt_count if progress else 0,
        "solution_available": passed
        and challenge.solution_visibility != CodingChallengeSolutionVisibility.NEVER
        and bool(challenge.solution_code),
        "submissions": [
            {
                "id": item.submission_uuid,
                "submission_uuid": item.submission_uuid,
                "attempt_number": item.attempt_number,
                "language_id": item.language_id,
                "source_code": item.source_code,
                "passed": item.passed,
                "total_tests": item.total_tests,
                "passed_tests": item.passed_tests,
                "execution_time_ms": item.execution_time_ms,
                "created_at": item.created_at,
            }
            for item in submissions
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


async def get_challenge_solution(
    challenge_uuid: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, course = await _load_challenge(
        challenge_uuid, request, current_user, db_session
    )
    checker = ResourceAccessChecker(request, db_session, current_user)
    update_access = await checker.check_access(course.course_uuid, AccessAction.UPDATE)
    if not update_access.allowed:
        progress = (
            await db_session.execute(
                select(CodingChallengeProgress).where(
                    CodingChallengeProgress.challenge_id == challenge.id,
                    CodingChallengeProgress.user_id == current_user.id,
                    CodingChallengeProgress.org_id == challenge.org_id,
                    CodingChallengeProgress.passed == True,  # noqa: E712
                )
            )
        ).scalars().first()
        if (
            progress is None
            or challenge.solution_visibility == CodingChallengeSolutionVisibility.NEVER
        ):
            raise HTTPException(status_code=403, detail="Solution is not available")
    return {"solution_code": challenge.solution_code}


def _role_has_dashboard_access(user_org: UserOrganization, role: Role | None) -> bool:
    if user_org.role_id in ADMIN_OR_MAINTAINER_ROLE_IDS:
        return True
    rights = getattr(role, "rights", None)
    if hasattr(rights, "model_dump"):
        rights = rights.model_dump()
    if not isinstance(rights, dict):
        return False
    dashboard = rights.get("dashboard") or {}
    if hasattr(dashboard, "model_dump"):
        dashboard = dashboard.model_dump()
    return isinstance(dashboard, dict) and bool(dashboard.get("action_access", False))


async def get_challenge_analytics(
    challenge_uuid: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    """Return teacher-only analytics for explicitly enrolled/class learners.

    The denominator is the union of direct TrailRun enrollments and members of
    user groups attached to the course. Public catalogue visibility alone is
    intentionally not treated as class enrollment.
    """
    challenge, course = await _load_challenge(
        challenge_uuid, request, current_user, db_session
    )
    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.UPDATE
    )

    learner_rows = (
        await db_session.execute(
            select(User, UserOrganization, Role)
            .join(UserOrganization, UserOrganization.user_id == User.id)
            .outerjoin(Role, Role.id == UserOrganization.role_id)
            .where(UserOrganization.org_id == challenge.org_id)
        )
    ).all()
    org_learners = {
        int(user.id): user
        for user, user_org, role in learner_rows
        if user.id is not None and not _role_has_dashboard_access(user_org, role)
    }

    enrolled_ids = set(
        (
            await db_session.execute(
                select(TrailRun.user_id).where(
                    TrailRun.course_id == challenge.course_id,
                    TrailRun.org_id == challenge.org_id,
                    TrailRun.status != StatusEnum.STATUS_CANCELLED,
                )
            )
        ).scalars().all()
    )
    course_group_ids = set(
        (
            await db_session.execute(
                select(UserGroupResource.usergroup_id).where(
                    UserGroupResource.resource_uuid == course.course_uuid,
                    UserGroupResource.org_id == challenge.org_id,
                )
            )
        ).scalars().all()
    )
    if course_group_ids:
        enrolled_ids.update(
            (
                await db_session.execute(
                    select(UserGroupUser.user_id).where(
                        UserGroupUser.org_id == challenge.org_id,
                        col(UserGroupUser.usergroup_id).in_(course_group_ids),
                    )
                )
            ).scalars().all()
        )
    eligible_ids = sorted(set(org_learners).intersection(int(item) for item in enrolled_ids))
    author_ids = set(
        (
            await db_session.execute(
                select(ResourceAuthor.user_id).where(
                    ResourceAuthor.resource_uuid == course.course_uuid,
                    ResourceAuthor.authorship_status == ResourceAuthorshipStatusEnum.ACTIVE,
                )
            )
        ).scalars().all()
    )
    eligible_ids = [user_id for user_id in eligible_ids if user_id not in author_ids]

    progress_by_user: dict[int, CodingChallengeProgress] = {}
    submissions: list[CodingChallengeSubmission] = []
    if eligible_ids:
        progress_rows = (
            await db_session.execute(
                select(CodingChallengeProgress).where(
                    CodingChallengeProgress.challenge_id == challenge.id,
                    CodingChallengeProgress.org_id == challenge.org_id,
                    col(CodingChallengeProgress.user_id).in_(eligible_ids),
                )
            )
        ).scalars().all()
        progress_by_user = {int(item.user_id): item for item in progress_rows}
        submissions = list(
            (
                await db_session.execute(
                    select(CodingChallengeSubmission).where(
                        CodingChallengeSubmission.challenge_id == challenge.id,
                        CodingChallengeSubmission.org_id == challenge.org_id,
                        col(CodingChallengeSubmission.user_id).in_(eligible_ids),
                    )
                )
            ).scalars().all()
        )

    passed_ids = {
        user_id for user_id, progress in progress_by_user.items() if progress.passed
    }
    visible_tests = (
        await db_session.execute(
            select(CodingChallengeTest).where(
                CodingChallengeTest.challenge_id == challenge.id,
                CodingChallengeTest.visibility == CodingChallengeTestVisibility.VISIBLE,
            )
        )
    ).scalars().all()
    visible_by_uuid = {test.test_uuid: test for test in visible_tests}
    failure_counts: dict[str, int] = {test_uuid: 0 for test_uuid in visible_by_uuid}
    for submission in submissions:
        for result in _ensure_list(_ensure_dict(submission.results).get("items")):
            result = _ensure_dict(result)
            test_uuid = str(result.get("id") or "")
            if test_uuid in visible_by_uuid and result.get("passed") is False:
                failure_counts[test_uuid] += 1

    total_students = len(eligible_ids)
    passed_students = len(passed_ids)
    not_passed = []
    for user_id in eligible_ids:
        if user_id in passed_ids:
            continue
        user = org_learners[user_id]
        display_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        not_passed.append({
            "user_id": user_id,
            "user_uuid": user.user_uuid,
            "display_name": display_name or user.username,
            "attempt_count": progress_by_user.get(user_id).attempt_count
            if user_id in progress_by_user
            else 0,
        })

    common_failures = [
        {
            "test_uuid": test_uuid,
            "label": visible_by_uuid[test_uuid].label,
            "failure_count": count,
        }
        for test_uuid, count in failure_counts.items()
        if count > 0
    ]
    common_failures.sort(key=lambda item: (-item["failure_count"], item["label"]))
    return {
        "challenge_uuid": challenge.challenge_uuid,
        "course_uuid": course.course_uuid,
        "eligible_students": total_students,
        "passed_students": passed_students,
        "pass_rate": round((passed_students / total_students) * 100, 1)
        if total_students
        else 0.0,
        "average_attempts": round(len(submissions) / total_students, 2)
        if total_students
        else 0.0,
        "not_passed_students": not_passed,
        "common_failing_tests": common_failures,
    }


async def _complete_activity_if_required_challenges_passed(
    activity_id: int,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> bool:
    required = (
        await db_session.execute(
            select(CodingChallenge)
            .where(
                CodingChallenge.activity_id == activity_id,
                CodingChallenge.required == True,  # noqa: E712
                CodingChallenge.archived == False,  # noqa: E712
            )
        )
    ).scalars().all()
    if not required:
        return False

    passed_ids = set(
        (
            await db_session.execute(
                select(CodingChallengeProgress.challenge_id).where(
                    CodingChallengeProgress.user_id == current_user.id,
                    CodingChallengeProgress.passed == True,  # noqa: E712
                    col(CodingChallengeProgress.challenge_id).in_([c.id for c in required]),
                )
            )
        ).scalars().all()
    )
    if any(challenge.id not in passed_ids for challenge in required):
        return False

    activity = (await db_session.execute(select(Activity).where(Activity.id == activity_id))).scalars().first()
    if not activity:
        return False
    await add_activity_to_trail(request, current_user, activity.activity_uuid, db_session)
    return True
