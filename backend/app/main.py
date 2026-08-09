from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.models

from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserResponse

from app.models.department import Department

print("Department:", Department)
print("User:", User)

app = FastAPI(
    title="SmartCity AI API",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "message": "Welcome to SmartCity AI API"
    }


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {
        "status": "Healthy",
        "database": "Connected"
    }


@app.get("/users", response_model=list[UserResponse])
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()