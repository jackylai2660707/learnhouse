from datetime import datetime

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from src.db.courses.assignments import Assignment, AssignmentTask, AssignmentTaskRead, AssignmentTaskTypeEnum, GradingTypeEnum
from src.db.question_bank import (
    AddQuestionBankItemToAssignmentRequest,
    QuestionBankCategoryCreate,
    QuestionBankItem,
    QuestionBankItemCreate,
    QuestionBankItemUpdate,
    QuestionBankVisibilityEnum,
    SaveAssignmentTaskToQuestionBankRequest,
)
from src.db.roles import Role, RoleTypeEnum
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.question_bank import (
    add_question_bank_item_to_assignment,
    create_question_bank_category,
    create_question_bank_item,
    list_question_bank_categories,
    list_question_bank_items,
    save_assignment_task_to_question_bank,
    update_question_bank_item,
)


pytestmark = pytest.mark.asyncio


async def _create_teacher(db, org, user_id: int, role_id: int, username: str) -> PublicUser:
    role = Role(
        id=role_id,
        name=f"Teacher {role_id}",
        org_id=org.id,
        role_type=RoleTypeEnum.TYPE_ORGANIZATION,
        role_uuid=f"role_teacher_{role_id}",
        rights={"dashboard": {"action_access": True}},
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    user = User(
        id=user_id,
        username=username,
        first_name="Teacher",
        last_name=str(user_id),
        email=f"{username}@test.com",
        password="hashed_password",
        user_uuid=f"user_{username}",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    db.add(
        UserOrganization(
            user_id=user.id,
            org_id=org.id,
            role_id=role.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
    )
    await db.commit()
    return PublicUser(
        id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        user_uuid=user.user_uuid,
    )


def _short_answer_item(org_id: int, category_id: int | None, visibility: QuestionBankVisibilityEnum):
    return QuestionBankItemCreate(
        title="Python function keyword",
        description="What keyword defines a Python function?",
        hint="It has three letters.",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "What keyword defines a Python function?",
            "correct_answers": ["def"],
            "match_mode": "case_insensitive",
            "explanation": "Python uses def.",
        },
        tags=["python", "Python", " basics "],
        difficulty="beginner",
        visibility=visibility,
        category_id=category_id,
        org_id=org_id,
        source_assignment_task_uuid=None,
        reference_file=None,
    )


async def test_question_bank_accumulates_classifies_and_shares_org_items(db, org, admin_user):
    other_teacher = await _create_teacher(db, org, user_id=21, role_id=21, username="other_teacher")
    category = await create_question_bank_category(
        QuestionBankCategoryCreate(
            name="Python Basics",
            description="Reusable intro Python questions",
            color="#111827",
            org_id=org.id,
        ),
        admin_user,
        db,
    )

    item = await create_question_bank_item(
        _short_answer_item(org.id, category.id, QuestionBankVisibilityEnum.ORG),
        admin_user,
        db,
    )

    teacher_categories = await list_question_bank_categories(org.id, other_teacher, db)
    teacher_items = await list_question_bank_items(
        org.id,
        other_teacher,
        db,
        category_id=category.id,
        tag="python",
    )

    assert [cat.id for cat in teacher_categories] == [category.id]
    assert [bank_item.item_uuid for bank_item in teacher_items] == [item.item_uuid]
    assert teacher_items[0].category_id == category.id
    assert teacher_items[0].tags == ["python", "basics"]


async def test_question_bank_category_trims_name_and_rejects_blank(db, org, admin_user):
    category = await create_question_bank_category(
        QuestionBankCategoryCreate(
            name="  小四數學  ",
            description="  分數與小數  ",
            color="",
            org_id=org.id,
        ),
        admin_user,
        db,
    )

    assert category.name == "小四數學"
    assert category.description == "分數與小數"
    assert category.color == "#111827"

    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_category(
            QuestionBankCategoryCreate(
                name="   ",
                description="",
                color="#111827",
                org_id=org.id,
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "分類名稱不能留空" in exc_info.value.detail


async def test_private_question_bank_items_only_show_to_owner(db, org, admin_user):
    other_teacher = await _create_teacher(db, org, user_id=22, role_id=22, username="second_teacher")
    private_item = await create_question_bank_item(
        _short_answer_item(org.id, None, QuestionBankVisibilityEnum.PRIVATE),
        admin_user,
        db,
    )

    owner_items = await list_question_bank_items(org.id, admin_user, db)
    teacher_items = await list_question_bank_items(org.id, other_teacher, db)

    assert private_item.item_uuid in {item.item_uuid for item in owner_items}
    assert private_item.item_uuid not in {item.item_uuid for item in teacher_items}


async def test_question_bank_item_trims_title_and_rejects_blank(db, org, admin_user):
    item = await create_question_bank_item(
        QuestionBankItemCreate(
            title="  分數   比較題  ",
            description="  比較兩個分數  ",
            hint="  先看分母  ",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={
                "prompt": "二分之一和三分之一，哪個較大？",
                "correct_answers": ["二分之一"],
                "match_mode": "case_insensitive",
            },
            tags=[],
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

    assert item.title == "分數 比較題"
    assert item.description == "比較兩個分數"
    assert item.hint == "先看分母"

    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="   ",
                description="",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
                contents={
                    "prompt": "澳門的英文是甚麼？",
                    "correct_answers": ["Macau"],
                    "match_mode": "case_insensitive",
                },
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "題目名稱不能留空" in exc_info.value.detail


async def test_rejects_updating_question_bank_item_to_blank_title(db, org, admin_user):
    item = await create_question_bank_item(
        _short_answer_item(org.id, None, QuestionBankVisibilityEnum.ORG),
        admin_user,
        db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_question_bank_item(
            item.item_uuid,
            QuestionBankItemUpdate(title="   "),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "題目名稱不能留空" in exc_info.value.detail


async def test_question_bank_requires_dashboard_access(db, org, regular_user):
    with pytest.raises(HTTPException) as exc_info:
        await list_question_bank_categories(org.id, regular_user, db)

    assert exc_info.value.status_code == 403


async def test_question_bank_rejects_complex_items(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="程式題",
                description="需要外部判題服務",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.CODE,
                contents={},
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "題庫只支援簡單題型" in exc_info.value.detail


async def test_question_bank_rejects_form_answer_that_is_too_long_for_auto_grading(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="水循環填空",
                description="請填寫水循環中的一個階段。",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.FORM,
                contents={
                    "questions": [
                        {
                            "questionText": "請填寫水循環中的一個階段。",
                            "blanks": [
                                {
                                    "correctAnswer": "水蒸發後遇冷凝結成雲，最後再以下雨方式回到地面。",
                                }
                            ],
                        }
                    ]
                },
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "填空答案太長" in exc_info.value.detail


async def test_question_bank_rejects_subjective_short_answer_for_auto_grading(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="水循環解釋題",
                description="請解釋水循環的過程。",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
                contents={
                    "prompt": "請解釋水循環的過程。",
                    "correct_answers": ["蒸發、凝結、降水"],
                    "match_mode": "case_insensitive",
                },
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "主觀問答題" in exc_info.value.detail


async def test_rejects_creating_ai_fallback_question_bank_item(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="AI 備用填空題 1",
                description="請完成有關「澳門地理」的基礎填空。",
                hint="AI 備用題，請老師檢查後再發布。",
                assignment_type=AssignmentTaskTypeEnum.FORM,
                contents={
                    "questions": [
                        {
                            "questionText": "這份練習的主題是____。",
                            "blanks": [{"correctAnswer": "澳門地理"}],
                        }
                    ]
                },
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "AI 備用題不能存入題庫" in exc_info.value.detail


async def test_rejects_updating_question_bank_item_into_ai_fallback(db, org, admin_user):
    item = await create_question_bank_item(
        _short_answer_item(org.id, None, QuestionBankVisibilityEnum.ORG),
        admin_user,
        db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_question_bank_item(
            item.item_uuid,
            QuestionBankItemUpdate(
                title="澳門地理選擇題",
                contents={
                    "questions": [
                        {
                            "questionText": "這份練習主要圍繞哪個主題？",
                            "options": [
                                {"text": "澳門地理", "assigned_right_answer": True},
                                {"text": "課外活動", "assigned_right_answer": False},
                            ],
                        }
                    ]
                },
                assignment_type=AssignmentTaskTypeEnum.QUIZ,
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "AI 備用題不能存入題庫" in exc_info.value.detail


async def test_rejects_saving_ai_fallback_assignment_task_to_question_bank(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
):
    assignment = Assignment(
        id=9901,
        title="澳門地理練習",
        description="",
        due_date="2030-01-01",
        grading_type=GradingTypeEnum.NUMERIC,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_qbank_fallback_guard",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    assignment_task = AssignmentTask(
        id=9902,
        title="AI 備用選擇題 1",
        description="",
        hint="AI 備用題，請老師檢查後再發布。",
        reference_file=None,
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionText": "這份練習主要圍繞哪個主題？",
                    "questionUUID": "question_ai_fallback",
                    "options": [
                        {
                            "optionUUID": "option_topic",
                            "text": "澳門地理",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_activity",
                            "text": "課外活動",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        },
        max_grade_value=100,
        assignment_id=assignment.id,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_task_uuid="assignmenttask_qbank_fallback_guard",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    db.add(assignment_task)
    await db.commit()

    with patch("src.services.question_bank.check_resource_access", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await save_assignment_task_to_question_bank(
                mock_request,
                SaveAssignmentTaskToQuestionBankRequest(
                    assignment_task_uuid=assignment_task.assignment_task_uuid,
                ),
                admin_user,
                db,
            )

    assert exc_info.value.status_code == 400
    assert "AI 備用題不能存入題庫" in exc_info.value.detail


async def test_question_bank_normalizes_simple_quiz_contents(db, org, admin_user):
    item = await create_question_bank_item(
        QuestionBankItemCreate(
            title="澳門地理選擇題",
            description="澳門位於哪個地區？",
            hint="留意珠江口西側。",
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            contents={
                "questions": [
                    {
                        "question": "澳門位於哪個地區？",
                        "options": [
                            {"text": "珠江口西側", "correct": True},
                            {"text": "長江口北側"},
                            {"text": "台灣海峽東側"},
                        ],
                    }
                ]
            },
            tags=["地理"],
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

    question = item.contents["questions"][0]
    assert question["questionText"] == "澳門位於哪個地區？"
    assert question["questionUUID"].startswith("question_")
    assert question["options"][0]["optionUUID"].startswith("option_")
    assert question["options"][0]["assigned_right_answer"] is True


async def test_question_bank_normalizes_string_boolean_quiz_answer_keys(db, org, admin_user):
    item = await create_question_bank_item(
        QuestionBankItemCreate(
            title="字串答案鍵選擇題",
            description="澳門位於哪個地區？",
            hint="留意珠江口西側。",
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            contents={
                "questions": [
                    {
                        "question": "澳門位於哪個地區？",
                        "options": [
                            {"text": "珠江口西側", "correct": "true"},
                            {"text": "長江口北側", "correct": "false"},
                            {"text": "台灣海峽東側", "assigned_right_answer": "0"},
                        ],
                    }
                ]
            },
            tags=["地理"],
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

    options = item.contents["questions"][0]["options"]
    assert [option["assigned_right_answer"] for option in options] == [True, False, False]


async def test_question_bank_rejects_quiz_without_correct_option(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="壞選擇題",
                description="沒有答案鍵",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.QUIZ,
                contents={
                    "questions": [
                        {
                            "questionText": "哪一個是正確答案？",
                            "options": [
                                {"text": "選項 A"},
                                {"text": "選項 B"},
                            ],
                        }
                    ]
                },
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "每條選擇題必須剛好有一個正確選項" in exc_info.value.detail


async def test_question_bank_rejects_quiz_with_multiple_correct_options(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="多答案選擇題",
                description="對學生來說太容易混淆",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.QUIZ,
                contents={
                    "questions": [
                        {
                            "questionText": "哪兩個是水果？",
                            "options": [
                                {"text": "蘋果", "correct": True},
                                {"text": "香蕉", "correct": True},
                                {"text": "書桌"},
                            ],
                        }
                    ]
                },
                tags=[],
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

    assert exc_info.value.status_code == 400
    assert "每條選擇題必須剛好有一個正確選項" in exc_info.value.detail


async def test_question_bank_short_answer_does_not_keep_regex_match_mode(db, org, admin_user):
    item = await create_question_bank_item(
        QuestionBankItemCreate(
            title="簡答題",
            description="澳門的英文是甚麼？",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={
                "prompt": "澳門的英文是甚麼？",
                "correct_answers": ["Macau", "Macao"],
                "match_mode": "regex",
            },
            tags=[],
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

    assert item.contents["match_mode"] == "case_insensitive"


async def test_question_bank_short_answer_does_not_keep_contains_match_mode(db, org, admin_user):
    item = await create_question_bank_item(
        QuestionBankItemCreate(
            title="簡答題",
            description="澳門的英文是甚麼？",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={
                "prompt": "澳門的英文是甚麼？",
                "correct_answers": ["Macau", "Macao"],
                "match_mode": "contains",
            },
            tags=[],
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

    assert item.contents["match_mode"] == "case_insensitive"


async def test_rejects_reusing_incomplete_legacy_question_bank_item(db, org, admin_user, mock_request):
    legacy_item = QuestionBankItem(
        item_uuid="qbankitem_incomplete_legacy_short_answer",
        title="壞短問答",
        description="舊資料缺少可接受答案",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "澳門的英文是甚麼？",
            "correct_answers": [],
        },
        tags=["legacy"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org.id,
        created_by_user_id=admin_user.id,
        updated_by_user_id=admin_user.id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(legacy_item)
    await db.commit()

    with patch("src.services.question_bank.create_assignment_task", new=AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc_info:
            await add_question_bank_item_to_assignment(
                mock_request,
                legacy_item.item_uuid,
                AddQuestionBankItemToAssignmentRequest(assignment_uuid="assignment_target").assignment_uuid,
                admin_user,
                db,
            )

    assert exc_info.value.status_code == 400
    assert "短問答需要題目和至少一個可接受答案" in exc_info.value.detail
    mocked.assert_not_called()


async def test_rejects_reusing_question_bank_item_with_grade_mismatch(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
):
    item_create = _short_answer_item(org.id, None, QuestionBankVisibilityEnum.ORG)
    item_create.grade_level = "小三"
    item = await create_question_bank_item(item_create, admin_user, db)
    assignment = Assignment(
        id=9911,
        title="小四澳門地理練習",
        description="",
        due_date="2030-01-01",
        grading_type=GradingTypeEnum.PERCENTAGE,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_qbank_grade_mismatch",
        grade_level="小四",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    with patch("src.services.question_bank.create_assignment_task", new=AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc_info:
            await add_question_bank_item_to_assignment(
                mock_request,
                item.item_uuid,
                assignment.assignment_uuid,
                admin_user,
                db,
            )

    assert exc_info.value.status_code == 400
    assert "這題適合「小三」" in exc_info.value.detail
    assert "作業年級是「小四」" in exc_info.value.detail
    mocked.assert_not_called()


async def test_reused_question_keeps_auto_gradable_contents_and_tracks_usage(db, org, admin_user, mock_request):
    item = await create_question_bank_item(
        _short_answer_item(org.id, None, QuestionBankVisibilityEnum.ORG),
        admin_user,
        db,
    )

    created_task = AssignmentTaskRead(
        id=99,
        assignment_task_uuid="assignmenttask_reused",
        title=item.title,
        description=item.description,
        hint=item.hint,
        reference_file=None,
        assignment_type=item.assignment_type,
        contents=item.contents,
        max_grade_value=100,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )

    with patch("src.services.question_bank.create_assignment_task", new=AsyncMock(return_value=created_task)) as mocked:
        response = await add_question_bank_item_to_assignment(
            mock_request,
            item.item_uuid,
            AddQuestionBankItemToAssignmentRequest(assignment_uuid="assignment_target").assignment_uuid,
            admin_user,
            db,
        )

    task_create = mocked.call_args.args[2]
    assert task_create.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER
    assert task_create.contents["correct_answers"] == ["def"]
    assert response.item.usage_count == 1
    assert response.task.contents["match_mode"] == "case_insensitive"
