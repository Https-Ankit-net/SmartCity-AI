from pydantic import BaseModel, Field


class DetectionResponse(BaseModel):
    filename: str
    prediction: str
    confidence: float = Field(ge=0.0, le=1.0)
