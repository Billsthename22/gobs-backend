
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.activity import Activity
from app.models.device import Device
from app.models.remote_session import RemoteMonitoringSession
from app.models.user import User
from app.schemas.remote_monitor import (
    RemoteMonitoringResponse,
    RemoteMonitoringStart,
)
from app.websocket.devices import device_manager


router = APIRouter(
    prefix="/remote-monitoring",
    tags=["Remote Monitoring"],
)


@router.post(
    "/start",
    response_model=RemoteMonitoringResponse,
)
async def start_monitoring(
    data: RemoteMonitoringStart,
    db: Session = Depends(get_db),
):
    device = (
        db.query(Device)
        .filter(Device.id == data.device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=404,
            detail="Device not found",
        )

    existing_session = (
        db.query(RemoteMonitoringSession)
        .filter(
            RemoteMonitoringSession.device_id == data.device_id,
            RemoteMonitoringSession.status == "active",
        )
        .first()
    )

    # If an active session already exists, make sure the
    # connected agent is told to start streaming again.
    if existing_session:
        device_connected = await device_manager.send_json(
            data.device_id,
            {
                "type": "START_SCREEN_STREAM",
                "session_id": existing_session.id,
            },
        )

        if not device_connected:
            raise HTTPException(
                status_code=409,
                detail="Device is not connected to GBOS",
            )

        return existing_session

    # Create a new authorized monitoring session.
    session = RemoteMonitoringSession(
        device_id=data.device_id,
        status="active",
        is_authorized=True,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    # Tell the connected device to begin screen streaming.
    device_connected = await device_manager.send_json(
        data.device_id,
        {
            "type": "START_SCREEN_STREAM",
            "session_id": session.id,
        },
    )

    if not device_connected:
        # Don't leave an active monitoring session behind
        # when the device agent isn't connected.
        session.status = "stopped"
        session.ended_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(session)

        raise HTTPException(
            status_code=409,
            detail="Device is not connected to GBOS",
        )

    return session


@router.post(
    "/{session_id}/stop",
    response_model=RemoteMonitoringResponse,
)
async def stop_monitoring(
    session_id: int,
    db: Session = Depends(get_db),
):
    session = (
        db.query(RemoteMonitoringSession)
        .filter(RemoteMonitoringSession.id == session_id)
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=404,
            detail="Monitoring session not found",
        )

    if session.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Monitoring session is already stopped",
        )

    session.status = "stopped"
    session.ended_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(session)

    # Tell the connected device to stop screen streaming.
    await device_manager.send_json(
        session.device_id,
        {
            "type": "STOP_SCREEN_STREAM",
            "session_id": session.id,
        },
    )

    return session


@router.post("/device/{device_id}/restart")
async def restart_device(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = (
        db.query(Device)
        .filter(Device.id == device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=404,
            detail="Device not found",
        )

    if not device.is_active:
        raise HTTPException(
            status_code=409,
            detail="Device is inactive",
        )

    device_connected = await device_manager.send_json(
        device_id,
        {
            "type": "RESTART_DEVICE",
        },
    )

    if not device_connected:
        raise HTTPException(
            status_code=409,
            detail="Device is not connected to GBOS",
        )

    db.add(
        Activity(
            device_id=device.id,
            employee_id=device.employee_id,
            activity_type="DEVICE_RESTART_REQUESTED",
            description=(
                f"{current_user.full_name} "
                f"({current_user.email}) requested a restart "
                f"for {device.device_name}"
            ),
        )
    )

    db.commit()

    return {
        "success": True,
        "message": f"Restart command sent to {device.device_name}",
        "device_id": device.id,
    }


@router.post("/device/{device_id}/shutdown")
async def shutdown_device(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = (
        db.query(Device)
        .filter(Device.id == device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=404,
            detail="Device not found",
        )

    if not device.is_active:
        raise HTTPException(
            status_code=409,
            detail="Device is inactive",
        )

    device_connected = await device_manager.send_json(
        device_id,
        {
            "type": "SHUTDOWN_DEVICE",
        },
    )

    if not device_connected:
        raise HTTPException(
            status_code=409,
            detail="Device is not connected to GBOS",
        )

    db.add(
        Activity(
            device_id=device.id,
            employee_id=device.employee_id,
            activity_type="DEVICE_SHUTDOWN_REQUESTED",
            description=(
                f"{current_user.full_name} "
                f"({current_user.email}) requested a shutdown "
                f"for {device.device_name}"
            ),
        )
    )

    db.commit()

    return {
        "success": True,
        "message": f"Shutdown command sent to {device.device_name}",
        "device_id": device.id,
    }
