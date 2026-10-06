from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import require_department_or_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.admin import AdminComplaintDetail, ComplaintPage, ReassignRequest, StatusChangeRequest
from app.services import admin_service
from app.services.notify import publish_from_sync

router = APIRouter()

SortKey = Literal[
    "id", "image", "category", "location", "reporter", "department", "status", "confidence", "priority", "created"
]


@router.get("/admin/dashboard")
def dashboard(
    current_user: User = Depends(require_department_or_admin),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=7, le=365),
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840),
) -> dict:
    """City-wide analytics for admins; department staff see only their department."""
    return admin_service.analytics(db, current_user, days, tz_offset_minutes)


@router.get("/admin/complaints", response_model=ComplaintPage)
def filter_complaints(
    current_user: User = Depends(require_department_or_admin),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(
        default=None, alias="status", pattern=r"(?i)^(pending|in[ _-]?progress|resolved|rejected)$"
    ),
    priority: str | None = None,
    incident_type: str | None = None,
    department_id: int | None = Query(default=None, description="Admins only; ignored for department staff"),
    date_from: datetime | None = None,
    date_to: datetime | None = Query(default=None, description="Exclusive upper bound"),
    escalated: bool | None = None,
    q: str | None = Query(default=None, max_length=100),
    sort_by: SortKey = "created",
    sort_dir: Literal["asc", "desc"] = "desc",
) -> ComplaintPage:
    return admin_service.list_complaints(
        db,
        current_user,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        priority=priority,
        incident_type=incident_type,
        department_id=department_id,
        date_from=date_from,
        date_to=date_to,
        escalated=escalated,
        q=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.get("/admin/complaints/{complaint_id}", response_model=AdminComplaintDetail)
def complaint_detail(
    complaint_id: int,
    current_user: User = Depends(require_department_or_admin),
    db: Session = Depends(get_db),
) -> AdminComplaintDetail:
    return admin_service.complaint_detail(admin_service.load_for_staff(db, current_user, complaint_id))


@router.patch("/admin/complaints/{complaint_id}/status", response_model=AdminComplaintDetail)
def change_status(
    complaint_id: int,
    data: StatusChangeRequest,
    current_user: User = Depends(require_department_or_admin),
    db: Session = Depends(get_db),
) -> AdminComplaintDetail:
    complaint = admin_service.load_for_staff(db, current_user, complaint_id)
    complaint = admin_service.change_status(
        db, current_user, complaint, data.status, data.internal_note, data.public_response
    )
    publish_from_sync("status_update", complaint, data.public_response)
    return admin_service.complaint_detail(complaint)


@router.patch("/admin/complaints/{complaint_id}/department", response_model=AdminComplaintDetail)
def reassign_department(
    complaint_id: int,
    data: ReassignRequest,
    current_user: User = Depends(require_department_or_admin),
    db: Session = Depends(get_db),
) -> AdminComplaintDetail:
    """Admins can move any complaint; department staff can transfer one of theirs to another department."""
    complaint = admin_service.load_for_staff(db, current_user, complaint_id)
    complaint = admin_service.reassign(
        db, current_user, complaint, data.department_id, data.internal_note, data.public_response
    )
    publish_from_sync("reassigned", complaint, data.public_response)
    return admin_service.complaint_detail(complaint)
