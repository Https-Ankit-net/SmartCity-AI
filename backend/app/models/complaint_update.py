from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ComplaintUpdate(Base):
    """One staff action on a complaint: a status change, a re-assignment, or a note."""

    __tablename__ = "complaint_updates"

    update_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.complaint_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # status | assignment | note
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    from_department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.department_id", ondelete="SET NULL"), nullable=True
    )
    to_department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.department_id", ondelete="SET NULL"), nullable=True
    )
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # staff only
    public_response: Mapped[str | None] = mapped_column(Text, nullable=True)  # shown to the citizen
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    complaint: Mapped[Complaint] = relationship(back_populates="updates")
    actor: Mapped[User | None] = relationship()
    from_department: Mapped[Department | None] = relationship(foreign_keys=[from_department_id])
    to_department: Mapped[Department | None] = relationship(foreign_keys=[to_department_id])
