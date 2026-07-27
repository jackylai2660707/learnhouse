"""RAG visibility regressions for courses, classes, drafts, and credits."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select

from src.db.courses.activities import (
    Activity,
    ActivityLockType,
    ActivitySubTypeEnum,
    ActivityTypeEnum,
)
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.routers.ai.rag import (
    _RAGCreditReservation,
    _RAGStreamingResponse,
    RAG_CREDIT_MARKER_TTL_SECONDS,
    RAGChatRequest,
    api_rag_chat,
    rag_chat_event_generator,
)
from src.routers.playgrounds.playgrounds_generator import _get_course_context
from src.services.ai.rag.access_scope import resolve_rag_retrieval_scope
from src.services.ai.rag.embedding_service import EmbeddingUnavailableError
from src.services.ai.rag.query_service import NonModelStreamChunk


def _now() -> str:
    return str(datetime.now())


async def _group_lock_course(db, org, course, *, user_id: int | None = None):
    group = UserGroup(
        name=f"Class {course.id}",
        description="Test class",
        org_id=org.id,
        usergroup_uuid=f"usergroup_{course.id}",
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    db.add(
        UserGroupResource(
            usergroup_id=group.id,
            resource_uuid=course.course_uuid,
            org_id=org.id,
            creation_date=_now(),
            update_date=_now(),
        )
    )
    if user_id is not None:
        db.add(
            UserGroupUser(
                usergroup_id=group.id,
                user_id=user_id,
                org_id=org.id,
                creation_date=_now(),
                update_date=_now(),
            )
        )
    await db.commit()
    return group


async def _activity(db, org, course, *, activity_id: int, published=True, lock="public"):
    row = Activity(
        id=activity_id,
        name=f"Activity {activity_id}",
        activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
        content={"type": "doc", "content": []},
        published=published,
        lock_type=ActivityLockType(lock),
        org_id=org.id,
        course_id=course.id,
        activity_uuid=f"activity_{activity_id}",
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_same_org_private_class_course_requires_its_usergroup(
    db, org, course, regular_user, mock_request
):
    course.public = False
    db.add(course)
    await db.commit()
    await _group_lock_course(db, org, course)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_rag_retrieval_scope(
            mock_request,
            regular_user,
            org.id,
            db,
            requested_course=course,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_private_class_member_can_retrieve_only_published_activity(
    db, org, course, activity, regular_user, mock_request
):
    course.public = False
    activity.lock_type = ActivityLockType.RESTRICTED
    db.add(course)
    db.add(activity)
    await db.commit()
    await _group_lock_course(db, org, course, user_id=regular_user.id)
    draft = await _activity(db, org, course, activity_id=101, published=False)

    scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
        requested_course=course,
    )

    assert scope.course_ids == [course.id]
    assert activity.id in scope.activity_ids
    assert draft.id not in scope.activity_ids


@pytest.mark.asyncio
async def test_unpublished_course_is_hidden_from_student_but_visible_to_updater(
    db, org, course, activity, regular_user, mock_request
):
    course.published = False
    activity.published = False
    db.add(course)
    db.add(activity)
    await db.commit()

    with pytest.raises(HTTPException) as exc_info:
        await resolve_rag_retrieval_scope(
            mock_request,
            regular_user,
            org.id,
            db,
            requested_course=course,
        )
    assert exc_info.value.status_code == 403

    db.add(
        ResourceAuthor(
            resource_uuid=course.course_uuid,
            user_id=regular_user.id,
            authorship=ResourceAuthorshipEnum.MAINTAINER,
            authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
            creation_date=_now(),
            update_date=_now(),
        )
    )
    await db.commit()

    updater_scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
        requested_course=course,
    )
    assert updater_scope.course_ids == [course.id]
    assert updater_scope.activity_ids == [activity.id]


@pytest.mark.asyncio
async def test_org_wide_scope_excludes_other_class_and_locked_or_draft_activity(
    db, org, course, activity, regular_user, mock_request
):
    other_class = Course(
        id=102,
        name="Other private class",
        description="private",
        public=False,
        published=True,
        open_to_contributors=False,
        org_id=org.id,
        course_uuid="course_other_class",
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(other_class)
    await db.commit()
    await _group_lock_course(db, org, other_class)
    await _activity(db, org, other_class, activity_id=102)

    draft = await _activity(db, org, course, activity_id=103, published=False)
    restricted = await _activity(
        db,
        org,
        course,
        activity_id=104,
        lock="restricted",
    )
    restricted_group = UserGroup(
        name="Restricted activity class",
        description="Other class",
        org_id=org.id,
        usergroup_uuid="usergroup_restricted_activity",
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(restricted_group)
    await db.commit()
    await db.refresh(restricted_group)
    db.add(
        UserGroupResource(
            usergroup_id=restricted_group.id,
            resource_uuid=restricted.activity_uuid,
            org_id=org.id,
            creation_date=_now(),
            update_date=_now(),
        )
    )
    await db.commit()

    scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
    )

    assert course.id in scope.course_ids
    assert other_class.id not in scope.course_ids
    assert activity.id in scope.activity_ids
    assert draft.id not in scope.activity_ids
    assert restricted.id not in scope.activity_ids


@pytest.mark.asyncio
async def test_student_scope_requires_course_chapter_navigation_link(
    db, org, course, chapter, activity, regular_user, mock_request
):
    link = (
        await db.execute(
            select(CourseChapter).where(
                CourseChapter.course_id == course.id,
                CourseChapter.chapter_id == chapter.id,
            )
        )
    ).scalars().one()
    await db.delete(link)
    await db.commit()

    scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
        requested_course=course,
    )

    assert scope.course_ids == [course.id]
    assert activity.id not in scope.activity_ids


@pytest.mark.asyncio
async def test_updater_scope_may_include_published_orphan_activity(
    db, org, course, regular_user, mock_request
):
    orphan = await _activity(db, org, course, activity_id=105)
    student_scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
        requested_course=course,
    )
    assert orphan.id not in student_scope.activity_ids

    db.add(
        ResourceAuthor(
            resource_uuid=course.course_uuid,
            user_id=regular_user.id,
            authorship=ResourceAuthorshipEnum.MAINTAINER,
            authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
            creation_date=_now(),
            update_date=_now(),
        )
    )
    await db.commit()

    scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
        requested_course=course,
    )

    assert orphan.id in scope.activity_ids


@pytest.mark.asyncio
async def test_cross_org_dangling_activity_path_is_not_student_visible(
    db, org, other_org, course, activity, regular_user, mock_request
):
    activity.org_id = other_org.id
    db.add(activity)
    await db.commit()

    scope = await resolve_rag_retrieval_scope(
        mock_request,
        regular_user,
        org.id,
        db,
        requested_course=course,
    )

    assert activity.id not in scope.activity_ids


@pytest.mark.asyncio
async def test_embedding_failure_refunds_two_reserved_credits_exactly_once(
    db, org, course, activity, regular_user, mock_request
):
    reserve = AsyncMock(return_value=2)
    refund = Mock()
    failure = EmbeddingUnavailableError("backend_down", operation="embed_single")

    with patch("src.services.security.rate_limiting.enforce_ai_rate_limit"), patch(
        "src.routers.ai.rag.get_ai_credit_period_token", return_value="period-0"
    ), patch(
        "src.routers.ai.rag.reserve_ai_credit_once", reserve
    ), patch("src.routers.ai.rag.refund_ai_credit_once", refund), patch(
        "src.routers.ai.rag.query_course_rag_stream",
        new=AsyncMock(side_effect=failure),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await api_rag_chat(
                request=mock_request,
                chat_request=RAGChatRequest(
                    message="What is this lesson?",
                    course_uuid=course.course_uuid,
                ),
                current_user=regular_user,
                db_session=db,
            )

    assert exc_info.value.status_code == 503
    reserve_args = reserve.await_args
    assert reserve_args.args[:2] == (org.id, db)
    assert reserve_args.kwargs == {
        "amount": 2,
        "period_token": "period-0",
        "marker_ttl_seconds": RAG_CREDIT_MARKER_TTL_SECONDS,
    }
    operation_key = reserve_args.args[2]
    refund.assert_called_once_with(
        org.id,
        operation_key,
        amount=2,
        period_token="period-0",
        marker_ttl_seconds=RAG_CREDIT_MARKER_TTL_SECONDS,
    )


async def _collect_sse(generator):
    return [event async for event in generator]


async def _empty_stream():
    if False:
        yield ""


async def _raising_stream():
    raise RuntimeError("provider failed before first token")
    yield ""


async def _cancelled_stream():
    raise asyncio.CancelledError()
    yield ""


async def _notice_then_raising_stream():
    yield NonModelStreamChunk("static degraded notice")
    raise RuntimeError("provider failed before first model token")


def _reserved_generation_credit(org_id: int) -> _RAGCreditReservation:
    return _RAGCreditReservation(
        org_id=org_id,
        operation_key="rag_chat:test",
        period_token="period-0",
        refundable_credits=1,
    )


@pytest.mark.asyncio
async def test_empty_scope_reserves_generation_but_not_embedding_credit(
    db, org, course, regular_user, mock_request
):
    reserve = AsyncMock(return_value=1)
    query = AsyncMock(return_value=(_empty_stream(), []))
    with patch("src.services.security.rate_limiting.enforce_ai_rate_limit"), patch(
        "src.routers.ai.rag.get_ai_credit_period_token", return_value="period-0"
    ), patch(
        "src.routers.ai.rag.reserve_ai_credit_once", reserve
    ), patch("src.routers.ai.rag.query_course_rag_stream", query):
        response = await api_rag_chat(
            request=mock_request,
            chat_request=RAGChatRequest(
                message="Is there any material?",
                course_uuid=course.course_uuid,
            ),
            current_user=regular_user,
            db_session=db,
        )

    assert response.status_code == 200
    reserve_args = reserve.await_args
    assert reserve_args.args[:2] == (org.id, db)
    assert reserve_args.kwargs["amount"] == 1
    assert reserve_args.kwargs["period_token"] == "period-0"
    assert (
        reserve_args.kwargs["marker_ttl_seconds"]
        == RAG_CREDIT_MARKER_TTL_SECONDS
    )
    assert query.await_args.kwargs["authorized_activity_ids"] == []


@pytest.mark.asyncio
async def test_zero_token_stream_exception_refunds_generation_once(org):
    credit = _reserved_generation_credit(org.id)
    refund = Mock()
    with patch("src.routers.ai.rag.refund_ai_credit_once", refund):
        events = await _collect_sse(
            rag_chat_event_generator(
                _raising_stream(),
                "chat_test",
                "hello",
                [],
                "context",
                "gemini",
                org_id=org.id,
                generation_credit=credit,
            )
        )
        credit.refund_remaining()

    assert any('"type": "error"' in event for event in events)
    refund.assert_called_once_with(
        org.id,
        "rag_chat:test",
        amount=1,
        period_token="period-0",
        marker_ttl_seconds=RAG_CREDIT_MARKER_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_static_notice_does_not_count_as_generation_token(org):
    credit = _reserved_generation_credit(org.id)
    refund = Mock()
    with patch("src.routers.ai.rag.refund_ai_credit_once", refund):
        await _collect_sse(
            rag_chat_event_generator(
                _notice_then_raising_stream(),
                "chat_test",
                "hello",
                [],
                "context",
                "gemini",
                org_id=org.id,
                generation_credit=credit,
            )
        )

    assert refund.call_count == 1


@pytest.mark.asyncio
async def test_zero_token_client_cancellation_refunds_generation_once(org):
    credit = _reserved_generation_credit(org.id)
    refund = Mock()
    with patch("src.routers.ai.rag.refund_ai_credit_once", refund):
        with pytest.raises(asyncio.CancelledError):
            await _collect_sse(
                rag_chat_event_generator(
                    _cancelled_stream(),
                    "chat_test",
                    "hello",
                    [],
                    "context",
                    "gemini",
                    org_id=org.id,
                    generation_credit=credit,
                )
            )

    assert refund.call_count == 1


@pytest.mark.asyncio
async def test_empty_model_stream_refunds_generation_once(org):
    credit = _reserved_generation_credit(org.id)
    refund = Mock()
    with patch("src.routers.ai.rag.refund_ai_credit_once", refund), patch(
        "src.routers.ai.rag.save_message_to_history"
    ), patch(
        "src.routers.ai.rag.generate_follow_up_suggestions",
        new=AsyncMock(return_value=[]),
    ):
        await _collect_sse(
            rag_chat_event_generator(
                _empty_stream(),
                "chat_test",
                "hello",
                [],
                "context",
                "gemini",
                org_id=org.id,
                generation_credit=credit,
            )
        )

    assert refund.call_count == 1


@pytest.mark.asyncio
async def test_response_finalizer_refunds_if_streaming_never_starts(org):
    credit = _reserved_generation_credit(org.id)
    refund = Mock()
    response = _RAGStreamingResponse(
        _empty_stream(),
        credit_reservation=credit,
        media_type="text/event-stream",
    )
    with patch("src.routers.ai.rag.refund_ai_credit_once", refund), patch.object(
        StreamingResponse,
        "__call__",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await response({}, AsyncMock(), AsyncMock())

    assert refund.call_count == 1


def test_failed_refund_can_retry_with_same_redis_operation_key(org):
    credit = _reserved_generation_credit(org.id)
    refund = Mock(side_effect=[RuntimeError("lost reply"), 0])
    with patch("src.routers.ai.rag.refund_ai_credit_once", refund):
        credit.refund_remaining()
        credit.refund_remaining()

    assert refund.call_count == 2
    assert refund.call_args_list[0] == refund.call_args_list[1]


@pytest.mark.asyncio
async def test_playground_same_org_private_course_does_not_bypass_course_read(
    db, org, course, activity, regular_user, mock_request
):
    course.public = False
    db.add(course)
    await db.commit()
    await _group_lock_course(db, org, course)
    query = AsyncMock(return_value={"context": "private context"})

    with patch(
        "src.services.ai.rag.query_service.query_course_rag",
        query,
    ):
        result = await _get_course_context(
            mock_request,
            regular_user,
            course.course_uuid,
            org.id,
            db,
            "private probe",
        )

    assert result == (None, course.id)
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_playground_student_cannot_use_unpublished_course_as_rag_context(
    db, org, course, activity, regular_user, mock_request
):
    course.published = False
    db.add(course)
    await db.commit()
    query = AsyncMock(return_value={"context": "draft context"})

    with patch(
        "src.services.ai.rag.query_service.query_course_rag",
        query,
    ):
        result = await _get_course_context(
            mock_request,
            regular_user,
            course.course_uuid,
            org.id,
            db,
            "draft probe",
        )

    assert result == (None, course.id)
    query.assert_not_awaited()
