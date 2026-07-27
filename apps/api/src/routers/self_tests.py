from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.self_tests import (
    ReviewSelfTestAttemptRequest,
    SelfTestAttemptRead,
    StartSelfTestRequest,
    SubmitSelfTestRequest,
)
from src.db.users import PublicUser
from src.security.auth import get_authenticated_user
from src.services.self_tests import (
    discard_started_self_test,
    list_my_self_test_attempts,
    list_org_self_test_attempts,
    read_self_test_attempt,
    review_self_test_attempt,
    start_self_test,
    submit_self_test,
)

router = APIRouter()


@router.post("/start", response_model=SelfTestAttemptRead)
async def api_start_self_test(
    request_body: StartSelfTestRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> SelfTestAttemptRead:
    return await start_self_test(request_body, current_user, db_session)


@router.post("/attempts/{attempt_uuid}/submit", response_model=SelfTestAttemptRead)
async def api_submit_self_test(
    attempt_uuid: str,
    request_body: SubmitSelfTestRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> SelfTestAttemptRead:
    return await submit_self_test(attempt_uuid, request_body, current_user, db_session)


@router.get("/attempts/me", response_model=list[SelfTestAttemptRead])
async def api_list_my_self_test_attempts(
    org_id: int = Query(...),
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[SelfTestAttemptRead]:
    return await list_my_self_test_attempts(org_id, current_user, db_session)


@router.get("/attempts", response_model=list[SelfTestAttemptRead])
async def api_list_org_self_test_attempts(
    org_id: int = Query(...),
    user_id: Optional[int] = Query(default=None),
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[SelfTestAttemptRead]:
    return await list_org_self_test_attempts(org_id, current_user, db_session, user_id=user_id)


@router.get("/attempts/{attempt_uuid}", response_model=SelfTestAttemptRead)
async def api_read_self_test_attempt(
    attempt_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> SelfTestAttemptRead:
    return await read_self_test_attempt(attempt_uuid, current_user, db_session)


@router.delete("/attempts/{attempt_uuid}")
async def api_discard_started_self_test(
    attempt_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await discard_started_self_test(attempt_uuid, current_user, db_session)


@router.patch("/attempts/{attempt_uuid}/review", response_model=SelfTestAttemptRead)
async def api_review_self_test_attempt(
    attempt_uuid: str,
    request_body: ReviewSelfTestAttemptRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> SelfTestAttemptRead:
    return await review_self_test_attempt(attempt_uuid, request_body, current_user, db_session)
