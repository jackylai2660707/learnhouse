from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.blocks import Block, BlockRead, BlockTypeEnum
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.security.auth import resolve_acting_user_id
from src.security.features_utils.usage import reserve_ai_credit
from src.security.rbac import AccessAction, check_resource_access
from src.services.ai.base import generate_openai_compatible_image
from src.services.ai.schemas.images import GenerateImageRequest, GenerateImageResponse
from src.services.blocks.schemas.files import BlockFile
from src.services.email.utils import get_base_url_from_request
from src.services.security.rate_limiting import enforce_ai_rate_limit
from src.services.utils.upload_content import upload_content


def _request_base_url(request: Request) -> str:
    return get_base_url_from_request(request).rstrip("/")


async def generate_activity_image_block(
    request: Request,
    image_request: GenerateImageRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> GenerateImageResponse:
    activity = (
        await db_session.execute(
            select(Activity).where(Activity.activity_uuid == image_request.activity_uuid)
        )
    ).scalars().first()
    if not activity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    if activity.org_id != image_request.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Activity does not belong to this organization")

    course = (
        await db_session.execute(select(Course).where(Course.id == activity.course_id))
    ).scalars().first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    org = (
        await db_session.execute(select(Organization).where(Organization.id == activity.org_id))
    ).scalars().first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    acting_user_id = resolve_acting_user_id(current_user)
    enforce_ai_rate_limit(acting_user_id, org.id or image_request.org_id)
    await reserve_ai_credit(org.id or image_request.org_id, db_session, amount=3)

    try:
        image_bytes, image_format, revised_prompt = generate_openai_compatible_image(
            image_request.prompt,
            model=image_request.model,
            size=image_request.size,
            quality=image_request.quality,
            background=image_request.background,
        )
    except Exception as exc:
        from src.security.features_utils.usage import refund_ai_credit

        refund_ai_credit(org.id or image_request.org_id, 3)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Image generation failed: {str(exc)}",
        ) from exc

    if image_format == "jpeg":
        image_format = "jpg"
    if image_format not in {"png", "jpg", "webp"}:
        image_format = "png"

    block_uuid = f"block_{uuid4()}"
    file_id = f"{uuid4()}_ai_image"
    file_name = f"{file_id}.{image_format}"
    directory = (
        f"courses/{course.course_uuid}/activities/{activity.activity_uuid}"
        f"/dynamic/blocks/imageBlock/{block_uuid}"
    )

    await upload_content(
        directory=directory,
        type_of_dir="orgs",
        uuid=org.org_uuid,
        file_binary=image_bytes,
        file_and_format=file_name,
        allowed_formats=["png", "jpg", "webp"],
    )

    block_file = BlockFile(
        file_id=file_id,
        file_format=image_format,
        file_name=file_name,
        file_size=len(image_bytes),
        file_type=f"image/{'jpeg' if image_format == 'jpg' else image_format}",
        activity_uuid=activity.activity_uuid,
    )

    now = str(datetime.now())
    block = Block(
        activity_id=activity.id or 0,
        block_type=BlockTypeEnum.BLOCK_IMAGE,
        content=block_file.model_dump(),
        org_id=org.id or 0,
        course_id=course.id or 0,
        block_uuid=block_uuid,
        creation_date=now,
        update_date=now,
    )
    db_session.add(block)
    await db_session.commit()
    await db_session.refresh(block)

    file_path = f"content/orgs/{org.org_uuid}/{directory}/{file_name}"
    return GenerateImageResponse(
        block=BlockRead.model_validate(block),
        file_name=file_name,
        file_path=file_path,
        public_url=f"{_request_base_url(request)}/{file_path}",
        revised_prompt=revised_prompt,
    )
