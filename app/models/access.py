"""Single-workspace memberships and per-device credentials; additive tables."""
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func
from app.core.database import Base


class AccessGrant(Base):
    __tablename__ = "access_grants"
    email = Column(String(255), primary_key=True)
    role = Column(String(20), nullable=False)
    invitation_hash = Column(String(64), nullable=True)
    invitation_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeviceCredential(Base):
    __tablename__ = "device_credentials"
    device_id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
