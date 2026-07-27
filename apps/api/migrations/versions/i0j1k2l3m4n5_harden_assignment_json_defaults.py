"""Harden assignment JSON list metadata against legacy NULL values.

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "i0j1k2l3m4n5"
down_revision: Union[str, None] = "h9i0j1k2l3m4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ASSIGNMENT_JSON_LIST_COLUMNS = ("learning_objectives", "target_usergroup_ids")
JSON_EMPTY_ARRAY_DEFAULT = sa.text("'[]'::json")


def _assignment_columns() -> dict[str, dict]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "assignment" not in inspector.get_table_names():
        return {}
    return {
        column["name"]: column
        for column in inspector.get_columns("assignment")
    }


def upgrade() -> None:
    columns = _assignment_columns()
    for column_name in ASSIGNMENT_JSON_LIST_COLUMNS:
        column = columns.get(column_name)
        if column is None:
            continue
        # JSON's SQL NULL and JSON literal null are distinct values. Both are
        # invalid for the list-shaped API contract and must be repaired before
        # applying the NOT NULL constraint.
        op.execute(
            sa.text(
                f"""
                UPDATE assignment
                SET {column_name} = '[]'::json
                WHERE {column_name} IS NULL
                   OR {column_name}::jsonb = 'null'::jsonb
                """
            )
        )
        op.alter_column(
            "assignment",
            column_name,
            existing_type=column["type"],
            existing_nullable=column["nullable"],
            nullable=False,
            server_default=JSON_EMPTY_ARRAY_DEFAULT,
        )


def downgrade() -> None:
    columns = _assignment_columns()
    for column_name in ASSIGNMENT_JSON_LIST_COLUMNS:
        column = columns.get(column_name)
        if column is None:
            continue
        # Do not erase repaired values: downgrade only removes the future
        # write constraint and default.
        op.alter_column(
            "assignment",
            column_name,
            existing_type=column["type"],
            existing_nullable=column["nullable"],
            nullable=True,
            server_default=None,
        )
