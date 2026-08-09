from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.incident_classifier import analyze_incident
from app.models.complaint import Complaint
from app.models.department import Department
from app.schemas.complaint import ComplaintCreate
from app.services.file_service import UPLOAD_DIRECTORY


def create_complaint(db: Session, data: ComplaintCreate) -> Complaint:
    image_path = UPLOAD_DIRECTORY / data.image_filename if data.image_filename else None
    analysis = analyze_incident(data.description, str(image_path) if image_path else None)
    department = db.scalar(
        select(Department).where(Department.department_name == analysis["department"])
    )
    complaint = Complaint(
        user_id=data.user_id,
        department_id=department.department_id if department else None,
        title=data.title,
        description=data.description,
        complaint_type=str(analysis["incident_type"]),
        priority=str(analysis["priority"]),
        image_filename=data.image_filename,
        latitude=data.latitude,
        longitude=data.longitude,
        detection_label=analysis["detection_label"],
        detection_confidence=analysis["detection_confidence"],
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


def get_all_complaints(db: Session) -> list[Complaint]:
    statement = select(Complaint).options(selectinload(Complaint.department)).order_by(Complaint.complaint_id)
    return list(db.scalars(statement).all())
