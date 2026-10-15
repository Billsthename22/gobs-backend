import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-only-key"
os.environ["ADMIN_EMAILS"] = "admin@example.org"
os.environ["POWER_ADMIN_EMAILS"] = "admin@example.org"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.core.database import Base, get_db
from app.core import auditing
from app.websocket import monitoring, devices


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(auditing, "SessionLocal", sessions)
    monkeypatch.setattr(monitoring, "SessionLocal", sessions)
    monkeypatch.setattr(devices, "SessionLocal", sessions)
    def get_test_db():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = get_test_db
    yield sessions
    app.dependency_overrides.clear()
    devices.device_manager.connections.clear()
    devices.device_manager.last_received.clear()
    monitoring.monitoring_manager.connections.clear()
    engine.dispose()
