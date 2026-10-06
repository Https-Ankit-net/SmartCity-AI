from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserBase(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=150)
    phone: str | None = Field(default=None, max_length=20)
    role: str = Field(default="citizen", min_length=1, max_length=20)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=72)
    department_id: int | None = None

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def bcrypt_limit(cls, value: str) -> str:
        # bcrypt ignores everything past 72 bytes; refuse rather than silently truncate.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return value


class UserResponse(UserBase):
    # Pydantic v2 equivalent of Pydantic v1's `orm_mode = True`.
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    department_id: int | None
    account_status: str = "active"
    created_at: datetime
