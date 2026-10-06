"""Import models here so SQLAlchemy registers every table before create_all()."""

from app.models.complaint import Complaint
from app.models.complaint_confirmation import ComplaintConfirmation
from app.models.complaint_update import ComplaintUpdate
from app.models.department import Department
from app.models.user import User

__all__ = ["Complaint", "ComplaintConfirmation", "ComplaintUpdate", "Department", "User"]
