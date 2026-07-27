from types import SimpleNamespace

import pytest

import src.services.ai.rag.query_service as query_service
from src.services.ai.rag.embedding_service import EmbeddingResult


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None
        self.statements = []
        self.params = None

    async def execute(self, statement, params=None):
        self.statement = str(statement)
        self.statements.append(self.statement)
        if params is not None:
            self.params = params
        return _Result(self.rows)


async def test_rag_query_scopes_by_org_and_course_and_deduplicates_sources(monkeypatch):
    monkeypatch.setattr(
        query_service,
        "embed_single_text",
        lambda question: _async_value(EmbeddingResult(vectors=[[0.1, 0.2, 0.3]])),
    )
    rows = [
        SimpleNamespace(
            chunk_text="海水蒸發形成雲。",
            activity_uuid="activity_water",
            activity_name="水循環",
            chapter_name="天氣",
            course_name="小學科學",
            course_uuid="course_science",
            source_type="dynamic_page",
            block_uuid=None,
        ),
        SimpleNamespace(
            chunk_text="雲冷卻後形成降雨。",
            activity_uuid="activity_water",
            activity_name="水循環",
            chapter_name="天氣",
            course_name="小學科學",
            course_uuid="course_science",
            source_type="dynamic_page",
            block_uuid=None,
        ),
    ]
    session = _Session(rows)

    result = await query_service.query_course_rag(
        "雨是怎樣形成的？",
        org_id=7,
        course_id=9,
        db_session=session,
        top_k=5,
        authorized_course_ids=[9],
        authorized_activity_ids=[21],
    )

    assert "ce.org_id = :org_id AND ce.course_id = :course_id" in session.statement
    assert session.params["org_id"] == 7
    assert session.params["course_id"] == 9
    assert session.params["top_k"] == 5
    assert len(result["sources"]) == 1
    assert result["sources"][0] == {
        "citation_number": 1,
        "activity_uuid": "activity_water",
        "activity_name": "水循環",
        "chapter_name": "天氣",
        "course_name": "小學科學",
        "course_uuid": "course_science",
        "source_type": "dynamic_page",
    }
    assert result["context"].count("[Source 1]") == 2
    assert "CAST(:query_embedding AS vector(768))" in session.statement
    assert session.statements[:2] == [
        "SET LOCAL hnsw.iterative_scan = 'strict_order'",
        "SET LOCAL hnsw.ef_search = 100",
    ]


async def test_rag_query_applies_server_authorized_course_and_activity_ids(monkeypatch):
    embed = lambda question: _async_value(  # noqa: E731
        EmbeddingResult(vectors=[[0.1, 0.2, 0.3]])
    )
    monkeypatch.setattr(query_service, "embed_single_text", embed)
    session = _Session([])

    await query_service.query_course_rag(
        "scope test",
        org_id=7,
        db_session=session,
        authorized_course_ids=[9, 11, 9],
        authorized_activity_ids=[21, 22],
    )

    assert "ce.course_id = ANY(CAST(:authorized_course_ids AS INTEGER[]))" in session.statement
    assert "ce.activity_id = ANY(CAST(:authorized_activity_ids AS INTEGER[]))" in session.statement
    assert session.params["authorized_course_ids"] == [9, 11]
    assert session.params["authorized_activity_ids"] == [21, 22]


async def test_rag_query_does_not_embed_when_authorized_scope_is_empty(monkeypatch):
    called = False

    async def embed(_question):
        nonlocal called
        called = True
        return EmbeddingResult(vectors=[[0.1]])

    monkeypatch.setattr(query_service, "embed_single_text", embed)
    result = await query_service.query_course_rag(
        "secret probe",
        org_id=7,
        db_session=_Session([]),
        authorized_course_ids=[],
        authorized_activity_ids=[],
    )

    assert called is False
    assert result["context"] == ""
    assert result["sources"] == []


async def test_rag_query_requires_explicit_server_authorization_scope():
    """Low-level callers cannot omit authorization and get an org-wide query."""
    with pytest.raises(TypeError):
        await query_service.query_course_rag(
            "secret probe",
            org_id=7,
            db_session=_Session([]),
        )


async def _async_value(value):
    return value
