from datetime import datetime

import pytest
from fastapi import HTTPException

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.db.question_bank import QuestionBankItemCreate, QuestionBankVisibilityEnum
from src.db.self_tests import (
    ReviewSelfTestAttemptRequest,
    StartSelfTestRequest,
    SubmitSelfTestAnswer,
    SubmitSelfTestRequest,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.question_bank import create_question_bank_item
from src.services.self_tests import (
    list_org_self_test_attempts,
    read_self_test_attempt,
    review_self_test_attempt,
    start_self_test,
    submit_self_test,
)


pytestmark = pytest.mark.asyncio


def _bank_item(org_id: int, title: str, visibility: QuestionBankVisibilityEnum = QuestionBankVisibilityEnum.ORG):
    return QuestionBankItemCreate(
        title=title,
        description="What keyword defines a Python function?",
        hint="Three letters",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "What keyword defines a Python function?",
            "correct_answers": ["def"],
            "match_mode": "case_insensitive",
            "explanation": "Python uses def.",
        },
        tags=["python", "self-test"],
        difficulty="beginner",
        visibility=visibility,
        category_id=None,
        org_id=org_id,
        source_assignment_task_uuid=None,
        reference_file=None,
    )


def _code_bank_item(org_id: int, title: str):
    return QuestionBankItemCreate(
        title=title,
        description="Complete a Python output task.",
        hint="Use print",
        assignment_type=AssignmentTaskTypeEnum.CODE,
        contents={
            "language_id": 71,
            "starter_code": "# Write your code here\n",
            "solution_code": "print('secret')\n",
            "solutionCode": "print('secret')\n",
            "show_solution_after_submit": True,
            "test_cases": [
                {
                    "id": "tc_visible",
                    "label": "Visible",
                    "stdin": "",
                    "expectedStdout": "secret",
                    "expected_stdout": "secret",
                }
            ],
            "hidden_test_cases": [
                {
                    "id": "tc_hidden",
                    "label": "Hidden",
                    "stdin": "",
                    "expectedStdout": "secret",
                    "expected_stdout": "secret",
                }
            ],
        },
        tags=["python", "self-test", "code"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        category_id=None,
        org_id=org_id,
        source_assignment_task_uuid=None,
        reference_file=None,
    )


async def test_student_starts_self_test_from_shared_question_bank_without_answer_leak(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    await create_question_bank_item(_bank_item(org.id, "Private teacher item", QuestionBankVisibilityEnum.PRIVATE), admin_user, db)

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["python"]),
        regular_user,
        db,
    )

    assert attempt.status == "STARTED"
    assert attempt.question_count == 1
    assert len(attempt.questions) == 1
    assert "correct_answers" not in attempt.questions[0].contents
    assert attempt.questions[0].answer == {}


async def test_student_starts_code_self_test_without_solution_or_expected_output_leak(db, org, admin_user, regular_user):
    await create_question_bank_item(_code_bank_item(org.id, "Shared code self-test item"), admin_user, db)

    attempt = await start_self_test(
        StartSelfTestRequest(
            org_id=org.id,
            question_count=1,
            tags=["code"],
            assignment_types=[AssignmentTaskTypeEnum.CODE],
        ),
        regular_user,
        db,
    )

    contents = attempt.questions[0].contents
    assert "solution_code" not in contents
    assert "solutionCode" not in contents
    assert "show_solution_after_submit" not in contents
    assert "expectedStdout" not in contents["test_cases"][0]
    assert "expected_stdout" not in contents["test_cases"][0]
    assert "expectedStdout" not in contents["hidden_test_cases"][0]
    assert "expected_stdout" not in contents["hidden_test_cases"][0]


async def test_student_submits_self_test_and_record_is_visible_to_teacher(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)
    question_uuid = attempt.questions[0].attempt_question_uuid

    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(
            answers=[
                SubmitSelfTestAnswer(
                    attempt_question_uuid=question_uuid,
                    answer={"answer": "DEF"},
                )
            ]
        ),
        regular_user,
        db,
    )

    assert submitted.status == "SUBMITTED"
    assert submitted.score == 100
    assert submitted.max_score == 100
    assert submitted.percentage == 100
    assert submitted.questions[0].contents["correct_answers"] == ["def"]

    teacher_records = await list_org_self_test_attempts(org.id, admin_user, db)
    assert [record.attempt_uuid for record in teacher_records] == [attempt.attempt_uuid]
    assert teacher_records[0].questions[0].answer == {"answer": "DEF"}


async def test_teacher_can_review_self_test_for_grading_use(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)
    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(
            answers=[
                SubmitSelfTestAnswer(
                    attempt_question_uuid=attempt.questions[0].attempt_question_uuid,
                    answer={"answer": "wrong"},
                )
            ]
        ),
        regular_user,
        db,
    )
    assert submitted.score == 0

    reviewed = await review_self_test_attempt(
        attempt.attempt_uuid,
        ReviewSelfTestAttemptRequest(
            teacher_score=80,
            teacher_feedback="Manual credit for partial reasoning",
            counts_for_grade=True,
        ),
        admin_user,
        db,
    )

    assert reviewed.status == "REVIEWED"
    assert reviewed.teacher_score == 80
    assert reviewed.teacher_feedback == "Manual credit for partial reasoning"
    assert reviewed.counts_for_grade is True


async def test_student_cannot_read_another_students_self_test(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)
    other = User(
        id=77,
        username="other_student",
        first_name="Other",
        last_name="Student",
        email="other.student@test.com",
        password="hashed_password",
        user_uuid="user_other_student",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(other)
    await db.commit()
    db.add(
        UserOrganization(
            user_id=other.id,
            org_id=org.id,
            role_id=4,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
    )
    await db.commit()
    other_public = PublicUser(
        id=other.id,
        username=other.username,
        first_name=other.first_name,
        last_name=other.last_name,
        email=other.email,
        user_uuid=other.user_uuid,
    )

    with pytest.raises(HTTPException) as exc_info:
        await read_self_test_attempt(attempt.attempt_uuid, other_public, db)

    assert exc_info.value.status_code == 403


async def test_unsubmitted_self_test_cannot_be_reviewed(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)

    with pytest.raises(HTTPException) as exc_info:
        await review_self_test_attempt(
            attempt.attempt_uuid,
            ReviewSelfTestAttemptRequest(teacher_score=100),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
