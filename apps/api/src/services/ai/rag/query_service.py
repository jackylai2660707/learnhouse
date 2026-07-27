"""
RAG query service.

Handles vector similarity search and streaming LLM responses
grounded in course content.
"""

import logging
from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from src.services.ai.rag.embedding_service import embed_single_text
from src.services.ai.base import ask_ai_stream

logger = logging.getLogger(__name__)

TOP_K = 5
GEMINI_MODEL = "gemini-2.5-flash"
HNSW_EF_SEARCH = 100


async def query_course_rag(
    question: str,
    org_id: int,
    db_session: AsyncSession,
    course_id: Optional[int] = None,
    top_k: int = TOP_K,
    *,
    authorized_course_ids: list[int],
    authorized_activity_ids: list[int],
) -> dict:
    """
    Retrieve relevant course content via vector similarity search.

    Args:
        question: The user's question
        org_id: Organization ID to scope the search
        course_id: Optional course ID to scope to a single course (None = all courses)
        db_session: Database session
        top_k: Number of results to return
        authorized_course_ids: Caller-authorized course IDs. RAG HTTP entry
            points must always provide this server-computed scope.
        authorized_activity_ids: Published/unlocked activity IDs visible to
            the caller, including drafts only for course updaters.

    Returns:
        {context: str, sources: list[dict], degraded: bool, degraded_reason: str|None}

    Raises:
        EmbeddingUnavailableError: the embedding backend is down and the
            lexical-hash fallback has not been explicitly enabled. Failing is
            deliberate — hash vectors retrieve near-random chunks, and an
            answer grounded in random chunks is worse than no answer.
    """
    authorized_course_ids = sorted(set(authorized_course_ids))
    authorized_activity_ids = sorted(set(authorized_activity_ids))
    if (
        not authorized_course_ids
        or not authorized_activity_ids
        or (course_id is not None and course_id not in authorized_course_ids)
    ):
        return {
            "context": "",
            "sources": [],
            "degraded": False,
            "degraded_reason": None,
        }

    # Embed the question only after proving there is authorized material to
    # search. This prevents provider calls from becoming a side channel.
    embedding_result = await embed_single_text(question)
    query_embedding = embedding_result.vector

    # Build the similarity search query
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    where_clauses = ["ce.org_id = :org_id"]
    params = {
        "query_embedding": embedding_str,
        "org_id": org_id,
        "top_k": top_k,
    }
    if course_id is not None:
        where_clauses.append("ce.course_id = :course_id")
        params["course_id"] = course_id
    where_clauses.append(
        "ce.course_id = ANY(CAST(:authorized_course_ids AS INTEGER[]))"
    )
    params["authorized_course_ids"] = authorized_course_ids
    where_clauses.append(
        "ce.activity_id = ANY(CAST(:authorized_activity_ids AS INTEGER[]))"
    )
    params["authorized_activity_ids"] = authorized_activity_ids

    sql = text(f"""
        SELECT ce.id, ce.chunk_text, ce.activity_uuid, ce.activity_name,
               ce.chapter_name, ce.course_name, ce.source_type, ce.block_uuid,
               c.course_uuid,
               ce.embedding <=> CAST(:query_embedding AS vector(768)) AS distance
        FROM course_embedding ce
        JOIN course c ON c.id = ce.course_id
        WHERE {" AND ".join(where_clauses)}
        ORDER BY ce.embedding <=> CAST(:query_embedding AS vector(768))
        LIMIT :top_k
    """)

    # Org/course filters are applied after approximate index traversal. Strict
    # iterative scan keeps searching until enough filtered neighbours exist;
    # the higher ef_search favours recall for the small top-k used by RAG.
    await db_session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    await db_session.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
    results = (await db_session.execute(sql, params)).fetchall()

    degraded_fields = {
        "degraded": embedding_result.degraded,
        "degraded_reason": embedding_result.degraded_reason,
    }

    if not results:
        return {"context": "", "sources": [], **degraded_fields}

    # Build numbered context and deduplicated source list
    context_parts = []
    sources = []
    seen_sources = {}  # source_key -> source index (1-based)
    source_index = 0

    for row in results:
        chunk_text = row.chunk_text
        activity_name = row.activity_name
        chapter_name = row.chapter_name
        course_name = row.course_name
        source_type = row.source_type

        # Deduplicate sources and assign a stable number
        source_key = (row.activity_uuid, row.source_type, row.block_uuid)
        if source_key not in seen_sources:
            source_index += 1
            seen_sources[source_key] = source_index
            sources.append({
                "citation_number": source_index,
                "activity_uuid": row.activity_uuid,
                "activity_name": activity_name,
                "chapter_name": chapter_name,
                "course_name": course_name,
                "course_uuid": row.course_uuid,
                "source_type": source_type,
            })

        ref_num = seen_sources[source_key]
        context_parts.append(f"[Source {ref_num}]\n{chunk_text}")

    context = "\n\n---\n\n".join(context_parts)
    return {"context": context, "sources": sources, **degraded_fields}


async def query_course_rag_stream(
    question: str,
    org_id: int,
    db_session: AsyncSession,
    message_history: list,
    course_id: Optional[int] = None,
    mode: str = "course_only",
    *,
    authorized_course_ids: list[int],
    authorized_activity_ids: list[int],
) -> tuple[AsyncGenerator[str, None], list[dict]]:
    """
    Perform RAG retrieval and return a streaming LLM response.

    Returns:
        Tuple of (stream_generator, sources)
    """
    # Retrieve relevant context
    rag_result = await query_course_rag(
        question=question,
        org_id=org_id,
        db_session=db_session,
        course_id=course_id,
        authorized_course_ids=authorized_course_ids,
        authorized_activity_ids=authorized_activity_ids,
    )

    context = rag_result["context"]
    sources = rag_result["sources"]
    degraded = bool(rag_result.get("degraded"))

    # Build the grounding prompt based on mode
    citation_instructions = (
        f"IMPORTANT: Only use source numbers 1 through {len(sources)}. When referencing information "
        "from the provided sources, use separate numbered citations like [1] [2], matching the "
        "source numbers exactly. Never combine numbers inside one label. Do NOT write full source names, "
        "paths, locations, or verbose references like '(From: Course > Chapter > Activity)'. "
        "Just use the short [1] notation inline. Example: 'The building has 5 floors [2].'"
    )

    if context and mode == "general":
        system_prompt = (
            "You are a helpful, knowledgeable educational assistant. Answer the student's question "
            "thoroughly using both the course content provided below AND your own general knowledge. "
            "Treat the course content as your primary reference, but freely expand with additional "
            "context, explanations, examples, and insights from your training data. "
            "When you add information beyond the course material, wrap that part in a blockquote "
            "using the > prefix.\n\n"
            f"{citation_instructions}\n\n"
            f"Course Content:\n{context}"
        )
    elif context:
        # course_only mode (default)
        system_prompt = (
            "You are a helpful educational assistant. Answer the student's question "
            "based on the course content provided below.\n\n"
            f"{citation_instructions}\n\n"
            "SUPPLEMENTARY KNOWLEDGE: When you add any information that is NOT directly from the "
            "provided course content — even small additions, clarifications, or general context — "
            "you MUST wrap that part in a blockquote using the > prefix. Always do this, even for "
            "brief supplementary notes. Example:\n"
            "> This is additional context from general knowledge.\n\n"
            f"Course Content:\n{context}"
        )
    elif mode == "general":
        system_prompt = (
            "You are a helpful, knowledgeable educational assistant. No specific course content "
            "was found for this question, but that's fine — answer the student's question using "
            "your general knowledge. Be thorough and helpful."
        )
    else:
        system_prompt = (
            "You are a helpful educational assistant. The student asked a question but "
            "no relevant course content was found. Let them know you couldn't find "
            "specific course material related to their question, but offer to help "
            "with what you know."
        )

    # Create the streaming generator
    stream = ask_ai_stream(
        question=question,
        message_history=message_history,
        text_reference=context,
        message_for_the_prompt=system_prompt,
        gemini_model_name=GEMINI_MODEL,
    )

    if degraded:
        # Reachable only when an operator has explicitly enabled the lexical
        # hash fallback. The retrieved context is then keyword noise, so say
        # so in the answer itself — a log line nobody reads is how this broke
        # the first time.
        logger.error(
            "rag.query.degraded_retrieval",
            extra={
                "integration": "rag",
                "operation": "query_course_rag_stream",
                "error_code": rag_result.get("degraded_reason"),
                "course_id": course_id,
                "org_id": org_id,
            },
        )
        stream = _with_degraded_notice(stream)

    return stream, sources


DEGRADED_RETRIEVAL_NOTICE = (
    "> ⚠️ **Search is degraded.** The embedding service is unavailable, so "
    "course content was matched by keyword overlap instead of meaning. "
    "The sources below may be irrelevant — please tell an administrator.\n\n"
)


class NonModelStreamChunk(str):
    """Static stream content that must not consume a generation credit."""


async def _with_degraded_notice(
    stream: AsyncGenerator[str, None],
) -> AsyncGenerator[str, None]:
    """Prefix a streamed answer with a visible degradation warning."""
    yield NonModelStreamChunk(DEGRADED_RETRIEVAL_NOTICE)
    async for chunk in stream:
        yield chunk
