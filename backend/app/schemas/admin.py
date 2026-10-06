from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.complaint import ComplaintResponse

STATUSES = ("Pending", "In Progress", "Resolved", "Rejected")


def canonical_status(value: str) -> str:
    """Accept any casing / separator ("in_progress", "IN PROGRESS") and return the stored form."""
    key = value.strip().lower().replace("_", " ").replace("-", " ")
    for status in STATUSES:
        if status.lower() == key:
            return status
    raise ValueError(f"status must be one of: {', '.join(STATUSES)}")


class AdminComplaintItem(ComplaintResponse):
    reporter_name: str | None
    reporter_email: str | None
    is_escalated: bool
    last_update_at: datetime | None
    update_count: int


class ComplaintPage(BaseModel):
    items: list[AdminComplaintItem]
    total: int
    page: int
    page_size: int
    pages: int


class ComplaintUpdateResponse(BaseModel):
    update_id: int
    kind: str
    from_status: str | None
    to_status: str | None
    from_department_name: str | None
    to_department_name: str | None
    internal_note: str | None
    public_response: str | None
    actor_name: str | None
    actor_role: str | None
    created_at: datetime


class PublicComplaintUpdate(BaseModel):
    """What the citizen sees: no internal notes, no staff names."""

    kind: str
    from_status: str | None
    to_status: str | None
    to_department_name: str | None
    public_response: str | None
    created_at: datetime


class AdminComplaintDetail(AdminComplaintItem):
    reporter_phone: str | None
    updates: list[ComplaintUpdateResponse]


class _ActionBase(BaseModel):
    internal_note: str | None = Field(default=None, max_length=2000)
    public_response: str | None = Field(default=None, max_length=2000)

    @field_validator("internal_note", "public_response")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class StatusChangeRequest(_ActionBase):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        return canonical_status(value)


class ReassignRequest(_ActionBase):
    department_id: int = Field(gt=0)
