"""Add assignment remediation practice records

Revision ID: f7g8h9i0j1k2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-02

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = "f7g8h9i0j1k2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _table_exists("assignmentremediationpractice"):
        return

    op.create_table(
        "assignmentremediationpractice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assignment_remediation_practice_uuid", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("feedback", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("assignment_submission_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["assignment.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assignment_submission_id"],
            ["assignmentusersubmission.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assignmentremediationpractice_assignment_id",
        "assignmentremediationpractice",
        ["assignment_id"],
        unique=False,
    )
    op.create_index(
        "ix_assignmentremediationpractice_uuid",
        "assignmentremediationpractice",
        ["assignment_remediation_practice_uuid"],
        unique=False,
    )
    op.create_index(
        "ix_assignmentremediationpractice_submission_user",
        "assignmentremediationpractice",
        ["assignment_submission_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    if not _table_exists("assignmentremediationpractice"):
        return
    op.drop_index(
        "ix_assignmentremediationpractice_submission_user",
        table_name="assignmentremediationpractice",
    )
    op.drop_index(
        "ix_assignmentremediationpractice_uuid",
        table_name="assignmentremediationpractice",
    )
    op.drop_index(
        "ix_assignmentremediationpractice_assignment_id",
        table_name="assignmentremediationpractice",
    )
    op.drop_table("assignmentremediationpractice")
