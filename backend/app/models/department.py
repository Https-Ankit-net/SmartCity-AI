from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Department(Base):
    __tablename__ = "departments"

    department_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    department_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    department_email: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    contact_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    office_address: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list[User]] = relationship(back_populates="department")
    complaints: Mapped[list[Complaint]] = relationship(back_populates="department")
