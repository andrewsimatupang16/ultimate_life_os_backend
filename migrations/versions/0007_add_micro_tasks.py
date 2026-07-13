"""Deprecated micro tasks migration.

Revision ID: 0007_add_micro_tasks
Revises: 0006_add_habit_description
Create Date: 2026-07-12

This revision is kept only to preserve the Alembic revision chain.
The Micro Task feature has been removed, so this migration is intentionally a no-op.
"""

from typing import Sequence, Union

revision: str = "0007_add_micro_tasks"
down_revision: Union[str, None] = "0006_add_habit_description"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
