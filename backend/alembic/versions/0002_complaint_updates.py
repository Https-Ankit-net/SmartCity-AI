"""Staff action history (status changes, re-assignments, notes, official responses).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "complaint_updates",
        sa.Column("update_id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id", sa.Integer(), sa.ForeignKey("complaints.complaint_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column(
            "from_department_id",
            sa.Integer(),
            sa.ForeignKey("departments.department_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_department_id", sa.Integer(), sa.ForeignKey("departments.department_id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("public_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_complaint_updates_update_id", "complaint_updates", ["update_id"])
    op.create_index("ix_complaint_updates_complaint_id", "complaint_updates", ["complaint_id"])


def downgrade() -> None:
    op.drop_table("complaint_updates")
