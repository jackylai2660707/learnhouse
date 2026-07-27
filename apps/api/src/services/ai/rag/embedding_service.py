"""
Embedding service for RAG.

Uses a dedicated embedding backend (see `get_embedding_client`) and
llama-index-core SentenceSplitter for chunking.

A note on the lexical hash fallback below, because it caused a silent outage:
it produces a blake2b bag-of-tokens fingerprint, NOT an embedding. Nearest
neighbours over those vectors are lexical overlaps at best — measured cosine
similarity of "volcano" vs "volcanoes" was exactly 0.0. Because the old code
returned those vectors from the same function on the same code path as real
embeddings, RAG looked healthy while retrieving nothing useful.

So the fallback is now opt-in and loud:
  * By default an embedding failure raises `EmbeddingUnavailableError`. A
    failed request is strictly better than a plausible-looking wrong answer.
  * Setting LEARNHOUSE_EMBEDDING_ALLOW_HASH_FALLBACK=true re-enables it, but
    every result is tagged `degraded=True` with a reason callers must
    propagate, each use is logged at ERROR, and /health/ready reports it.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.course_embeddings import CourseEmbedding
from src.services.ai.base import (
    AIProviderError,
    embedding_backend_description,
    get_embedding_client,
)
from src.services.ai.rag.content_extraction import extract_all_course_content
from src.services.ai.rag.index_lock import acquire_course_index_lock

logger = logging.getLogger(__name__)

CHUNK_SIZE = 512
CHUNK_OVERLAP = 50
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768  # Must match Vector(768) in CourseEmbedding model
# Sized for the self-hosted CPU backend, which embeds roughly one 512-token
# chunk per second on this host. 32 keeps a single request near 30s — short
# enough to retry cheaply and to show progress in the logs — where a batch of
# 100 ran past the old 120s client timeout and could never complete.
EMBEDDING_BATCH_SIZE = 32
BATCH_DELAY_SECONDS = 0.5
MAX_RETRIES = 3

HASH_FALLBACK_ENV = "LEARNHOUSE_EMBEDDING_ALLOW_HASH_FALLBACK"

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")

_TRUTHY = {"1", "true", "yes", "on"}


class EmbeddingUnavailableError(RuntimeError):
    """Real embeddings could not be produced and the hash fallback is disabled.

    Raised instead of returning fake vectors. Callers should surface this —
    an error the operator can see is the whole point.
    """

    def __init__(self, code: str, *, operation: str) -> None:
        super().__init__(
            f"Embeddings unavailable during {operation} ({code}). "
            f"RAG cannot answer without real embeddings. Check the embedding "
            f"service at LEARNHOUSE_EMBEDDING_BASE_URL, or set "
            f"{HASH_FALLBACK_ENV}=true to accept degraded lexical-hash search."
        )
        self.code = code
        self.operation = operation


class StaleCourseIndexError(RuntimeError):
    """Course content changed while its embeddings were being generated."""

    def __init__(self, course_id: int) -> None:
        super().__init__(
            f"Course {course_id} changed while its RAG index was being built; "
            "the stale index was not written."
        )
        self.course_id = course_id


@dataclass(frozen=True)
class EmbeddingResult:
    """Vectors plus an unmissable signal about how they were produced.

    `degraded` exists so no caller can mistake hash fingerprints for
    embeddings without going out of its way to ignore the flag.
    """

    vectors: list[list[float]]
    degraded: bool = False
    degraded_reason: str | None = None

    @property
    def vector(self) -> list[float]:
        """First vector — convenience for single-text callers."""
        return self.vectors[0]


@dataclass(frozen=True)
class CourseIndexResult:
    """Outcome of indexing one course."""

    chunks_indexed: int
    degraded: bool = False
    degraded_reason: str | None = None


@dataclass
class _DegradationState:
    """Last known degradation, surfaced by /health/ready."""

    degraded: bool = False
    reason: str | None = None
    at: str | None = None
    count: int = 0
    detail: dict = field(default_factory=dict)


_state = _DegradationState()


def hash_fallback_enabled() -> bool:
    """Read at call time so the setting can be flipped without a code change."""
    return os.environ.get(HASH_FALLBACK_ENV, "").strip().lower() in _TRUTHY


def embedding_status() -> dict:
    """Current embedding backend health, for the readiness report."""
    backend, base_url = embedding_backend_description()
    return {
        "backend": backend,
        "dedicated_endpoint_configured": backend == "dedicated",
        "hash_fallback_allowed": hash_fallback_enabled(),
        "degraded": _state.degraded,
        "degraded_reason": _state.reason,
        "degraded_at": _state.at,
        "degraded_count": _state.count,
        # base_url is deployment config, never a secret, and is the first thing
        # anyone debugging this needs to see.
        "base_url": base_url,
    }


def reset_embedding_status() -> None:
    """Clear the degradation marker (used by tests)."""
    global _state
    _state = _DegradationState()


def chunk_text(text: str) -> list[str]:
    """Split text into chunks using LlamaIndex SentenceSplitter."""
    from llama_index.core.node_parser import SentenceSplitter

    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks = splitter.split_text(text)
    return chunks


def course_content_revision(content_items: list[dict]) -> str:
    """Return a stable fingerprint of everything that will enter the index.

    The extracted text is the source of truth here, rather than process-local
    timestamps. This also covers PDF bytes and block content that may not bump
    the parent course's ``update_date``.
    """
    serialized_items = sorted(
        json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        for item in content_items
    )
    digest = hashlib.sha256()
    for serialized in serialized_items:
        digest.update(serialized.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def normalize_embedding_dimensions(values: list[float]) -> list[float]:
    """Ensure vectors match CourseEmbedding's pgvector dimension.

    The configured backend (jina-embeddings-v2-base-zh) is natively 768-wide,
    so this is a no-op in practice. It only pads or truncates when someone
    swaps in a model of a different width — which is worth an ERROR, since
    padding an embedding with zeros quietly distorts every distance.
    """
    if len(values) == EMBEDDING_DIMENSIONS:
        return values
    logger.error(
        "ai.embedding.dimension_mismatch",
        extra={
            "integration": "ai_embedding",
            "operation": "normalize_dimensions",
            "received_dimensions": len(values),
            "expected_dimensions": EMBEDDING_DIMENSIONS,
        },
    )
    if len(values) > EMBEDDING_DIMENSIONS:
        return values[:EMBEDDING_DIMENSIONS]
    return values + [0.0] * (EMBEDDING_DIMENSIONS - len(values))


def generate_local_embedding(text: str) -> list[float]:
    """Deterministic lexical fingerprint. NOT a semantic embedding.

    Kept as a genuine last resort so an org with a dead embedding backend can
    still get keyword-ish retrieval instead of nothing — but only when an
    operator has explicitly accepted that trade via HASH_FALLBACK_ENV.
    """
    vector = [0.0] * EMBEDDING_DIMENSIONS
    normalized_text = text.lower()
    tokens = _TOKEN_RE.findall(normalized_text)
    compact_cjk = "".join(ch for ch in normalized_text if "\u4e00" <= ch <= "\u9fff")
    tokens.extend(compact_cjk[i:i + 2] for i in range(max(0, len(compact_cjk) - 1)))

    for token in tokens:
        if not token:
            continue
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + min(len(token), 12) / 12.0
        vector[index] += sign * weight

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def generate_local_embeddings(texts: list[str]) -> list[list[float]]:
    return [generate_local_embedding(text) for text in texts]


def _embedding_failure(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, AIProviderError):
        return exc.code, exc.retryable
    return f"{type(exc).__name__}", True


def _record_degradation(code: str, detail: dict) -> None:
    _state.degraded = True
    _state.reason = code
    _state.at = datetime.now().isoformat(timespec="seconds")
    _state.count += 1
    _state.detail = detail


def _degrade(
    texts: list[str],
    *,
    code: str,
    operation: str,
    reason: str,
) -> EmbeddingResult:
    """Handle an embedding failure: raise, or fall back loudly.

    Never returns quietly. Either the caller gets an exception, or it gets a
    result carrying `degraded=True` that it is expected to propagate.
    """
    backend, base_url = embedding_backend_description()
    detail = {
        "integration": "ai_embedding",
        "operation": operation,
        "error_code": code,
        "reason": reason,
        "backend": backend,
        "base_url": base_url,
    }
    _record_degradation(code, detail)

    if not hash_fallback_enabled():
        logger.error(
            "ai.embedding.unavailable",
            extra={**detail, "action": "request_failed"},
        )
        raise EmbeddingUnavailableError(code, operation=operation)

    # ERROR, not WARNING: these are lexical hashes standing in for semantic
    # search, and a WARNING is exactly how the previous outage went unseen.
    logger.error(
        "ai.embedding.hash_fallback_active",
        extra={
            **detail,
            "action": "returned_lexical_hash",
            "warning": (
                "RAG results are NOT semantic — vectors are blake2b lexical "
                "fingerprints. Fix the embedding backend."
            ),
            "chunks": len(texts),
        },
    )
    return EmbeddingResult(
        vectors=generate_local_embeddings(texts),
        degraded=True,
        degraded_reason=code,
    )


def _mark_healthy() -> None:
    if _state.degraded:
        logger.info(
            "ai.embedding.recovered",
            extra={
                "integration": "ai_embedding",
                "previous_error_code": _state.reason,
            },
        )
    _state.degraded = False
    _state.reason = None


async def generate_embeddings(texts: list[str]) -> EmbeddingResult:
    """
    Generate embeddings for a list of texts using the configured backend.

    Runs blocking calls off the event loop via asyncio.to_thread and retries
    transient failures with exponential backoff.

    Raises EmbeddingUnavailableError when the backend fails and the hash
    fallback is not explicitly enabled.
    """
    try:
        client = get_embedding_client()
    except Exception as exc:
        error_code, _ = _embedding_failure(exc)
        return _degrade(
            texts,
            code=error_code,
            operation="embed_batch",
            reason="client_unavailable",
        )

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i:i + EMBEDDING_BATCH_SIZE]

        for attempt in range(MAX_RETRIES):
            try:
                result = await asyncio.to_thread(
                    client.models.embed_content,
                    model=EMBEDDING_MODEL,
                    contents=batch,
                    config={"output_dimensionality": EMBEDDING_DIMENSIONS},
                )
                for emb in result.embeddings:
                    all_embeddings.append(normalize_embedding_dimensions(list(emb.values)))
                break
            except Exception as exc:
                error_code, retryable = _embedding_failure(exc)
                if attempt == MAX_RETRIES - 1 or not retryable:
                    # One bad batch poisons the whole set: mixing real vectors
                    # with hash vectors in one index is worse than either
                    # alone, because distances stop being comparable.
                    return _degrade(
                        texts,
                        code=error_code,
                        operation="embed_batch",
                        reason="provider_failure",
                    )
                wait = 2 ** attempt
                logger.warning(
                    "ai.embedding.retrying",
                    extra={
                        "integration": "ai_embedding",
                        "operation": "embed_batch",
                        "error_code": error_code,
                        "attempt": attempt + 1,
                        "max_attempts": MAX_RETRIES,
                        "retry_in_seconds": wait,
                    },
                )
                await asyncio.sleep(wait)

        if i + EMBEDDING_BATCH_SIZE < len(texts):
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    _mark_healthy()
    return EmbeddingResult(vectors=all_embeddings)


async def embed_single_text(text: str) -> EmbeddingResult:
    """Generate an embedding for a single text, with retry.

    Raises EmbeddingUnavailableError when the backend fails and the hash
    fallback is not explicitly enabled.
    """
    try:
        client = get_embedding_client()
    except Exception as exc:
        error_code, _ = _embedding_failure(exc)
        return _degrade(
            [text],
            code=error_code,
            operation="embed_single",
            reason="client_unavailable",
        )

    for attempt in range(MAX_RETRIES):
        try:
            result = await asyncio.to_thread(
                client.models.embed_content,
                model=EMBEDDING_MODEL,
                contents=[text],
                config={"output_dimensionality": EMBEDDING_DIMENSIONS},
            )
            _mark_healthy()
            return EmbeddingResult(
                vectors=[
                    normalize_embedding_dimensions(list(result.embeddings[0].values))
                ]
            )
        except Exception as exc:
            error_code, retryable = _embedding_failure(exc)
            if attempt == MAX_RETRIES - 1 or not retryable:
                return _degrade(
                    [text],
                    code=error_code,
                    operation="embed_single",
                    reason="provider_failure",
                )
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


async def embed_course_content(
    course_id: int,
    org_id: int,
    db_session: AsyncSession,
) -> CourseIndexResult:
    """
    Index all content from a course into embeddings.

    Generates new embeddings first, then atomically replaces old ones so a
    backend failure never leaves the course with zero searchable content.

    Raises EmbeddingUnavailableError — leaving existing embeddings untouched —
    when the backend fails and the hash fallback is not enabled.

    Raises StaleCourseIndexError when the extracted content no longer matches
    the course at swap time. The existing (newer) index is left untouched.
    """
    started = time.perf_counter()
    content_items = await extract_all_course_content(course_id, org_id, db_session)
    source_revision = course_content_revision(content_items)

    chunks_to_embed: list[tuple[str, dict]] = []
    for item in content_items:
        text = item["text"]
        if not text.strip():
            continue
        for idx, chunk in enumerate(chunk_text(text)):
            chunks_to_embed.append((chunk, {**item, "chunk_index": idx}))

    if chunks_to_embed:
        texts = [c[0] for c in chunks_to_embed]
        # Generate all embeddings before touching the DB — a backend failure
        # here leaves the existing embeddings intact rather than wiping them.
        embedding_result = await generate_embeddings(texts)
        embeddings = embedding_result.vectors
    else:
        # Empty extraction is a real index state. It must atomically remove old
        # rows, otherwise deleted/unpublished material remains retrievable.
        embedding_result = EmbeddingResult(vectors=[])
        embeddings = []

    if len(embeddings) != len(chunks_to_embed):
        # zip() below would silently drop the tail, leaving a partially
        # indexed course that still reports success.
        raise EmbeddingUnavailableError(
            "ai_embedding_count_mismatch", operation="embed_course_content"
        )

    now = str(datetime.now())
    new_records = [
        CourseEmbedding(
            org_id=org_id,
            course_id=course_id,
            activity_id=metadata.get("activity_id"),
            activity_uuid=metadata.get("activity_uuid", ""),
            block_uuid=metadata.get("block_uuid"),
            source_type=metadata.get("source_type", ""),
            chunk_text=chunk_text_val,
            chunk_index=metadata.get("chunk_index", 0),
            activity_name=metadata.get("activity_name", ""),
            chapter_name=metadata.get("chapter_name", ""),
            course_name=metadata.get("course_name", ""),
            embedding=embedding,
            creation_date=now,
            update_date=now,
        )
        for (chunk_text_val, metadata), embedding in zip(chunks_to_embed, embeddings)
    ]

    # Serialize only the short final swap across all API workers. Re-extract
    # while holding the transaction lock and compare the exact index input: a
    # slow run that embedded old content can never overwrite a newer run.
    await acquire_course_index_lock(course_id, db_session)
    current_content_items = await extract_all_course_content(
        course_id, org_id, db_session
    )
    current_revision = course_content_revision(current_content_items)
    if current_revision != source_revision:
        await db_session.rollback()
        logger.info(
            "ai.embedding.stale_course_index_discarded",
            extra={
                "integration": "rag",
                "operation": "embed_course_content",
                "course_id": course_id,
                "org_id": org_id,
            },
        )
        raise StaleCourseIndexError(course_id)

    # Delete + insert + commit is the atomic replacement. This also handles an
    # empty current extraction by deleting every stale row and inserting none.
    await db_session.execute(
        delete(CourseEmbedding).where(CourseEmbedding.course_id == course_id)
    )
    for record in new_records:
        db_session.add(record)
    await db_session.commit()

    # A degraded index is an operational incident, not an info line.
    log = logger.error if embedding_result.degraded else logger.info
    log(
        "ai.embedding.course_indexed",
        extra={
            "integration": "rag",
            "operation": "embed_course_content",
            "course_id": course_id,
            "org_id": org_id,
            "chunks": len(chunks_to_embed),
            "seconds": round(time.perf_counter() - started, 2),
            "degraded": embedding_result.degraded,
            "degraded_reason": embedding_result.degraded_reason,
        },
    )
    return CourseIndexResult(
        chunks_indexed=len(chunks_to_embed),
        degraded=embedding_result.degraded,
        degraded_reason=embedding_result.degraded_reason,
    )
