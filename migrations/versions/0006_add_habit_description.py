"""Add optional description to habits.

Revision ID: 0006_add_habit_description
Revises: 0005_add_task_start_date
Create Date: 2026-07-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_add_habit_description"
down_revision: Union[str, None] = "0005_add_task_start_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("habits", "description"):
        op.add_column("habits", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("habits", "description"):
        op.drop_column("habits", "description")
