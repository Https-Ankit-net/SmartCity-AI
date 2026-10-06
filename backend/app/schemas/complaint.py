from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ComplaintCreateRequest(BaseModel):
    """Body of POST /api/complaints. Photos are attached via /api/complaints/with-image only."""

    model_config = ConfigDict(extra="forbid")

    # Taken from the login token; a value here must match the signed-in user.
    user_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ComplaintCreate(BaseModel):
    """Internal: a validated submission, including the stored photo's generated name."""

    user_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    image_filename: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ComplaintStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=20)


class ComplaintResponse(ComplaintCreate):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    complaint_id: int
    incident_type: str
    priority: str
    status: str
    department_id: int | None
    department_name: str | None
    detection_label: str | None
    detection_confidence: float | None
    latitude: float | None
    longitude: float | None
    severity_score: int | None = None
    severity_factors: list[dict] | None = None
    confirmation_count: int = 0
    created_at: datetime
    # "confirmer": the viewer's own report was merged into this complaint; their own details are shown.
    relation: Literal["owner", "confirmer"] = "owner"


class ComplaintSubmitResponse(ComplaintResponse):
    outcome: Literal["created", "confirmed", "already_reported"]
    duplicate: bool
    distance_m: float | None = None
    message: str


class ComplaintMapItem(BaseModel):
    id: int
    lat: float
    lng: float
    type: str
    status: str
