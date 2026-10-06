"""Baseline: departments, users and complaints as they existed before Alembic.

Revision ID: 0001
Revises:
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("department_id", sa.Integer(), primary_key=True),
        sa.Column("department_name", sa.String(100), nullable=False, unique=True),
        sa.Column("department_email", sa.String(100), nullable=True, unique=True),
        sa.Column("contact_number", sa.String(20), nullable=True),
        sa.Column("office_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_departments_department_id", "departments", ["department_id"])

    op.create_table(
        "users",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(150), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True, unique=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="citizen"),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.department_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_user_id", "users", ["user_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "complaints",
        sa.Column("complaint_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "department_id", sa.Integer(), sa.ForeignKey("departments.department_id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("complaint_type", sa.String(100), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="Pending"),
        sa.Column("image_filename", sa.String(255), nullable=True),
        sa.Column("detection_label", sa.String(100), nullable=True),
        sa.Column("detection_confidence", sa.Float(), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 8), nullable=True),
        sa.Column("longitude", sa.Numeric(11, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_complaints_complaint_id", "complaints", ["complaint_id"])
    op.create_index("ix_complaints_user_id", "complaints", ["user_id"])
    op.create_index("ix_complaints_department_id", "complaints", ["department_id"])


def downgrade() -> None:
    op.drop_table("complaints")
    op.drop_table("users")
    op.drop_table("departments")
