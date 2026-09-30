from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, computed_field

from app.services.device_status import get_device_status


class DeviceCreate(BaseModel):
    device_name: str
    hostname: str
    operating_system: str
    ip_address: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_updated_at: Optional[datetime] = None
    gps_enabled: Optional[bool] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_updated_at: Optional[datetime] = None
    employee_id: Optional[int] = None


class DeviceUpdate(BaseModel):
    device_name: Optional[str] = None
    hostname: Optional[str] = None
    operating_system: Optional[str] = None
    ip_address: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_updated_at: Optional[datetime] = None
    gps_enabled: Optional[bool] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_updated_at: Optional[datetime] = None
    employee_id: Optional[int] = None
    status: Optional[str] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    storage_usage: Optional[float] = None
    is_active: Optional[bool] = None



class DeviceGPSToggle(BaseModel):
    enabled: bool


class DeviceResponse(BaseModel):
    id: int
    device_name: str
    hostname: str
    operating_system: str
    ip_address: Optional[str]
    location_name: Optional[str]
    location_address: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    location_updated_at: Optional[datetime]
    gps_enabled: bool
    gps_latitude: Optional[float]
    gps_longitude: Optional[float]
    gps_updated_at: Optional[datetime]
    employee_id: Optional[int]
    cpu_usage: float
    memory_usage: float
    storage_usage: float
    last_seen: Optional[datetime]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def status(self) -> str:
        """Device status, derived from `last_seen`.

        This intentionally shadows the `devices.status` column: the column is
        only ever written imperatively and goes stale, so every REST consumer
        gets the derived value instead. See `app.services.device_status`.
        """
        return get_device_status(self.last_seen)
