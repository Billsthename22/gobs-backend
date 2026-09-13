from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer
from sqlalchemy.sql import func

from app.core.database import Base


class DeviceTelemetry(Base):
    __tablename__ = "device_telemetry"

    id = Column(Integer, primary_key=True, index=True)

    device_id = Column(
        Integer,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    cpu_usage = Column(Float, nullable=False, default=0)
    memory_usage = Column(Float, nullable=False, default=0)
    storage_usage = Column(Float, nullable=False, default=0)
    network_speed_mbps = Column(Float, nullable=False, default=0)

    recorded_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
