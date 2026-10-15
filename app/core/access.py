import hashlib
import secrets
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.access import AccessGrant, DeviceCredential
from app.models.user import User


def administrator_emails():
    return {email.strip().lower() for email in settings.ADMIN_EMAILS.split(",") if email.strip()}


def workspace_role(email, db):
    if email.lower() in administrator_emails():
        return "admin"
    grant = db.get(AccessGrant, email.lower())
    return grant.role if grant and grant.role in {"viewer", "operator", "admin"} else None


def require_member(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    request.state.actor_email = user.email
    role = workspace_role(user.email, db)
    if not role:
        raise HTTPException(403, "You do not have access to this workspace")
    request.state.role = role
    return user


def require_access(request: Request, user: User = Depends(require_member)):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.state.role not in {"operator", "admin"}:
        raise HTTPException(403, "This action requires an operator or administrator")
    return user


def require_admin(request: Request, user: User = Depends(require_member)):
    if request.state.role != "admin":
        raise HTTPException(403, "This action requires an administrator")
    return user


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def device_authenticated(db, device_id, token):
    if not token:
        return False
    credential = db.get(DeviceCredential, device_id)
    return bool(credential and secrets.compare_digest(credential.token_hash, token_hash(token)))
