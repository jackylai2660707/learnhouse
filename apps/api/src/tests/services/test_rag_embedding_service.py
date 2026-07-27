import asyncio
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified, set_committed_value
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import src.services.ai.rag.embedding_service as embedding_service
import src.services.ai.rag.index_lock as index_lock
from src.db.course_embeddings import CourseEmbedding
from src.db.courses.activities import Activity


@pytest.fixture(autouse=True)
def _clear_embedding_degradation():
    """The degradation marker is module state shared across tests."""
    embedding_service.reset_embedding_status()
    yield
    embedding_service.reset_embedding_status()


def _stub_water_cycle_activity(activity):
    activity.content = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [{"type": "text", "text": "水循環"}],
            },
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "海水蒸發形成雲，冷卻後降雨並流回河流和海洋。",
                    }
                ],
            },
        ],
    }
    flag_modified(activity, "content")


class _FakeEmbeddings:
    """Stands in for a working backend, returning distinguishable vectors."""

    def embed_content(self, **kwargs):
        texts = kwargs.get("contents") or []
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[(index + 1) / 1000] * 768)
                for index, _ in enumerate(texts)
            ]
        )


async def test_course_indexing_stores_real_embeddings_and_source_metadata(
    monkeypatch,
    db,
    org,
    course,
    chapter,
    activity,
):
    _stub_water_cycle_activity(activity)
    db.add(activity)
    await db.commit()

    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: SimpleNamespace(models=_FakeEmbeddings()),
    )

    result = await embedding_service.embed_course_content(course.id, org.id, db)

    records = (
        await db.execute(
            select(CourseEmbedding).where(CourseEmbedding.course_id == course.id)
        )
    ).scalars().all()
    assert result.chunks_indexed == len(records)
    assert result.chunks_indexed >= 1
    assert result.degraded is False
    assert result.degraded_reason is None
    assert all(record.org_id == org.id for record in records)
    assert all(record.course_id == course.id for record in records)
    assert all(record.activity_uuid == activity.activity_uuid for record in records)
    assert all(record.activity_name == activity.name for record in records)
    assert all(record.chapter_name == chapter.name for record in records)
    assert all(len(record.embedding) == 768 for record in records)
    assert any("水循環" in record.chunk_text for record in records)


async def test_course_indexing_refuses_to_write_hash_vectors_by_default(
    monkeypatch,
    db,
    org,
    course,
    chapter,
    activity,
):
    """A dead backend must not quietly fill the index with lexical hashes.

    This is the exact regression that made RAG look healthy while retrieving
    nothing: indexing "succeeded" and the stored rows looked like embeddings.
    """
    _stub_water_cycle_activity(activity)
    db.add(activity)
    await db.commit()

    monkeypatch.delenv(embedding_service.HASH_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: (_ for _ in ()).throw(RuntimeError("provider-internal-secret")),
    )

    with pytest.raises(embedding_service.EmbeddingUnavailableError):
        await embedding_service.embed_course_content(course.id, org.id, db)

    # Nothing was written: a failed reindex leaves the index exactly as it was.
    records = (
        await db.execute(
            select(CourseEmbedding).where(CourseEmbedding.course_id == course.id)
        )
    ).scalars().all()
    assert records == []


async def test_course_indexing_flags_degradation_when_fallback_opted_in(
    monkeypatch,
    db,
    org,
    course,
    chapter,
    activity,
):
    _stub_water_cycle_activity(activity)
    db.add(activity)
    await db.commit()

    monkeypatch.setenv(embedding_service.HASH_FALLBACK_ENV, "true")
    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: (_ for _ in ()).throw(RuntimeError("provider-internal-secret")),
    )

    result = await embedding_service.embed_course_content(course.id, org.id, db)

    assert result.chunks_indexed >= 1
    # Rows are written, but the caller is explicitly told they are not semantic.
    assert result.degraded is True
    assert result.degraded_reason == "RuntimeError"
    assert embedding_service.embedding_status()["degraded"] is True


async def test_empty_extraction_atomically_deletes_existing_course_embeddings(
    monkeypatch,
    db,
    org,
    course,
):
    """Deleting all indexable content must not leave old answers searchable."""
    old = CourseEmbedding(
        org_id=org.id,
        course_id=course.id,
        activity_id=None,
        activity_uuid="activity_deleted",
        source_type="dynamic_page",
        chunk_text="Deleted private lesson",
        chunk_index=0,
        embedding=[0.1] * 768,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(old)
    await db.commit()
    monkeypatch.setattr(
        embedding_service,
        "extract_all_course_content",
        AsyncMock(return_value=[]),
    )
    generate = AsyncMock()
    monkeypatch.setattr(embedding_service, "generate_embeddings", generate)

    result = await embedding_service.embed_course_content(course.id, org.id, db)

    remaining = (
        await db.execute(
            select(CourseEmbedding).where(CourseEmbedding.course_id == course.id)
        )
    ).scalars().all()
    assert result.chunks_indexed == 0
    assert remaining == []
    generate.assert_not_awaited()


async def test_stale_reindex_cannot_overwrite_newer_course_content(
    monkeypatch,
    db,
    org,
    course,
):
    """A slow old run is discarded after the database-wide final lock."""
    old_items = [{
        "text": "Old lesson",
        "activity_id": 1,
        "activity_uuid": "activity_test",
        "activity_name": "Lesson",
        "chapter_name": "Chapter",
        "course_name": "Course",
        "source_type": "dynamic_page",
        "block_uuid": None,
    }]
    new_items = [{**old_items[0], "text": "New lesson"}]
    extract = AsyncMock(side_effect=[old_items, new_items])
    monkeypatch.setattr(embedding_service, "extract_all_course_content", extract)
    monkeypatch.setattr(embedding_service, "chunk_text", lambda value: [value])
    monkeypatch.setattr(
        embedding_service,
        "generate_embeddings",
        AsyncMock(
            return_value=embedding_service.EmbeddingResult(
                vectors=[[0.2] * 768]
            )
        ),
    )
    newer_index = CourseEmbedding(
        org_id=org.id,
        course_id=course.id,
        activity_id=1,
        activity_uuid="activity_test",
        source_type="dynamic_page",
        chunk_text="New lesson",
        chunk_index=0,
        embedding=[0.3] * 768,
        creation_date="2026-01-02",
        update_date="2026-01-02",
    )
    db.add(newer_index)
    await db.commit()

    with pytest.raises(embedding_service.StaleCourseIndexError):
        await embedding_service.embed_course_content(course.id, org.id, db)

    remaining = (
        await db.execute(
            select(CourseEmbedding).where(CourseEmbedding.course_id == course.id)
        )
    ).scalars().all()
    assert [record.chunk_text for record in remaining] == ["New lesson"]


async def test_postgresql_course_index_lock_is_transaction_scoped():
    sync_session = SimpleNamespace(info={})
    session = SimpleNamespace(
        sync_session=sync_session,
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
        execute=AsyncMock(),
        no_autoflush=nullcontext(),
    )

    await index_lock.acquire_course_index_lock(77, session)

    statement, params = session.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params["course_id"] == 77
    assert sync_session.info[index_lock._DECLARED_COURSE_IDS_KEY] == frozenset({77})


async def test_predeclared_multi_course_write_set_locks_all_ids_sorted():
    sync_session = SimpleNamespace(info={})
    session = SimpleNamespace(
        sync_session=sync_session,
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
        execute=AsyncMock(),
        no_autoflush=nullcontext(),
    )

    declared = await index_lock.declare_course_index_write_set(
        [10, 5, 10],
        session,
    )

    assert declared == (5, 10)
    assert [
        call.args[1]["course_id"] for call in session.execute.await_args_list
    ] == [5, 10]
    assert sync_session.info[index_lock._DECLARED_COURSE_IDS_KEY] == frozenset(
        {5, 10}
    )


@pytest.mark.asyncio
async def test_content_writer_flush_uses_same_postgresql_course_lock(activity):
    execute = Mock()
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
        new=[],
        dirty=[activity],
        deleted=[],
        info={},
        connection=lambda: SimpleNamespace(execute=execute),
    )

    index_lock._lock_indexed_content_writes(session, None, None)

    statement, params = execute.call_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {
        "namespace": index_lock.COURSE_INDEX_LOCK_NAMESPACE,
        "course_id": activity.course_id,
    }


def test_course_move_locks_old_and_new_course_ids_in_sorted_order(activity):
    execute = Mock()
    set_committed_value(activity, "course_id", 10)
    activity.course_id = 5

    assert index_lock._indexed_course_ids([activity]) == [5, 10]
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
        new=[],
        dirty=[activity],
        deleted=[],
        info={},
        connection=lambda: SimpleNamespace(execute=execute),
    )
    index_lock._lock_indexed_content_writes(session, None, None)

    assert [call.args[1]["course_id"] for call in execute.call_args_list] == [5, 10]


def test_indexed_transaction_rejects_course_set_expansion_across_flushes(activity):
    execute = Mock()
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql")
        ),
        new=[],
        dirty=[activity],
        deleted=[],
        info={},
        connection=lambda: SimpleNamespace(execute=execute),
    )
    set_committed_value(activity, "course_id", 10)
    index_lock._lock_indexed_content_writes(session, None, None)

    set_committed_value(activity, "course_id", 5)
    with pytest.raises(RuntimeError, match="cannot expand their course set"):
        index_lock._lock_indexed_content_writes(session, None, None)

    assert [call.args[1]["course_id"] for call in execute.call_args_list] == [10]


def test_central_database_import_installs_index_lock_in_fresh_process():
    api_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["TESTING"] = "true"
    code = """
from sqlalchemy import event
from sqlalchemy.orm import Session
import src.core.events.database
from src.services.ai.rag.index_lock import _lock_indexed_content_writes
assert event.contains(Session, 'before_flush', _lock_indexed_content_writes)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=api_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_repair_script_declares_index_lock_contract():
    api_root = Path(__file__).resolve().parents[3]
    script = (api_root / "scripts" / "repair_challenge_content.py").read_text()

    assert "install_indexed_content_write_lock()" in script
    assert "await declare_course_index_write_set(" in script


def test_nested_transaction_does_not_clear_declared_course_set():
    session = SimpleNamespace(
        info={
            index_lock._DECLARED_COURSE_IDS_KEY: frozenset({5, 10}),
            index_lock._ACQUIRED_COURSE_IDS_KEY: {5, 10},
        }
    )

    index_lock._clear_declared_course_ids(
        session,
        SimpleNamespace(parent=object()),
    )
    assert session.info[index_lock._DECLARED_COURSE_IDS_KEY] == frozenset({5, 10})

    index_lock._clear_declared_course_ids(
        session,
        SimpleNamespace(parent=None),
    )
    assert index_lock._DECLARED_COURSE_IDS_KEY not in session.info
    assert index_lock._ACQUIRED_COURSE_IDS_KEY not in session.info


@pytest.mark.asyncio
async def test_postgresql_writer_waits_for_index_recheck_swap_lock(
    engine,
    activity,
):
    """Two real sessions prove writers cannot cross the recheck/swap window."""
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL advisory-lock concurrency regression")

    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as index_session, factory() as writer_session:
        await index_lock.acquire_course_index_lock(activity.course_id, index_session)

        writer_activity = await writer_session.get(Activity, activity.id)
        assert writer_activity is not None
        writer_activity.content = {"type": "doc", "content": []}
        flag_modified(writer_activity, "content")
        writer_commit = asyncio.create_task(writer_session.commit())

        await asyncio.sleep(0.1)
        assert writer_commit.done() is False

        await index_session.commit()
        await asyncio.wait_for(writer_commit, timeout=2)


async def test_embedding_outage_is_optional_readiness_degradation(monkeypatch):
    """An unavailable optional model is visible but cannot make core unready."""
    import src.services.health.health as health

    monkeypatch.delenv(embedding_service.HASH_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        embedding_service,
        "get_embedding_client",
        lambda: (_ for _ in ()).throw(RuntimeError("backend down")),
    )
    with pytest.raises(embedding_service.EmbeddingUnavailableError):
        await embedding_service.embed_single_text("health probe")

    config = SimpleNamespace(
        ai_config=SimpleNamespace(is_ai_enabled=False),
        judge0_config=None,
        mailing_config=object(),
        general_config=SimpleNamespace(
            sentry_config=SimpleNamespace(dsn=None),
        ),
        hosting_config=SimpleNamespace(
            content_delivery=SimpleNamespace(
                type="s3api",
                s3api=SimpleNamespace(
                    bucket_name="configured",
                    endpoint_url="https://storage.example.test",
                ),
            ),
        ),
    )
    monkeypatch.setattr(health, "get_learnhouse_config", lambda: config)
    monkeypatch.setattr(
        health,
        "email_configuration_status",
        lambda _mail: SimpleNamespace(configured=True, code="configured"),
    )

    status = health._optional_integration_statuses()["embeddings"]

    assert status.status == "degraded"
    assert status.required is False
    assert status.code.startswith("embeddings_degraded:")
