from app.core.auditing import AuditedRouter
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import UserCreate, UserLogin, UserResponse, Token
from app.core.access import require_member, token_hash, workspace_role
from app.models.access import AccessGrant
import secrets
from datetime import datetime, timezone


router = AuditedRouter(
    prefix="/auth",
    tags=["Authentication"],
)

security = HTTPBearer()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    user_data: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    email = str(user_data.email).lower()
    grant = db.query(AccessGrant).filter(AccessGrant.email == email).with_for_update().first()
    if (not grant or not grant.invitation_hash or not user_data.invitation_token
            or not grant.invitation_expires_at
            or grant.invitation_expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)
            or not secrets.compare_digest(grant.invitation_hash, token_hash(user_data.invitation_token))):
        raise HTTPException(403, "A valid administrator invitation is required")
    if len(user_data.password) < 12 or len(user_data.password.encode()) > 72:
        raise HTTPException(422, "Password must be at least 12 characters and at most 72 UTF-8 bytes")
    existing_user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=email,
        full_name=user_data.full_name,
        hashed_password=hash_password(user_data.password),
    )

    db.add(user)
    grant.invitation_hash = None
    grant.invitation_expires_at = None
    db.commit()
    db.refresh(user)
    request.state.actor_email = user.email

    return user


@router.post(
    "/login",
    response_model=Token,
)
def login(
    user_data: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    user = (
        db.query(User)
        .filter(User.email == str(user_data.email).lower())
        .first()
    )

    if not user or not verify_password(
        user_data.password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    request.state.actor_email = user.email
    if not workspace_role(user.email, db):
        raise HTTPException(403, "You do not have access to this workspace")
    token = create_access_token(user.id)

    return {
        "access_token": token,
        "token_type": "bearer",
    }


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_me(request: Request, user: User = Depends(require_member), db: Session = Depends(get_db)):
    data = UserResponse.model_validate(user).model_dump()
    data["role"] = workspace_role(user.email, db)
    return data
