from typing import Union

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.users import AnonymousUser, PublicUser
from src.security.auth import get_current_user
from src.services.coding_challenges.challenges import (
    get_challenge_editor_payload,
    get_challenge_analytics,
    get_challenge_solution,
    get_challenge_state,
    run_visible_tests,
    submit_challenge,
)


router = APIRouter()


class ChallengeExecutionRequest(BaseModel):
    source_code: str


def _require_user(current_user: Union[PublicUser, AnonymousUser]) -> PublicUser:
    if isinstance(current_user, AnonymousUser):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


@router.get(
    "/{challenge_uuid}",
    summary="Get coding challenge editor payload",
    description="Return a coding challenge with visible and hidden tests for teachers who can update the parent course.",
)
async def api_get_challenge_editor_payload(
    challenge_uuid: str,
    request: Request,
    current_user: Union[PublicUser, AnonymousUser] = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    current_user = _require_user(current_user)
    return await get_challenge_editor_payload(
        challenge_uuid,
        request,
        current_user,
        db_session,
    )


@router.post(
    "/{challenge_uuid}/run",
    summary="Run visible coding challenge tests",
    description="Run only the visible tests for a coding challenge. This does not create a formal submission attempt.",
)
async def api_run_visible_tests(
    challenge_uuid: str,
    body: ChallengeExecutionRequest,
    request: Request,
    current_user: Union[PublicUser, AnonymousUser] = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    current_user = _require_user(current_user)
    return await run_visible_tests(
        challenge_uuid,
        body.source_code,
        request,
        current_user,
        db_session,
    )


@router.post(
    "/{challenge_uuid}/submit",
    summary="Submit a coding challenge attempt",
    description="Run visible and hidden tests for a coding challenge, save the attempt, and update progress when all tests pass.",
)
async def api_submit_challenge(
    challenge_uuid: str,
    body: ChallengeExecutionRequest,
    request: Request,
    current_user: Union[PublicUser, AnonymousUser] = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    current_user = _require_user(current_user)
    return await submit_challenge(
        challenge_uuid,
        body.source_code,
        request,
        current_user,
        db_session,
    )


@router.get("/{challenge_uuid}/state", summary="Get my coding challenge progress and history")
async def api_get_challenge_state(
    challenge_uuid: str,
    request: Request,
    page: int = 1,
    limit: int = 10,
    current_user: Union[PublicUser, AnonymousUser] = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await get_challenge_state(
        challenge_uuid,
        page,
        limit,
        request,
        _require_user(current_user),
        db_session,
    )


@router.get("/{challenge_uuid}/solution", summary="Get an authorized challenge solution")
async def api_get_challenge_solution(
    challenge_uuid: str,
    request: Request,
    current_user: Union[PublicUser, AnonymousUser] = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await get_challenge_solution(
        challenge_uuid,
        request,
        _require_user(current_user),
        db_session,
    )


@router.get("/{challenge_uuid}/analytics", summary="Get teacher coding challenge analytics")
async def api_get_challenge_analytics(
    challenge_uuid: str,
    request: Request,
    current_user: Union[PublicUser, AnonymousUser] = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await get_challenge_analytics(
        challenge_uuid,
        request,
        _require_user(current_user),
        db_session,
    )
