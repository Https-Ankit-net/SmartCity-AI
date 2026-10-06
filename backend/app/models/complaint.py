from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Complaint(Base):
    __tablename__ = "complaints"
    # Duplicate detection filters on category + recency before checking distance.
    __table_args__ = (Index("ix_complaints_type_created", "complaint_type", "created_at"),)

    complaint_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.department_id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    complaint_type: Mapped[str] = mapped_column(String(100), nullable=False, default="general")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="Medium")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Pending", server_default="Pending"
    )
    image_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detection_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detection_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-10, see severity_service
    severity_factors: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    confirmation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 8), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(11, 8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="complaints")
    department: Mapped[Department | None] = relationship(back_populates="complaints")
    updates: Mapped[list[ComplaintUpdate]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan", order_by="ComplaintUpdate.update_id"
    )
    confirmations: Mapped[list[ComplaintConfirmation]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan", order_by="ComplaintConfirmation.confirmation_id"
    )

    @property
    def incident_type(self) -> str:
        return self.complaint_type

    @property
    def department_name(self) -> str | None:
        return self.department.department_name if self.department else None

    @property
    def reporter_name(self) -> str | None:
        return self.user.full_name if self.user else None

    @property
    def reporter_email(self) -> str | None:
        return self.user.email if self.user else None
