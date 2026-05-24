"""Add question bank tables

Revision ID: ab2c3d4e5f6a
Revises: aa1b2c3d4e5f
Create Date: 2026-05-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = 'ab2c3d4e5f6a'
down_revision: Union[str, None] = 'aa1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'questionbankcategory' not in tables:
        op.create_table(
            'questionbankcategory',
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('description', sa.String(), nullable=False, server_default=''),
            sa.Column('color', sa.String(), nullable=False, server_default='#111827'),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('parent_category_id', sa.Integer(), nullable=True),
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('category_uuid', sa.String(), nullable=False),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
            sa.Column('update_date', sa.String(), nullable=False, server_default=''),
            sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['parent_category_id'], ['questionbankcategory.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('category_uuid'),
        )
        op.create_index('ix_questionbankcategory_category_uuid', 'questionbankcategory', ['category_uuid'])
        op.create_index('ix_questionbankcategory_org_id', 'questionbankcategory', ['org_id'])

    if 'questionbankitem' not in tables:
        op.create_table(
            'questionbankitem',
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('description', sa.String(), nullable=False, server_default=''),
            sa.Column('hint', sa.String(), nullable=False, server_default=''),
            sa.Column('assignment_type', sa.Enum('FILE_SUBMISSION', 'QUIZ', 'FORM', 'CODE', 'SHORT_ANSWER', 'NUMBER_ANSWER', 'OTHER', name='assignmenttasktypeenum'), nullable=False),
            sa.Column('contents', sa.JSON(), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('difficulty', sa.String(), nullable=False, server_default='intermediate'),
            sa.Column('visibility', sa.String(), nullable=False, server_default='ORG'),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('source_assignment_task_uuid', sa.String(), nullable=True),
            sa.Column('reference_file', sa.String(), nullable=True),
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('item_uuid', sa.String(), nullable=False),
            sa.Column('explanation', sa.Text(), nullable=False, server_default=''),
            sa.Column('usage_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
            sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
            sa.Column('update_date', sa.String(), nullable=False, server_default=''),
            sa.ForeignKeyConstraint(['category_id'], ['questionbankcategory.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['updated_by_user_id'], ['user.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('item_uuid'),
        )
        op.create_index('ix_questionbankitem_created_by', 'questionbankitem', ['created_by_user_id'])
        op.create_index('ix_questionbankitem_item_uuid', 'questionbankitem', ['item_uuid'])
        op.create_index('ix_questionbankitem_org_category', 'questionbankitem', ['org_id', 'category_id'])
        op.create_index('ix_questionbankitem_org_id', 'questionbankitem', ['org_id'])
        op.create_index('ix_questionbankitem_org_type', 'questionbankitem', ['org_id', 'assignment_type'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'questionbankitem' in tables:
        for index_name in [
            'ix_questionbankitem_org_type',
            'ix_questionbankitem_org_id',
            'ix_questionbankitem_org_category',
            'ix_questionbankitem_item_uuid',
            'ix_questionbankitem_created_by',
        ]:
            try:
                op.drop_index(index_name, table_name='questionbankitem')
            except Exception:
                pass
        op.drop_table('questionbankitem')

    if 'questionbankcategory' in tables:
        for index_name in [
            'ix_questionbankcategory_org_id',
            'ix_questionbankcategory_category_uuid',
        ]:
            try:
                op.drop_index(index_name, table_name='questionbankcategory')
            except Exception:
                pass
        op.drop_table('questionbankcategory')
