from app.core.auditing import AuditedRouter
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.access import device_authenticated
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.device import Device
from app.services.device_status import get_device_status


router = AuditedRouter(
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
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.hostname == agent_data.hostname).first()
    if not device or not device.is_active or not device_authenticated(db, device.id, credentials.credentials):
        raise HTTPException(403, "A valid credential for an active, pre-enrolled device is required")
    request.state.actor_email = f"device:{device.id}"
    return AgentRegisterResponse(
        device_id=device.id, device_name=device.device_name, hostname=device.hostname,
        status=get_device_status(device.last_seen), websocket_url=f"/ws/devices/{device.id}",
    )
