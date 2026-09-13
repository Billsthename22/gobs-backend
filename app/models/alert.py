from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    device_id = Column(
        Integer,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    employee_id = Column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    alert_type = Column(
        String(100),
        nullable=False,
    )

    severity = Column(
        String(50),
        nullable=False,
        default="medium",
    )

    title = Column(
        String(255),
        nullable=False,
    )

    description = Column(
        Text,
        nullable=False,
    )

    is_resolved = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    resolved_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
