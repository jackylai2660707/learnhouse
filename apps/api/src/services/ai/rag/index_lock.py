"""Database-wide serialization for RAG index swaps and content writers.

Every PostgreSQL transaction that flushes indexed course content acquires the
same per-course transaction advisory lock used by ``embed_course_content``.
The lock lives in PostgreSQL, so it coordinates API workers and background
workers across processes and hosts. SQLite tests skip it.
"""

from collections.abc import Iterable

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session
from sqlmodel.ext.asyncio.session import AsyncSession


COURSE_INDEX_LOCK_NAMESPACE = 5_390_279
_DECLARED_COURSE_IDS_KEY = "rag_indexed_content_course_ids"
_ACQUIRED_COURSE_IDS_KEY = "rag_indexed_content_acquired_course_ids"
_COURSE_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock("
    "CAST(:namespace AS INTEGER), CAST(:course_id AS INTEGER))"
)


def _postgresql_bind(session: Session | AsyncSession):
    bind = session.get_bind()
    return bind if bind.dialect.name == "postgresql" else None


async def acquire_course_index_lock(
    course_id: int,
    db_session: AsyncSession,
) -> None:
    """Declare and lock the index transaction's single-course write set."""
    await declare_course_index_write_set([course_id], db_session)


def _normalize_course_ids(course_ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                course_id
                for course_id in course_ids
                if isinstance(course_id, int) and course_id > 0
            }
        )
    )


def _declare_or_validate_course_set(
    session: Session,
    course_ids: Iterable[int],
) -> tuple[int, ...]:
    normalized = _normalize_course_ids(course_ids)
    if not normalized:
        return normalized
    declared_ids = session.info.get(_DECLARED_COURSE_IDS_KEY)
    if declared_ids is None:
        session.info[_DECLARED_COURSE_IDS_KEY] = frozenset(normalized)
    elif not set(normalized).issubset(declared_ids):
        raise RuntimeError(
            "Indexed-content transactions cannot expand their course set "
            "after declaration; declare the complete write set before any "
            "mutation or autoflush."
        )
    return normalized


async def declare_course_index_write_set(
    course_ids: Iterable[int],
    db_session: AsyncSession,
) -> tuple[int, ...]:
    """Declare and acquire a transaction's complete indexed-course lock set.

    Call this before mutations whenever a workflow may touch more than one
    course or may flush incrementally. The declaration is immutable for the
    outer transaction; subsequent flushes may only touch a subset.
    """
    sync_session = db_session.sync_session
    normalized = _declare_or_validate_course_set(sync_session, course_ids)
    if not normalized or _postgresql_bind(db_session) is None:
        return normalized

    acquired_ids = sync_session.info.setdefault(
        _ACQUIRED_COURSE_IDS_KEY,
        set(),
    )
    with db_session.no_autoflush:
        for course_id in normalized:
            if course_id in acquired_ids:
                continue
            await db_session.execute(
                text(_COURSE_LOCK_SQL),
                {
                    "namespace": COURSE_INDEX_LOCK_NAMESPACE,
                    "course_id": course_id,
                },
            )
            acquired_ids.add(course_id)
    return normalized


def _indexed_course_ids(objects: Iterable[object]) -> list[int]:
    """Return sorted old and new course IDs changed by an ORM flush."""
    # Lazy imports avoid a model/service import cycle during application boot.
    from src.db.courses.activities import Activity
    from src.db.courses.blocks import Block
    from src.db.courses.chapter_activities import ChapterActivity
    from src.db.courses.chapters import Chapter
    from src.db.courses.course_chapters import CourseChapter
    from src.db.courses.courses import Course

    child_types = (Activity, Block, ChapterActivity, Chapter, CourseChapter)
    course_ids: set[int] = set()
    for obj in objects:
        if isinstance(obj, Course):
            candidate_ids = [obj.id]
        elif isinstance(obj, child_types):
            course_id_state = inspect(obj).attrs.course_id
            candidate_ids = [obj.course_id, *course_id_state.history.deleted]
        else:
            continue
        for course_id in candidate_ids:
            if isinstance(course_id, int) and course_id > 0:
                course_ids.add(course_id)
    # Global ordering prevents two multi-course writer transactions from
    # deadlocking while acquiring the same set in opposite orders.
    return sorted(course_ids)


def _lock_indexed_content_writes(
    session: Session,
    _flush_context,
    _instances,
) -> None:
    """SQLAlchemy before_flush hook for indexed content mutations."""
    course_ids = _indexed_course_ids(
        [*session.new, *session.dirty, *session.deleted]
    )
    if not course_ids:
        return

    # Without an explicit predeclaration, the first indexed flush implicitly
    # declares its complete set. Once declared, expansion fails before any new
    # lock is acquired, eliminating cross-flush reverse-order deadlocks.
    _declare_or_validate_course_set(session, course_ids)
    if _postgresql_bind(session) is None:
        return

    connection = session.connection()
    statement = text(_COURSE_LOCK_SQL)
    acquired_ids = session.info.setdefault(_ACQUIRED_COURSE_IDS_KEY, set())
    for course_id in course_ids:
        if course_id in acquired_ids:
            continue
        connection.execute(
            statement,
            {
                "namespace": COURSE_INDEX_LOCK_NAMESPACE,
                "course_id": course_id,
            },
        )
        acquired_ids.add(course_id)


def _clear_declared_course_ids(session: Session, transaction) -> None:
    """Reset state only when the outermost transaction has ended."""
    if transaction.parent is not None:
        return
    session.info.pop(_DECLARED_COURSE_IDS_KEY, None)
    session.info.pop(_ACQUIRED_COURSE_IDS_KEY, None)


def install_indexed_content_write_lock() -> None:
    """Install the process hook once; coordination itself remains in Postgres."""
    if not event.contains(Session, "before_flush", _lock_indexed_content_writes):
        event.listen(Session, "before_flush", _lock_indexed_content_writes)
    if not event.contains(
        Session,
        "after_transaction_end",
        _clear_declared_course_ids,
    ):
        event.listen(Session, "after_transaction_end", _clear_declared_course_ids)
