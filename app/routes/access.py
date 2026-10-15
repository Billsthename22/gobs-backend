import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.access import administrator_emails, require_admin, token_hash
from app.core.auditing import AuditedRouter
from app.core.database import get_db
from app.models.access import AccessGrant, DeviceCredential
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.user import User
from app.websocket.devices import device_manager
from app.websocket.monitoring import monitoring_manager

router = AuditedRouter(prefix="/access", tags=["Workspace access"], dependencies=[Depends(require_admin)])


class Invitation(BaseModel):
    email: EmailStr
    role: Literal["viewer", "operator", "admin"] = "viewer"


@router.get("/members")
def members(db: Session = Depends(get_db)):
    result = {grant.email: {"email": grant.email, "role": grant.role, "bootstrap": False} for grant in db.query(AccessGrant).all()}
    for email in administrator_emails():
        result[email] = {"email": email, "role": "admin", "bootstrap": True}
    return list(result.values())


@router.post("/invitations")
async def invite(data: Invitation, db: Session = Depends(get_db)):
    email = str(data.email).lower()
    if email in administrator_emails():
        raise HTTPException(409, "Bootstrap administrator is configured through ADMIN_EMAILS")
    grant = db.get(AccessGrant, email)
    if not grant:
        grant = AccessGrant(email=email, role=data.role)
        db.add(grant)
    grant.role = data.role
    invitation_token = secrets.token_urlsafe(32)
    grant.invitation_hash = token_hash(invitation_token)
    grant.invitation_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.commit()
    await monitoring_manager.close_all()
    return {"email": email, "role": data.role, "invitation_token": invitation_token, "existing_account": db.query(User).filter(func.lower(User.email) == email).first() is not None}


@router.delete("/members/{email}", status_code=204)
async def revoke(email: str, db: Session = Depends(get_db)):
    email = email.lower()
    if email in administrator_emails():
        raise HTTPException(409, "Change ADMIN_EMAILS to remove a bootstrap administrator")
    grant = db.get(AccessGrant, email)
    if not grant:
        raise HTTPException(404, "Member not found")
    db.delete(grant)
    db.commit()
    await monitoring_manager.close_all()


@router.post("/devices/{device_id}/credential")
async def issue_device_credential(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device or not device.is_active:
        raise HTTPException(404, "Active device not found")
    token = secrets.token_urlsafe(48)
    credential = db.get(DeviceCredential, device_id)
    if credential:
        credential.token_hash = token_hash(token)
    else:
        db.add(DeviceCredential(device_id=device_id, token_hash=token_hash(token)))
    db.commit()
    socket = device_manager.connections.get(device_id)
    if socket:
        device_manager.disconnect(device_id, socket)
        await socket.close(code=1008)
    return {"device_id": device_id, "device_token": token}


@router.get("/audit-logs")
def audit_logs(limit: int = 100, before_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(AuditLog)
    if before_id is not None:
        query = query.filter(AuditLog.id < before_id)
    entries = query.order_by(AuditLog.id.desc()).limit(max(1, min(limit, 500))).all()
    return [{key: getattr(entry, key) for key in ("id", "actor_email", "action", "resource", "result", "status_code", "command_id", "created_at")} for entry in entries]
