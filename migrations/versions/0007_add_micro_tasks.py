"""Add micro tasks table.

Revision ID: 0007_add_micro_tasks
Revises: 0006_add_habit_description
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_add_micro_tasks"
down_revision: Union[str, None] = "0006_add_habit_description"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS micro_tasks (
              id UUID PRIMARY KEY,
              user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title VARCHAR(180) NOT NULL,
              hint VARCHAR(240),
              duration_key VARCHAR(32) NOT NULL,
              category VARCHAR(48) NOT NULL,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TIMESTAMP
            )
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_micro_tasks_user_duration_created_at
              ON micro_tasks(user_id, duration_key, created_at)
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_micro_tasks_user_id ON micro_tasks(user_id)")
    else:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS micro_tasks (
              id CHAR(36) PRIMARY KEY,
              user_id CHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title VARCHAR(180) NOT NULL,
              hint VARCHAR(240),
              duration_key VARCHAR(32) NOT NULL,
              category VARCHAR(48) NOT NULL,
              is_active BOOLEAN NOT NULL DEFAULT 1,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TIMESTAMP
            )
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_micro_tasks_user_duration_created_at
              ON micro_tasks(user_id, duration_key, created_at)
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_micro_tasks_user_id ON micro_tasks(user_id)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_micro_tasks_user_duration_created_at")
        op.execute("DROP INDEX IF EXISTS ix_micro_tasks_user_id")
    op.execute("DROP TABLE IF EXISTS micro_tasks")
