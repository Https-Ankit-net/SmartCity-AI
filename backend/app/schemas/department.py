from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department_id: int
    department_name: str
    department_email: str | None
    contact_number: str | None
    office_address: str | None
    created_at: datetime
