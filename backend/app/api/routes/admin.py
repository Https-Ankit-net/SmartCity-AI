from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_admin
from app.db.session import get_db
from app.models.complaint import Complaint
from app.models.department import Department
from app.models.user import User
from app.schemas.complaint import ComplaintResponse

router = APIRouter()


@router.get("/admin/dashboard")
def dashboard(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    by_status = dict(db.execute(select(Complaint.status, func.count()).group_by(Complaint.status)).all())
    by_department = dict(
        db.execute(
            select(Department.department_name, func.count(Complaint.complaint_id))
            .outerjoin(Complaint, Complaint.department_id == Department.department_id)
            .group_by(Department.department_name)
        ).all()
    )
    return {
        "total_users": db.scalar(select(func.count(User.user_id))) or 0,
        "total_complaints": db.scalar(select(func.count(Complaint.complaint_id))) or 0,
        "complaints_by_status": by_status,
        "complaints_by_department": by_department,
    }


@router.get("/admin/complaints", response_model=list[ComplaintResponse])
def filter_complaints(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    priority: str | None = None,
    incident_type: str | None = None,
    department_id: int | None = None,
) -> list[Complaint]:
    statement = select(Complaint).options(selectinload(Complaint.department))
    if status_filter:
        statement = statement.where(Complaint.status == status_filter)
    if priority:
        statement = statement.where(Complaint.priority == priority)
    if incident_type:
        statement = statement.where(Complaint.complaint_type == incident_type)
    if department_id:
        statement = statement.where(Complaint.department_id == department_id)
    return list(db.scalars(statement.order_by(Complaint.complaint_id)).all())
