"""Drop removed micro tasks table.

Revision ID: 0008_drop_micro_tasks
Revises: 0007_add_micro_tasks
Create Date: 2026-07-13
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008_drop_micro_tasks"
down_revision: Union[str, None] = "0007_add_micro_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_micro_tasks_user_duration_created_at")
        op.execute("DROP INDEX IF EXISTS ix_micro_tasks_user_id")
    op.execute("DROP TABLE IF EXISTS micro_tasks")


def downgrade() -> None:
    # Feature removed permanently; do not recreate deprecated table.
    pass
