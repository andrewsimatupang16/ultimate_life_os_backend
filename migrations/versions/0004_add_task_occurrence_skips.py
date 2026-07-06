"""Add per-date skips for recurring task occurrences.

Revision ID: 0004_add_task_occurrence_skips
Revises: 0003_add_task_recurrence_days
Create Date: 2026-07-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models import GUID

revision: str = "0004_add_task_occurrence_skips"
down_revision: Union[str, None] = "0003_add_task_recurrence_days"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("task_occurrence_skips"):
        return

    op.create_table(
        "task_occurrence_skips",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("task_id", GUID(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("skipped_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "local_date", name="uq_task_occurrence_skips_task_local_date"),
    )
    op.create_index(
        "ix_task_occurrence_skips_id",
        "task_occurrence_skips",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_task_occurrence_skips_user_local_date",
        "task_occurrence_skips",
        ["user_id", "local_date"],
        unique=False,
    )


def downgrade() -> None:
    if not _has_table("task_occurrence_skips"):
        return

    op.drop_index("ix_task_occurrence_skips_user_local_date", table_name="task_occurrence_skips")
    op.drop_index("ix_task_occurrence_skips_id", table_name="task_occurrence_skips")
    op.drop_table("task_occurrence_skips")
