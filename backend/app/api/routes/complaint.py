import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, require_admin
from app.db.session import get_db
from app.models.complaint import Complaint
from app.models.user import User
from app.schemas.admin import PublicComplaintUpdate, canonical_status
from app.schemas.complaint import (
    ComplaintCreate,
    ComplaintCreateRequest,
    ComplaintMapItem,
    ComplaintResponse,
    ComplaintStatusUpdate,
    ComplaintSubmitResponse,
)
from app.services import admin_service
from app.services.complaint_service import (
    complaints_for_user,
    get_all_complaints,
    submit_complaint,
    user_can_view,
)
from app.services.file_service import remove_upload, save_image
from app.services.notify import publish, publish_from_sync

router = APIRouter()
map_router = APIRouter()


SUBMIT_MESSAGES = {
    "created": "Complaint #{id} created and routed to {dept}.",
    "confirmed": "A matching report (#{id}) already exists {dist} away. Your report was added as a confirmation.",
    "already_reported": "You have already reported this issue as #{id}.",
}


def _submit(db: Session, data: ComplaintCreate, current_user: User, response: Response) -> ComplaintSubmitResponse:
    if data.user_id is not None and data.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only file complaints as yourself")
    data = data.model_copy(update={"user_id": current_user.user_id})
    try:
        result = submit_complaint(db, data)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create complaint") from exc
    except ValueError as exc:  # e.g. an invalid upload reference
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    complaint = result.complaint
    response.status_code = status.HTTP_201_CREATED if result.outcome == "created" else status.HTTP_200_OK
    message = SUBMIT_MESSAGES[result.outcome].format(
        id=complaint.complaint_id,
        dept=complaint.department_name or "the control room",
        dist=f"{result.distance_m:.0f} m" if result.distance_m is not None else "nearby",
    )
    # Merged reports return the submitter's own view, never the original reporter's details.
    return ComplaintSubmitResponse.model_validate(
        {
            **result.view,
            "outcome": result.outcome,
            "duplicate": result.outcome != "created",
            "distance_m": round(result.distance_m, 1) if result.distance_m is not None else None,
            "message": message,
        }
    )


def _event_for(outcome: str) -> str | None:
    return {"created": "new_complaint", "confirmed": "confirmed"}.get(outcome)


def _reload(db: Session, complaint_id: int) -> Complaint:
    return db.scalar(
        select(Complaint).options(selectinload(Complaint.department)).where(Complaint.complaint_id == complaint_id)
    )


def _submit_and_reload(db, data, current_user, response):
    result = _submit(db, data, current_user, response)
    return result, _reload(db, result.complaint_id)


@router.post("/complaints", response_model=ComplaintSubmitResponse, status_code=status.HTTP_201_CREATED)
def create(
    data: ComplaintCreateRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComplaintSubmitResponse:
    """File a complaint. Returns 201 when created, 200 when merged into an existing duplicate."""
    result = _submit(db, ComplaintCreate(**data.model_dump()), current_user, response)
    event = _event_for(result.outcome)
    if event:
        publish_from_sync(event, _reload(db, result.complaint_id))
    return result


@router.post(
    "/complaints/with-image", response_model=ComplaintSubmitResponse, status_code=status.HTTP_201_CREATED
)
async def create_with_image(
    response: Response,
    title: str = Form(...),
    description: str = Form(...),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    user_id: int | None = Form(default=None),
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComplaintSubmitResponse:
    # Validate the form before anything is written to disk.
    try:
        fields = ComplaintCreateRequest(
            user_id=user_id, title=title, description=description, latitude=latitude, longitude=longitude
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc

    filename, _ = await save_image(image)
    try:
        data = ComplaintCreate(**fields.model_dump(), image_filename=filename)
        # Classification runs YOLO and the DB work is synchronous: keep both off the event loop.
        result, complaint = await anyio.to_thread.run_sync(_submit_and_reload, db, data, current_user, response)
    except BaseException:
        remove_upload(filename)
        raise
    if result.outcome == "already_reported":
        remove_upload(filename)  # nothing references this photo
    event = _event_for(result.outcome)
    if event:
        await publish(event, complaint)
    return result


@router.patch("/complaints/{complaint_id}/status", response_model=ComplaintResponse)
def update_status(
    complaint_id: int,
    data: ComplaintStatusUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Complaint:
    """Kept for existing clients; the staff portal uses PATCH /api/admin/complaints/{id}/status."""
    try:
        new_status = canonical_status(data.status)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    complaint = admin_service.load_for_staff(db, current_user, complaint_id)
    complaint = admin_service.change_status(db, current_user, complaint, new_status)
    publish_from_sync("status_update", complaint)
    return complaint


@router.get("/complaints/my", response_model=list[ComplaintResponse])
def read_my_complaints(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return complaints_for_user(db, current_user.user_id)


@router.get("/complaints/{complaint_id}/updates", response_model=list[PublicComplaintUpdate])
def read_complaint_updates(
    complaint_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PublicComplaintUpdate]:
    """Status history and official responses, for the citizen who filed or confirmed the complaint
    and for staff who manage it."""
    complaint = db.scalar(
        select(Complaint)
        .options(selectinload(Complaint.updates))
        .where(Complaint.complaint_id == complaint_id)
    )
    if complaint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Complaint not found")
    if current_user.role == "citizen" and not user_can_view(db, complaint, current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Complaint not found")
    if current_user.role == "department":
        admin_service.load_for_staff(db, current_user, complaint_id)  # 403 outside their department
    return admin_service.public_updates(complaint)


@router.get("/complaints", response_model=list[ComplaintResponse])
def read_all(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[Complaint]:
    """Every complaint, unpaginated. Admin only; the staff portal uses /api/admin/complaints."""
    return get_all_complaints(db)


@map_router.get("/complaints/map", response_model=list[ComplaintMapItem])
def map_data(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=2000, ge=1, le=10000, description="Most recent complaints to return"),
) -> list[ComplaintMapItem]:
    """Positions, categories and statuses for the GIS map (no descriptions, photos or reporters)."""
    complaints = db.scalars(
        select(Complaint)
        .where(Complaint.latitude.is_not(None), Complaint.longitude.is_not(None))
        .order_by(Complaint.complaint_id.desc())
        .limit(limit)
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
