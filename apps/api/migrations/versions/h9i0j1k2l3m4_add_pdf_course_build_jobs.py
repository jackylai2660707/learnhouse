"""Add durable PDF course build job ledger.

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-07-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "h9i0j1k2l3m4"
down_revision: Union[str, None] = "g8h9i0j1k2l3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Fresh installs create the current SQLModel metadata before replaying the
    # reconciliation migrations from bc3d4e5f6a7b.  That metadata already
    # includes this ledger, while a versioned g8 database does not.  Keep the
    # migration safe for both paths without changing their stamped revisions.
    if _table_exists("pdf_course_build_job"):
        return

    op.create_table(
        "pdf_course_build_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_uuid", sa.String(length=80), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("creator_user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("chapters_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activities_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_documents_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexing_status", sa.String(length=24), nullable=True),
        sa.Column("indexing_code", sa.String(length=80), nullable=True),
        sa.Column("chunks_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_code", sa.String(length=80), nullable=True),
        sa.Column("warning_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("credit_amount", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("credit_period_token", sa.String(length=80), nullable=False, server_default="0"),
        sa.Column("credit_state", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("staging_cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_pdf_build_attempts"),
        sa.CheckConstraint(
            "credit_state IN ('pending','reserved','consumed','refunded')",
            name="ck_pdf_build_credit_state",
        ),
        sa.CheckConstraint("progress_current >= 0", name="ck_pdf_build_progress_current"),
        sa.CheckConstraint("progress_total >= 0", name="ck_pdf_build_progress_total"),
        sa.CheckConstraint(
            "stage IN ('extracting','planning','creating','indexing','done','failed')",
            name="ck_pdf_build_stage",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["creator_user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "creator_user_id",
            "idempotency_key",
            name="uq_pdf_build_org_creator_idempotency",
        ),
    )
    op.create_index("ix_pdf_course_build_job_job_uuid", "pdf_course_build_job", ["job_uuid"], unique=True)
    op.create_index(
        "ix_pdf_build_org_creator_created",
        "pdf_course_build_job",
        ["org_id", "creator_user_id", "created_at"],
    )
    op.create_index(
        "ix_pdf_build_org_stage_created",
        "pdf_course_build_job",
        ["org_id", "stage", "created_at"],
    )
    op.create_index(
        "ix_pdf_build_claim",
        "pdf_course_build_job",
        ["stage", "next_attempt_at", "lease_expires_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pdf_build_claim", table_name="pdf_course_build_job")
    op.drop_index("ix_pdf_build_org_stage_created", table_name="pdf_course_build_job")
    op.drop_index("ix_pdf_build_org_creator_created", table_name="pdf_course_build_job")
    op.drop_index("ix_pdf_course_build_job_job_uuid", table_name="pdf_course_build_job")
    op.drop_table("pdf_course_build_job")
