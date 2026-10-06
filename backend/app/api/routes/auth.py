from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.rate_limit import limiter, per_ip
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserResponse

router = APIRouter()


@router.post("/auth/login", response_model=TokenResponse, dependencies=[Depends(per_ip("login", limit=30, window_s=300))])
def login(data: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = data.email.strip().lower()
    # Per-account limit stops password guessing spread across many addresses.
    limiter.check(f"login:account:{email}", limit=10, window_s=300)
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        burn_password_check(data.password)  # same cost as a real check: no account enumeration by timing
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    # Checked only after the password, so the status of an account isn't revealed to strangers.
    if user.account_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your department account is awaiting approval by a city administrator.",
        )
    if user.account_status != "active":
        reason = f" Reason: {user.review_note}" if user.review_note else ""
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your department account has not been approved.{reason} Contact the city administration.",
        )
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
        db.commit()
    return TokenResponse(access_token=create_access_token(str(user.user_id), user.role), role=user.role)


@router.get("/auth/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
