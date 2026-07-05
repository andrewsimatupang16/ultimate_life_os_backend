"""Add independent task priority.

Revision ID: 0002_add_task_priority
Revises: 0001_initial_schema
Create Date: 2026-07-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_add_task_priority"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column("tasks", "priority"):
        op.add_column(
            "tasks",
            sa.Column("priority", sa.String(), nullable=True),
        )

    bind.execute(sa.text(
        """
        UPDATE tasks
        SET priority = CASE difficulty
          WHEN 'hard' THEN 'high'
          WHEN 'easy' THEN 'low'
          ELSE 'medium'
        END
        WHERE priority IS NULL
        """
    ))

    op.alter_column(
        "tasks",
        "priority",
        existing_type=sa.String(),
        nullable=False,
        server_default="medium",
    )


def downgrade() -> None:
    if _has_column("tasks", "priority"):
        op.drop_column("tasks", "priority")
