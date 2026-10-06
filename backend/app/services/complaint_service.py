import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.ai.incident_classifier import analyze_incident
from app.models.complaint import Complaint
from app.models.complaint_confirmation import ComplaintConfirmation
from app.models.department import Department
from app.schemas.complaint import ComplaintCreate, ComplaintResponse
from app.services.duplicate_detector import find_duplicate
from app.services.file_service import upload_path
from app.services.severity_service import (
    bump_for_confirmations,
    keyword_hits,
    priority_for,
    score_severity,
)

PRIORITIES = ("Low", "Medium", "High")
# "Please fix it urgently": the citizen's urgency raises priority one level (presentation, slide 5).
URGENCY_TERMS = {"urgent", "urgently", "immediately", "asap", "emergency"}

Outcome = Literal["created", "confirmed", "already_reported"]


@dataclass
class SubmissionResult:
    complaint: Complaint
    outcome: Outcome
    distance_m: float | None = None
    # What the submitting citizen may see (their own view for merged reports).
    view: dict = field(default_factory=dict)


def owner_view(complaint: Complaint) -> dict:
    return {**ComplaintResponse.model_validate(complaint).model_dump(mode="json"), "relation": "owner"}


def confirmer_view(complaint: Complaint, confirmation: ComplaintConfirmation) -> dict:
    """A confirmer's view: the official complaint's progress, with *their own* report details.

    Never includes the original reporter's text, photo, coordinates or user id.
    """
    full = ComplaintResponse.model_validate(complaint).model_dump(mode="json")
    return {
        **{k: full[k] for k in (
            "complaint_id", "incident_type", "priority", "status", "department_id", "department_name",
            "severity_score", "confirmation_count", "created_at",
        )},
        "user_id": confirmation.user_id,
        "title": confirmation.title or f"Confirmed {complaint.complaint_type} report",
        "description": confirmation.description or "",
        "image_filename": confirmation.image_filename,
        "latitude": confirmation.latitude,
        "longitude": confirmation.longitude,
        "detection_label": None,
        "detection_confidence": None,
        "severity_factors": None,
        "relation": "confirmer",
    }


def _load(db: Session, complaint_id: int) -> Complaint:
    return db.scalar(
        select(Complaint)
        .options(selectinload(Complaint.department), selectinload(Complaint.confirmations))
        .where(Complaint.complaint_id == complaint_id)
        .execution_options(populate_existing=True)
    )


_process_locks: dict[str, threading.Lock] = {}
_process_locks_guard = threading.Lock()


@contextmanager
def _dedupe_lock(db: Session, category: str) -> Iterator[None]:
    """Serialise "is this a duplicate? → create" per category so two simultaneous reports of the same
    incident can't both become new complaints.

    PostgreSQL: a transaction-scoped advisory lock, shared by every worker and container.
    SQLite (development, single process): a process-local lock.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"smartcity:dedupe:{category}"})
        yield  # released when the transaction commits or rolls back
        return
    with _process_locks_guard:
        lock = _process_locks.setdefault(category, threading.Lock())
    with lock:
        yield


def _priority(rule_priority: str, severity_score: int, text_value: str) -> str:
    rank = max(PRIORITIES.index(rule_priority) if rule_priority in PRIORITIES else 0, PRIORITIES.index(priority_for(severity_score)))
    if any(phrase in URGENCY_TERMS for phrase, _ in keyword_hits(text_value)):
        rank = min(rank + 1, len(PRIORITIES) - 1)
    return PRIORITIES[rank]


def submit_complaint(db: Session, data: ComplaintCreate) -> SubmissionResult:
    """Classify, de-duplicate and score a citizen report.

    A report within 50 m of an active complaint of the same category from the last 24 h
    becomes a confirmation of that complaint instead of a new row.
    """
    image_path = upload_path(data.image_filename) if data.image_filename else None
    # Image detection (slow) runs before taking the de-duplication lock.
    analysis = analyze_incident(data.description, str(image_path) if image_path else None)
    category = str(analysis["incident_type"])
    text_value = f"{data.title}. {data.description}"

    try:
        with _dedupe_lock(db, category):
            match = find_duplicate(db, category=category, latitude=data.latitude, longitude=data.longitude)
            if match is not None:
                return _confirm(db, match.complaint, data, match.distance_m)

            severity = score_severity(
                category=category,
                text=text_value,
                detection_label=analysis["detection_label"],
                detection_confidence=analysis["detection_confidence"],
                latitude=data.latitude,
                longitude=data.longitude,
            )
            department = db.scalar(select(Department).where(Department.department_name == analysis["department"]))
            complaint = Complaint(
                user_id=data.user_id,
                department_id=department.department_id if department else None,
                title=data.title,
                description=data.description,
                complaint_type=category,
                priority=_priority(str(analysis["priority"]), severity.score, text_value),
                image_filename=data.image_filename,
                latitude=data.latitude,
                longitude=data.longitude,
                detection_label=analysis["detection_label"],
                detection_confidence=analysis["detection_confidence"],
                severity_score=severity.score,
                severity_factors=severity.factors,
            )
            db.add(complaint)
            db.commit()
    except Exception:
        db.rollback()
        raise
    complaint = _load(db, complaint.complaint_id)
    return SubmissionResult(complaint, "created", view=owner_view(complaint))


def _view_for(db: Session, complaint: Complaint, user_id: int) -> dict:
    if complaint.user_id == user_id:
        return owner_view(complaint)
    confirmation = db.scalar(
        select(ComplaintConfirmation).where(
            ComplaintConfirmation.complaint_id == complaint.complaint_id, ComplaintConfirmation.user_id == user_id
        )
    )
    return confirmer_view(complaint, confirmation) if confirmation else owner_view(complaint)


def _confirm(db: Session, existing: Complaint, data: ComplaintCreate, distance_m: float) -> SubmissionResult:
    already = existing.user_id == data.user_id or db.scalar(
        select(ComplaintConfirmation.confirmation_id).where(
            ComplaintConfirmation.complaint_id == existing.complaint_id,
            ComplaintConfirmation.user_id == data.user_id,
        )
    )
    if already:
        db.commit()  # end the transaction (releases the de-duplication lock)
        complaint = _load(db, existing.complaint_id)
        return SubmissionResult(complaint, "already_reported", distance_m, view=_view_for(db, complaint, data.user_id))

    db.add(
        ComplaintConfirmation(
            complaint_id=existing.complaint_id,
            user_id=data.user_id,
            title=data.title,
            description=data.description,
            image_filename=data.image_filename,
            latitude=data.latitude,
            longitude=data.longitude,
            distance_m=round(distance_m, 1),
        )
    )
    # Atomic increment: concurrent confirmations can't overwrite each other's count.
    db.execute(
        update(Complaint)
        .where(Complaint.complaint_id == existing.complaint_id)
        .values(confirmation_count=Complaint.confirmation_count + 1)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    db.refresh(existing)
    if existing.severity_score is not None:
        existing.severity_score, existing.severity_factors = bump_for_confirmations(
            existing.severity_score, existing.severity_factors, existing.confirmation_count
        )
    try:
        db.commit()
    except IntegrityError:
        # The same citizen confirmed concurrently from another request.
        db.rollback()
        complaint = _load(db, existing.complaint_id)
        return SubmissionResult(complaint, "already_reported", distance_m, view=_view_for(db, complaint, data.user_id))
    complaint = _load(db, existing.complaint_id)
    return SubmissionResult(complaint, "confirmed", distance_m, view=_view_for(db, complaint, data.user_id))


def create_complaint(db: Session, data: ComplaintCreate) -> Complaint:
    """Kept for callers that only need the resulting complaint."""
    return submit_complaint(db, data).complaint


def complaints_for_user(db: Session, user_id: int) -> list[dict]:
    """Complaints the citizen filed (full view), plus ones they confirmed (their own view of them)."""
    owned = db.scalars(
        select(Complaint).options(selectinload(Complaint.department)).where(Complaint.user_id == user_id)
    ).all()
    confirmations = db.scalars(
        select(ComplaintConfirmation)
        .options(selectinload(ComplaintConfirmation.complaint).selectinload(Complaint.department))
        .where(ComplaintConfirmation.user_id == user_id)
    ).all()
    views = [owner_view(c) for c in owned]
    owned_ids = {c.complaint_id for c in owned}
    views += [confirmer_view(c.complaint, c) for c in confirmations if c.complaint_id not in owned_ids]
    return sorted(views, key=lambda v: v["complaint_id"], reverse=True)


def user_can_view(db: Session, complaint: Complaint, user_id: int) -> bool:
    if complaint.user_id == user_id:
        return True
    return db.scalar(
        select(ComplaintConfirmation.confirmation_id).where(
            ComplaintConfirmation.complaint_id == complaint.complaint_id,
            ComplaintConfirmation.user_id == user_id,
        )
    ) is not None


def get_all_complaints(db: Session) -> list[Complaint]:
    statement = select(Complaint).options(selectinload(Complaint.department)).order_by(Complaint.complaint_id)
    return list(db.scalars(statement).all())
