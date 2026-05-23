import asyncio
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
from src.db.users import PublicUser
from src.routers.code_execution import (
    PYTHON3_LANGUAGE_ID,
    SQL_LANGUAGE_ID,
    _course_uuid_from_sqlite_path,
    _get_judge0_config,
    _make_additional_files_zip,
    _read_storage_file,
    _submit_single,
    _wrap_sql_in_python,
)
from src.security.rbac import AccessAction, check_resource_access
from src.services.security.rate_limiting import check_rate_limit
from src.services.trail.trail import add_activity_to_trail


SUPPORTED_LANGUAGE_IDS = {71, 63, 82}


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

        language_id = _coerce_int(attrs.get("languageId") or attrs.get("language_id"), 71)
        if language_id not in SUPPORTED_LANGUAGE_IDS:
            language_id = PYTHON3_LANGUAGE_ID

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
        challenge.solution_code = attrs.get("solutionCode") or attrs.get("solution_code") or ""
        challenge.solution_visibility = _normalize_solution_visibility(attrs.get("solutionVisibility"))
        challenge.hints = _ensure_list(attrs.get("hints"))
        challenge.difficulty = attrs.get("difficulty") or "medium"
        challenge.time_limit_ms = _coerce_int(attrs.get("timeLimitMs") or attrs.get("time_limit_ms"), 10000)
        challenge.sqlite_db_path = attrs.get("sqliteDbPath") or ""
        challenge.additional_files = _ensure_list(attrs.get("additionalFiles"))
        challenge.external_id = attrs.get("externalId") or attrs.get("external_id")
        challenge.source_template_uuid = attrs.get("sourceTemplateUuid") or attrs.get("source_template_uuid")
        challenge.tags = _ensure_list(attrs.get("tags"))
        challenge.extra_metadata = _ensure_dict(attrs.get("metadata") or attrs.get("extraMetadata"))
        challenge.updated_at = _now()
        db_session.add(challenge)
        await db_session.flush()
        seen_challenge_ids.add(challenge.id or 0)

        raw_tests = _ensure_list(attrs.get("testCases"))
        raw_hidden_tests = _ensure_list(attrs.get("hiddenTestCases"))
        normalized_tests = [
            _test_from_attrs(raw, idx, CodingChallengeTestVisibility.VISIBLE)
            for idx, raw in enumerate(raw_tests)
        ] + [
            _test_from_attrs(raw, len(raw_tests) + idx, CodingChallengeTestVisibility.HIDDEN)
            for idx, raw in enumerate(raw_hidden_tests)
        ]

        old_tests = (
            await db_session.execute(
                select(CodingChallengeTest).where(CodingChallengeTest.challenge_id == challenge.id)
            )
        ).scalars().all()
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
            .where(CodingChallenge.challenge_uuid == challenge_uuid)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Coding challenge not found")
    challenge, course = row
    if challenge.archived:
        raise HTTPException(status_code=404, detail="Coding challenge not found")
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    return challenge, course


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

    async def run_one(test: CodingChallengeTest) -> dict:
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
            "id": test.test_uuid,
            "label": "Hidden test" if hidden else test.label,
            "visibility": test.visibility.value,
            "hidden": hidden,
            "passed": passed,
            "actual_stdout": None if hidden else result.get("stdout"),
            "expected_stdout": None if hidden else test.expected_stdout,
            "stderr": result.get("stderr"),
            "compile_output": result.get("compile_output"),
            "status": status_obj,
            "time": result.get("time"),
            "memory": result.get("memory"),
        }

    return list(await asyncio.gather(*[run_one(test) for test in tests]))


async def run_visible_tests(
    challenge_uuid: str,
    source_code: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, _course = await _load_challenge(challenge_uuid, request, current_user, db_session)
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
    results = await _run_test_suite(challenge, list(tests), source_code)
    return {
        "results": results,
        "passed": bool(results) and all(r["passed"] for r in results),
        "total_tests": len(results),
        "passed_tests": len([r for r in results if r["passed"]]),
    }


async def submit_challenge(
    challenge_uuid: str,
    source_code: str,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    challenge, _course = await _load_challenge(challenge_uuid, request, current_user, db_session)
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

    results = await _run_test_suite(challenge, list(tests), source_code)
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
        "activity_completed": activity_completed,
        "total_tests": len(results),
        "passed_tests": passed_tests,
        "visible_total_tests": len(visible_results),
        "visible_passed_tests": len([r for r in visible_results if r["passed"]]),
        "hidden_total_tests": len(hidden_results),
        "hidden_passed_tests": len([r for r in hidden_results if r["passed"]]),
        "results": results,
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
