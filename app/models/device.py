
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    device_name = Column(
        String(255),
        nullable=False,
    )

    hostname = Column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    operating_system = Column(
        String(100),
        nullable=False,
    )

    ip_address = Column(
        String(45),
        nullable=True,
    )

    location_name = Column(
        String(255),
        nullable=True,
    )

    location_address = Column(
        String(500),
        nullable=True,
    )

    latitude = Column(
        Float,
        nullable=True,
    )

    longitude = Column(
        Float,
        nullable=True,
    )

    location_updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    gps_enabled = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    gps_latitude = Column(
        Float,
        nullable=True,
    )

    gps_longitude = Column(
        Float,
        nullable=True,
    )

    gps_updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    employee_id = Column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(50),
        default="offline",
        nullable=False,
    )

    cpu_usage = Column(
        Float,
        default=0,
        nullable=False,
    )

    memory_usage = Column(
        Float,
        default=0,
        nullable=False,
    )

    storage_usage = Column(
        Float,
        default=0,
        nullable=False,
    )

    last_seen = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
