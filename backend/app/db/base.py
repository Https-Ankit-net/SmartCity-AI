"""Compatibility module that registers all ORM models."""

from app.db.database import Base
from app.models import Complaint, Department, User

__all__ = ["Base", "Complaint", "Department", "User"]
