"""Store the confirming citizen's own title, so they never need to see the original reporter's.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("complaint_confirmations") as batch:
        batch.add_column(sa.Column("title", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("complaint_confirmations") as batch:
        batch.drop_column("title")
