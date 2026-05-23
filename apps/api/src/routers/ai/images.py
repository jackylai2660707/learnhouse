from fastapi import APIRouter, Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.users import PublicUser
from src.security.auth import get_authenticated_user
from src.services.ai.images import generate_activity_image_block
from src.services.ai.schemas.images import GenerateImageRequest, GenerateImageResponse

router = APIRouter()


@router.post(
    "/images/generate",
    response_model=GenerateImageResponse,
    summary="Generate an AI image for an activity",
    description=(
        "Generate an image through the configured OpenAI-compatible image model, "
        "store it in LearnHouse content storage, and create a reusable image block."
    ),
)
async def api_generate_image(
    request: Request,
    image_request: GenerateImageRequest,
    current_user: PublicUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> GenerateImageResponse:
    return await generate_activity_image_block(
        request,
        image_request,
        current_user,
        db_session,
    )
