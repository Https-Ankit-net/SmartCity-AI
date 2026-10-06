"""Staff-side complaint management: scoping, listing, actions and analytics."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from math import ceil

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.complaint import Complaint
from app.models.complaint_update import ComplaintUpdate
from app.models.department import Department
from app.models.user import User
from app.schemas.admin import (
    STATUSES,
    AdminComplaintDetail,
    AdminComplaintItem,
    ComplaintPage,
    ComplaintUpdateResponse,
    PublicComplaintUpdate,
    canonical_status,
)
from app.schemas.complaint import ComplaintResponse

# A complaint is "escalated" when it is still open and has waited too long for its urgency.
ESCALATE_HIGH_AFTER = timedelta(hours=24)
ESCALATE_ANY_AFTER = timedelta(hours=72)
OPEN_STATUSES = ("pending", "in progress")

SORT_COLUMNS = {
    "id": (Complaint.complaint_id,),
    "image": (case((Complaint.image_filename.is_(None), 0), else_=1),),
    "category": (Complaint.complaint_type,),
    "location": (Complaint.latitude, Complaint.longitude),
    "reporter": (User.full_name,),
    "department": (Department.department_name,),
    "status": (
        case(
            *((func.lower(Complaint.status) == s.lower(), i) for i, s in enumerate(STATUSES)),
            else_=len(STATUSES),
        ),
    ),
    "confidence": (Complaint.detection_confidence,),
    "priority": (case((func.lower(Complaint.priority) == "high", 3), (func.lower(Complaint.priority) == "medium", 2), else_=1),),
    "created": (Complaint.created_at,),
}


def utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; they are UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def department_scope(user: User) -> int | None:
    """None for admins (whole city); the user's department id for department staff."""
    if user.role == "admin":
        return None
    if user.department_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account is not linked to a department")
    return user.department_id


def _scoped(statement, user: User):
    scope = department_scope(user)
    return statement if scope is None else statement.where(Complaint.department_id == scope)


def escalated_clause(now: datetime):
    return and_(
        func.lower(Complaint.status).in_(OPEN_STATUSES),
        or_(
            and_(func.lower(Complaint.priority) == "high", Complaint.created_at < now - ESCALATE_HIGH_AFTER),
            Complaint.created_at < now - ESCALATE_ANY_AFTER,
        ),
    )


def is_escalated(complaint: Complaint, now: datetime) -> bool:
    if complaint.status.lower() not in OPEN_STATUSES:
        return False
    age = now - utc(complaint.created_at)
    return age > ESCALATE_ANY_AFTER or (complaint.priority.lower() == "high" and age > ESCALATE_HIGH_AFTER)


def _item_fields(complaint: Complaint, now: datetime) -> dict:
    updates = complaint.updates
    return {
        **ComplaintResponse.model_validate(complaint).model_dump(),
        "reporter_name": complaint.reporter_name,
        "reporter_email": complaint.reporter_email,
        "is_escalated": is_escalated(complaint, now),
        "last_update_at": updates[-1].created_at if updates else None,
        "update_count": len(updates),
    }


def _load_options():
    return (
        selectinload(Complaint.department),
        selectinload(Complaint.user),
        selectinload(Complaint.updates),
    )


def list_complaints(
    db: Session,
    user: User,
    *,
    page: int,
    page_size: int,
    status_filter: str | None,
    priority: str | None,
    incident_type: str | None,
    department_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    escalated: bool | None,
    q: str | None,
    sort_by: str,
    sort_dir: str,
) -> ComplaintPage:
    now = now_utc()
    statement = _scoped(
        select(Complaint)
        .join(User, Complaint.user_id == User.user_id)
        .outerjoin(Department, Complaint.department_id == Department.department_id),
        user,
    )
    if status_filter:
        statement = statement.where(func.lower(Complaint.status) == canonical_status(status_filter).lower())
    if priority:
        statement = statement.where(func.lower(Complaint.priority) == priority.lower())
    if incident_type:
        statement = statement.where(Complaint.complaint_type == incident_type)
    if department_id and department_scope(user) is None:
        statement = statement.where(Complaint.department_id == department_id)
    # SQLite stores naive UTC; convert offset-aware bounds (e.g. +05:30) to UTC before comparing.
    if date_from:
        statement = statement.where(Complaint.created_at >= _as_utc(date_from))
    if date_to:
        statement = statement.where(Complaint.created_at < _as_utc(date_to))
    if escalated is not None:
        clause = escalated_clause(now)
        statement = statement.where(clause if escalated else ~clause)
    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        conditions = [
            func.lower(Complaint.title).like(term),
            func.lower(Complaint.description).like(term),
            func.lower(User.full_name).like(term),
            func.lower(Complaint.complaint_type).like(term),
        ]
        if q.strip().lstrip("#").isdigit():
            conditions.append(Complaint.complaint_id == int(q.strip().lstrip("#")))
        statement = statement.where(or_(*conditions))

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0

    columns = SORT_COLUMNS.get(sort_by, SORT_COLUMNS["id"])
    order = [(c.desc() if sort_dir == "desc" else c.asc()).nulls_last() for c in columns]
    order.append(Complaint.complaint_id.desc())
    rows = db.scalars(
        statement.options(*_load_options()).order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).all()

    return ComplaintPage(
        items=[AdminComplaintItem.model_validate(_item_fields(c, now)) for c in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, ceil(total / page_size)),
    )


def load_for_staff(db: Session, user: User, complaint_id: int) -> Complaint:
    complaint = db.scalar(
        select(Complaint).options(*_load_options()).where(Complaint.complaint_id == complaint_id)
    )
    if complaint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Complaint not found")
    scope = department_scope(user)
    if scope is not None and complaint.department_id != scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This complaint belongs to another department")
    return complaint


def _update_response(update: ComplaintUpdate) -> ComplaintUpdateResponse:
    return ComplaintUpdateResponse(
        update_id=update.update_id,
        kind=update.kind,
        from_status=update.from_status,
        to_status=update.to_status,
        from_department_name=update.from_department.department_name if update.from_department else None,
        to_department_name=update.to_department.department_name if update.to_department else None,
        internal_note=update.internal_note,
        public_response=update.public_response,
        actor_name=update.actor.full_name if update.actor else None,
        actor_role=update.actor.role if update.actor else None,
        created_at=update.created_at,
    )


def complaint_detail(complaint: Complaint) -> AdminComplaintDetail:
    return AdminComplaintDetail.model_validate(
        {
            **_item_fields(complaint, now_utc()),
            "reporter_phone": complaint.user.phone if complaint.user else None,
            "updates": [_update_response(u) for u in complaint.updates],
        }
    )


def public_updates(complaint: Complaint) -> list[PublicComplaintUpdate]:
    return [
        PublicComplaintUpdate(
            kind=u.kind,
            from_status=u.from_status,
            to_status=u.to_status,
            to_department_name=u.to_department.department_name if u.to_department else None,
            public_response=u.public_response,
            created_at=u.created_at,
        )
        for u in complaint.updates
    ]


def _reload(db: Session, complaint_id: int) -> Complaint:
    db.expire_all()
    return db.scalar(select(Complaint).options(*_load_options()).where(Complaint.complaint_id == complaint_id))


def change_status(
    db: Session,
    actor: User,
    complaint: Complaint,
    new_status: str,
    internal_note: str | None = None,
    public_response: str | None = None,
) -> Complaint:
    old_status = complaint.status
    changed = old_status.lower() != new_status.lower()
    if not changed and not internal_note and not public_response:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Complaint is already {new_status}")
    complaint.status = new_status
    db.add(
        ComplaintUpdate(
            complaint_id=complaint.complaint_id,
            actor_user_id=actor.user_id,
            kind="status" if changed else "note",
            from_status=old_status if changed else None,
            to_status=new_status if changed else None,
            internal_note=internal_note,
            public_response=public_response,
        )
    )
    db.commit()
    return _reload(db, complaint.complaint_id)


def reassign(
    db: Session,
    actor: User,
    complaint: Complaint,
    department_id: int,
    internal_note: str | None = None,
    public_response: str | None = None,
) -> Complaint:
    if db.get(Department, department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    if complaint.department_id == department_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Complaint is already assigned to that department")
    db.add(
        ComplaintUpdate(
            complaint_id=complaint.complaint_id,
            actor_user_id=actor.user_id,
            kind="assignment",
            from_department_id=complaint.department_id,
            to_department_id=department_id,
            internal_note=internal_note,
            public_response=public_response,
        )
    )
    complaint.department_id = department_id
    db.commit()
    return _reload(db, complaint.complaint_id)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def analytics(db: Session, user: User, days: int, tz_offset_minutes: int) -> dict:
    """Headline metrics and chart series for the staff dashboard, scoped to the user's department."""
    now = now_utc()
    scope = department_scope(user)
    complaints = db.scalars(
        _scoped(select(Complaint), user).options(selectinload(Complaint.department), selectinload(Complaint.updates))
    ).all()

    by_status = Counter()
    for c in complaints:
        try:
            by_status[canonical_status(c.status)] += 1
        except ValueError:
            by_status[c.status] += 1
    total = len(complaints)
    resolved = by_status.get("Resolved", 0)

    response_times: list[timedelta] = []
    resolution_times: list[timedelta] = []
    # Local calendar day for the trend chart: the browser sends Date.getTimezoneOffset() (minutes behind UTC).
    local = timedelta(minutes=-tz_offset_minutes)
    today = (now + local).date()
    first_day = today - timedelta(days=days - 1)
    reported_per_day: Counter[date] = Counter()
    resolved_per_day: Counter[date] = Counter()

    for c in complaints:
        created = utc(c.created_at)
        reported_per_day[(created + local).date()] += 1
        if c.updates:
            response_times.append(utc(c.updates[0].created_at) - created)
        resolved_updates = [u for u in c.updates if (u.to_status or "").lower() == "resolved"]
        if resolved_updates and c.status.lower() == "resolved":
            resolved_at = utc(resolved_updates[-1].created_at)
            resolution_times.append(resolved_at - created)
            resolved_per_day[(resolved_at + local).date()] += 1

    trend = []
    for i in range(days):
        day = first_day + timedelta(days=i)
        trend.append({"date": day.isoformat(), "reported": reported_per_day[day], "resolved": resolved_per_day[day]})

    by_department = Counter(c.department_name or "Unassigned" for c in complaints)
    if scope is None:
        for name in db.scalars(select(Department.department_name)).all():
            by_department.setdefault(name, 0)

    return {
        "scope": {
            "role": user.role,
            "department_id": scope,
            "department_name": db.get(Department, scope).department_name if scope else None,
        },
        "total_users": (db.scalar(select(func.count(User.user_id))) or 0) if scope is None else None,
        "total_complaints": total,
        "open_complaints": sum(by_status.get(s, 0) for s in ("Pending", "In Progress")),
        "resolution_rate": round(resolved / total * 100, 1) if total else 0.0,
        "avg_response_hours": _hours(sum(response_times, timedelta()) / len(response_times)) if response_times else None,
        "responded_count": len(response_times),
        "avg_resolution_hours": _hours(sum(resolution_times, timedelta()) / len(resolution_times)) if resolution_times else None,
        "escalated_count": sum(is_escalated(c, now) for c in complaints),
        "escalation_rule": {
            "high_priority_hours": ESCALATE_HIGH_AFTER.total_seconds() / 3600,
            "any_priority_hours": ESCALATE_ANY_AFTER.total_seconds() / 3600,
        },
        "complaints_by_status": dict(by_status),
        "complaints_by_category": dict(Counter(c.complaint_type for c in complaints)),
        "complaints_by_priority": dict(Counter(c.priority for c in complaints)),
        "complaints_by_department": dict(by_department),
        "trend": trend,
    }
