from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.db.question_bank import QuestionBankItem, QuestionBankItemCreate, QuestionBankVisibilityEnum
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
    discard_started_self_test,
    list_my_self_test_attempts,
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


def _legacy_incomplete_bank_item(org_id: int, title: str):
    return QuestionBankItem(
        item_uuid=f"legacy_bad_{title.lower().replace(' ', '_')}",
        title=title,
        description="Legacy imported item without accepted answers",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "What keyword defines a Python function?",
            "correct_answers": [],
        },
        tags=["python", "self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _legacy_multi_correct_quiz_item(org_id: int, title: str):
    return QuestionBankItem(
        item_uuid=f"legacy_multi_{title.lower().replace(' ', '_')}",
        title=title,
        description="Legacy quiz with more than one correct option",
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionText": "哪兩個是水果？",
                    "questionUUID": "question_multi",
                    "options": [
                        {
                            "optionUUID": "option_apple",
                            "text": "蘋果",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_banana",
                            "text": "香蕉",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_desk",
                            "text": "書桌",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        },
        tags=["self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _legacy_string_boolean_quiz_item(org_id: int, title: str):
    return QuestionBankItem(
        item_uuid=f"legacy_string_bool_{title.lower().replace(' ', '_')}",
        title=title,
        description="Legacy quiz with string boolean answer keys",
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionText": "澳門位於哪個地區？",
                    "questionUUID": "question_string_bool",
                    "options": [
                        {
                            "optionUUID": "option_west",
                            "text": "珠江口西側",
                            "assigned_right_answer": "true",
                        },
                        {
                            "optionUUID": "option_north",
                            "text": "長江口北側",
                            "assigned_right_answer": "false",
                        },
                        {
                            "optionUUID": "option_east",
                            "text": "台灣海峽東側",
                            "assigned_right_answer": "0",
                        },
                    ],
                }
            ]
        },
        tags=["self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _legacy_ai_fallback_bank_item(org_id: int, title: str):
    return QuestionBankItem(
        item_uuid=f"legacy_fallback_{title.lower().replace(' ', '_')}",
        title=title,
        description="AI 備用題，請老師檢查後再發布。",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "這份練習主要學習哪個主題？",
            "correct_answers": ["本課主題"],
            "match_mode": "case_insensitive",
        },
        tags=["self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _legacy_contains_short_answer_item(org_id: int, title: str):
    return QuestionBankItem(
        item_uuid=f"legacy_contains_{title.lower().replace(' ', '_')}",
        title=title,
        description="Legacy short answer with loose contains matching",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "What keyword defines a Python function?",
            "correct_answers": ["def"],
            "match_mode": "contains",
        },
        tags=["python", "self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _quiz_bank_item(org_id: int, title: str):
    return QuestionBankItemCreate(
        title=title,
        description="澳門特別行政區位於哪個國家？",
        hint="留意國家名稱",
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionText": "澳門特別行政區位於哪個國家？",
                    "questionUUID": "question_macau_country",
                    "options": [
                        {
                            "optionUUID": "option_china",
                            "text": "中國",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_japan",
                            "text": "日本",
                            "assigned_right_answer": False,
                        },
                        {
                            "optionUUID": "option_france",
                            "text": "法國",
                            "assigned_right_answer": False,
                        },
                        {
                            "optionUUID": "option_canada",
                            "text": "加拿大",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        },
        tags=["macau", "self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        category_id=None,
        org_id=org_id,
        source_assignment_task_uuid=None,
        reference_file=None,
    )


def _form_bank_item(org_id: int, title: str):
    return QuestionBankItemCreate(
        title=title,
        description="澳門的官方語言之一是____。",
        hint="留意課本關鍵詞",
        assignment_type=AssignmentTaskTypeEnum.FORM,
        contents={
            "questions": [
                {
                    "questionText": "澳門的官方語言之一是____。",
                    "questionUUID": "question_macau_language",
                    "blanks": [
                        {
                            "blankUUID": "blank_language",
                            "placeholder": "填寫答案",
                            "correctAnswer": "中文",
                        }
                    ],
                }
            ]
        },
        tags=["macau", "self-test"],
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
    assert attempt.questions[0].assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER
    assert "correct_answers" not in attempt.questions[0].contents
    assert attempt.questions[0].answer == {}


async def test_student_started_self_test_hides_legacy_answer_key_fields(db, org, admin_user, regular_user):
    await create_question_bank_item(
        QuestionBankItemCreate(
            title="Legacy quiz answer fields",
            description="澳門位於哪個國家？",
            hint="國家名稱",
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            contents={
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "questionUUID": "question_legacy_quiz_answer",
                        "correctAnswer": "中國",
                        "answer": "中國",
                        "options": [
                            {
                                "optionUUID": "option_legacy_china",
                                "text": "中國",
                                "assigned_right_answer": True,
                                "is_correct": True,
                            },
                            {
                                "optionUUID": "option_legacy_japan",
                                "text": "日本",
                                "assigned_right_answer": False,
                                "is_correct": False,
                            },
                        ],
                    }
                ]
            },
            tags=["answer-leak"],
            difficulty="beginner",
            visibility=QuestionBankVisibilityEnum.ORG,
            category_id=None,
            org_id=org.id,
            source_assignment_task_uuid=None,
            reference_file=None,
        ),
        admin_user,
        db,
    )
    await create_question_bank_item(
        QuestionBankItemCreate(
            title="Legacy form answer fields",
            description="澳門的官方語言之一是____。",
            hint="官方語言",
            assignment_type=AssignmentTaskTypeEnum.FORM,
            contents={
                "questions": [
                    {
                        "questionText": "澳門的官方語言之一是____。",
                        "questionUUID": "question_legacy_form_answer",
                        "correctAnswer": "中文",
                        "answer": "中文",
                        "blanks": [
                            {
                                "blankUUID": "blank_legacy_language",
                                "placeholder": "填寫答案",
                                "correctAnswer": "中文",
                                "answer": "中文",
                            }
                        ],
                    }
                ]
            },
            tags=["answer-leak"],
            difficulty="beginner",
            visibility=QuestionBankVisibilityEnum.ORG,
            category_id=None,
            org_id=org.id,
            source_assignment_task_uuid=None,
            reference_file=None,
        ),
        admin_user,
        db,
    )
    await create_question_bank_item(
        QuestionBankItemCreate(
            title="Legacy short answer fields",
            description="澳門位於哪個國家？",
            hint="國家名稱",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={
                "prompt": "澳門位於哪個國家？",
                "correct_answers": ["中國"],
                "correctAnswer": "中國",
                "correct_answer": "中國",
                "answer": "中國",
                "match_mode": "case_insensitive",
            },
            tags=["answer-leak"],
            difficulty="beginner",
            visibility=QuestionBankVisibilityEnum.ORG,
            category_id=None,
            org_id=org.id,
            source_assignment_task_uuid=None,
            reference_file=None,
        ),
        admin_user,
        db,
    )

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=3, tags=["answer-leak"]),
        regular_user,
        db,
    )

    assert attempt.question_count == 3
    quiz = next(question for question in attempt.questions if question.assignment_type == AssignmentTaskTypeEnum.QUIZ)
    quiz_question = quiz.contents["questions"][0]
    assert "correctAnswer" not in quiz_question
    assert "answer" not in quiz_question
    assert all("assigned_right_answer" not in option for option in quiz_question["options"])
    assert all("is_correct" not in option for option in quiz_question["options"])

    form = next(question for question in attempt.questions if question.assignment_type == AssignmentTaskTypeEnum.FORM)
    form_question = form.contents["questions"][0]
    assert "correctAnswer" not in form_question
    assert "answer" not in form_question
    assert "correctAnswer" not in form_question["blanks"][0]
    assert "answer" not in form_question["blanks"][0]

    short_answer = next(question for question in attempt.questions if question.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER)
    assert "correct_answers" not in short_answer.contents
    assert "correctAnswer" not in short_answer.contents
    assert "correct_answer" not in short_answer.contents
    assert "answer" not in short_answer.contents


async def test_student_self_test_limits_question_count_to_simple_pilot_max(db, org, admin_user, regular_user):
    for index in range(8):
        await create_question_bank_item(_bank_item(org.id, f"Shared self-test item {index + 1}"), admin_user, db)

    request = StartSelfTestRequest(org_id=org.id, question_count=10, tags=["python"])

    assert request.question_count == 5

    attempt = await start_self_test(request, regular_user, db)

    assert attempt.question_count == 5
    assert len(attempt.questions) == 5
    assert attempt.max_score == 500


async def test_student_self_test_defaults_to_three_question_quick_practice(db, org, admin_user, regular_user):
    for index in range(5):
        await create_question_bank_item(_bank_item(org.id, f"Shared self-test default item {index + 1}"), admin_user, db)

    request = StartSelfTestRequest(org_id=org.id, tags=["python"])

    assert request.question_count == 3

    attempt = await start_self_test(request, regular_user, db)

    assert attempt.question_count == 3
    assert len(attempt.questions) == 3
    assert attempt.max_score == 300


async def test_student_self_test_accepts_comma_separated_tag_string(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared comma tag item"), admin_user, db)

    request = StartSelfTestRequest(org_id=org.id, question_count=1, tags="python，self-test")

    assert request.tags == ["python", "self-test"]

    attempt = await start_self_test(request, regular_user, db)

    assert attempt.status == "STARTED"
    assert attempt.question_count == 1


def test_start_self_test_request_cleans_duplicate_and_blank_tags():
    request = StartSelfTestRequest(
        org_id=1,
        tags=[" python ", "", "python", "self-test", "  "],
    )

    assert request.tags == ["python", "self-test"]


async def test_student_self_test_prefers_less_used_question_bank_items(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Least used self-test item"), admin_user, db)
    await create_question_bank_item(_bank_item(org.id, "Middle used self-test item"), admin_user, db)
    await create_question_bank_item(_bank_item(org.id, "Most used self-test item"), admin_user, db)

    items = (
        await db.execute(
            select(QuestionBankItem).where(QuestionBankItem.org_id == org.id)
        )
    ).scalars().all()
    usage_by_title = {
        "Least used self-test item": 0,
        "Middle used self-test item": 5,
        "Most used self-test item": 20,
    }
    for item in items:
        item.usage_count = usage_by_title[item.title]
        db.add(item)
    await db.commit()

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=2, tags=["python"]),
        regular_user,
        db,
    )

    assert {question.title for question in attempt.questions} == {
        "Least used self-test item",
        "Middle used self-test item",
    }


async def test_student_self_test_skips_incomplete_legacy_bank_items(db, org, admin_user, regular_user):
    db.add(_legacy_incomplete_bank_item(org.id, "Broken legacy self-test item"))
    await db.commit()
    await create_question_bank_item(_bank_item(org.id, "Good self-test item"), admin_user, db)

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["python"]),
        regular_user,
        db,
    )

    assert attempt.question_count == 1
    assert attempt.questions[0].title == "Good self-test item"


async def test_student_self_test_skips_legacy_multi_correct_quiz_items(db, org, admin_user, regular_user):
    db.add(_legacy_multi_correct_quiz_item(org.id, "Confusing legacy quiz"))
    await db.commit()
    await create_question_bank_item(_bank_item(org.id, "Good self-test item"), admin_user, db)

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["self-test"]),
        regular_user,
        db,
    )

    assert attempt.question_count == 1
    assert attempt.questions[0].title == "Good self-test item"


async def test_student_self_test_accepts_legacy_string_boolean_quiz_items(db, org, admin_user, regular_user):
    db.add(_legacy_string_boolean_quiz_item(org.id, "String boolean quiz"))
    await db.commit()

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["self-test"]),
        regular_user,
        db,
    )

    assert attempt.question_count == 1
    assert attempt.questions[0].title == "String boolean quiz"


async def test_student_self_test_skips_legacy_ai_fallback_bank_items(db, org, admin_user, regular_user):
    db.add(_legacy_ai_fallback_bank_item(org.id, "AI 備用短問答 1"))
    await db.commit()
    await create_question_bank_item(_bank_item(org.id, "Good self-test item"), admin_user, db)

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["self-test"]),
        regular_user,
        db,
    )

    assert attempt.question_count == 1
    assert attempt.questions[0].title == "Good self-test item"


async def test_student_self_test_skips_legacy_contains_short_answer_items(db, org, admin_user, regular_user):
    db.add(_legacy_contains_short_answer_item(org.id, "Loose contains short answer"))
    await db.commit()
    await create_question_bank_item(_bank_item(org.id, "Good self-test item"), admin_user, db)

    attempt = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["self-test"]),
        regular_user,
        db,
    )

    assert attempt.question_count == 1
    assert attempt.questions[0].title == "Good self-test item"


async def test_student_start_self_test_reuses_unsubmitted_attempt(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    first = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=1, tags=["python"]),
        regular_user,
        db,
    )
    second = await start_self_test(
        StartSelfTestRequest(org_id=org.id, question_count=10, tags=["missing-tag"]),
        regular_user,
        db,
    )

    assert second.attempt_uuid == first.attempt_uuid
    assert second.status == "STARTED"
    assert second.question_count == first.question_count
    assert "correct_answers" not in second.questions[0].contents

    teacher_records = await list_org_self_test_attempts(org.id, admin_user, db)
    assert [record.attempt_uuid for record in teacher_records] == [first.attempt_uuid]


async def test_student_cannot_start_code_self_test(db, org, admin_user, regular_user):
    with pytest.raises(HTTPException) as exc_info:
        await start_self_test(
            StartSelfTestRequest(
                org_id=org.id,
                question_count=1,
                tags=["code"],
                assignment_types=[AssignmentTaskTypeEnum.CODE],
            ),
            regular_user,
            db,
    )

    assert exc_info.value.status_code == 400
    assert "自測只支援簡單題型" in exc_info.value.detail


async def test_student_gets_traditional_chinese_message_when_self_test_bank_is_empty(db, org, regular_user):
    with pytest.raises(HTTPException) as exc_info:
        await start_self_test(
            StartSelfTestRequest(org_id=org.id, question_count=5),
            regular_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "暫時沒有可用的共享題庫題目" in exc_info.value.detail


async def test_student_gets_clear_message_when_self_test_scope_has_no_questions(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)

    with pytest.raises(HTTPException) as exc_info:
        await start_self_test(
            StartSelfTestRequest(org_id=org.id, question_count=5, tags=["不存在的範圍"]),
            regular_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "找不到符合練習範圍的題目" in exc_info.value.detail
    assert "清除練習範圍" in exc_info.value.detail


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
    assert submitted.questions[0].feedback == "答案正確。"

    teacher_records = await list_org_self_test_attempts(org.id, admin_user, db)
    assert [record.attempt_uuid for record in teacher_records] == [attempt.attempt_uuid]
    assert teacher_records[0].questions[0].answer == {"answer": "DEF"}


async def test_teacher_self_test_list_excludes_removed_org_members(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared removed student self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)
    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(
            answers=[
                SubmitSelfTestAnswer(
                    attempt_question_uuid=attempt.questions[0].attempt_question_uuid,
                    answer={"answer": "def"},
                )
            ]
        ),
        regular_user,
        db,
    )
    assert submitted.status == "SUBMITTED"

    teacher_records_before_remove = await list_org_self_test_attempts(org.id, admin_user, db)
    assert [record.attempt_uuid for record in teacher_records_before_remove] == [attempt.attempt_uuid]

    user_org = (
        await db.execute(
            select(UserOrganization).where(
                UserOrganization.user_id == regular_user.id,
                UserOrganization.org_id == org.id,
            )
        )
    ).scalars().first()
    await db.delete(user_org)
    await db.commit()

    teacher_records_after_remove = await list_org_self_test_attempts(org.id, admin_user, db)
    assert teacher_records_after_remove == []


async def test_student_self_test_uses_classroom_friendly_text_matching(db, org, admin_user, regular_user):
    await create_question_bank_item(_form_bank_item(org.id, "Shared Macau form item"), admin_user, db)
    await create_question_bank_item(
        QuestionBankItemCreate(
            title="Shared Macau short answer item",
            description="澳門在哪個國家？",
            hint="國家名稱",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={
                "prompt": "澳門在哪個國家？",
                "correct_answers": ["中國"],
                "match_mode": "case_insensitive",
            },
            tags=["macau", "self-test"],
            difficulty="beginner",
            visibility=QuestionBankVisibilityEnum.ORG,
            category_id=None,
            org_id=org.id,
            source_assignment_task_uuid=None,
            reference_file=None,
        ),
        admin_user,
        db,
    )
    attempt = await start_self_test(
        StartSelfTestRequest(
            org_id=org.id,
            question_count=2,
            tags=["macau"],
            assignment_types=[
                AssignmentTaskTypeEnum.FORM,
                AssignmentTaskTypeEnum.SHORT_ANSWER,
            ],
        ),
        regular_user,
        db,
    )

    answers = []
    for question in attempt.questions:
        if question.assignment_type == AssignmentTaskTypeEnum.FORM:
            answers.append(
                SubmitSelfTestAnswer(
                    attempt_question_uuid=question.attempt_question_uuid,
                    answer={
                        "submissions": [
                            {
                                "questionUUID": "question_macau_language",
                                "blankUUID": "blank_language",
                                "answer": " 中 文。 ",
                            }
                        ]
                    },
                )
            )
        else:
            answers.append(
                SubmitSelfTestAnswer(
                    attempt_question_uuid=question.attempt_question_uuid,
                    answer={"answer": "中 國。"},
                )
            )

    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(answers=answers),
        regular_user,
        db,
    )

    assert submitted.score == 200
    assert submitted.max_score == 200
    assert submitted.percentage == 100
    assert {question.assignment_type for question in submitted.questions} == {
        AssignmentTaskTypeEnum.FORM,
        AssignmentTaskTypeEnum.SHORT_ANSWER,
    }
    assert [question.feedback for question in submitted.questions] == ["答案正確。", "答案正確。"]


async def test_student_cannot_submit_incomplete_self_test(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)

    with pytest.raises(HTTPException) as exc_info:
        await submit_self_test(
            attempt.attempt_uuid,
            SubmitSelfTestRequest(
                answers=[
                    SubmitSelfTestAnswer(
                        attempt_question_uuid=attempt.questions[0].attempt_question_uuid,
                        answer={"answer": "   "},
                    )
                ]
            ),
            regular_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "請先完成所有題目再提交" in exc_info.value.detail

    still_started = await read_self_test_attempt(attempt.attempt_uuid, regular_user, db)
    assert still_started.status == "STARTED"
    assert still_started.score == 0
    assert still_started.questions[0].answer == {}


async def test_student_cannot_submit_self_test_quiz_when_false_strings_have_no_selection(
    db, org, admin_user, regular_user
):
    await create_question_bank_item(_quiz_bank_item(org.id, "Shared false-string quiz item"), admin_user, db)
    attempt = await start_self_test(
        StartSelfTestRequest(
            org_id=org.id,
            question_count=1,
            tags=["macau"],
            assignment_types=[AssignmentTaskTypeEnum.QUIZ],
        ),
        regular_user,
        db,
    )
    question = attempt.questions[0]
    quiz_question = question.contents["questions"][0]

    with pytest.raises(HTTPException) as exc_info:
        await submit_self_test(
            attempt.attempt_uuid,
            SubmitSelfTestRequest(
                answers=[
                    SubmitSelfTestAnswer(
                        attempt_question_uuid=question.attempt_question_uuid,
                        answer={
                            "submissions": [
                                {
                                    "questionUUID": quiz_question["questionUUID"],
                                    "optionUUID": option["optionUUID"],
                                    "answer": "false",
                                }
                                for option in quiz_question["options"]
                            ]
                        },
                    )
                ]
            ),
            regular_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "請先完成所有題目再提交" in exc_info.value.detail


async def test_student_can_discard_started_self_test_and_start_again(db, org, admin_user, regular_user):
    bank_item = await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    first_attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)

    item_after_start = (
        await db.execute(select(QuestionBankItem).where(QuestionBankItem.item_uuid == bank_item.item_uuid))
    ).scalars().first()
    assert item_after_start.usage_count == 1

    result = await discard_started_self_test(first_attempt.attempt_uuid, regular_user, db)

    assert result == {"ok": True}

    attempts_after_discard = await list_my_self_test_attempts(org.id, regular_user, db)
    assert attempts_after_discard == []

    item_after_discard = (
        await db.execute(select(QuestionBankItem).where(QuestionBankItem.item_uuid == bank_item.item_uuid))
    ).scalars().first()
    assert item_after_discard.usage_count == 0

    second_attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)

    assert second_attempt.status == "STARTED"
    assert second_attempt.attempt_uuid != first_attempt.attempt_uuid


async def test_student_cannot_discard_submitted_self_test_record(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared self-test item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)
    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(
            answers=[
                SubmitSelfTestAnswer(
                    attempt_question_uuid=attempt.questions[0].attempt_question_uuid,
                    answer={"answer": "def"},
                )
            ]
        ),
        regular_user,
        db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await discard_started_self_test(submitted.attempt_uuid, regular_user, db)

    assert exc_info.value.status_code == 400
    assert "已提交的自測記錄不能刪除" in exc_info.value.detail

    attempts = await list_my_self_test_attempts(org.id, regular_user, db)
    assert [item.attempt_uuid for item in attempts] == [submitted.attempt_uuid]


async def test_student_self_test_wrong_answer_gets_clear_feedback(db, org, admin_user, regular_user):
    await create_question_bank_item(_bank_item(org.id, "Shared wrong-feedback item"), admin_user, db)
    attempt = await start_self_test(StartSelfTestRequest(org_id=org.id, question_count=1), regular_user, db)

    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(
            answers=[
                SubmitSelfTestAnswer(
                    attempt_question_uuid=attempt.questions[0].attempt_question_uuid,
                    answer={"answer": "class"},
                )
            ]
        ),
        regular_user,
        db,
    )

    assert submitted.score == 0
    assert submitted.questions[0].feedback == "答案未符合參考答案，請查看提示或參考答案後再試。"


async def test_student_self_test_single_answer_quiz_wrong_choice_gets_zero(db, org, admin_user, regular_user):
    await create_question_bank_item(_quiz_bank_item(org.id, "Shared Macau quiz item"), admin_user, db)
    attempt = await start_self_test(
        StartSelfTestRequest(
            org_id=org.id,
            question_count=1,
            tags=["macau"],
            assignment_types=[AssignmentTaskTypeEnum.QUIZ],
        ),
        regular_user,
        db,
    )

    submitted = await submit_self_test(
        attempt.attempt_uuid,
        SubmitSelfTestRequest(
            answers=[
                SubmitSelfTestAnswer(
                    attempt_question_uuid=attempt.questions[0].attempt_question_uuid,
                    answer={
                        "submissions": [
                            {
                                "questionUUID": "question_macau_country",
                                "optionUUID": "option_china",
                                "answer": False,
                            },
                            {
                                "questionUUID": "question_macau_country",
                                "optionUUID": "option_japan",
                                "answer": True,
                            },
                            {
                                "questionUUID": "question_macau_country",
                                "optionUUID": "option_france",
                                "answer": False,
                            },
                            {
                                "questionUUID": "question_macau_country",
                                "optionUUID": "option_canada",
                                "answer": False,
                            },
                        ]
                    },
                )
            ]
        ),
        regular_user,
        db,
    )

    assert submitted.score == 0
    assert submitted.max_score == 100
    assert submitted.percentage == 0
    assert submitted.questions[0].feedback == "答案未符合參考答案，請查看提示或參考答案後再試。"


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
