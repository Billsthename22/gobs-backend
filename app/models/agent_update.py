"""New tables only: deployment does not alter existing device rows."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from app.core.database import Base


class AgentRelease(Base):
    __tablename__ = "agent_releases"
    id = Column(String(36), primary_key=True)
    version = Column(String(40), nullable=False)
    platform = Column(String(16), nullable=False)
    architecture = Column(String(16), nullable=False)
    manifest = Column(Text, nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("version", "platform", "architecture"),)


class AgentState(Base):
    __tablename__ = "agent_states"
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    version = Column(String(40), nullable=False)
    platform = Column(String(16), nullable=False)
    architecture = Column(String(16), nullable=False)
    updater_ready = Column(Boolean, nullable=False, default=False)
    reported_at = Column(DateTime(timezone=True), nullable=False)
    active_job_id = Column(String(36), nullable=True)


class AgentUpdate(Base):
    __tablename__ = "agent_updates"
    id = Column(String(36), primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    release_id = Column(String(36), ForeignKey("agent_releases.id"), nullable=False)
    requested_by = Column(String(255), nullable=False)
    status = Column(String(24), nullable=False, default="queued")
    detail = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
