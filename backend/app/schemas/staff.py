from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AccountStatus = Literal["pending", "active", "rejected"]


class StaffAccount(BaseModel):
    user_id: int
    full_name: str
    email: str
    phone: str | None
    department_id: int | None
    department_name: str | None
    account_status: AccountStatus
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by_name: str | None
    review_note: str | None


class StaffAccountList(BaseModel):
    items: list[StaffAccount]
    counts: dict[str, int] = Field(description="Department accounts per status, for the whole city")


class ApproveRequest(BaseModel):
    # Lets the admin correct the department the applicant picked.
    department_id: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000, description="Shown to the applicant when they try to log in")

    @field_validator("reason")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return (value.strip() or None) if value is not None else None
