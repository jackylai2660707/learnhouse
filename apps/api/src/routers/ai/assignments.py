from fastapi import APIRouter, Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.users import PublicUser
from src.security.auth import get_authenticated_user
from src.services.ai.assignment_config import assignment_ai_status
from src.services.ai.assignments import generate_assignment_tasks
from src.services.ai.schemas.assignments import (
    GenerateAssignmentTasksRequest,
    GenerateAssignmentTasksResponse,
)

router = APIRouter()


@router.get(
    "/assignments/status",
    summary="Get AI assignment generation status",
    description="Return whether the configured AI provider is ready for simple assignment generation.",
)
async def api_get_assignment_ai_status(
    _current_user: PublicUser = Depends(get_authenticated_user),
) -> dict:
    return assignment_ai_status()


@router.post(
    "/assignments/generate-tasks",
    response_model=GenerateAssignmentTasksResponse,
    summary="Generate auto-gradable assignment tasks",
    description=(
        "Generate teacher-owned simple quiz, fill-in-the-blank, and short-answer "
        "tasks with the configured AI provider, then save them to the target assignment."
    ),
)
async def api_generate_assignment_tasks(
    request: Request,
    generation_request: GenerateAssignmentTasksRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> GenerateAssignmentTasksResponse:
    return await generate_assignment_tasks(
        request,
        generation_request,
        current_user,
        db_session,
    )
