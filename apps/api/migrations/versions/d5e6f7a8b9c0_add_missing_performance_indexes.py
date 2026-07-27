"""Add missing performance indexes

Adds indexes that were absent from payment tables, collection tables, and
key query paths used in completion checks, discussion sorting, and audit logs.

Revision ID: d5e6f7a8b9c0
Revises: cd4e5f6a7b8c
Create Date: 2026-05-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "cd4e5f6a7b8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = (
    # Legacy payment tables may already be gone on newer installations.
    ("ix_paymentsconfig_org_id", "paymentsconfig", ("org_id",)),
    ("ix_paymentsproduct_org_id", "paymentsproduct", ("org_id",)),
    ("ix_paymentsproduct_payments_config_id", "paymentsproduct", ("payments_config_id",)),
    ("ix_paymentscourse_course_org_product", "paymentscourse", ("course_id", "org_id", "payment_product_id")),
    ("ix_paymentsuser_user_org_product", "paymentsuser", ("user_id", "org_id", "payment_product_id")),
    ("ix_collection_org_id", "collection", ("org_id",)),
    ("ix_collection_collection_uuid", "collection", ("collection_uuid",)),
    ("ix_collectioncourse_collection_id", "collectioncourse", ("collection_id",)),
    ("ix_collectioncourse_course_id", "collectioncourse", ("course_id",)),
    ("ix_trailstep_course_user_complete", "trailstep", ("course_id", "user_id", "complete")),
    ("ix_discussion_community_pinned_date", "discussion", ("community_id", "is_pinned", "creation_date")),
    ("ix_auditlog_org_id", "auditlog", ("org_id",)),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _table_columns(inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _table_indexes(inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    inspector = _inspector()
    for index_name, table_name, columns in INDEXES:
        table_columns = _table_columns(inspector, table_name)
        if not table_columns or not set(columns).issubset(table_columns):
            continue
        if index_name in _table_indexes(inspector, table_name):
            continue
        op.create_index(index_name, table_name, list(columns), unique=False)


def downgrade() -> None:
    inspector = _inspector()
    for index_name, table_name, _columns in reversed(INDEXES):
        if index_name in _table_indexes(inspector, table_name):
            op.drop_index(index_name, table_name=table_name)
