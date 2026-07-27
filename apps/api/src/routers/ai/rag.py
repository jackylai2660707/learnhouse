"""
RAG (Retrieval-Augmented Generation) API router.

Provides streaming chatbot grounded in course content and manual re-index trigger.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.events.database import get_db_session
from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.users import PublicUser, AnonymousUser, APITokenUser
from src.security.auth import get_current_user, get_authenticated_user, resolve_acting_user_id
from src.security.features_utils.usage import (
    get_ai_credit_period_token,
    refund_ai_credit_once,
    reserve_ai_credit_once,
)
from src.security.org_auth import is_org_member
from src.security.rbac import AccessAction, check_resource_access
from src.services.ai.base import (
    get_chat_session_history,
    save_message_to_history,
    generate_follow_up_suggestions,
    generate_chat_title,
    get_user_chat_sessions,
    get_chat_messages,
    delete_chat_session,
    update_chat_session_meta,
)
from src.services.ai.rag.embedding_service import (
    EmbeddingUnavailableError,
    StaleCourseIndexError,
    embed_course_content,
)
from src.services.ai.rag.access_scope import resolve_rag_retrieval_scope
from src.services.ai.rag.query_service import (
    NonModelStreamChunk,
    query_course_rag_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter()

CITATION_GROUP_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
RAG_CREDIT_MARKER_TTL_SECONDS = 86_400


@dataclass
class _RAGCreditReservation:
    """Redis-idempotent credit reservation for one RAG request.

    ``refundable_credits`` starts at generation + optional retrieval. Successful
    retrieval and the first non-empty model token consume their stages. Any
    remainder is refunded using the persistent operation marker, so generator
    and response finalizers may safely overlap or retry after a lost response.
    """

    org_id: int
    operation_key: str
    period_token: str
    refundable_credits: int = 0
    refund_confirmed: bool = False

    async def reserve(self, db_session: AsyncSession, amount: int) -> None:
        await reserve_ai_credit_once(
            self.org_id,
            db_session,
            self.operation_key,
            amount=amount,
            period_token=self.period_token,
            marker_ttl_seconds=RAG_CREDIT_MARKER_TTL_SECONDS,
        )
        self.refundable_credits = amount

    def consume(self, amount: int = 1) -> None:
        self.refundable_credits = max(0, self.refundable_credits - amount)

    def refund_remaining(self) -> None:
        if self.refund_confirmed or self.refundable_credits <= 0:
            return
        try:
            refund_ai_credit_once(
                self.org_id,
                self.operation_key,
                amount=self.refundable_credits,
                period_token=self.period_token,
                marker_ttl_seconds=RAG_CREDIT_MARKER_TTL_SECONDS,
            )
        except Exception:
            # Do not mark confirmed. The response-level finalizer retries with
            # the same Redis operation key, which remains safe if the first
            # response was lost after Redis applied it.
            logger.warning(
                "rag.chat.credit_refund_failed",
                extra={
                    "integration": "rag",
                    "operation": "refund_rag_request_failure",
                    "org_id": self.org_id,
                    "reservation_id": self.operation_key,
                },
            )
            return
        self.refund_confirmed = True


class _RAGStreamingResponse(StreamingResponse):
    """Guarantee a refund attempt even if the body iterator never starts."""

    def __init__(self, *args, credit_reservation: _RAGCreditReservation, **kwargs):
        super().__init__(*args, **kwargs)
        self._credit_reservation = credit_reservation

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._credit_reservation.refund_remaining()


def normalize_citation_sources(sources: list[dict]) -> list[dict]:
    """Make citation numbers contiguous and server-authoritative."""
    return [
        {**source, "citation_number": index}
        for index, source in enumerate(sources, start=1)
    ]


def citation_audit(answer: str, source_count: int) -> dict[str, list[int]]:
    """Return bounded citation numbers without trusting model-generated labels."""
    referenced: set[int] = set()
    invalid: set[int] = set()
    for match in CITATION_GROUP_RE.finditer(answer):
        for value in match.group(1).split(","):
            number = int(value.strip())
            if 1 <= number <= source_count:
                referenced.add(number)
            else:
                invalid.add(number)
    return {"referenced": sorted(referenced), "invalid": sorted(invalid)}


def citation_footer(source_count: int) -> str:
    """Build the deterministic source map shown after every grounded answer."""
    labels = " ".join(f"[{number}]" for number in range(1, source_count + 1))
    return f"\n\n---\n**教材來源：** {labels}"


# ============================================================================
# Request/Response schemas
# ============================================================================


class RAGChatRequest(BaseModel):
    message: str
    course_uuid: Optional[str] = None
    aichat_uuid: Optional[str] = None
    mode: Literal["course_only", "general"] = "course_only"
    org_slug: Optional[str] = None


class RAGIndexRequest(BaseModel):
    course_uuid: str


class RAGIndexResponse(BaseModel):
    status: str
    chunks_indexed: int
    # True when the index was built from lexical hashes instead of real
    # embeddings. Surfaced so an admin who triggers a reindex cannot be told
    # "success" for an index that does not support semantic search.
    degraded: bool = False
    degraded_reason: Optional[str] = None


# ============================================================================
# SSE Event Generator
# ============================================================================


async def rag_chat_event_generator(
    stream_generator,
    aichat_uuid: str,
    user_message: str,
    sources: list[dict],
    context_text: str,
    ai_model: str,
    user_id: Optional[int] = None,
    course_uuid: Optional[str] = None,
    is_new_session: bool = False,
    mode: str = "course_only",
    org_id: Optional[int] = None,
    generation_credit: _RAGCreditReservation | None = None,
):
    """Convert async generator to SSE format with source references."""
    full_response = ""
    received_model_output = False
    sources = normalize_citation_sources(sources)
    try:
        # Send start event
        yield f"data: {json.dumps({'type': 'start', 'aichat_uuid': aichat_uuid})}\n\n"

        async for chunk in stream_generator:
            full_response += chunk
            if (
                chunk
                and not isinstance(chunk, NonModelStreamChunk)
                and not received_model_output
            ):
                received_model_output = True
                if generation_credit is not None:
                    generation_credit.consume()
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"

        if not received_model_output:
            yield f"data: {json.dumps({'type': 'error', 'message': '教材問答暫時不可用，請稍後重試。'})}\n\n"
            return

        if sources:
            audit = citation_audit(full_response, len(sources))
            if audit["invalid"]:
                logger.warning(
                    "ai.rag.invalid_citation_numbers",
                    extra={
                        "integration": "rag",
                        "operation": "chat_stream",
                        "invalid_citation_count": len(audit["invalid"]),
                        "source_count": len(sources),
                    },
                )
            footer = citation_footer(len(sources))
            full_response += footer
            yield f"data: {json.dumps({'type': 'chunk', 'content': footer})}\n\n"

        # Save the message exchange to history (including sources for persistence)
        save_message_to_history(aichat_uuid, user_message, full_response, user_id=user_id, course_uuid=course_uuid, sources=sources if sources else None, mode=mode, org_id=org_id)

        # Send sources event before done
        if sources:
            yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"

        # Send done event
        yield f"data: {json.dumps({'type': 'done', 'aichat_uuid': aichat_uuid})}\n\n"

        # Generate follow-up suggestions
        follow_ups = await generate_follow_up_suggestions(
            full_response,
            context_text[:1000],
            ai_model,
            user_message,
        )
        if follow_ups:
            yield f"data: {json.dumps({'type': 'follow_ups', 'follow_up_suggestions': follow_ups})}\n\n"

        # Generate AI-summarized title for new sessions
        if is_new_session and user_id is not None:
            title = generate_chat_title(user_message, full_response)
            update_chat_session_meta(aichat_uuid, user_id, title=title)
            yield f"data: {json.dumps({'type': 'session_title', 'title': title})}\n\n"

    except asyncio.CancelledError:
        logger.info(
            "ai.rag.stream_cancelled",
            extra={
                "integration": "rag",
                "operation": "chat_stream",
                "org_id": org_id,
            },
        )
        raise
    except Exception as exc:
        logger.warning(
            "ai.rag.stream_failed",
            extra={
                "integration": "rag",
                "operation": "chat_stream",
                "error_code": type(exc).__name__,
            },
        )
        yield f"data: {json.dumps({'type': 'error', 'message': '教材問答暫時不可用，請稍後重試。'})}\n\n"
    finally:
        # An empty provider stream, an exception before the first token, and a
        # client cancellation before the first token did not consume the
        # generation stage. ``refund`` is safe if finalization runs twice.
        if not received_model_output and generation_credit is not None:
            generation_credit.refund_remaining()


# ============================================================================
# Endpoints
# ============================================================================


@router.post(
    "/rag/chat",
    summary="RAG chat (streaming)",
    description="Streaming RAG chatbot grounded in course content. If `course_uuid` is provided, searches within that course only. Otherwise searches across all courses for the user's organization. Responses are delivered as Server-Sent Events (SSE).",
    responses={
        200: {
            "description": "SSE stream of chat events (start, chunk, sources, done, follow_ups, session_title, error).",
            "content": {"text/event-stream": {}},
        },
        401: {"description": "Authentication required"},
        403: {"description": "AI features disabled, copilot disabled, or user has no organization"},
        404: {"description": "Course or organization not found"},
    },
)
async def api_rag_chat(
    request: Request,
    chat_request: RAGChatRequest,
    current_user: PublicUser | AnonymousUser | APITokenUser = Depends(get_authenticated_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    Streaming RAG chatbot (SSE).

    - If course_uuid is provided, searches within that course only.
    - If course_uuid is omitted, searches across all courses for the user's org.
    """
    course_id = None
    org_id = None
    requested_course: Course | None = None

    if chat_request.course_uuid:
        course = (await db_session.execute(
            select(Course).where(Course.course_uuid == chat_request.course_uuid)
        )).scalars().first()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        requested_course = course
        course_id = course.id
        org_id = course.org_id
    else:
        if chat_request.org_slug:
            org = (await db_session.execute(
                select(Organization).where(Organization.slug == chat_request.org_slug)
            )).scalars().first()
            if not org:
                raise HTTPException(status_code=404, detail="Organization not found")
            org_id = org.id
        else:
            from src.db.user_organizations import UserOrganization
            user_org = (await db_session.execute(
                select(UserOrganization).where(
                    UserOrganization.user_id == resolve_acting_user_id(current_user)
                )
            )).scalars().first()
            if not user_org:
                raise HTTPException(status_code=403, detail="User has no organization")
            org_id = user_org.org_id

    # SECURITY (F-5): before touching any org-scoped resource (including the
    # credit bucket), verify the authenticated user is actually a member of
    # the resolved org. Without this check, an attacker in org A can drain
    # org B's AI credits or run RAG against org B's indexed content by
    # supplying a course_uuid or org_slug from org B.
    if not await is_org_member(resolve_acting_user_id(current_user), org_id, db_session):
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this organization",
        )

    # Org membership is only the tenant boundary. Course READ authorization
    # and activity publication/lock visibility determine what can enter the
    # model context. The resulting integer IDs are server-computed and passed
    # into the SQL query; no org-wide fallback can search unauthorized rows.
    retrieval_scope = await resolve_rag_retrieval_scope(
        request,
        current_user,
        org_id,
        db_session,
        requested_course=requested_course,
    )

    # Check if copilot is enabled for this org
    org_config = (await db_session.execute(
        select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    )).scalars().first()
    if org_config and org_config.config:
        from src.security.features_utils.resolve import resolve_feature
        resolved_ai = resolve_feature("ai", org_config.config, org_id)
        if not resolved_ai["enabled"]:
            raise HTTPException(status_code=403, detail="AI features are disabled for this organization")
        # Check copilot_enabled from admin toggles (v2) or features.ai (v1)
        config = org_config.config
        version = config.get("config_version", "1.0")
        if version.startswith("2"):
            copilot_enabled = config.get("admin_toggles", {}).get("ai", {}).get("copilot_enabled", True)
        else:
            copilot_enabled = config.get("features", {}).get("ai", {}).get("copilot_enabled", True)
        if not copilot_enabled:
            raise HTTPException(status_code=403, detail="Copilot is disabled for this organization")

    # F-9: per-user + per-org rate limit before any compute / credit spend.
    # Resolve API tokens to creator so rate-limit buckets per creator, not 0.
    chat_acting_user_id = resolve_acting_user_id(current_user)
    from src.services.security.rate_limiting import enforce_ai_rate_limit
    enforce_ai_rate_limit(chat_acting_user_id, org_id)

    # Read session history before reserving: local state failures must not bill
    # either provider stage.
    is_new_session = chat_request.aichat_uuid is None
    chat_session = get_chat_session_history(chat_request.aichat_uuid)

    operation_key = f"rag_chat:{uuid4().hex}"
    credit_reservation = _RAGCreditReservation(
        org_id=org_id,
        operation_key=operation_key,
        period_token=get_ai_credit_period_token(org_id),
    )

    # Empty authorization scopes short-circuit retrieval before the embedding
    # provider, so they must not reserve an embedding credit.
    has_retrieval_scope = bool(
        retrieval_scope.course_ids
        and retrieval_scope.activity_ids
        and (course_id is None or course_id in retrieval_scope.course_ids)
    )
    await credit_reservation.reserve(
        db_session,
        amount=1 + int(has_retrieval_scope),
    )

    # Perform RAG query with streaming
    try:
        stream, sources = await query_course_rag_stream(
            question=chat_request.message,
            org_id=org_id,
            db_session=db_session,
            message_history=chat_session["message_history"],
            course_id=course_id,
            mode=chat_request.mode or "course_only",
            authorized_course_ids=retrieval_scope.course_ids,
            authorized_activity_ids=retrieval_scope.activity_ids,
        )
    except EmbeddingUnavailableError as exc:
        # Retrieval without real embeddings returns near-random chunks, and an
        # answer confidently grounded in those is worse than a clear failure.
        logger.error(
            "rag.chat.embeddings_unavailable",
            extra={
                "integration": "rag",
                "operation": "api_rag_chat",
                "error_code": exc.code,
                "org_id": org_id,
            },
        )
        credit_reservation.refund_remaining()
        raise HTTPException(
            status_code=503,
            detail=(
                "Course search is temporarily unavailable — the embedding "
                "service is not responding. Please try again shortly."
            ),
        ) from exc
    except Exception as exc:
        # No answer stream exists, so neither reserved stage should remain
        # charged for this failed request.
        credit_reservation.refund_remaining()
        logger.warning(
            "rag.chat.retrieval_unavailable",
            extra={
                "integration": "rag",
                "operation": "api_rag_chat",
                "error_code": type(exc).__name__,
                "org_id": org_id,
            },
        )
        raise HTTPException(
            status_code=503,
            detail="教材搜尋暫時不可用，請稍後重試。",
        ) from exc

    # Retrieval is now complete. Its credit is consumed; only generation
    # remains refundable until the first non-empty model token arrives.
    if has_retrieval_scope:
        credit_reservation.consume()

    return _RAGStreamingResponse(
        rag_chat_event_generator(
            stream,
            chat_session["aichat_uuid"],
            chat_request.message,
            sources,
            chat_request.message,  # context_text for follow-ups
            "gemini-2.5-flash",
            user_id=chat_acting_user_id,
            course_uuid=chat_request.course_uuid,
            is_new_session=is_new_session,
            mode=chat_request.mode,
            org_id=org_id,
            generation_credit=credit_reservation,
        ),
        credit_reservation=credit_reservation,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/rag/index",
    response_model=RAGIndexResponse,
    summary="Reindex course content for RAG",
    description="Manually trigger re-indexing (embedding) of a course's content for the RAG chatbot. Requires UPDATE access to the requested course.",
    responses={
        200: {"description": "Course re-indexed successfully.", "model": RAGIndexResponse},
        401: {"description": "Authentication required"},
        403: {"description": "User lacks UPDATE access to the requested course"},
        404: {"description": "Course not found"},
        409: {"description": "Course content changed while the index was being built"},
        503: {"description": "Embedding service unavailable; existing embeddings were left untouched"},
    },
)
async def api_rag_index(
    request: Request,
    index_request: RAGIndexRequest,
    current_user: PublicUser | AnonymousUser | APITokenUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    Manually trigger re-indexing of a course's content for RAG.
    Requires UPDATE access to the requested course. This is deliberately a
    resource check rather than an organization-role check: active course
    authors may reindex their own course without receiving organization-wide
    administration privileges.
    """
    # Resolve course
    course = (await db_session.execute(
        select(Course).where(Course.course_uuid == index_request.course_uuid)
    )).scalars().first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Resolve authorization against the server-owned course resource. The
    # resource checker binds authorship/role permissions to ``course.org_id``,
    # so a role in another organization cannot grant reindex access here.
    await check_resource_access(
        request,
        db_session,
        current_user,
        course.course_uuid,
        AccessAction.UPDATE,
    )

    # Run indexing. EmbeddingUnavailableError propagates as a 503 (see the
    # handler below) rather than being reported as a successful index.
    try:
        result = await embed_course_content(
            course_id=course.id,
            org_id=course.org_id,
            db_session=db_session,
        )
    except StaleCourseIndexError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "重新建立索引期間課程內容已更新；系統已捨棄舊索引。"
                "如未有新的背景索引正在執行，請稍後重試。"
            ),
        ) from exc
    except EmbeddingUnavailableError as exc:
        logger.error(
            "rag.index.embeddings_unavailable",
            extra={
                "integration": "rag",
                "operation": "api_rag_index",
                "error_code": exc.code,
                "course_id": course.id,
            },
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Embedding service unavailable — the course was not re-indexed. "
                "Existing embeddings were left untouched."
            ),
        ) from exc

    return RAGIndexResponse(
        status="degraded" if result.degraded else "success",
        chunks_indexed=result.chunks_indexed,
        degraded=result.degraded,
        degraded_reason=result.degraded_reason,
    )


# ============================================================================
# Session management endpoints
# ============================================================================


@router.get(
    "/rag/sessions",
    summary="List RAG chat sessions",
    description="List all RAG chat sessions owned by the current user, optionally filtered by organization via `org_slug`.",
    responses={
        200: {"description": "Object containing the list of chat sessions for the current user."},
        401: {"description": "Authentication required"},
    },
)
async def api_rag_sessions(
    current_user: PublicUser | AnonymousUser | APITokenUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    org_slug: Optional[str] = None,
):
    """List all chat sessions for the current user, optionally filtered by org."""
    org_id = None
    if org_slug:
        org = (await db_session.execute(
            select(Organization).where(Organization.slug == org_slug)
        )).scalars().first()
        if org:
            org_id = org.id
    sessions = get_user_chat_sessions(resolve_acting_user_id(current_user), org_id=org_id)
    return {"sessions": sessions}


@router.get(
    "/rag/sessions/{aichat_uuid}/messages",
    summary="Get RAG chat session messages",
    description="Load the full message history for a specific RAG chat session owned by the current user.",
    responses={
        200: {"description": "Object containing the session's messages."},
        401: {"description": "Authentication required"},
        404: {"description": "Session not found"},
    },
)
async def api_rag_session_messages(
    aichat_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser = Depends(get_current_user),
):
    """Load message history for a specific chat session."""
    messages = get_chat_messages(aichat_uuid, resolve_acting_user_id(current_user))
    if messages is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": messages}


@router.delete(
    "/rag/sessions/{aichat_uuid}",
    summary="Delete a RAG chat session",
    description="Delete a RAG chat session and all its messages. The session must belong to the current user.",
    responses={
        200: {"description": "Session deleted successfully."},
        401: {"description": "Authentication required"},
        404: {"description": "Session not found"},
    },
)
async def api_rag_session_delete(
    aichat_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser = Depends(get_current_user),
):
    """Delete a chat session."""
    deleted = delete_chat_session(aichat_uuid, resolve_acting_user_id(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


class RAGSessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    favorite: Optional[bool] = None


@router.patch(
    "/rag/sessions/{aichat_uuid}",
    summary="Update a RAG chat session",
    description="Update a RAG chat session's title and/or favorite status. The session must belong to the current user.",
    responses={
        200: {"description": "Session updated successfully."},
        401: {"description": "Authentication required"},
        404: {"description": "Session not found"},
    },
)
async def api_rag_session_update(
    aichat_uuid: str,
    body: RAGSessionUpdateRequest,
    current_user: PublicUser | AnonymousUser | APITokenUser = Depends(get_current_user),
):
    """Update session title and/or favorite status."""
    updated = update_chat_session_meta(
        aichat_uuid, resolve_acting_user_id(current_user),
        title=body.title,
        favorite=body.favorite,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": updated}
