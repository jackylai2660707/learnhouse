"""Add cosine HNSW index for course embeddings.

Revision ID: g8h9i0j1k2l3
Revises: f7g8h9i0j1k2
Create Date: 2026-07-26

"""

from typing import Sequence, Union

from alembic import op


revision: str = "g8h9i0j1k2l3"
down_revision: Union[str, None] = "f7g8h9i0j1k2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_course_embedding_embedding_hnsw_cosine"


def upgrade() -> None:
    # Filtered HNSW queries below rely on iterative scans, introduced in
    # pgvector 0.8. Fail before creating an index that the application cannot
    # query correctly when org/course filters discard early neighbours.
    op.execute(
        """
        DO $$
        DECLARE installed_version text;
        BEGIN
            SELECT extversion INTO installed_version
            FROM pg_extension
            WHERE extname = 'vector';

            IF installed_version IS NULL THEN
                RAISE EXCEPTION 'pgvector extension is required for RAG';
            END IF;
            IF string_to_array(installed_version, '.')::int[]
               < string_to_array('0.8.0', '.')::int[] THEN
                RAISE EXCEPTION
                    'pgvector >= 0.8.0 is required for filtered HNSW queries (installed: %)',
                    installed_version;
            END IF;
        END $$;
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON course_embedding
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
