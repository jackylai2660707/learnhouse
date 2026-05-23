from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import func, select

from src.db.coding_challenges import (
    CodingChallenge,
    CodingChallengeProgress,
    CodingChallengeSubmission,
    CodingChallengeTest,
    CodingChallengeTestVisibility,
)
from src.services.coding_challenges.challenges import (
    run_visible_tests,
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
