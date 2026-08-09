from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=150)
    phone: str | None = Field(default=None, max_length=20)
    role: str = Field(default="citizen", min_length=1, max_length=20)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=255)
    department_id: int | None = None


class UserResponse(UserBase):
    # Pydantic v2 equivalent of Pydantic v1's `orm_mode = True`.
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    department_id: int | None
    created_at: datetime
