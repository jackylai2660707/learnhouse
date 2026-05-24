"""Add self-test attempt tables

Revision ID: bc3d4e5f6a7b
Revises: ab2c3d4e5f6a
Create Date: 2026-05-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = 'bc3d4e5f6a7b'
down_revision: Union[str, None] = 'ab2c3d4e5f6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'selftestattempt' not in tables:
        op.create_table(
            'selftestattempt',
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('question_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('status', sa.String(), nullable=False, server_default='STARTED'),
            sa.Column('score', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('max_score', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('percentage', sa.Float(), nullable=False, server_default='0'),
            sa.Column('teacher_score', sa.Integer(), nullable=True),
            sa.Column('teacher_feedback', sa.String(), nullable=False, server_default=''),
            sa.Column('counts_for_grade', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('reviewed_by_user_id', sa.Integer(), nullable=True),
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('attempt_uuid', sa.String(), nullable=False),
            sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
            sa.Column('update_date', sa.String(), nullable=False, server_default=''),
            sa.Column('submitted_at', sa.String(), nullable=True),
            sa.Column('reviewed_at', sa.String(), nullable=True),
            sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['user.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('attempt_uuid'),
        )
        op.create_index('ix_selftestattempt_attempt_uuid', 'selftestattempt', ['attempt_uuid'])
        op.create_index('ix_selftestattempt_org_status', 'selftestattempt', ['org_id', 'status'])
        op.create_index('ix_selftestattempt_org_user', 'selftestattempt', ['org_id', 'user_id'])

    if 'selftestattemptquestion' not in tables:
        op.create_table(
            'selftestattemptquestion',
            sa.Column('question_bank_item_id', sa.Integer(), nullable=True),
            sa.Column('question_bank_item_uuid', sa.String(), nullable=True),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('description', sa.String(), nullable=False, server_default=''),
            sa.Column('hint', sa.String(), nullable=False, server_default=''),
            sa.Column('assignment_type', sa.Enum('FILE_SUBMISSION', 'QUIZ', 'FORM', 'CODE', 'SHORT_ANSWER', 'NUMBER_ANSWER', 'OTHER', name='assignmenttasktypeenum'), nullable=False),
            sa.Column('contents', sa.JSON(), nullable=True),
            sa.Column('answer', sa.JSON(), nullable=True),
            sa.Column('grade', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('max_grade', sa.Integer(), nullable=False, server_default='100'),
            sa.Column('feedback', sa.String(), nullable=False, server_default=''),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('attempt_question_uuid', sa.String(), nullable=False),
            sa.Column('attempt_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['attempt_id'], ['selftestattempt.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('attempt_question_uuid'),
        )
        op.create_index('ix_selftestattemptquestion_attempt_id', 'selftestattemptquestion', ['attempt_id'])
        op.create_index('ix_selftestattemptquestion_question_uuid', 'selftestattemptquestion', ['attempt_question_uuid'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'selftestattemptquestion' in tables:
        for index_name in [
            'ix_selftestattemptquestion_question_uuid',
            'ix_selftestattemptquestion_attempt_id',
        ]:
            try:
                op.drop_index(index_name, table_name='selftestattemptquestion')
            except Exception:
                pass
        op.drop_table('selftestattemptquestion')

    if 'selftestattempt' in tables:
        for index_name in [
            'ix_selftestattempt_org_user',
            'ix_selftestattempt_org_status',
            'ix_selftestattempt_attempt_uuid',
        ]:
            try:
                op.drop_index(index_name, table_name='selftestattempt')
            except Exception:
                pass
        op.drop_table('selftestattempt')
