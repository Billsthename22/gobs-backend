from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RemoteMonitoringStart(BaseModel):
    device_id: int


class RemoteMonitoringResponse(BaseModel):
    id: int
    device_id: int
    started_by: Optional[int]
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    is_authorized: bool

    model_config = ConfigDict(from_attributes=True)
