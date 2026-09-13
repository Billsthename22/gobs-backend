from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.device import Device


router = APIRouter(
    prefix="/agents",
    tags=["Agents"],
)


class AgentRegisterRequest(BaseModel):
    device_name: str
    hostname: str
    operating_system: str
    ip_address: Optional[str] = None


class AgentRegisterResponse(BaseModel):
    device_id: int
    device_name: str
    hostname: str
    status: str
    websocket_url: str


@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_agent(
    agent_data: AgentRegisterRequest,
    db: Session = Depends(get_db),
):
    existing_device = (
        db.query(Device)
        .filter(Device.hostname == agent_data.hostname)
        .first()
    )

    if existing_device:
        return AgentRegisterResponse(
            device_id=existing_device.id,
            device_name=existing_device.device_name,
            hostname=existing_device.hostname,
            status=existing_device.status,
            websocket_url=f"/ws/devices/{existing_device.id}",
        )

    device = Device(
        device_name=agent_data.device_name,
        hostname=agent_data.hostname,
        operating_system=agent_data.operating_system,
        ip_address=agent_data.ip_address,
        status="offline",
        is_active=True,
    )

    db.add(device)
    db.commit()
    db.refresh(device)

    return AgentRegisterResponse(
        device_id=device.id,
        device_name=device.device_name,
        hostname=device.hostname,
        status=device.status,
        websocket_url=f"/ws/devices/{device.id}",
    )
