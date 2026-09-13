from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

class AlertCreate(BaseModel):
    device_id: int
    employee_id: Optional[int] = None
    alert_type: str
    severity: str = "medium"
    title: str
    description: str


class AlertUpdate(BaseModel):
    severity: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    is_resolved: Optional[bool] = None


class AlertResponse(BaseModel):
    id: int
    device_id: int
    employee_id: Optional[int]
    alert_type: str
    severity: str
    title: str
    description: str
    is_resolved: bool
    created_at: datetime
    resolved_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)