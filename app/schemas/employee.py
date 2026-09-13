
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class EmployeeCreate(BaseModel):
    full_name: str
    email: EmailStr
    department: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None


class EmployeeResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    department: Optional[str]
    job_title: Optional[str]
    phone: Optional[str]
    status: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
