from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


# Department staff self-register as "pending" until an admin approves them.
ACCOUNT_STATUSES = ("pending", "active", "rejected")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="citizen", server_default="citizen"
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.department_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    account_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL", name="fk_users_reviewed_by_user_id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    department: Mapped[Department | None] = relationship(back_populates="users")
    complaints: Mapped[list[Complaint]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
