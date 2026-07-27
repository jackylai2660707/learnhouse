from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.question_bank import (
    AddQuestionBankItemToAssignmentRequest,
    AddQuestionBankItemToAssignmentResponse,
    QuestionBankCategoryCreate,
    QuestionBankCategoryRead,
    QuestionBankCategoryUpdate,
    QuestionBankItemCreate,
    QuestionBankItemRead,
    QuestionBankItemUpdate,
    QuestionBankVisibilityEnum,
    SaveAssignmentTaskToQuestionBankRequest,
)
from src.db.users import PublicUser
from src.security.auth import get_authenticated_user
from src.services.question_bank import (
    add_question_bank_item_to_assignment,
    create_question_bank_category,
    create_question_bank_item,
    delete_question_bank_category,
    delete_question_bank_item,
    list_question_bank_categories,
    list_question_bank_items,
    save_assignment_task_to_question_bank,
    update_question_bank_category,
    update_question_bank_item,
)

router = APIRouter()


@router.get("/categories", response_model=list[QuestionBankCategoryRead])
async def api_list_question_bank_categories(
    org_id: int = Query(...),
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[QuestionBankCategoryRead]:
    return await list_question_bank_categories(org_id, current_user, db_session)


@router.post("/categories", response_model=QuestionBankCategoryRead)
async def api_create_question_bank_category(
    category_object: QuestionBankCategoryCreate,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> QuestionBankCategoryRead:
    return await create_question_bank_category(category_object, current_user, db_session)


@router.put("/categories/{category_uuid}", response_model=QuestionBankCategoryRead)
async def api_update_question_bank_category(
    category_uuid: str,
    category_object: QuestionBankCategoryUpdate,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> QuestionBankCategoryRead:
    return await update_question_bank_category(category_uuid, category_object, current_user, db_session)


@router.delete("/categories/{category_uuid}")
async def api_delete_question_bank_category(
    category_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await delete_question_bank_category(category_uuid, current_user, db_session)


@router.get("/items", response_model=list[QuestionBankItemRead])
async def api_list_question_bank_items(
    org_id: int = Query(...),
    q: Optional[str] = Query(default=None),
    category_id: Optional[int] = Query(default=None),
    assignment_type: Optional[str] = Query(default=None),
    difficulty: Optional[str] = Query(default=None),
    subject: Optional[str] = Query(default=None),
    education_stage: Optional[str] = Query(default=None),
    grade_level: Optional[str] = Query(default=None),
    unit: Optional[str] = Query(default=None),
    visibility: Optional[QuestionBankVisibilityEnum] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[QuestionBankItemRead]:
    return await list_question_bank_items(
        org_id,
        current_user,
        db_session,
        q=q,
        category_id=category_id,
        assignment_type=assignment_type,
        difficulty=difficulty,
        subject=subject,
        education_stage=education_stage,
        grade_level=grade_level,
        unit=unit,
        visibility=visibility,
        tag=tag,
    )


@router.post("/items", response_model=QuestionBankItemRead)
async def api_create_question_bank_item(
    item_object: QuestionBankItemCreate,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> QuestionBankItemRead:
    return await create_question_bank_item(item_object, current_user, db_session)


@router.put("/items/{item_uuid}", response_model=QuestionBankItemRead)
async def api_update_question_bank_item(
    item_uuid: str,
    item_object: QuestionBankItemUpdate,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> QuestionBankItemRead:
    return await update_question_bank_item(item_uuid, item_object, current_user, db_session)


@router.delete("/items/{item_uuid}")
async def api_delete_question_bank_item(
    item_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await delete_question_bank_item(item_uuid, current_user, db_session)


@router.post("/from-assignment-task", response_model=QuestionBankItemRead)
async def api_save_assignment_task_to_question_bank(
    request: Request,
    save_request: SaveAssignmentTaskToQuestionBankRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> QuestionBankItemRead:
    return await save_assignment_task_to_question_bank(request, save_request, current_user, db_session)


@router.post("/items/{item_uuid}/add-to-assignment", response_model=AddQuestionBankItemToAssignmentResponse)
async def api_add_question_bank_item_to_assignment(
    request: Request,
    item_uuid: str,
    add_request: AddQuestionBankItemToAssignmentRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> AddQuestionBankItemToAssignmentResponse:
    return await add_question_bank_item_to_assignment(
        request,
        item_uuid,
        add_request.assignment_uuid,
        current_user,
        db_session,
    )
