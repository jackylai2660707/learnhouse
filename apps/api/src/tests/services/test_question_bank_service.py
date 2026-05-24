from datetime import datetime

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from src.db.courses.assignments import AssignmentTaskRead, AssignmentTaskTypeEnum
from src.db.question_bank import (
    AddQuestionBankItemToAssignmentRequest,
    QuestionBankCategoryCreate,
    QuestionBankItemCreate,
    QuestionBankVisibilityEnum,
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


async def test_question_bank_requires_dashboard_access(db, org, regular_user):
    with pytest.raises(HTTPException) as exc_info:
        await list_question_bank_categories(org.id, regular_user, db)

    assert exc_info.value.status_code == 403


async def test_question_bank_rejects_non_auto_gradable_items(db, org, admin_user):
    with pytest.raises(HTTPException) as exc_info:
        await create_question_bank_item(
            QuestionBankItemCreate(
                title="Upload essay",
                description="Upload a file",
                hint="",
                assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
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
