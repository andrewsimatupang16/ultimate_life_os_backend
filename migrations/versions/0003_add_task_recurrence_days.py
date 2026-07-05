"""Add weekday recurrence for tasks.

Revision ID: 0003_add_task_recurrence_days
Revises: 0002_add_task_priority
Create Date: 2026-07-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_task_recurrence_days"
down_revision: Union[str, None] = "0002_add_task_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("tasks", "recurrence_days"):
        op.add_column("tasks", sa.Column("recurrence_days", sa.String(), nullable=True))


def downgrade() -> None:
    if _has_column("tasks", "recurrence_days"):
        op.drop_column("tasks", "recurrence_days")
