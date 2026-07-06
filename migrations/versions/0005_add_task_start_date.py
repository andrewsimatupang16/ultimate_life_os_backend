"""Add start date for task visibility.

Revision ID: 0005_add_task_start_date
Revises: 0004_add_task_occurrence_skips
Create Date: 2026-07-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_add_task_start_date"
down_revision: Union[str, None] = "0004_add_task_occurrence_skips"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("tasks", "start_date"):
        op.add_column("tasks", sa.Column("start_date", sa.Date(), nullable=True))

    if not _has_index("tasks", "ix_tasks_user_start_date"):
        op.create_index("ix_tasks_user_start_date", "tasks", ["user_id", "start_date"], unique=False)


def downgrade() -> None:
    if _has_index("tasks", "ix_tasks_user_start_date"):
        op.drop_index("ix_tasks_user_start_date", table_name="tasks")

    if _has_column("tasks", "start_date"):
        op.drop_column("tasks", "start_date")
