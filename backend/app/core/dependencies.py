from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

bearer_scheme = HTTPBearer()
optional_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_access_token(credentials.credentials)
    if payload is None or not isinstance(payload.get("sub"), str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.account_status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not active")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def require_department_or_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in ("department", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Department or admin access required")
    return current_user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """The signed-in user if a valid token was sent, else None (for endpoints open to everyone)."""
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    if payload is None or not isinstance(payload.get("sub"), str):
        return None
    user = db.get(User, int(payload["sub"]))
    return user if user is not None and user.account_status == "active" else None


def websocket_user(token: str) -> User | None:
    """Resolve a WebSocket's ?token= to an active user (browsers can't send headers on WebSockets)."""
    from app.db.session import SessionLocal

    payload = decode_access_token(token or "")
    if payload is None or not isinstance(payload.get("sub"), str):
        return None
    with SessionLocal() as db:
        user = db.get(User, int(payload["sub"]))
        if user is None or user.account_status != "active":
            return None
        db.expunge(user)
        return user
