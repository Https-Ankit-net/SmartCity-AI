from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse


router = APIRouter()


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, db: Session = Depends(get_db)) -> User:
    if db.scalar(select(User).where(User.email == user.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    if user.phone and db.scalar(select(User).where(User.phone == user.phone)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone already registered")

    new_user = User(
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        password_hash=hash_password(user.password),
        role=user.role,
        department_id=user.department_id,
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
    return new_user


@router.get("/users", response_model=list[UserResponse])
def get_users(db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.user_id)).all())
