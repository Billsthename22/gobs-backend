from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.database import Base, engine
from app.models.user import User
from app.models.device import Device
from app.models.employee import Employee
from app.models.activity import Activity
from app.models.alert import Alert
from app.models.remote_session import RemoteMonitoringSession

from app.routes.auth import router as auth_router
from app.routes.devices import router as devices_router
from app.routes.employees import router as employees_router
from app.routes.activity import router as activity_router
from app.routes.alerts import router as alerts_router
from app.routes.dashboard import router as dashboard_router
from app.routes.analytics import router as analytics_router
from app.routes.remote_monitor import router as remote_monitor_router
from app.routes.agents import router as agents_router

from app.websocket.devices import router as websocket_router
from app.websocket.monitoring import router as monitoring_websocket_router


Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="GBOS API",
    description="Backend API for the Gbaja-partners device management platform.",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(employees_router)
app.include_router(activity_router)
app.include_router(alerts_router)
app.include_router(dashboard_router)
app.include_router(analytics_router)
app.include_router(remote_monitor_router)
app.include_router(agents_router)
app.include_router(websocket_router)
app.include_router(monitoring_websocket_router)


@app.get("/")
def root():
    return {
        "message": "GBOS API is running",
        "version": "1.0.0",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
    }


@app.get("/health/database")
def database_health():
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT current_database()")
        )
        database = result.scalar()

    return {
        "status": "ok",
        "database": database,
    }