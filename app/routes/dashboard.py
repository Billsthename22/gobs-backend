from app.core.access import require_access
from app.core.auditing import AuditedRouter
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.device import Device
from app.models.employee import Employee
from app.models.activity import Activity
from app.models.alert import Alert
from app.models.telemetry import DeviceTelemetry
from app.services.device_status import get_device_status


router = AuditedRouter(
    dependencies=[Depends(require_access)],
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get("/overview")
def get_dashboard_overview(
    db: Session = Depends(get_db),
):
    total_devices = db.query(Device).count()

    online_devices = 0
    stale_devices = 0
    offline_devices = 0

    devices = db.query(Device).all()

    for device in devices:
        current_status = get_device_status(device.last_seen)

        if current_status == "online":
            online_devices += 1
        elif current_status == "stale":
            stale_devices += 1
        else:
            offline_devices += 1

    total_employees = db.query(Employee).count()

    active_employees = (
        db.query(Employee)
        .filter(Employee.is_active == True)
        .count()
    )

    active_alerts = (
        db.query(Alert)
        .filter(Alert.is_resolved == False)
        .count()
    )

    resolved_alerts = (
        db.query(Alert)
        .filter(Alert.is_resolved == True)
        .count()
    )

    return {
        "total_devices": total_devices,
        "online_devices": online_devices,
        "stale_devices": stale_devices,
        "offline_devices": offline_devices,
        "total_employees": total_employees,
        "active_employees": active_employees,
        "active_alerts": active_alerts,
        "resolved_alerts": resolved_alerts,
    }


@router.get("/recent-activity")
def get_recent_activity(
    db: Session = Depends(get_db),
):
    activities = (
        db.query(Activity)
        .order_by(Activity.timestamp.desc())
        .limit(10)
        .all()
    )

    return [
        {
            "id": activity.id,
            "device_id": activity.device_id,
            "employee_id": activity.employee_id,
            "activity_type": activity.activity_type,
            "description": activity.description,
            "timestamp": activity.timestamp,
        }
        for activity in activities
    ]


@router.get("/device-health")
def get_device_health(
    db: Session = Depends(get_db),
):
    devices = (
        db.query(Device)
        .order_by(Device.id.desc())
        .all()
    )

    results = []

    for device in devices:
        latest_telemetry = (
            db.query(DeviceTelemetry)
            .filter(DeviceTelemetry.device_id == device.id)
            .order_by(DeviceTelemetry.recorded_at.desc())
            .first()
        )

        results.append({
            "id": device.id,
            "device_name": device.device_name,
            "hostname": device.hostname,
            "status": get_device_status(device.last_seen),

            "cpu_usage": (
                latest_telemetry.cpu_usage
                if latest_telemetry
                else device.cpu_usage
            ),

            "memory_usage": (
                latest_telemetry.memory_usage
                if latest_telemetry
                else device.memory_usage
            ),

            "storage_usage": (
                latest_telemetry.storage_usage
                if latest_telemetry
                else device.storage_usage
            ),

            "network_speed_mbps": (
                latest_telemetry.network_speed_mbps
                if latest_telemetry
                else 0
            ),

            "last_seen": device.last_seen,

            "telemetry_recorded_at": (
                latest_telemetry.recorded_at
                if latest_telemetry
                else None
            ),
        })

    return results