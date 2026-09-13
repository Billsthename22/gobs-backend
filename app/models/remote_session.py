from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class RemoteMonitoringSession(Base):
    __tablename__ = "remote_monitoring_sessions"

    id = Column(Integer, primary_key=True, index=True)

    device_id = Column(
        Integer,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    started_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(50),
        default="active",
        nullable=False,
    )

    started_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ended_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_authorized = Column(
        Boolean,
        default=True,
        nullable=False,
    )
