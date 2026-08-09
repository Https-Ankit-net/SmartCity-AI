import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_admin
from app.core.notification_manager import notification_manager
from app.core.websocket import manager
from app.db.session import get_db
from app.models.complaint import Complaint
from app.models.user import User
from app.schemas.complaint import (
    ComplaintCreate,
    ComplaintMapItem,
    ComplaintResponse,
    ComplaintStatusUpdate,
)
from app.services.complaint_service import create_complaint, get_all_complaints
from app.services.file_service import save_image

router = APIRouter()
map_router = APIRouter()


def _event_data(complaint: Complaint) -> dict:
    return ComplaintResponse.model_validate(complaint).model_dump(mode="json")


def _broadcast_from_sync(event_type: str, complaint: Complaint) -> None:
    anyio.from_thread.run(manager.broadcast, {"type": event_type, "data": _event_data(complaint)})


def _notification_data(event_type: str, complaint: Complaint) -> dict:
    if event_type == "new_complaint":
        message = "New complaint reported"
    else:
        message = f"Complaint #{complaint.complaint_id} status updated to {complaint.status}"
    return {
        "type": event_type,
        "message": message,
        "data": {
            "id": complaint.complaint_id,
            "title": complaint.title,
            "status": complaint.status.lower(),
        },
    }


def _broadcast_notification_from_sync(event_type: str, complaint: Complaint) -> None:
    anyio.from_thread.run(notification_manager.broadcast, _notification_data(event_type, complaint))


def _create(db: Session, data: ComplaintCreate) -> Complaint:
    if db.get(User, data.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    try:
        complaint = create_complaint(db, data)
        # Make the department relationship available to the response and event payload.
        complaint = db.scalar(
            select(Complaint)
            .options(selectinload(Complaint.department))
            .where(Complaint.complaint_id == complaint.complaint_id)
        )
        return complaint
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create complaint") from exc


@router.post("/complaints", response_model=ComplaintResponse, status_code=status.HTTP_201_CREATED)
def create(data: ComplaintCreate, db: Session = Depends(get_db)) -> Complaint:
    complaint = _create(db, data)
    _broadcast_from_sync("new_complaint", complaint)
    _broadcast_notification_from_sync("new_complaint", complaint)
    return complaint


@router.post(
    "/complaints/with-image", response_model=ComplaintResponse, status_code=status.HTTP_201_CREATED
)
async def create_with_image(
    user_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Complaint:
    filename, _ = await save_image(image)
    complaint = _create(
        db,
        ComplaintCreate(
            user_id=user_id,
            title=title,
            description=description,
            image_filename=filename,
            latitude=latitude,
            longitude=longitude,
        ),
    )
    await manager.broadcast({"type": "new_complaint", "data": _event_data(complaint)})
    await notification_manager.broadcast(_notification_data("new_complaint", complaint))
    return complaint


@router.patch("/complaints/{complaint_id}/status", response_model=ComplaintResponse)
def update_status(
    complaint_id: int,
    data: ComplaintStatusUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Complaint:
    complaint = db.scalar(
        select(Complaint).options(selectinload(Complaint.department)).where(Complaint.complaint_id == complaint_id)
    )
    if complaint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Complaint not found")
    complaint.status = data.status
    db.commit()
    db.refresh(complaint)
    _broadcast_from_sync("complaint_status_updated", complaint)
    _broadcast_notification_from_sync("status_update", complaint)
    return complaint


@router.get("/complaints", response_model=list[ComplaintResponse])
def read_all(db: Session = Depends(get_db)) -> list[Complaint]:
    return get_all_complaints(db)


@map_router.get("/complaints/map", response_model=list[ComplaintMapItem])
def map_data(db: Session = Depends(get_db)) -> list[ComplaintMapItem]:
    complaints = db.scalars(
        select(Complaint)
        .where(Complaint.latitude.is_not(None), Complaint.longitude.is_not(None))
        .order_by(Complaint.complaint_id)
    ).all()
    return [
        ComplaintMapItem(
            id=complaint.complaint_id,
            lat=float(complaint.latitude),
            lng=float(complaint.longitude),
            type=complaint.complaint_type,
            status=complaint.status,
        )
        for complaint in complaints
    ]
