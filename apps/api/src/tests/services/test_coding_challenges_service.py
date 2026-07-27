from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import func, select

from src.db.coding_challenges import (
    CodingChallenge,
    CodingChallengeProgress,
    CodingChallengeSubmission,
    CodingChallengeTest,
    CodingChallengeTestVisibility,
)
from src.db.user_organizations import UserOrganization
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.db.users import User
from src.db.trails import Trail
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.services.coding_challenges.challenges import (
    MAX_SOURCE_CODE_BYTES,
    MAX_CHALLENGE_TESTS,
    MAX_TEST_VALUE_BYTES,
    MAX_ADDITIONAL_FILES,
    MAX_ADDITIONAL_FILE_BYTES,
    MAX_ADDITIONAL_FILES_TOTAL_BYTES,
    SUPPORTED_LANGUAGE_IDS,
    get_challenge_editor_payload,
    get_challenge_analytics,
    get_challenge_solution,
    get_challenge_state,
    run_visible_tests,
    sanitize_coding_challenge_content,
    submit_challenge,
    sync_coding_challenges_for_activity,
)


def _challenge_content():
    return {
        "type": "doc",
        "content": [
            {
                "type": "blockCode",
                "attrs": {
                    "id": "block_1",
                    "challengeUuid": "challenge_1",
                    "required": True,
                    "languageId": 71,
                    "starterCode": "print('hi')",
                    "solutionCode": "print('ok')",
                    "testCases": [
                        {
                            "testUuid": "visible_1",
                            "label": "Visible",
                            "stdin": "1\n",
                            "expectedStdout": "2\n",
                        }
                    ],
                    "hiddenTestCases": [
                        {
                            "testUuid": "hidden_1",
                            "label": "Hidden",
                            "stdin": "41\n",
                            "expectedStdout": "42\n",
                        }
                    ],
                },
            }
        ],
    }


async def _create_challenge(db, org, course, activity, *, required=False):
    challenge = CodingChallenge(
        challenge_uuid="challenge_submit",
        org_id=org.id,
        course_id=course.id,
        activity_id=activity.id,
        block_id="block_submit",
        required=required,
        language_id=71,
        solution_code="print('ok')",
    )
    db.add(challenge)
    await db.flush()
    db.add(
        CodingChallengeTest(
            challenge_id=challenge.id,
            test_uuid="visible_submit",
            label="Visible",
            stdin="",
            expected_stdout="ok\n",
            visibility=CodingChallengeTestVisibility.VISIBLE,
            order=0,
        )
    )
    db.add(
        CodingChallengeTest(
            challenge_id=challenge.id,
            test_uuid="hidden_submit",
            label="Hidden",
            stdin="",
            expected_stdout="ok\n",
            visibility=CodingChallengeTestVisibility.HIDDEN,
            order=1,
        )
    )
    await db.commit()
    await db.refresh(challenge)
    return challenge


@pytest.mark.asyncio
async def test_sync_persists_hidden_tests_but_strips_them_from_activity_content(
    db, course, activity
):
    content = _challenge_content()

    sanitized = await sync_coding_challenges_for_activity(activity, course, content, db)
    await db.commit()

    attrs = sanitized["content"][0]["attrs"]
    assert "hiddenTestCases" not in attrs
    assert "solutionCode" not in attrs
    assert attrs["testCases"] == [
        {
            "id": "visible_1",
            "testUuid": "visible_1",
            "label": "Visible",
            "stdin": "1\n",
            "expectedStdout": "2\n",
            "hidden": False,
        }
    ]

    challenge = (
        await db.execute(
            select(CodingChallenge).where(CodingChallenge.challenge_uuid == "challenge_1")
        )
    ).scalars().one()
    tests = (
        await db.execute(
            select(CodingChallengeTest)
            .where(CodingChallengeTest.challenge_id == challenge.id)
            .order_by(CodingChallengeTest.order)
        )
    ).scalars().all()

    assert [test.test_uuid for test in tests] == ["visible_1", "hidden_1"]
    assert tests[0].visibility == CodingChallengeTestVisibility.VISIBLE
    assert tests[1].visibility == CodingChallengeTestVisibility.HIDDEN
    assert tests[1].expected_stdout == "42\n"
    assert challenge.solution_code == "print('ok')"


@pytest.mark.asyncio
@pytest.mark.parametrize("language_id", [50, 54, 62, 63, 71, 74, 1000])
async def test_sync_preserves_every_manifest_runtime_and_preview_language(
    db, course, activity, language_id
):
    content = _challenge_content()
    content["content"][0]["attrs"]["languageId"] = language_id

    await sync_coding_challenges_for_activity(activity, course, content, db)

    challenge = (
        await db.execute(
            select(CodingChallenge).where(CodingChallenge.challenge_uuid == "challenge_1")
        )
    ).scalars().one()
    assert challenge.language_id == language_id


@pytest.mark.asyncio
async def test_sync_preserves_sql_adapter_language_when_owned_database_is_present(
    db, course, activity
):
    content = _challenge_content()
    attrs = content["content"][0]["attrs"]
    attrs["languageId"] = 82
    attrs["sqliteDbPath"] = (
        f"orgs/org_test/courses/{course.course_uuid}/activities/activity_test/db.sqlite"
    )

    await sync_coding_challenges_for_activity(activity, course, content, db)

    challenge = (
        await db.execute(
            select(CodingChallenge).where(CodingChallenge.challenge_uuid == "challenge_1")
        )
    ).scalars().one()
    assert challenge.language_id == 82
    assert challenge.sqlite_db_path == attrs["sqliteDbPath"]


@pytest.mark.asyncio
async def test_sync_rejects_sql_adapter_without_sqlite_database(db, course, activity):
    content = _challenge_content()
    content["content"][0]["attrs"]["languageId"] = 82

    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(activity, course, content, db)

    assert exc_info.value.status_code == 400
    assert "SQLite" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sync_rejects_unknown_language_instead_of_rewriting_to_python(
    db, course, activity
):
    content = _challenge_content()
    content["content"][0]["attrs"]["languageId"] = 73

    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(activity, course, content, db)

    assert exc_info.value.status_code == 400
    assert "不支援" in exc_info.value.detail


def test_durable_challenge_ids_match_manifest_execution_and_preview_capabilities():
    assert SUPPORTED_LANGUAGE_IDS == {50, 54, 62, 63, 71, 74, 82, 1000}


@pytest.mark.asyncio
async def test_sync_preserves_hidden_tests_when_sanitized_content_is_saved_again(
    db, course, activity
):
    sanitized = await sync_coding_challenges_for_activity(activity, course, _challenge_content(), db)
    await db.commit()

    await sync_coding_challenges_for_activity(activity, course, sanitized, db)
    await db.commit()

    challenge = (
        await db.execute(
            select(CodingChallenge).where(CodingChallenge.challenge_uuid == "challenge_1")
        )
    ).scalars().one()
    hidden_tests = (
        await db.execute(
            select(CodingChallengeTest).where(
                CodingChallengeTest.challenge_id == challenge.id,
                CodingChallengeTest.visibility == CodingChallengeTestVisibility.HIDDEN,
            )
        )
    ).scalars().all()

    assert [test.test_uuid for test in hidden_tests] == ["hidden_1"]
    assert hidden_tests[0].expected_stdout == "42\n"
    assert challenge.solution_code == "print('ok')"


def test_activity_response_sanitizer_removes_legacy_solution_and_hidden_tests():
    sanitized = sanitize_coding_challenge_content(_challenge_content())
    attrs = sanitized["content"][0]["attrs"]
    assert "solutionCode" not in attrs
    assert "hiddenTestCases" not in attrs
    assert [test["testUuid"] for test in attrs["testCases"]] == ["visible_1"]


@pytest.mark.asyncio
async def test_sync_rejects_excessive_test_count(db, course, activity):
    content = _challenge_content()
    attrs = content["content"][0]["attrs"]
    attrs["testCases"] = [
        {"testUuid": f"visible_{index}", "expectedStdout": "ok"}
        for index in range(MAX_CHALLENGE_TESTS + 1)
    ]
    attrs["hiddenTestCases"] = []
    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(activity, course, content, db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_sync_rejects_oversized_hidden_expected_output(db, course, activity):
    content = _challenge_content()
    content["content"][0]["attrs"]["hiddenTestCases"][0]["expectedStdout"] = (
        "x" * (MAX_TEST_VALUE_BYTES + 1)
    )
    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(activity, course, content, db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_teacher_can_load_hidden_tests_for_editor(
    db, org, course, activity, admin_user, mock_request
):
    await _create_challenge(db, org, course, activity)

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ):
        payload = await get_challenge_editor_payload(
            "challenge_submit",
            mock_request,
            admin_user,
            db,
        )

    assert payload["challengeUuid"] == "challenge_submit"
    assert [test["testUuid"] for test in payload["testCases"]] == ["visible_submit"]
    assert [test["testUuid"] for test in payload["hiddenTestCases"]] == ["hidden_submit"]
    assert payload["hiddenTestCases"][0]["expectedStdout"] == "ok\n"


@pytest.mark.asyncio
async def test_run_executes_only_visible_tests_and_does_not_save_attempts(
    db, org, course, activity, admin_user, mock_request
):
    await _create_challenge(db, org, course, activity)
    seen_test_ids = []

    async def fake_run_suite(_challenge, tests, _source_code):
        seen_test_ids.extend(test.test_uuid for test in tests)
        return [
            {
                "id": "visible_submit",
                "visibility": "visible",
                "passed": True,
            }
        ]

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.coding_challenges.challenges._enforce_challenge_rate_limit",
    ), patch(
        "src.services.coding_challenges.challenges._run_test_suite",
        new=AsyncMock(side_effect=fake_run_suite),
    ):
        result = await run_visible_tests(
            "challenge_submit",
            "print('ok')",
            mock_request,
            admin_user,
            db,
        )

    assert result["passed"] is True
    assert seen_test_ids == ["visible_submit"]
    saved = (await db.execute(select(func.count(CodingChallengeSubmission.id)))).scalar_one()
    assert saved == 0


@pytest.mark.asyncio
async def test_run_sql_challenge_without_database_never_reaches_executor(
    db, org, course, activity, admin_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    challenge.language_id = 82
    challenge.sqlite_db_path = ""
    db.add(challenge)
    await db.commit()
    submit = AsyncMock()

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.coding_challenges.challenges._enforce_challenge_rate_limit",
    ), patch(
        "src.services.coding_challenges.challenges._submit_single",
        new=submit,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await run_visible_tests(
                challenge.challenge_uuid,
                "SELECT 1",
                mock_request,
                admin_user,
                db,
            )

    assert exc_info.value.status_code == 400
    assert "SQLite" in exc_info.value.detail
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_can_be_retried_and_saves_each_attempt(
    db, org, course, activity, admin_user, mock_request
):
    await _create_challenge(db, org, course, activity)

    failed_results = [
        {"id": "visible_submit", "visibility": "visible", "passed": False},
        {"id": "hidden_submit", "visibility": "hidden", "passed": False},
    ]

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.coding_challenges.challenges._enforce_challenge_rate_limit",
    ), patch(
        "src.services.coding_challenges.challenges._run_test_suite",
        new=AsyncMock(return_value=failed_results),
    ):
        first = await submit_challenge(
            "challenge_submit",
            "print('wrong')",
            mock_request,
            admin_user,
            db,
        )
        second = await submit_challenge(
            "challenge_submit",
            "print('still wrong')",
            mock_request,
            admin_user,
            db,
        )

    assert first["attempt_number"] == 1
    assert second["attempt_number"] == 2
    assert first["passed"] is False
    assert second["passed"] is False

    saved = (
        await db.execute(
            select(CodingChallengeSubmission)
            .where(CodingChallengeSubmission.user_id == admin_user.id)
            .order_by(CodingChallengeSubmission.attempt_number)
        )
    ).scalars().all()
    assert [attempt.source_code for attempt in saved] == [
        "print('wrong')",
        "print('still wrong')",
    ]

    progress = (await db.execute(select(CodingChallengeProgress))).scalars().one()
    assert progress.attempt_count == 2
    assert progress.passed is False


@pytest.mark.asyncio
async def test_state_reads_durable_submissions_for_current_user_only(
    db, org, course, activity, admin_user, regular_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    for user, suffix in ((admin_user, "mine"), (regular_user, "other")):
        db.add(CodingChallengeSubmission(
            submission_uuid=f"ccsub_{suffix}", challenge_id=challenge.id,
            user_id=user.id, activity_id=activity.id, course_id=course.id,
            org_id=org.id, attempt_number=1, language_id=71,
            source_code=f"# {suffix}", passed=user.id == admin_user.id,
            total_tests=2, passed_tests=2 if user.id == admin_user.id else 0,
        ))
    db.add(CodingChallengeProgress(
        challenge_id=challenge.id, user_id=admin_user.id, activity_id=activity.id,
        course_id=course.id, org_id=org.id, passed=True, attempt_count=1,
    ))
    await db.commit()

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ):
        state = await get_challenge_state(
            challenge.challenge_uuid, 1, 10, mock_request, admin_user, db
        )

    assert state["passed"] is True
    assert state["solution_available"] is True
    assert state["total"] == 1
    assert [item["id"] for item in state["submissions"]] == ["ccsub_mine"]


@pytest.mark.asyncio
async def test_student_solution_requires_formal_pass(
    db, org, course, activity, regular_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    denied = type("Decision", (), {"allowed": False})()
    checker = AsyncMock()
    checker.check_access.return_value = denied

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.coding_challenges.challenges.ResourceAccessChecker",
        return_value=checker,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_challenge_solution(
                challenge.challenge_uuid, mock_request, regular_user, db
            )
        assert getattr(exc_info.value, "status_code", None) == 403

        db.add(CodingChallengeProgress(
            challenge_id=challenge.id, user_id=regular_user.id,
            activity_id=activity.id, course_id=course.id, org_id=org.id,
            passed=True, attempt_count=1,
        ))
        await db.commit()
        payload = await get_challenge_solution(
            challenge.challenge_uuid, mock_request, regular_user, db
        )

    assert payload == {"solution_code": "print('ok')"}


@pytest.mark.asyncio
async def test_teacher_update_access_can_load_solution_without_pass(
    db, org, course, activity, admin_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    allowed = type("Decision", (), {"allowed": True})()
    checker = AsyncMock()
    checker.check_access.return_value = allowed
    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.coding_challenges.challenges.ResourceAccessChecker",
        return_value=checker,
    ):
        payload = await get_challenge_solution(
            challenge.challenge_uuid, mock_request, admin_user, db
        )
    assert payload["solution_code"] == "print('ok')"


@pytest.mark.asyncio
async def test_run_rejects_oversized_source_before_execution(
    db, org, course, activity, admin_user, mock_request
):
    await _create_challenge(db, org, course, activity)
    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await run_visible_tests(
                "challenge_submit", "x" * (MAX_SOURCE_CODE_BYTES + 1),
                mock_request, admin_user, db,
            )
    assert getattr(exc_info.value, "status_code", None) == 413


@pytest.mark.asyncio
async def test_public_student_can_read_own_state_but_not_update_solution(
    db, org, course, activity, regular_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    state = await get_challenge_state(
        challenge.challenge_uuid, 1, 10, mock_request, regular_user, db
    )
    assert state["total"] == 0
    with pytest.raises(HTTPException) as exc_info:
        await get_challenge_solution(
            challenge.challenge_uuid, mock_request, regular_user, db
        )
    assert getattr(exc_info.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_challenge_with_cross_org_course_mismatch_is_not_loadable(
    db, other_org, course, activity, regular_user, mock_request
):
    challenge = CodingChallenge(
        challenge_uuid="challenge_cross_org",
        org_id=other_org.id,
        course_id=course.id,
        activity_id=activity.id,
        block_id="block_cross_org",
        language_id=71,
    )
    db.add(challenge)
    await db.commit()
    with pytest.raises(HTTPException) as exc_info:
        await get_challenge_state(
            challenge.challenge_uuid, 1, 10, mock_request, regular_user, db
        )
    assert getattr(exc_info.value, "status_code", None) == 404


async def _add_course_class(db, org, course, user_ids):
    group = UserGroup(
        id=10,
        org_id=org.id,
        name="Class A",
        description="",
        usergroup_uuid="usergroup_class_a",
    )
    db.add(group)
    await db.flush()
    db.add(UserGroupResource(
        usergroup_id=group.id,
        resource_uuid=course.course_uuid,
        org_id=org.id,
    ))
    for user_id in user_ids:
        db.add(UserGroupUser(
            usergroup_id=group.id,
            user_id=user_id,
            org_id=org.id,
        ))
    await db.commit()


@pytest.mark.asyncio
async def test_teacher_analytics_uses_enrolled_learners_and_redacts_private_data(
    db, org, course, activity, admin_user, regular_user, user_role, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    second_student = User(
        id=3,
        username="student_b",
        first_name="Student",
        last_name="B",
        email="student-b@test.com",
        password="hashed",
        user_uuid="user_student_b",
    )
    db.add(second_student)
    await db.flush()
    db.add(UserOrganization(
        user_id=second_student.id,
        org_id=org.id,
        role_id=user_role.id,
        creation_date="",
        update_date="",
    ))
    await db.commit()
    await _add_course_class(
        db, org, course, [admin_user.id, regular_user.id, second_student.id]
    )

    db.add(CodingChallengeProgress(
        challenge_id=challenge.id,
        user_id=regular_user.id,
        activity_id=activity.id,
        course_id=course.id,
        org_id=org.id,
        passed=True,
        attempt_count=1,
    ))
    db.add(CodingChallengeProgress(
        challenge_id=challenge.id,
        user_id=second_student.id,
        activity_id=activity.id,
        course_id=course.id,
        org_id=org.id,
        passed=False,
        attempt_count=2,
    ))
    submissions = [
        (regular_user.id, "passed", True),
        (second_student.id, "failed-1", False),
        (second_student.id, "failed-2", False),
    ]
    for attempt, (user_id, suffix, passed) in enumerate(submissions, start=1):
        db.add(CodingChallengeSubmission(
            submission_uuid=f"analytics_{suffix}",
            challenge_id=challenge.id,
            user_id=user_id,
            activity_id=activity.id,
            course_id=course.id,
            org_id=org.id,
            attempt_number=attempt,
            language_id=71,
            source_code=f"private-source-{suffix}",
            passed=passed,
            total_tests=2,
            passed_tests=2 if passed else 0,
            results={"items": [
                {"id": "visible_submit", "label": "client-label", "passed": passed,
                 "stdin": "private-input", "expected_stdout": "private-output"},
                {"id": "hidden_submit", "label": "hidden-label", "passed": passed,
                 "stdin": "hidden-input", "expected_stdout": "hidden-output"},
            ]},
        ))
    await db.commit()

    analytics = await get_challenge_analytics(
        challenge.challenge_uuid, mock_request, admin_user, db
    )

    assert analytics["eligible_students"] == 2
    assert analytics["passed_students"] == 1
    assert analytics["pass_rate"] == 50.0
    assert analytics["average_attempts"] == 1.5
    assert analytics["not_passed_students"] == [{
        "user_id": second_student.id,
        "user_uuid": second_student.user_uuid,
        "display_name": "Student B",
        "attempt_count": 2,
    }]
    assert analytics["common_failing_tests"] == [{
        "test_uuid": "visible_submit",
        "label": "Visible",
        "failure_count": 2,
    }]
    serialized = str(analytics)
    assert "private-source" not in serialized
    assert "hidden_submit" not in serialized
    assert "hidden-label" not in serialized
    assert "expected_stdout" not in serialized
    assert "stdin" not in serialized


@pytest.mark.asyncio
async def test_challenge_analytics_empty_class_returns_zeroes(
    db, org, course, activity, admin_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    analytics = await get_challenge_analytics(
        challenge.challenge_uuid, mock_request, admin_user, db
    )
    assert analytics["eligible_students"] == 0
    assert analytics["pass_rate"] == 0.0
    assert analytics["average_attempts"] == 0.0
    assert analytics["not_passed_students"] == []
    assert analytics["common_failing_tests"] == []


@pytest.mark.asyncio
async def test_challenge_analytics_includes_direct_course_enrollment(
    db, org, course, activity, admin_user, regular_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    trail = Trail(
        org_id=org.id,
        user_id=regular_user.id,
        trail_uuid="trail_analytics",
    )
    db.add(trail)
    await db.flush()
    db.add(TrailRun(
        trail_id=trail.id,
        course_id=course.id,
        org_id=org.id,
        user_id=regular_user.id,
        creation_date="",
        update_date="",
    ))
    await db.commit()
    analytics = await get_challenge_analytics(
        challenge.challenge_uuid, mock_request, admin_user, db
    )
    assert analytics["eligible_students"] == 1
    assert analytics["not_passed_students"][0]["user_uuid"] == regular_user.user_uuid


@pytest.mark.asyncio
async def test_challenge_analytics_excludes_cancelled_enrollment_and_course_author(
    db, org, course, activity, admin_user, regular_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    trail = Trail(org_id=org.id, user_id=regular_user.id, trail_uuid="trail_cancelled")
    db.add(trail)
    await db.flush()
    db.add(TrailRun(
        trail_id=trail.id,
        course_id=course.id,
        org_id=org.id,
        user_id=regular_user.id,
        status=StatusEnum.STATUS_CANCELLED,
        creation_date="",
        update_date="",
    ))
    await _add_course_class(db, org, course, [regular_user.id])
    db.add(ResourceAuthor(
        resource_uuid=course.course_uuid,
        user_id=regular_user.id,
        authorship=ResourceAuthorshipEnum.CONTRIBUTOR,
        authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
    ))
    await db.commit()
    analytics = await get_challenge_analytics(
        challenge.challenge_uuid, mock_request, admin_user, db
    )
    assert analytics["eligible_students"] == 0


@pytest.mark.asyncio
async def test_student_cannot_read_challenge_analytics(
    db, org, course, activity, regular_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    with pytest.raises(HTTPException) as exc_info:
        await get_challenge_analytics(
            challenge.challenge_uuid, mock_request, regular_user, db
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_cross_org_challenge_analytics_is_not_loadable(
    db, other_org, course, activity, admin_user, mock_request
):
    challenge = CodingChallenge(
        challenge_uuid="challenge_cross_org_analytics",
        org_id=other_org.id,
        course_id=course.id,
        activity_id=activity.id,
        block_id="block_cross_org_analytics",
        language_id=71,
    )
    db.add(challenge)
    await db.commit()
    with pytest.raises(HTTPException) as exc_info:
        await get_challenge_analytics(
            challenge.challenge_uuid, mock_request, admin_user, db
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_sync_rejects_challenge_uuid_owned_by_another_activity(
    db, org, course, activity
):
    await sync_coding_challenges_for_activity(
        activity, course, _challenge_content(), db
    )
    await db.commit()
    attacker_activity = Activity(
        id=999,
        name="Attacker Activity",
        activity_type=activity.activity_type,
        activity_sub_type=activity.activity_sub_type,
        org_id=999,
        course_id=999,
        activity_uuid="activity_attacker",
    )
    attacker_course = Course(
        id=999,
        name="Attacker Course",
        public=False,
        published=False,
        open_to_contributors=False,
        org_id=999,
        course_uuid="course_attacker",
    )
    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(
            attacker_activity, attacker_course, _challenge_content(), db
        )
    assert exc_info.value.status_code == 409
    challenge = (
        await db.execute(select(CodingChallenge).where(
            CodingChallenge.challenge_uuid == "challenge_1"
        ))
    ).scalars().one()
    assert challenge.org_id == org.id
    assert challenge.course_id == course.id
    assert challenge.solution_code == "print('ok')"


@pytest.mark.asyncio
async def test_sync_rejects_challenge_uuid_reused_by_another_block(
    db, course, activity
):
    await sync_coding_challenges_for_activity(
        activity, course, _challenge_content(), db
    )
    await db.commit()

    reused = _challenge_content()
    reused["content"][0]["attrs"]["id"] = "block_2"
    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(activity, course, reused, db)

    assert exc_info.value.status_code == 409
    challenge = (
        await db.execute(
            select(CodingChallenge).where(
                CodingChallenge.challenge_uuid == "challenge_1"
            )
        )
    ).scalars().one()
    assert challenge.block_id == "block_1"


@pytest.mark.asyncio
async def test_run_rejects_sqlite_database_from_another_course(
    db, org, course, activity, admin_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)
    challenge.sqlite_db_path = "orgs/org_other/courses/course_other/activities/a/db.sqlite"
    db.add(challenge)
    await db.commit()
    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await run_visible_tests(
                challenge.challenge_uuid, "print('ok')", mock_request, admin_user, db
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_sync_rejects_additional_file_count_and_size(db, course, activity):
    too_many = _challenge_content()
    too_many["content"][0]["attrs"]["additionalFiles"] = [
        {"name": f"file-{index}.txt", "content": "x"}
        for index in range(MAX_ADDITIONAL_FILES + 1)
    ]
    with pytest.raises(HTTPException) as count_error:
        await sync_coding_challenges_for_activity(activity, course, too_many, db)
    assert count_error.value.status_code == 400
    await db.rollback()
    await db.refresh(activity)
    await db.refresh(course)

    too_large = _challenge_content()
    too_large["content"][0]["attrs"]["additionalFiles"] = [{
        "name": "large.txt",
        "content": "x" * (MAX_ADDITIONAL_FILE_BYTES + 1),
    }]
    with pytest.raises(HTTPException) as size_error:
        await sync_coding_challenges_for_activity(activity, course, too_large, db)
    assert size_error.value.status_code == 400
    await db.rollback()
    await db.refresh(activity)
    await db.refresh(course)

    total_too_large = _challenge_content()
    file_count = (MAX_ADDITIONAL_FILES_TOTAL_BYTES // MAX_ADDITIONAL_FILE_BYTES) + 1
    total_too_large["content"][0]["attrs"]["additionalFiles"] = [
        {
            "name": f"large-{index}.txt",
            "content": "x" * MAX_ADDITIONAL_FILE_BYTES,
        }
        for index in range(file_count)
    ]
    with pytest.raises(HTTPException) as total_size_error:
        await sync_coding_challenges_for_activity(
            activity, course, total_too_large, db
        )
    assert total_size_error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "additional_file",
    [
        {"name": "", "content": "x"},
        {"name": 123, "content": "x"},
        {"name": "../secret.py", "content": "x"},
        {"name": "nested/file.py", "content": "x"},
        {"name": "nested\\file.py", "content": "x"},
        {"name": "bad\x00.py", "content": "x"},
        {"name": "a" * 256, "content": "x"},
        {"name": "file.py", "content": ""},
        {"name": "file.py", "content": 123},
    ],
)
async def test_sync_rejects_invalid_additional_file_schema(
    db, course, activity, additional_file
):
    content = _challenge_content()
    content["content"][0]["attrs"]["additionalFiles"] = [additional_file]

    with pytest.raises(HTTPException) as exc_info:
        await sync_coding_challenges_for_activity(activity, course, content, db)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_submit_does_not_persist_when_challenge_changes_during_execution(
    db, org, course, activity, admin_user, mock_request
):
    challenge = await _create_challenge(db, org, course, activity)

    async def mutate_during_execution(_challenge, tests, _source_code):
        tests[0].expected_stdout = "changed\n"
        tests[0].updated_at = "changed"
        db.add(tests[0])
        await db.commit()
        return [
            {"id": test.test_uuid, "visibility": test.visibility.value, "passed": True}
            for test in tests
        ]

    with patch(
        "src.services.coding_challenges.challenges.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.coding_challenges.challenges._enforce_challenge_rate_limit",
    ), patch(
        "src.services.coding_challenges.challenges._run_test_suite",
        new=AsyncMock(side_effect=mutate_during_execution),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await submit_challenge(
                challenge.challenge_uuid, "print('ok')", mock_request, admin_user, db
            )
    assert exc_info.value.status_code == 409
    count = (
        await db.execute(select(func.count(CodingChallengeSubmission.id)))
    ).scalar_one()
    assert count == 0
