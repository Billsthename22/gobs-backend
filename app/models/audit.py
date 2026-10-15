from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_email = Column(String(255), nullable=True)
    action = Column(String(150), nullable=False)
    resource = Column(String(255), nullable=False)
    result = Column(String(40), nullable=False, default="attempted")
    status_code = Column(Integer, nullable=True)
    command_id = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
