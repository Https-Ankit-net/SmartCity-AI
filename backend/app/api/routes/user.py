from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_optional_user, require_admin
from app.core.rate_limit import per_ip
from app.core.security import hash_password
from app.db.session import get_db
from app.models.department import Department
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse
from app.services import account_emails, mailer
from app.services.notify import notify_admins_from_sync


router = APIRouter()


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(per_ip("register", limit=20, window_s=3600))],
)
def create_user(
    user: UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> User:
    """Self-registration for citizens and department staff. Only an admin can create another admin;
    the first admin is created with `python -m app.manage create-admin`."""
    if user.role not in {"citizen", "department", "admin"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="role must be citizen, department or admin")
    created_by_admin = current_user is not None and current_user.role == "admin"
    if user.role == "admin" and not created_by_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin accounts can only be created by an admin")
    if user.role == "department":
        if user.department_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose the department you work for")
        if db.get(Department, user.department_id) is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown department")
    if db.scalar(select(User).where(func.lower(User.email) == user.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    if user.phone and db.scalar(select(User).where(User.phone == user.phone)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone already registered")

    new_user = User(
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        password_hash=hash_password(user.password),
        role=user.role,
        department_id=user.department_id if user.role == "department" else None,
        # Self-registered department staff wait for an admin; everyone else is active at once.
        account_status="pending" if user.role == "department" and not created_by_admin else "active",
    )
    db.add(new_user)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email, phone, or department conflicts with an existing record",
        ) from exc

    db.refresh(new_user)
    if new_user.account_status == "pending":
        department = new_user.department.department_name
        admins = db.execute(
            select(User.email).where(User.role == "admin", User.account_status == "active")
        ).scalars()
        mailer.queue(
            background_tasks,
            account_emails.request_received(new_user.full_name, new_user.email, department),
            *(account_emails.new_request_for_admin(email, new_user.full_name, new_user.email, department) for email in admins),
        )
        notify_admins_from_sync(
            db,
            {
                "type": "staff_request",
                "message": f"{new_user.full_name} requested access to {new_user.department.department_name}",
                "data": {"user_id": new_user.user_id},
            },
        )
    return new_user


@router.get("/users", response_model=list[UserResponse])
def get_users(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.user_id)).all())
