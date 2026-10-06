"""Compatibility module that registers all ORM models."""

from app.db.database import Base
from app.models import Complaint, ComplaintConfirmation, ComplaintUpdate, Department, User

__all__ = ["Base", "Complaint", "ComplaintConfirmation", "ComplaintUpdate", "Department", "User"]
