"""Add coding challenge tables

Revision ID: aa1b2c3d4e5f
Revises: n4o5p6q7r8s9
Create Date: 2026-05-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import sqlmodel  # noqa: F401


revision: str = 'aa1b2c3d4e5f'
down_revision: Union[str, None] = 'n4o5p6q7r8s9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'coding_challenge' not in tables:
        op.create_table(
            'coding_challenge',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('challenge_uuid', sa.String(), nullable=False),
            sa.Column('external_id', sa.String(), nullable=True),
            sa.Column('source_template_uuid', sa.String(), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('course_id', sa.Integer(), nullable=False),
            sa.Column('activity_id', sa.Integer(), nullable=False),
            sa.Column('block_id', sa.String(), nullable=False),
            sa.Column('required', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('language_id', sa.Integer(), nullable=False, server_default='71'),
            sa.Column('title', sa.String(), nullable=False, server_default=''),
            sa.Column('description', sa.Text(), nullable=False, server_default=''),
            sa.Column('starter_code', sa.Text(), nullable=False, server_default=''),
            sa.Column('solution_code', sa.Text(), nullable=False, server_default=''),
            sa.Column('solution_visibility', sa.String(), nullable=False, server_default='after_pass'),
            sa.Column('hints', sa.JSON(), nullable=True),
            sa.Column('difficulty', sa.String(), nullable=False, server_default='medium'),
            sa.Column('time_limit_ms', sa.Integer(), nullable=False, server_default='10000'),
            sa.Column('sqlite_db_path', sa.Text(), nullable=False, server_default=''),
            sa.Column('additional_files', sa.JSON(), nullable=True),
            sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['activity_id'], ['activity.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['course_id'], ['course.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('challenge_uuid', name='uq_coding_challenge_uuid'),
        )
        op.create_index('ix_coding_challenge_activity_block', 'coding_challenge', ['activity_id', 'block_id'])
        op.create_index('ix_coding_challenge_challenge_uuid', 'coding_challenge', ['challenge_uuid'])
        op.create_index('ix_coding_challenge_external_id', 'coding_challenge', ['external_id'])
        op.create_index('ix_coding_challenge_org_course', 'coding_challenge', ['org_id', 'course_id'])
        op.create_index('ix_coding_challenge_source_template_uuid', 'coding_challenge', ['source_template_uuid'])

    if 'coding_challenge_test' not in tables:
        op.create_table(
            'coding_challenge_test',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('challenge_id', sa.Integer(), nullable=False),
            sa.Column('test_uuid', sa.String(), nullable=False),
            sa.Column('label', sa.String(), nullable=False, server_default=''),
            sa.Column('stdin', sa.Text(), nullable=False, server_default=''),
            sa.Column('expected_stdout', sa.Text(), nullable=False, server_default=''),
            sa.Column('visibility', sa.String(), nullable=False, server_default='visible'),
            sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['challenge_id'], ['coding_challenge.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_coding_challenge_test_challenge_id', 'coding_challenge_test', ['challenge_id'])
        op.create_index('ix_coding_challenge_test_challenge_order', 'coding_challenge_test', ['challenge_id', 'order'])
        op.create_index('ix_coding_challenge_test_test_uuid', 'coding_challenge_test', ['test_uuid'])

    if 'coding_challenge_submission' not in tables:
        op.create_table(
            'coding_challenge_submission',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('submission_uuid', sa.String(), nullable=False),
            sa.Column('challenge_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('activity_id', sa.Integer(), nullable=False),
            sa.Column('course_id', sa.Integer(), nullable=False),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('attempt_number', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('language_id', sa.Integer(), nullable=False),
            sa.Column('source_code', sa.Text(), nullable=False),
            sa.Column('passed', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('total_tests', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('passed_tests', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('visible_total_tests', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('visible_passed_tests', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('hidden_total_tests', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('hidden_passed_tests', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('results', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('execution_time_ms', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['activity_id'], ['activity.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['challenge_id'], ['coding_challenge.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['course_id'], ['course.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_coding_challenge_submission_activity_id', 'coding_challenge_submission', ['activity_id'])
        op.create_index('ix_coding_challenge_submission_challenge_id', 'coding_challenge_submission', ['challenge_id'])
        op.create_index('ix_coding_challenge_submission_course_id', 'coding_challenge_submission', ['course_id'])
        op.create_index('ix_coding_challenge_submission_org_id', 'coding_challenge_submission', ['org_id'])
        op.create_index('ix_coding_challenge_submission_submission_uuid', 'coding_challenge_submission', ['submission_uuid'])
        op.create_index('ix_coding_challenge_submission_user_challenge', 'coding_challenge_submission', ['user_id', 'challenge_id'])
        op.create_index('ix_coding_challenge_submission_user_id', 'coding_challenge_submission', ['user_id'])

    if 'coding_challenge_progress' not in tables:
        op.create_table(
            'coding_challenge_progress',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('challenge_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('activity_id', sa.Integer(), nullable=False),
            sa.Column('course_id', sa.Integer(), nullable=False),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('passed', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('first_passed_at', sa.String(), nullable=True),
            sa.Column('latest_submission_id', sa.Integer(), nullable=True),
            sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['activity_id'], ['activity.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['challenge_id'], ['coding_challenge.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['course_id'], ['course.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['latest_submission_id'], ['coding_challenge_submission.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('challenge_id', 'user_id', name='uq_coding_challenge_progress_user'),
        )
        op.create_index('ix_coding_challenge_progress_activity_id', 'coding_challenge_progress', ['activity_id'])
        op.create_index('ix_coding_challenge_progress_challenge_id', 'coding_challenge_progress', ['challenge_id'])
        op.create_index('ix_coding_challenge_progress_course_id', 'coding_challenge_progress', ['course_id'])
        op.create_index('ix_coding_challenge_progress_org_id', 'coding_challenge_progress', ['org_id'])
        op.create_index('ix_coding_challenge_progress_user_activity', 'coding_challenge_progress', ['user_id', 'activity_id'])
        op.create_index('ix_coding_challenge_progress_user_id', 'coding_challenge_progress', ['user_id'])


def downgrade() -> None:
    for table in (
        'coding_challenge_progress',
        'coding_challenge_submission',
        'coding_challenge_test',
        'coding_challenge',
    ):
        op.drop_table(table)
