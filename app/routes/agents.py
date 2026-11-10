from app.core.auditing import AuditedRouter
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.access import device_authenticated, token_hash
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.access import DeviceCredential
from app.models.device import Device
from app.services.device_status import get_device_status


router = AuditedRouter(
    prefix="/agents",
    tags=["Agents"],
)


class AgentRegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    device_name: str = Field(min_length=1, max_length=255)
    hostname: str = Field(min_length=1, max_length=255)
    operating_system: str = Field(min_length=1, max_length=100)
    ip_address: Optional[str] = Field(default=None, max_length=45)
    auto_enroll: bool = False


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
    token = credentials.credentials
    # The agent persists its randomly generated credential before this request.
    # Retries use that same credential; enrollment never replaces an existing one.
    for attempt in range(2):
        device = db.query(Device).filter(Device.hostname == agent_data.hostname).first()
        if device and not device.is_active:
            raise HTTPException(403, "This device is inactive")
        credential = db.get(DeviceCredential, device.id) if device else None
        if credential:
            if not device_authenticated(db, device.id, token):
                raise HTTPException(403, "A valid credential for this device is required")
            break
        if not agent_data.auto_enroll:
            raise HTTPException(403, "A valid credential for an active, pre-enrolled device is required")
        if len(token) < 32:
            raise HTTPException(403, "Automatic enrollment requires a generated device credential of at least 32 characters")
        try:
            if not device:
                device = Device(
                    device_name=agent_data.device_name,
                    hostname=agent_data.hostname,
                    operating_system=agent_data.operating_system,
                    ip_address=agent_data.ip_address,
                    is_active=True,
                )
                db.add(device)
                db.flush()
            db.add(DeviceCredential(device_id=device.id, token_hash=token_hash(token)))
            # Commit both rows together so a failed credential insert cannot
            # leave a newly created device available for another agent to claim.
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            # A simultaneous install may have inserted this hostname or bound
            # its credential. Reload and authenticate the winner before retrying.
            if attempt:
                raise HTTPException(409, "Device enrollment changed; retry registration")
    request.state.actor_email = f"device:{device.id}"
    return AgentRegisterResponse(
        device_id=device.id, device_name=device.device_name, hostname=device.hostname,
        status=get_device_status(device.last_seen), websocket_url=f"/ws/devices/{device.id}",
    )
