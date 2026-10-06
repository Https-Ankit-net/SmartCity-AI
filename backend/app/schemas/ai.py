from pydantic import BaseModel, Field


class DetectionResponse(BaseModel):
    filename: str
    prediction: str
    confidence: float = Field(ge=0.0, le=1.0)


class TextAnalysisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000, description="Complaint text or a voice transcription")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class TextEntity(BaseModel):
    text: str
    label: str
    start: int
    end: int


class KeyIssue(BaseModel):
    issue: str
    category: str
    evidence: list[str]


class Intent(BaseModel):
    label: str = Field(description="report_issue | emergency | follow_up | feedback | question | other")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    alternatives: list[str]


class SeverityFactor(BaseModel):
    signal: str
    detail: str
    points: int


class SeverityResult(BaseModel):
    score: int = Field(ge=1, le=10)
    level: str
    factors: list[SeverityFactor]


class TextAnalysisResponse(BaseModel):
    backend: str = Field(description="'spacy:<model>' or 'rules' when spaCy is unavailable")
    text: str
    entities: list[TextEntity]
    locations: list[str]
    time_expressions: list[str]
    key_issues: list[KeyIssue]
    intent: Intent
    suggested_category: str
    suggested_department: str | None
    suggested_title: str
    severity: SeverityResult
