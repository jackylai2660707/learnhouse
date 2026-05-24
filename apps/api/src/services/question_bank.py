import os
import shutil
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import Assignment, AssignmentTask, AssignmentTaskCreate, AssignmentTaskRead
from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.question_bank import (
    AddQuestionBankItemToAssignmentResponse,
    QuestionBankCategory,
    QuestionBankCategoryCreate,
    QuestionBankCategoryRead,
    QuestionBankCategoryUpdate,
    QuestionBankItem,
    QuestionBankItemCreate,
    QuestionBankItemRead,
    QuestionBankItemUpdate,
    QuestionBankVisibilityEnum,
    SaveAssignmentTaskToQuestionBankRequest,
)
from src.db.users import PublicUser
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import is_org_admin, require_org_role_permission
from src.security.rbac import AccessAction, check_resource_access
from src.services.courses.activities.assignments import create_assignment_task


AUTO_GRADABLE_BANK_TYPES = {"QUIZ", "FORM", "CODE", "SHORT_ANSWER", "NUMBER_ANSWER"}


def _now() -> str:
    return str(datetime.now())


def _normalize_tags(tags: list[str] | None) -> list[str]:
    clean: list[str] = []
    for tag in tags or []:
        value = str(tag).strip()
        if value and value.lower() not in {t.lower() for t in clean}:
            clean.append(value[:40])
    return clean[:20]


def _assignment_type_value(assignment_type) -> str:
    return getattr(assignment_type, "value", str(assignment_type))


def _ensure_auto_gradable_type(assignment_type) -> None:
    if _assignment_type_value(assignment_type) not in AUTO_GRADABLE_BANK_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question bank only supports auto-gradable assignment task types",
        )


async def _get_org_or_404(org_id: int, db_session: AsyncSession) -> Organization:
    org = (await db_session.execute(select(Organization).where(Organization.id == org_id))).scalars().first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


async def _ensure_question_bank_access(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    await require_org_role_permission(user_id, org_id, db_session, "dashboard", "action_access")


async def _ensure_category_access(
    category_id: int | None,
    org_id: int,
    db_session: AsyncSession,
) -> None:
    if category_id is None:
        return
    category = (
        await db_session.execute(
            select(QuestionBankCategory).where(
                QuestionBankCategory.id == category_id,
                QuestionBankCategory.org_id == org_id,
            )
        )
    ).scalars().first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank category not found")


async def _ensure_can_modify_item(
    item: QuestionBankItem,
    user_id: int,
    db_session: AsyncSession,
) -> None:
    if item.created_by_user_id == user_id:
        return
    if await is_org_admin(user_id, item.org_id, db_session):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own bank items")


async def _copy_reference_file_for_reused_task(
    item: QuestionBankItem,
    target_task: AssignmentTaskRead,
    target_assignment_uuid: str,
    db_session: AsyncSession,
) -> str:
    if not item.reference_file or not item.source_assignment_task_uuid:
        return ""

    source_task = (
        await db_session.execute(
            select(AssignmentTask).where(
                AssignmentTask.assignment_task_uuid == item.source_assignment_task_uuid
            )
        )
    ).scalars().first()
    target_task_row = (
        await db_session.execute(
            select(AssignmentTask).where(
                AssignmentTask.assignment_task_uuid == target_task.assignment_task_uuid
            )
        )
    ).scalars().first()
    if not source_task or not target_task_row:
        return ""

    source_assignment = (
        await db_session.execute(select(Assignment).where(Assignment.id == source_task.assignment_id))
    ).scalars().first()
    target_assignment = (
        await db_session.execute(select(Assignment).where(Assignment.assignment_uuid == target_assignment_uuid))
    ).scalars().first()
    source_course = (
        await db_session.execute(select(Course).where(Course.id == source_task.course_id))
    ).scalars().first()
    target_course = (
        await db_session.execute(select(Course).where(Course.id == target_task_row.course_id))
    ).scalars().first()
    source_activity = (
        await db_session.execute(select(Activity).where(Activity.id == source_task.activity_id))
    ).scalars().first()
    target_activity = (
        await db_session.execute(select(Activity).where(Activity.id == target_task_row.activity_id))
    ).scalars().first()
    org = (
        await db_session.execute(select(Organization).where(Organization.id == item.org_id))
    ).scalars().first()
    if not all([source_assignment, target_assignment, source_course, target_course, source_activity, target_activity, org]):
        return ""

    source_path = os.path.join(
        "content",
        "orgs",
        org.org_uuid,
        "courses",
        source_course.course_uuid,
        "activities",
        source_activity.activity_uuid,
        "assignments",
        source_assignment.assignment_uuid,
        "tasks",
        source_task.assignment_task_uuid,
        item.reference_file,
    )
    target_dir = os.path.join(
        "content",
        "orgs",
        org.org_uuid,
        "courses",
        target_course.course_uuid,
        "activities",
        target_activity.activity_uuid,
        "assignments",
        target_assignment.assignment_uuid,
        "tasks",
        target_task.assignment_task_uuid,
    )
    if not os.path.isfile(source_path):
        return ""

    os.makedirs(target_dir, exist_ok=True)
    shutil.copyfile(source_path, os.path.join(target_dir, item.reference_file))
    target_task_row.reference_file = item.reference_file
    target_task_row.update_date = _now()
    db_session.add(target_task_row)
    await db_session.commit()
    await db_session.refresh(target_task_row)
    return item.reference_file


async def list_question_bank_categories(
    org_id: int,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> list[QuestionBankCategoryRead]:
    await _get_org_or_404(org_id, db_session)
    await _ensure_question_bank_access(resolve_acting_user_id(current_user), org_id, db_session)
    categories = (
        await db_session.execute(
            select(QuestionBankCategory)
            .where(QuestionBankCategory.org_id == org_id)
            .order_by(QuestionBankCategory.name.asc())
        )
    ).scalars().all()
    return [QuestionBankCategoryRead.model_validate(category) for category in categories]


async def create_question_bank_category(
    category_object: QuestionBankCategoryCreate,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> QuestionBankCategoryRead:
    await _get_org_or_404(category_object.org_id, db_session)
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, category_object.org_id, db_session)
    await _ensure_category_access(category_object.parent_category_id, category_object.org_id, db_session)

    category = QuestionBankCategory(**category_object.model_dump())
    category.category_uuid = f"qbankcat_{uuid4()}"
    category.created_by_user_id = acting_user_id
    category.creation_date = _now()
    category.update_date = _now()
    db_session.add(category)
    await db_session.commit()
    await db_session.refresh(category)
    return QuestionBankCategoryRead.model_validate(category)


async def update_question_bank_category(
    category_uuid: str,
    category_object: QuestionBankCategoryUpdate,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> QuestionBankCategoryRead:
    category = (
        await db_session.execute(
            select(QuestionBankCategory).where(QuestionBankCategory.category_uuid == category_uuid)
        )
    ).scalars().first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank category not found")

    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, category.org_id, db_session)
    if category.created_by_user_id != acting_user_id and not await is_org_admin(acting_user_id, category.org_id, db_session):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own categories")

    update_data = category_object.model_dump(exclude_unset=True)
    if "parent_category_id" in update_data:
        await _ensure_category_access(update_data["parent_category_id"], category.org_id, db_session)
    for key, value in update_data.items():
        setattr(category, key, value)
    category.update_date = _now()
    db_session.add(category)
    await db_session.commit()
    await db_session.refresh(category)
    return QuestionBankCategoryRead.model_validate(category)


async def delete_question_bank_category(
    category_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
):
    category = (
        await db_session.execute(
            select(QuestionBankCategory).where(QuestionBankCategory.category_uuid == category_uuid)
        )
    ).scalars().first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank category not found")
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, category.org_id, db_session)
    if category.created_by_user_id != acting_user_id and not await is_org_admin(acting_user_id, category.org_id, db_session):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own categories")

    await db_session.delete(category)
    await db_session.commit()
    return {"message": "Question bank category deleted"}


async def list_question_bank_items(
    org_id: int,
    current_user: PublicUser,
    db_session: AsyncSession,
    q: Optional[str] = None,
    category_id: Optional[int] = None,
    assignment_type: Optional[str] = None,
    difficulty: Optional[str] = None,
    visibility: Optional[QuestionBankVisibilityEnum] = None,
    tag: Optional[str] = None,
) -> list[QuestionBankItemRead]:
    await _get_org_or_404(org_id, db_session)
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, org_id, db_session)

    statement = select(QuestionBankItem).where(
        QuestionBankItem.org_id == org_id,
        or_(
            QuestionBankItem.visibility == QuestionBankVisibilityEnum.ORG,
            QuestionBankItem.created_by_user_id == acting_user_id,
        ),
    )
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(QuestionBankItem.title.ilike(pattern), QuestionBankItem.description.ilike(pattern))
        )
    if category_id is not None:
        statement = statement.where(QuestionBankItem.category_id == category_id)
    if assignment_type:
        statement = statement.where(QuestionBankItem.assignment_type == assignment_type)
    if difficulty:
        statement = statement.where(QuestionBankItem.difficulty == difficulty)
    if visibility:
        statement = statement.where(QuestionBankItem.visibility == visibility)

    items = (await db_session.execute(statement.order_by(QuestionBankItem.update_date.desc()))).scalars().all()
    if tag:
        needle = tag.strip().lower()
        items = [item for item in items if needle in [str(t).lower() for t in (item.tags or [])]]
    return [QuestionBankItemRead.model_validate(item) for item in items]


async def create_question_bank_item(
    item_object: QuestionBankItemCreate,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> QuestionBankItemRead:
    await _get_org_or_404(item_object.org_id, db_session)
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, item_object.org_id, db_session)
    await _ensure_category_access(item_object.category_id, item_object.org_id, db_session)
    _ensure_auto_gradable_type(item_object.assignment_type)

    item = QuestionBankItem(**item_object.model_dump())
    item.item_uuid = f"qbankitem_{uuid4()}"
    item.tags = _normalize_tags(item.tags)
    item.created_by_user_id = acting_user_id
    item.updated_by_user_id = acting_user_id
    item.creation_date = _now()
    item.update_date = _now()
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return QuestionBankItemRead.model_validate(item)


async def update_question_bank_item(
    item_uuid: str,
    item_object: QuestionBankItemUpdate,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> QuestionBankItemRead:
    item = (
        await db_session.execute(select(QuestionBankItem).where(QuestionBankItem.item_uuid == item_uuid))
    ).scalars().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank item not found")
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, item.org_id, db_session)
    await _ensure_can_modify_item(item, acting_user_id, db_session)

    update_data = item_object.model_dump(exclude_unset=True)
    if "category_id" in update_data:
        await _ensure_category_access(update_data["category_id"], item.org_id, db_session)
    if "assignment_type" in update_data:
        _ensure_auto_gradable_type(update_data["assignment_type"])
    if "tags" in update_data:
        update_data["tags"] = _normalize_tags(update_data["tags"])
    for key, value in update_data.items():
        setattr(item, key, value)
    item.updated_by_user_id = acting_user_id
    item.update_date = _now()
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return QuestionBankItemRead.model_validate(item)


async def delete_question_bank_item(
    item_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
):
    item = (
        await db_session.execute(select(QuestionBankItem).where(QuestionBankItem.item_uuid == item_uuid))
    ).scalars().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank item not found")
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, item.org_id, db_session)
    await _ensure_can_modify_item(item, acting_user_id, db_session)
    await db_session.delete(item)
    await db_session.commit()
    return {"message": "Question bank item deleted"}


async def save_assignment_task_to_question_bank(
    request: Request,
    save_request: SaveAssignmentTaskToQuestionBankRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> QuestionBankItemRead:
    task = (
        await db_session.execute(
            select(AssignmentTask).where(
                AssignmentTask.assignment_task_uuid == save_request.assignment_task_uuid
            )
        )
    ).scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment task not found")

    assignment = (await db_session.execute(select(Assignment).where(Assignment.id == task.assignment_id))).scalars().first()
    course = (await db_session.execute(select(Course).where(Course.id == task.course_id))).scalars().first()
    if not assignment or not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent assignment or course not found")

    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, task.org_id, db_session)
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)
    await _ensure_category_access(save_request.category_id, task.org_id, db_session)
    _ensure_auto_gradable_type(task.assignment_type)

    item = QuestionBankItem(
        title=task.title,
        description=task.description,
        hint=task.hint,
        reference_file=task.reference_file,
        assignment_type=task.assignment_type,
        contents=task.contents or {},
        tags=_normalize_tags(save_request.tags),
        difficulty=save_request.difficulty,
        visibility=save_request.visibility,
        category_id=save_request.category_id,
        org_id=task.org_id,
        source_assignment_task_uuid=task.assignment_task_uuid,
        item_uuid=f"qbankitem_{uuid4()}",
        created_by_user_id=acting_user_id,
        updated_by_user_id=acting_user_id,
        creation_date=_now(),
        update_date=_now(),
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return QuestionBankItemRead.model_validate(item)


async def add_question_bank_item_to_assignment(
    request: Request,
    item_uuid: str,
    assignment_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> AddQuestionBankItemToAssignmentResponse:
    item = (
        await db_session.execute(select(QuestionBankItem).where(QuestionBankItem.item_uuid == item_uuid))
    ).scalars().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank item not found")

    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, item.org_id, db_session)
    if item.visibility == QuestionBankVisibilityEnum.PRIVATE and item.created_by_user_id != acting_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This question is private")
    _ensure_auto_gradable_type(item.assignment_type)

    task = await create_assignment_task(
        request,
        assignment_uuid,
        AssignmentTaskCreate(
            title=item.title,
            description=item.description,
            hint=item.hint,
            reference_file="",
            assignment_type=item.assignment_type,
            contents=item.contents or {},
            max_grade_value=100,
        ),
        current_user,
        db_session,
    )

    item.usage_count = int(item.usage_count or 0) + 1
    item.update_date = _now()
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    copied_reference_file = await _copy_reference_file_for_reused_task(item, task, assignment_uuid, db_session)
    if copied_reference_file:
        task.reference_file = copied_reference_file

    return AddQuestionBankItemToAssignmentResponse(
        task=AssignmentTaskRead.model_validate(task),
        item=QuestionBankItemRead.model_validate(item),
    )
