"""Initial Life OS schema.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-07-05
"""

from typing import Sequence, Union

from alembic import op

from app.database import Base
from app import models  # noqa: F401 - register all SQLAlchemy models

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
