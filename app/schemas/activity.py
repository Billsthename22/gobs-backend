from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ActivityCreate(BaseModel):
    device_id: int
    employee_id: Optional[int] = None
    activity_type: str
    description: str


class ActivityResponse(BaseModel):
    id: int
    device_id: int
    employee_id: Optional[int]
    activity_type: str
    description: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)
