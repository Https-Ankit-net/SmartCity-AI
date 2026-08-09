from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ComplaintCreate(BaseModel):
    user_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    image_filename: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ComplaintStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=20)


class ComplaintResponse(ComplaintCreate):
    model_config = ConfigDict(from_attributes=True)

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
    created_at: datetime


class ComplaintMapItem(BaseModel):
    id: int
    lat: float
    lng: float
    type: str
    status: str
