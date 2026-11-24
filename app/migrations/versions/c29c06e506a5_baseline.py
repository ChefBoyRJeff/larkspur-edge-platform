"""baseline

Revision ID: c29c06e506a5
Revises:
Create Date: 2026-11-24 12:14:27.898803
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c29c06e506a5"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
