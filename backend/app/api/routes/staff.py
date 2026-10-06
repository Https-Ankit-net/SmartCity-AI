"""Admin review of department staff accounts: approve, reject, revoke, reinstate."""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.core.dependencies import require_admin
from app.db.session import get_db
from app.models.department import Department
from app.models.user import User
from app.schemas.staff import AccountStatus, ApproveRequest, RejectRequest, StaffAccount, StaffAccountList
from app.services import account_emails, mailer
from app.services.notify import disconnect_user_from_sync

router = APIRouter()


def _account(user: User, reviewer_name: str | None) -> StaffAccount:
    return StaffAccount(
        user_id=user.user_id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        department_id=user.department_id,
        department_name=user.department.department_name if user.department else None,
        account_status=user.account_status,
        created_at=user.created_at,
        reviewed_at=user.reviewed_at,
        reviewed_by_name=reviewer_name,
        review_note=user.review_note,
    )


def _department_account(db: Session, user_id: int) -> User:
    user = db.scalar(select(User).options(selectinload(User.department)).where(User.user_id == user_id))
    if user is None or user.role != "department":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department staff account not found")
    return user


@router.get("/admin/staff", response_model=StaffAccountList)
def list_staff(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    status_filter: AccountStatus | None = Query(default=None, alias="status"),
    department_id: int | None = None,
) -> StaffAccountList:
    reviewer = aliased(User)
    statement = (
        select(User, reviewer.full_name)
        .outerjoin(reviewer, User.reviewed_by_user_id == reviewer.user_id)
        .options(selectinload(User.department))
        .where(User.role == "department")
    )
    if status_filter:
        statement = statement.where(User.account_status == status_filter)
    if department_id:
        statement = statement.where(User.department_id == department_id)
    # Oldest pending request first; reviewed accounts most recent first.
    statement = statement.order_by(User.created_at.asc() if status_filter == "pending" else User.user_id.desc())

    counts = dict(
        db.execute(
            select(User.account_status, func.count()).where(User.role == "department").group_by(User.account_status)
        ).all()
    )
    return StaffAccountList(
        items=[_account(user, name) for user, name in db.execute(statement).all()],
        counts={s: counts.get(s, 0) for s in ("pending", "active", "rejected")},
    )


@router.post("/admin/staff/{user_id}/approve", response_model=StaffAccount)
def approve(
    user_id: int,
    data: ApproveRequest,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StaffAccount:
    """Activate a pending account, or reinstate a rejected/revoked one."""
    user = _department_account(db, user_id)
    previous_status = user.account_status
    if user.account_status == "active" and data.department_id in (None, user.department_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account is already active")
    if data.department_id is not None:
        if db.get(Department, data.department_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
        user.department_id = data.department_id
    user.account_status = "active"
    user.reviewed_by_user_id = admin.user_id
    user.reviewed_at = datetime.now(timezone.utc)
    user.review_note = data.note
    db.commit()
    db.expire_all()
    user = _department_account(db, user_id)
    if previous_status != "active":
        mailer.queue(
            background_tasks,
            account_emails.approved(
                user.full_name, user.email, user.department.department_name, data.note, restored=previous_status == "rejected"
            ),
        )
    return _account(user, admin.full_name)


@router.post("/admin/staff/{user_id}/reject", response_model=StaffAccount)
def reject(
    user_id: int,
    data: RejectRequest,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StaffAccount:
    """Decline a pending request, or revoke an active account (its tokens stop working immediately)."""
    user = _department_account(db, user_id)
    if user.account_status == "rejected":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account is already rejected")
    revoked = user.account_status == "active"
    user.account_status = "rejected"
    user.reviewed_by_user_id = admin.user_id
    user.reviewed_at = datetime.now(timezone.utc)
    user.review_note = data.reason
    db.commit()
    db.expire_all()
    user = _department_account(db, user_id)
    disconnect_user_from_sync(user.user_id)
    mailer.queue(
        background_tasks,
        account_emails.rejected(
            user.full_name,
            user.email,
            user.department.department_name if user.department else "your department",
            data.reason,
            revoked=revoked,
        ),
    )
    return _account(user, admin.full_name)
