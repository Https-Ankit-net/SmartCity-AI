"""Severity scoring and duplicate-report confirmations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("complaints") as batch:
        batch.add_column(sa.Column("severity_score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("severity_factors", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("confirmation_count", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "complaint_confirmations",
        sa.Column("confirmation_id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id", sa.Integer(), sa.ForeignKey("complaints.complaint_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_filename", sa.String(255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("complaint_id", "user_id", name="uq_confirmation_complaint_user"),
    )
    op.create_index("ix_complaint_confirmations_confirmation_id", "complaint_confirmations", ["confirmation_id"])
    op.create_index("ix_complaint_confirmations_complaint_id", "complaint_confirmations", ["complaint_id"])
    op.create_index("ix_complaint_confirmations_user_id", "complaint_confirmations", ["user_id"])
    # Duplicate detection filters on category + recency + location.
    op.create_index("ix_complaints_type_created", "complaints", ["complaint_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_complaints_type_created", table_name="complaints")
    op.drop_table("complaint_confirmations")
    with op.batch_alter_table("complaints") as batch:
        batch.drop_column("confirmation_count")
        batch.drop_column("severity_factors")
        batch.drop_column("severity_score")
