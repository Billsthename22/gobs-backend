from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.activity import Activity
from app.models.device import Device
from app.models.user import User
from app.websocket.devices import device_manager
from app.models.employee import Employee
from app.schemas.device import (
    DeviceCreate,
    DeviceGPSToggle,
    DeviceResponse,
    DeviceUpdate,
)


router = APIRouter(
    prefix="/devices",
    tags=["Devices"],
)


@router.post(
    "",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_device(
    device_data: DeviceCreate,
    db: Session = Depends(get_db),
):
    existing_device = (
        db.query(Device)
        .filter(Device.hostname == device_data.hostname)
        .first()
    )

    if existing_device:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A device with this hostname already exists",
        )

    if device_data.employee_id is not None:
        employee = (
            db.query(Employee)
            .filter(Employee.id == device_data.employee_id)
            .first()
        )

        if not employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee not found",
            )

    device = Device(
        device_name=device_data.device_name,
        hostname=device_data.hostname,
        operating_system=device_data.operating_system,
        ip_address=device_data.ip_address,
        location_name=device_data.location_name,
        location_address=device_data.location_address,
        latitude=device_data.latitude,
        longitude=device_data.longitude,
        location_updated_at=device_data.location_updated_at,
        gps_enabled=False,
        gps_latitude=None,
        gps_longitude=None,
        gps_updated_at=None,
        employee_id=device_data.employee_id,
    )

    db.add(device)
    db.commit()
    db.refresh(device)

    return device


@router.get(
    "",
    response_model=list[DeviceResponse],
)
def get_devices(
    db: Session = Depends(get_db),
):
    return (
        db.query(Device)
        .order_by(Device.id.desc())
        .all()
    )


@router.get(
    "/{device_id}",
    response_model=DeviceResponse,
)
def get_device(
    device_id: int,
    db: Session = Depends(get_db),
):
    device = (
        db.query(Device)
        .filter(Device.id == device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    return device


@router.patch(
    "/{device_id}",
    response_model=DeviceResponse,
)
def update_device(
    device_id: int,
    device_data: DeviceUpdate,
    db: Session = Depends(get_db),
):
    device = (
        db.query(Device)
        .filter(Device.id == device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    update_data = device_data.model_dump(
        exclude_unset=True
    )

    if "hostname" in update_data:
        existing_device = (
            db.query(Device)
            .filter(
                Device.hostname == update_data["hostname"],
                Device.id != device_id,
            )
            .first()
        )

        if existing_device:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A device with this hostname already exists",
            )

    if "employee_id" in update_data:
        employee_id = update_data["employee_id"]

        if employee_id is not None:
            employee = (
                db.query(Employee)
                .filter(Employee.id == employee_id)
                .first()
            )

            if not employee:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Employee not found",
                )

    # GPS coordinates and timestamps are agent-owned.
    # They must never be written through the generic device PATCH endpoint.
    protected_gps_fields = {
        "gps_latitude",
        "gps_longitude",
        "gps_updated_at",
    }

    if protected_gps_fields.intersection(update_data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GPS coordinates can only be updated by the authorized device agent",
        )

    for field, value in update_data.items():
        setattr(device, field, value)

    db.commit()
    db.refresh(device)

    return device


@router.patch(
    "/{device_id}/gps",
)
async def toggle_device_gps(
    device_id: int,
    gps_data: DeviceGPSToggle,
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    if not device.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device is inactive",
        )

    enabled = gps_data.enabled

    device.gps_enabled = enabled

    db.add(
        Activity(
            device_id=device.id,
            employee_id=device.employee_id,
            activity_type=(
                "GPS_COLLECTION_ENABLED"
                if enabled
                else "GPS_COLLECTION_DISABLED"
            ),
            description=(
                f"{current_user.full_name} "
                f"({current_user.email}) "
                f"{'enabled' if enabled else 'disabled'} "
                f"GPS collection for {device.device_name}"
            ),
        )
    )

    device_connected = await device_manager.send_json(
        device_id,
        {
            "type": "GPS_COLLECTION",
            "enabled": enabled,
        },
    )

    db.commit()
    db.refresh(device)

    return {
        "success": True,
        "gps_enabled": device.gps_enabled,
        "device_connected": device_connected,
        "message": (
            f"GPS collection {'enabled' if enabled else 'disabled'} "
            f"for {device.device_name}"
        ),
        "device_id": device.id,
    }


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
):
    device = (
        db.query(Device)
        .filter(Device.id == device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    db.delete(device)
    db.commit()

    return None