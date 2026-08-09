from datetime import datetime
from pydantic import BaseModel, ConfigDict


class UserBase(BaseModel):
    full_name: str
    email: str
    phone: str | None = None
    role: str
    department_id: int | None = None


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)