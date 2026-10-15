from app.core.access import require_access
from app.core.auditing import AuditedRouter
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.activity import Activity
from app.models.alert import Alert
from app.models.device import Device
from app.models.employee import Employee
from app.models.telemetry import DeviceTelemetry
from app.services.device_status import ONLINE, get_device_status


router = AuditedRouter(
    dependencies=[Depends(require_access)],
    prefix="/analytics",
    tags=["Analytics"],
)


def day_key(value: datetime) -> str:
    return value.strftime("%a")


@router.get("/overview")
def get_analytics_overview(
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    today_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    # ---------------------------------------------------------
    # Activity
    # ---------------------------------------------------------
    activities = (
        db.query(Activity)
        .filter(Activity.timestamp >= seven_days_ago)
        .order_by(Activity.timestamp.asc())
        .all()
    )

    total_activity = len(activities)

    today_activity = sum(
        1
        for activity in activities
        if activity.timestamp >= today_start
    )

    # ---------------------------------------------------------
    # Employee activity
    # ---------------------------------------------------------
    employees = db.query(Employee).all()

    employee_lookup = {
        employee.id: employee.full_name
        for employee in employees
    }

    employee_counts = defaultdict(int)

    for activity in activities:
        if (
            activity.employee_id is not None
            and activity.activity_type == "APPLICATION_ACTIVE"
        ):
            employee_counts[activity.employee_id] += 1

    employee_activity = []

    for employee_id, name in employee_lookup.items():
        employee_activity.append(
            {
                "id": employee_id,
                "name": name,
                "activity_events": employee_counts.get(
                    employee_id,
                    0,
                ),
            }
        )

    employee_activity.sort(
        key=lambda item: item["activity_events"],
        reverse=True,
    )

    # ---------------------------------------------------------
    # Application usage
    # ---------------------------------------------------------
    application_counts = defaultdict(int)

    for activity in activities:
        if activity.activity_type != "APPLICATION_ACTIVE":
            continue

        description = activity.description or ""

        marker = " became active on "
        if marker in description:
            application = description.split(
                marker,
                1,
            )[0].strip()
        else:
            application = description.strip()

        if application:
            application_counts[application] += 1

    total_application_events = sum(
        application_counts.values()
    )

    application_usage = []

    for name, count in sorted(
        application_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        percentage = (
            round(
                (count / total_application_events) * 100,
                1,
            )
            if total_application_events
            else 0
        )

        application_usage.append(
            {
                "name": name,
                "events": count,
                "percentage": percentage,
            }
        )

    # Keep the chart readable while preserving all smaller apps.
    if len(application_usage) > 5:
        top_apps = application_usage[:4]
        other_events = sum(
            item["events"]
            for item in application_usage[4:]
        )

        other_percentage = (
            round(
                (other_events / total_application_events) * 100,
                1,
            )
            if total_application_events
            else 0
        )

        application_usage = [
            *top_apps,
            {
                "name": "Other",
                "events": other_events,
                "percentage": other_percentage,
            },
        ]

    # ---------------------------------------------------------
    # Telemetry performance
    # ---------------------------------------------------------
    telemetry = (
        db.query(DeviceTelemetry)
        .filter(
            DeviceTelemetry.recorded_at >= seven_days_ago
        )
        .all()
    )

    if telemetry:
        average_cpu = round(
            sum(item.cpu_usage for item in telemetry)
            / len(telemetry),
            1,
        )

        average_memory = round(
            sum(item.memory_usage for item in telemetry)
            / len(telemetry),
            1,
        )

        average_storage = round(
            sum(item.storage_usage for item in telemetry)
            / len(telemetry),
            1,
        )

        average_network = round(
            sum(item.network_speed_mbps for item in telemetry)
            / len(telemetry),
            1,
        )
    else:
        average_cpu = 0
        average_memory = 0
        average_storage = 0
        average_network = 0

    # ---------------------------------------------------------
    # Device summary
    # ---------------------------------------------------------
    total_devices = db.query(Device).count()

    # Counted from `last_seen` so analytics agrees with the dashboard. The
    # stored `status` column is never authoritative — see
    # app.services.device_status.
    online_devices = sum(
        1
        for device in db.query(Device).all()
        if get_device_status(device.last_seen) == ONLINE
    )

    # ---------------------------------------------------------
    # Daily uptime from actual device connection intervals
    # ---------------------------------------------------------
    connection_events = (
        db.query(Activity)
        .filter(
            Activity.activity_type.in_(
                [
                    "DEVICE_CONNECTED",
                    "DEVICE_DISCONNECTED",
                ]
            ),
            Activity.timestamp >= seven_days_ago - timedelta(days=1),
            Activity.timestamp <= now,
        )
        .order_by(Activity.timestamp.asc())
        .all()
    )

    # Build connection intervals using device connection state.
    # Repeated CONNECTED events while already connected are ignored.
    # Repeated DISCONNECTED events while already disconnected are ignored.
    connection_intervals = []
    connected_since = {}

    # Reconstruct the connection state at the start of the analytics window.
    # This handles devices that connected before the 7-day window began.
    prior_events = (
        db.query(Activity)
        .filter(
            Activity.activity_type.in_(
                [
                    "DEVICE_CONNECTED",
                    "DEVICE_DISCONNECTED",
                ]
            ),
            Activity.timestamp < seven_days_ago,
        )
        .order_by(Activity.device_id.asc(), Activity.timestamp.desc())
        .all()
    )

    prior_state = {}

    for event in prior_events:
        if event.device_id not in prior_state:
            prior_state[event.device_id] = event.activity_type

    for device_id, activity_type in prior_state.items():
        if activity_type == "DEVICE_CONNECTED":
            connected_since[device_id] = seven_days_ago

    for event in connection_events:
        device_id = event.device_id

        if event.activity_type == "DEVICE_CONNECTED":
            if device_id not in connected_since:
                connected_since[device_id] = event.timestamp

        elif event.activity_type == "DEVICE_DISCONNECTED":
            connected_at = connected_since.pop(device_id, None)

            if connected_at is not None:
                connection_intervals.append(
                    (
                        device_id,
                        connected_at,
                        event.timestamp,
                    )
                )

    # Any device still connected remains online until "now".
    for device_id, connected_at in connected_since.items():
        connection_intervals.append(
            (
                device_id,
                connected_at,
                now,
            )
        )

    uptime_data = []

    for offset in range(6, -1, -1):
        day_start = (
            now - timedelta(days=offset)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        day_end = min(
            day_start + timedelta(days=1),
            now,
        )

        elapsed_seconds = (
            day_end - day_start
        ).total_seconds()

        total_connected_seconds = 0.0

        for device_id, connected_at, disconnected_at in connection_intervals:
            interval_start = max(
                connected_at,
                day_start,
            )

            interval_end = min(
                disconnected_at,
                day_end,
            )

            if interval_end > interval_start:
                total_connected_seconds += (
                    interval_end - interval_start
                ).total_seconds()

        uptime = round(
            min(
                total_connected_seconds
                / (
                    elapsed_seconds
                    * max(total_devices, 1)
                )
                * 100,
                100,
            ),
            2,
        )

        uptime_data.append(
            {
                "day": day_start.strftime("%a"),
                "date": day_start.date().isoformat(),
                "uptime": uptime,
            }
        )

    # ---------------------------------------------------------
    # Alerts / issues
    # ---------------------------------------------------------
    issues_detected = (
        db.query(Alert)
        .filter(Alert.created_at >= seven_days_ago)
        .count()
    )

    active_alerts = (
        db.query(Alert)
        .filter(Alert.is_resolved == False)
        .count()
    )

    # ---------------------------------------------------------
    # Device summary
    # ---------------------------------------------------------
    total_devices = db.query(Device).count()

    # Counted from `last_seen` so analytics agrees with the dashboard. The
    # stored `status` column is never authoritative — see
    # app.services.device_status.
    online_devices = sum(
        1
        for device in db.query(Device).all()
        if get_device_status(device.last_seen) == ONLINE
    )

    return {
        "period": {
            "start": seven_days_ago,
            "end": now,
        },
        "stats": {
            "total_activity": total_activity,
            "today_activity": today_activity,
            "issues_detected": issues_detected,
            "active_alerts": active_alerts,
            "total_devices": total_devices,
            "online_devices": online_devices,
            "average_cpu": average_cpu,
            "average_memory": average_memory,
            "average_storage": average_storage,
            "average_network_mbps": average_network,
        },
        "uptime": uptime_data,
        "employee_activity": employee_activity,
        "application_usage": application_usage,
    }
