"""Add school pilot assignment and question bank metadata

Revision ID: cd4e5f6a7b8c
Revises: bc3d4e5f6a7b
Create Date: 2026-06-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = 'cd4e5f6a7b8c'
down_revision: Union[str, None] = 'bc3d4e5f6a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ASSIGNMENT_COLUMNS = (
    ('subject', sa.Column('subject', sa.String(), nullable=False, server_default='')),
    ('education_stage', sa.Column('education_stage', sa.String(), nullable=False, server_default='')),
    ('grade_level', sa.Column('grade_level', sa.String(), nullable=False, server_default='')),
    ('school_year', sa.Column('school_year', sa.String(), nullable=False, server_default='')),
    ('term', sa.Column('term', sa.String(), nullable=False, server_default='')),
    ('unit', sa.Column('unit', sa.String(), nullable=False, server_default='')),
    ('learning_objectives', sa.Column('learning_objectives', sa.JSON(), nullable=True)),
    ('target_usergroup_ids', sa.Column('target_usergroup_ids', sa.JSON(), nullable=True)),
    ('score_policy', sa.Column('score_policy', sa.String(), nullable=False, server_default='highest')),
    ('teacher_review_required', sa.Column('teacher_review_required', sa.Boolean(), nullable=True, server_default=sa.false())),
    ('teacher_review_status', sa.Column('teacher_review_status', sa.String(), nullable=False, server_default='not_required')),
)

QUESTION_BANK_COLUMNS = (
    ('subject', sa.Column('subject', sa.String(), nullable=False, server_default='')),
    ('education_stage', sa.Column('education_stage', sa.String(), nullable=False, server_default='')),
    ('grade_level', sa.Column('grade_level', sa.String(), nullable=False, server_default='')),
    ('unit', sa.Column('unit', sa.String(), nullable=False, server_default='')),
)

ASSIGNMENT_USER_SUBMISSION_COLUMNS = (
    ('best_grade', sa.Column('best_grade', sa.Integer(), nullable=False, server_default='0')),
    ('best_attempt_number', sa.Column('best_attempt_number', sa.Integer(), nullable=False, server_default='1')),
    ('teacher_review_status', sa.Column('teacher_review_status', sa.String(), nullable=False, server_default='pending')),
)

ASSIGNMENT_PILOT_DEFAULTS = (
    ('auto_grading', sa.true(), True),
    ('show_correct_answers', sa.true(), True),
    ('allow_retries', sa.true(), True),
    ('max_retries', sa.text('0'), 0),
    ('score_policy', sa.text("'highest'"), 'highest'),
)


def _add_missing_columns(table_name: str, columns: tuple[tuple[str, sa.Column], ...]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    existing = {col['name'] for col in inspector.get_columns(table_name)}
    for name, column in columns:
        if name not in existing:
            op.add_column(table_name, column)


def _align_assignment_pilot_defaults() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'assignment' not in inspector.get_table_names():
        return

    existing_columns = {
        col['name']: col
        for col in inspector.get_columns('assignment')
    }
    for name, server_default, backfill_value in ASSIGNMENT_PILOT_DEFAULTS:
        column = existing_columns.get(name)
        if not column:
            continue
        op.alter_column(
            'assignment',
            name,
            server_default=server_default,
            existing_type=column.get('type'),
            existing_nullable=column.get('nullable', True),
        )
        assignment_table = sa.table('assignment', sa.column(name))
        bind.execute(
            sa.update(assignment_table)
            .where(assignment_table.c[name].is_(None))
            .values({name: backfill_value})
        )


def upgrade() -> None:
    _add_missing_columns('assignment', ASSIGNMENT_COLUMNS)
    _align_assignment_pilot_defaults()
    _add_missing_columns('questionbankitem', QUESTION_BANK_COLUMNS)
    _add_missing_columns('assignmentusersubmission', ASSIGNMENT_USER_SUBMISSION_COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'assignmentusersubmission' in tables:
        existing = {col['name'] for col in inspector.get_columns('assignmentusersubmission')}
        for name, _column in reversed(ASSIGNMENT_USER_SUBMISSION_COLUMNS):
            if name in existing:
                op.drop_column('assignmentusersubmission', name)

    if 'questionbankitem' in tables:
        existing = {col['name'] for col in inspector.get_columns('questionbankitem')}
        for name, _column in reversed(QUESTION_BANK_COLUMNS):
            if name in existing:
                op.drop_column('questionbankitem', name)

    if 'assignment' in tables:
        existing = {col['name'] for col in inspector.get_columns('assignment')}
        for name, _column in reversed(ASSIGNMENT_COLUMNS):
            if name in existing:
                op.drop_column('assignment', name)
