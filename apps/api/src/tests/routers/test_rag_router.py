"""Authorization and failure contracts for the manual RAG reindex endpoint."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.courses.courses import Course
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.routers.ai.rag import router as rag_router
from src.security.auth import get_current_user
from src.services.ai.rag.embedding_service import (
    CourseIndexResult,
    EmbeddingUnavailableError,
    StaleCourseIndexError,
)


async def _post_reindex(db, user, course_uuid: str):
    app = FastAPI()
    app.include_router(rag_router, prefix="/api/v1/ai")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/ai/rag/index",
                json={"course_uuid": course_uuid},
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_course_author_can_reindex_without_org_admin(
    db, course, regular_user
):
    """Active authors receive course UPDATE access without org-wide admin."""
    db.add(
        ResourceAuthor(
            resource_uuid=course.course_uuid,
            user_id=regular_user.id,
            authorship=ResourceAuthorshipEnum.CREATOR,
            authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
        )
    )
    await db.commit()

    with patch(
        "src.routers.ai.rag.embed_course_content",
        new=AsyncMock(return_value=CourseIndexResult(chunks_indexed=3)),
    ) as embed:
        response = await _post_reindex(db, regular_user, course.course_uuid)

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "chunks_indexed": 3,
        "degraded": False,
        "degraded_reason": None,
    }
    embed.assert_awaited_once_with(
        course_id=course.id,
        org_id=course.org_id,
        db_session=db,
    )


@pytest.mark.asyncio
async def test_reindex_requires_course_update_access(db, course, regular_user):
    response = await _post_reindex(db, regular_user, course.course_uuid)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reindex_rejects_cross_org_course_even_for_member_elsewhere(
    db, other_org, regular_user
):
    foreign_course = Course(
        id=902,
        name="Foreign course",
        description="x",
        public=False,
        published=False,
        open_to_contributors=False,
        org_id=other_org.id,
        course_uuid="course_foreign_reindex",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(foreign_course)
    await db.commit()

    response = await _post_reindex(db, regular_user, foreign_course.course_uuid)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reindex_preserves_embedding_failure_and_stale_conflict_statuses(
    db, course, admin_user
):
    with patch(
        "src.routers.ai.rag.embed_course_content",
        new=AsyncMock(
            side_effect=EmbeddingUnavailableError(
                "backend_unavailable", operation="embed_course"
            )
        ),
    ):
        unavailable = await _post_reindex(db, admin_user, course.course_uuid)

    with patch(
        "src.routers.ai.rag.embed_course_content",
        new=AsyncMock(side_effect=StaleCourseIndexError(course.id)),
    ):
        stale = await _post_reindex(db, admin_user, course.course_uuid)

    assert unavailable.status_code == 503
    assert stale.status_code == 409
