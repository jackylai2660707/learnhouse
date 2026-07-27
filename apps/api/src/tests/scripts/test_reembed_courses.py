import pytest

from scripts import reembed_courses
from src.services.ai.rag.embedding_service import EmbeddingResult


async def test_semantic_verify_requires_chinese_and_english(monkeypatch):
    related = [1.0] + [0.0] * 767
    unrelated = [0.0, 1.0] + [0.0] * 766
    vectors = iter([related, related, unrelated, related, related, unrelated])

    async def embed(_text):
        return EmbeddingResult(vectors=[next(vectors)])

    monkeypatch.setattr(reembed_courses, "embed_single_text", embed)

    result = await reembed_courses.verify_semantic()

    assert result["semantic"] is True
    assert set(result["language_checks"]) == {"en", "zh-Hant"}
    assert all(check["healthy"] for check in result["language_checks"].values())


class _MappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row
        self.statement = ""

    async def execute(self, statement):
        self.statement = str(statement)
        return _MappingResult(self.row)


async def test_vector_database_verify_checks_version_cosine_index_dimensions_and_norms():
    session = _Session({
        "pgvector_version": "0.8.1",
        "index_definition": (
            "CREATE INDEX ix_course_embedding_embedding_hnsw_cosine "
            "ON course_embedding USING hnsw (embedding vector_cosine_ops)"
        ),
        "vectors_total": 312,
        "invalid_dimensions": 0,
        "invalid_norms": 0,
    })

    result = await reembed_courses.verify_vector_database(session)

    assert result["healthy"] is True
    assert result["vectors_total"] == 312
    assert "chunk_text" not in session.statement
    assert "vector_dims(embedding)" in session.statement
    assert "vector_norm(embedding)" in session.statement


def test_verify_only_rejects_indexing_options():
    with pytest.raises(SystemExit):
        reembed_courses.parse_args(["--verify-only", "--course-id", "7"])
