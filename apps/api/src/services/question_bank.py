import json
import os
import shutil
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskCreate,
    AssignmentTaskRead,
    AssignmentTaskTypeEnum,
)
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
from src.services.courses.activities.assignments import (
    _simple_task_publish_error_for_values,
    create_assignment_task,
)


SIMPLE_BANK_TYPE_VALUES = ("QUIZ", "FORM", "SHORT_ANSWER")
SIMPLE_BANK_TYPES = set(SIMPLE_BANK_TYPE_VALUES)


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


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def _clean_text(value, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _clean_display_text(value, default: str = "") -> str:
    return " ".join(_clean_text(value, default).split())


def _clean_required_text(value, field_label: str) -> str:
    text = _clean_display_text(value)
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_label}不能留空。",
        )
    return text


def _coerce_answer_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "correct", "right", "是", "對", "正確"}:
            return True
        if normalized in {"false", "0", "no", "n", "incorrect", "wrong", "否", "錯", "錯誤", ""}:
            return False
    return False


def _ensure_auto_gradable_type(assignment_type) -> None:
    if _assignment_type_value(assignment_type) not in SIMPLE_BANK_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="題庫只支援簡單題型：選擇題、填空題、短問答。",
        )


def _is_ai_fallback_task_payload(title, description, hint, contents) -> bool:
    text = " ".join(
        [
            _clean_text(title),
            _clean_text(description),
            _clean_text(hint),
            json.dumps(contents or {}, ensure_ascii=False),
        ]
    )
    return (
        "AI 備用題" in text
        or "AI 備用" in text
        or "請老師檢查後再發布" in text
        or "這份練習的主題是" in text
        or "這份練習主要學習哪個主題" in text
        or "這份練習主要圍繞哪個主題" in text
    )


def _ensure_not_ai_fallback_task(title, description, hint, contents) -> None:
    if _is_ai_fallback_task_payload(title, description, hint, contents):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI 備用題不能存入題庫。請先改成正式課堂題目和答案，再手動存入題庫。",
        )


def _ensure_question_bank_item_publish_ready(
    title,
    assignment_type,
    contents,
    description="",
    hint="",
) -> None:
    publish_error = _simple_task_publish_error_for_values(
        title,
        assignment_type,
        contents,
        description,
        hint,
    )
    if publish_error:
        _invalid_contents(publish_error)


def _normalize_grade_match_value(value) -> str:
    return "".join(str(value or "").strip().lower().split())


def _ensure_question_bank_grade_matches_assignment(
    item: QuestionBankItem,
    assignment: Assignment,
) -> None:
    item_grade = _normalize_grade_match_value(item.grade_level)
    assignment_grade = _normalize_grade_match_value(assignment.grade_level)
    if not item_grade or not assignment_grade or item_grade == assignment_grade:
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"這題適合「{item.grade_level}」，但這份作業年級是「{assignment.grade_level}」。"
            "請改選同年級題目，或先修正題庫/作業年級。"
        ),
    )


def _invalid_contents(message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"題目內容無法自動批改：{message}",
    )


def _normalize_question_bank_contents(assignment_type, contents: dict | None) -> dict:
    type_value = _assignment_type_value(assignment_type)
    data = contents or {}
    if not isinstance(data, dict):
        _invalid_contents("內容格式必須是物件。")

    if type_value == AssignmentTaskTypeEnum.QUIZ.value:
        questions = data.get("questions") if isinstance(data.get("questions"), list) else []
        normalized_questions = []
        for question in questions[:6]:
            if not isinstance(question, dict):
                continue
            question_text = _clean_text(question.get("questionText") or question.get("question"))
            options = question.get("options") if isinstance(question.get("options"), list) else []
            if not question_text or len(options) < 2:
                continue
            normalized_options = []
            for option in options[:6]:
                if not isinstance(option, dict):
                    continue
                text = _clean_text(option.get("text") or option.get("option") or option.get("label"))
                if not text:
                    continue
                normalized_options.append(
                    {
                        "optionUUID": option.get("optionUUID") or _short_id("option"),
                        "text": text,
                        "fileID": option.get("fileID") or "",
                        "type": option.get("type") or "text",
                        "assigned_right_answer": _coerce_answer_bool(
                            option.get("assigned_right_answer", option.get("correct", False))
                        ),
                    }
                )
            if len(normalized_options) < 2:
                continue
            correct_count = sum(1 for option in normalized_options if option["assigned_right_answer"])
            if correct_count != 1:
                _invalid_contents("每條選擇題必須剛好有一個正確選項。")
            normalized_questions.append(
                {
                    "questionText": question_text,
                    "questionUUID": question.get("questionUUID") or _short_id("question"),
                    "options": normalized_options,
                }
            )
        if not normalized_questions:
            _invalid_contents("選擇題至少需要一條題目，並且有兩個或以上選項。")
        return {"questions": normalized_questions}

    if type_value == AssignmentTaskTypeEnum.FORM.value:
        questions = data.get("questions") if isinstance(data.get("questions"), list) else []
        normalized_questions = []
        for question in questions[:6]:
            if not isinstance(question, dict):
                continue
            question_text = _clean_text(question.get("questionText") or question.get("question"))
            blanks = question.get("blanks") if isinstance(question.get("blanks"), list) else []
            if not question_text or not blanks:
                continue
            normalized_blanks = []
            for blank in blanks[:6]:
                if not isinstance(blank, dict):
                    continue
                correct_answer = _clean_text(blank.get("correctAnswer") or blank.get("correct_answer") or blank.get("answer"))
                if not correct_answer:
                    continue
                normalized_blanks.append(
                    {
                        "blankUUID": blank.get("blankUUID") or _short_id("blank"),
                        "placeholder": _clean_text(blank.get("placeholder"), "填寫答案"),
                        "correctAnswer": correct_answer,
                        "hint": _clean_text(blank.get("hint")),
                    }
                )
            if normalized_blanks:
                normalized_questions.append(
                    {
                        "questionText": question_text,
                        "questionUUID": question.get("questionUUID") or _short_id("question"),
                        "blanks": normalized_blanks,
                    }
                )
        if not normalized_questions:
            _invalid_contents("填空題至少需要一條題目，並且每個填空都要有答案。")
        return {"questions": normalized_questions}

    if type_value == AssignmentTaskTypeEnum.SHORT_ANSWER.value:
        prompt = _clean_text(data.get("prompt") or data.get("question"))
        answers = data.get("correct_answers") or data.get("accepted_answers") or []
        if isinstance(answers, str):
            answers = [answers]
        if not isinstance(answers, list):
            answers = []
        clean_answers = [_clean_text(answer) for answer in answers if _clean_text(answer)]
        match_mode = data.get("match_mode") or "case_insensitive"
        if match_mode not in {"exact", "case_insensitive"}:
            match_mode = "case_insensitive"
        if not prompt or not clean_answers:
            _invalid_contents("短問答需要題目和至少一個可接受答案。")
        return {
            "prompt": prompt,
            "correct_answers": clean_answers[:10],
            "match_mode": match_mode,
            "explanation": _clean_text(data.get("explanation")),
        }

    return data


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

    category_data = category_object.model_dump()
    category_data["name"] = _clean_required_text(category_data.get("name"), "分類名稱")
    category_data["description"] = _clean_text(category_data.get("description"))
    category_data["color"] = _clean_text(category_data.get("color"), "#111827")
    category = QuestionBankCategory(**category_data)
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
    if "name" in update_data:
        update_data["name"] = _clean_required_text(update_data["name"], "分類名稱")
    if "description" in update_data:
        update_data["description"] = _clean_text(update_data["description"])
    if "color" in update_data:
        update_data["color"] = _clean_text(update_data["color"], "#111827")
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
    subject: Optional[str] = None,
    education_stage: Optional[str] = None,
    grade_level: Optional[str] = None,
    unit: Optional[str] = None,
    visibility: Optional[QuestionBankVisibilityEnum] = None,
    tag: Optional[str] = None,
) -> list[QuestionBankItemRead]:
    await _get_org_or_404(org_id, db_session)
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_question_bank_access(acting_user_id, org_id, db_session)

    statement = select(QuestionBankItem).where(
        QuestionBankItem.org_id == org_id,
        QuestionBankItem.assignment_type.in_(SIMPLE_BANK_TYPE_VALUES),
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
    if subject:
        statement = statement.where(QuestionBankItem.subject == subject)
    if education_stage:
        statement = statement.where(QuestionBankItem.education_stage == education_stage)
    if grade_level:
        statement = statement.where(QuestionBankItem.grade_level == grade_level)
    if unit:
        statement = statement.where(QuestionBankItem.unit == unit)
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

    item_data = item_object.model_dump()
    item_data["title"] = _clean_required_text(item_data.get("title"), "題目名稱")
    item_data["description"] = _clean_text(item_data.get("description"))
    item_data["hint"] = _clean_text(item_data.get("hint"))
    item_data["contents"] = _normalize_question_bank_contents(
        item_object.assignment_type,
        item_object.contents,
    )
    _ensure_not_ai_fallback_task(
        item_data.get("title"),
        item_data.get("description"),
        item_data.get("hint"),
        item_data.get("contents"),
    )
    _ensure_question_bank_item_publish_ready(
        item_data.get("title"),
        item_object.assignment_type,
        item_data.get("contents"),
        item_data.get("description"),
        item_data.get("hint"),
    )
    item = QuestionBankItem(**item_data)
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
    if "title" in update_data:
        update_data["title"] = _clean_required_text(update_data["title"], "題目名稱")
    if "description" in update_data:
        update_data["description"] = _clean_text(update_data["description"])
    if "hint" in update_data:
        update_data["hint"] = _clean_text(update_data["hint"])
    if "assignment_type" in update_data or "contents" in update_data:
        effective_type = update_data.get("assignment_type", item.assignment_type)
        effective_contents = update_data.get("contents", item.contents or {})
        update_data["contents"] = _normalize_question_bank_contents(
            effective_type,
            effective_contents,
        )
    _ensure_not_ai_fallback_task(
        update_data.get("title", item.title),
        update_data.get("description", item.description),
        update_data.get("hint", item.hint),
        update_data.get("contents", item.contents),
    )
    _ensure_question_bank_item_publish_ready(
        update_data.get("title", item.title),
        update_data.get("assignment_type", item.assignment_type),
        update_data.get("contents", item.contents),
        update_data.get("description", item.description),
        update_data.get("hint", item.hint),
    )
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
    _ensure_not_ai_fallback_task(task.title, task.description, task.hint, task.contents)
    contents = _normalize_question_bank_contents(task.assignment_type, task.contents or {})
    _ensure_question_bank_item_publish_ready(
        task.title,
        task.assignment_type,
        contents,
        task.description,
        task.hint,
    )

    item = QuestionBankItem(
        title=task.title,
        description=task.description,
        hint=task.hint,
        reference_file=task.reference_file,
        assignment_type=task.assignment_type,
        contents=contents,
        tags=_normalize_tags(save_request.tags),
        subject=assignment.subject,
        education_stage=assignment.education_stage,
        grade_level=assignment.grade_level,
        unit=assignment.unit,
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
    target_assignment = (
        await db_session.execute(
            select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
        )
    ).scalars().first()
    if target_assignment:
        _ensure_question_bank_grade_matches_assignment(item, target_assignment)
    contents = _normalize_question_bank_contents(item.assignment_type, item.contents or {})
    _ensure_question_bank_item_publish_ready(
        item.title,
        item.assignment_type,
        contents,
        item.description,
        item.hint,
    )

    task = await create_assignment_task(
        request,
        assignment_uuid,
        AssignmentTaskCreate(
            title=item.title,
            description=item.description,
            hint=item.hint,
            reference_file="",
            assignment_type=item.assignment_type,
            contents=contents,
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
