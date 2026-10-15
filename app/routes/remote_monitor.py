from app.core.access import require_access, require_admin
from app.core.auditing import AuditedRouter
from typing import Optional

from datetime import datetime, timezone
import logging
import asyncio
import time

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.config import settings
from app.services.power_commands import power_commands
from app.models.activity import Activity
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.remote_session import RemoteMonitoringSession
from app.models.user import User
from app.schemas.remote_monitor import (
    RemoteMonitoringResponse,
    RemoteMonitoringStart,
)
from app.websocket.devices import device_manager


router = AuditedRouter(
    dependencies=[Depends(require_access)],
    prefix="/remote-monitoring",
    tags=["Remote Monitoring"],
)

logger = logging.getLogger(__name__)


def get_power_admin(user: User = Depends(require_admin)) -> User:
    allowed = {email.strip() for email in settings.POWER_ADMIN_EMAILS.split(",") if email.strip()}
    if user.email not in allowed:
        raise HTTPException(status_code=403, detail="Power control requires an authorized administrator")
    return user


async def dispatch_power_command(*, device, command, db, current_user) -> dict:
    audit = AuditLog(actor_email=current_user.email, action=f"power.{command}", resource=f"device:{device.id}", result="attempted")
    db.add(audit)
    if not device.is_active:
        audit.result = "inactive"
        return {"device_id": device.id, "status": "inactive"}
    socket = device_manager.connections.get(device.id)
    if socket is None or not device_manager.is_connected(device.id):
        audit.result = "offline"
        return {"device_id": device.id, "status": "offline"}
    record = power_commands.start(device.id, command, socket)
    if record is None:
        audit.result = "busy"
        return {"device_id": device.id, "status": "busy"}
    audit.command_id = record["command_id"]
    logger.info("[COMMAND] requested device=%s command=%s command_id=%s", device.id, command, record["command_id"])
    try:
        await asyncio.wait_for(socket.send_json({
            "type": f"{command.upper()}_DEVICE",
            "command_id": record["command_id"],
            "expires_at": int(time.time()) + 15,
        }), timeout=3)
        if record["status"] == "pending":
            record["status"] = "sent"
    except Exception:
        record.update(status="failed", detail="Agent connection failed; delivery is unconfirmed")
        device_manager.disconnect(device.id, socket)
    if record["status"] != "failed":
        # Wait for the execution result, or return the last confirmed phase.
        deadline = asyncio.get_running_loop().time() + 8
        while record["status"] not in {"completed", "failed"}:
            record["event"].clear()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(record["event"].wait(), remaining)
            except asyncio.TimeoutError:
                break
        if record["status"] == "sent":
            record["detail"] = "No agent acknowledgement; delivery is unconfirmed. Check agent version and connection."
    db.add(Activity(device_id=device.id, employee_id=device.employee_id,
                    activity_type=f"DEVICE_{command.upper()}_REQUESTED",
                    description=f"{current_user.email} requested {command} (command_id={record['command_id']}); status={record['status']}"))
    logger.info("[COMMAND] device=%s command_id=%s status=%s", device.id, record["command_id"], record["status"])
    audit.result = record["status"]
    return power_commands.public(record)


def individual_result(result):
    status = result["status"]
    if status in {"inactive", "offline", "busy", "failed"}:
        raise HTTPException(status_code=409, detail=result.get("detail") or f"Device is {status}")
    return {**result, "message": f"{result['command'].title()} command: {status}. "
            + (result.get("detail") or "Physical power change is not verified.")}


@router.get("/power/eligible")
async def power_eligible(db: Session = Depends(get_db), current_user: User = Depends(get_power_admin)):
    devices = db.query(Device).filter(Device.is_active == True).all()
    connected = [d.id for d in devices if device_manager.is_connected(d.id)]
    return {"device_ids": connected, "offline_count": len(devices) - len(connected)}


@router.get("/power/commands/{command_id}")
async def power_status(command_id: str, current_user: User = Depends(get_power_admin)):
    power_commands.prune()
    record = power_commands.records.get(command_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Command receipt expired or backend restarted; outcome unknown")
    return power_commands.public(record)


@router.post(
    "/start",
    response_model=RemoteMonitoringResponse,
)
async def start_monitoring(
    data: RemoteMonitoringStart,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_access),
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
    "/{session_id:int}/stop",
    response_model=RemoteMonitoringResponse,
)
async def stop_monitoring(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_access),
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

    from app.websocket.monitoring import monitoring_manager
    await monitoring_manager.close_session(session.id)

    return session


@router.post("/device/{device_id}/restart")
async def restart_device(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_power_admin),
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

    result = await dispatch_power_command(
        device=device, command="restart", db=db, current_user=current_user
    )
    db.commit()
    return individual_result(result)


@router.post("/device/{device_id}/shutdown")
async def shutdown_device(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_power_admin),
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

    result = await dispatch_power_command(
        device=device, command="shutdown", db=db, current_user=current_user
    )
    db.commit()
    return individual_result(result)


async def dispatch_power_command_to_all(
    command: str,
    db: Session,
    current_user: User,
    device_ids: Optional[list[int]] = None,
) -> dict:
    devices = db.query(Device).filter(Device.is_active == True).all()
    if device_ids is not None:
        selected_ids = set(device_ids)
        devices = [device for device in devices if device.id in selected_ids]
    # Sends run concurrently so one unresponsive device cannot block the fleet.
    results = await asyncio.gather(*(dispatch_power_command(
        device=device, command=command, db=db, current_user=current_user
    ) for device in devices))
    db.commit()
    counts = {status: sum(r["status"] == status for r in results) for status in
              ("offline", "busy", "sent", "acknowledged", "executing", "completed", "failed")}
    return {"command": command, "results": results, "counts": counts,
            "message": "; ".join(f"{count} {status}" for status, count in counts.items() if count)
                       or "No eligible devices",
            "note": "Completed means the native request was accepted (or dry-run); physical power change is unverified."}


@router.post("/devices/restart")
async def restart_all_devices(
    device_ids: Optional[list[int]] = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_power_admin),
):
    return await dispatch_power_command_to_all("restart", db, current_user, device_ids)


@router.post("/devices/shutdown")
async def shutdown_all_devices(
    device_ids: Optional[list[int]] = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_power_admin),
):
    return await dispatch_power_command_to_all("shutdown", db, current_user, device_ids)
