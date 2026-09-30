
from datetime import datetime, timezone
import json
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.activity import Activity
from app.models.alert import Alert
from app.models.device import Device
from app.models.telemetry import DeviceTelemetry
from app.models.remote_session import RemoteMonitoringSession
from app.websocket.monitoring import monitoring_manager


router = APIRouter()


class DeviceConnectionManager:
    def __init__(self):
        self.connections: Dict[int, WebSocket] = {}

    async def connect(self, device_id: int, websocket: WebSocket):
        self.connections[device_id] = websocket

    def disconnect(self, device_id: int, websocket: WebSocket) -> bool:
        """Deregister `websocket`, reporting whether it was the live one.

        Returns True only when this websocket was still the device's registered
        connection. A reconnecting agent replaces the entry before the previous
        handler unwinds, so the late handler gets False and must not act on the
        device — otherwise its cleanup would mark a live device offline.
        """
        current = self.connections.get(device_id)

        if current is websocket:
            self.connections.pop(device_id, None)
            return True

        return False

    def is_connected(self, device_id: int) -> bool:
        return device_id in self.connections

    async def send_json(self, device_id: int, message: dict) -> bool:
        websocket = self.connections.get(device_id)

        if websocket is None:
            return False

        try:
            await websocket.send_json(message)
            return True
        except Exception:
            self.connections.pop(device_id, None)
            return False


device_manager = DeviceConnectionManager()


CPU_ALERT_THRESHOLD = 90.0
MEMORY_ALERT_THRESHOLD = 90.0
STORAGE_ALERT_THRESHOLD = 90.0


def evaluate_metric_alert(
    db: Session,
    device: Device,
    alert_type: str,
    severity: str,
    title: str,
    description: str,
    condition_active: bool,
):
    active_alert = (
        db.query(Alert)
        .filter(
            Alert.device_id == device.id,
            Alert.alert_type == alert_type,
            Alert.is_resolved == False,
        )
        .first()
    )

    if condition_active and active_alert is None:
        db.add(
            Alert(
                device_id=device.id,
                employee_id=device.employee_id,
                alert_type=alert_type,
                severity=severity,
                title=title,
                description=description,
            )
        )

    elif not condition_active and active_alert is not None:
        active_alert.is_resolved = True
        active_alert.resolved_at = datetime.now(timezone.utc)




@router.websocket("/ws/devices/{device_id}")
async def device_websocket(
    websocket: WebSocket,
    device_id: int,
):
    db: Session = SessionLocal()

    device = (
        db.query(Device)
        .filter(Device.id == device_id)
        .first()
    )

    if not device:
        await websocket.close(code=1008)
        db.close()
        return

    await websocket.accept()

    await device_manager.connect(
        device_id,
        websocket,
    )

    # Only `last_seen` is recorded; status is derived from it when read, so
    # there is no stored status to keep in sync here.
    device.last_seen = datetime.now(timezone.utc)

    active_offline_alert = (
        db.query(Alert)
        .filter(
            Alert.device_id == device_id,
            Alert.alert_type == "DEVICE_OFFLINE",
            Alert.is_resolved == False,
        )
        .first()
    )

    if active_offline_alert is not None:
        active_offline_alert.is_resolved = True
        active_offline_alert.resolved_at = datetime.now(timezone.utc)

    db.commit()

    db.add(
        Activity(
            device_id=device_id,
            employee_id=device.employee_id,
            activity_type="DEVICE_CONNECTED",
            description=f"{device.device_name} connected to GBOS",
        )
    )

    db.commit()

    await websocket.send_json(
        {
            "type": "connection",
            "status": "connected",
            "device_id": device_id,
        }
    )

    # Synchronize the current GPS collection permission with a reconnecting
    # agent. The agent starts with GPS disabled and must receive the backend's
    # current authorization state before it can collect or send coordinates.
    await websocket.send_json(
        {
            "type": "GPS_COLLECTION",
            "enabled": bool(device.gps_enabled),
        }
    )

    # An agent can reconnect while an authorized monitoring session is still
    # active (for example after the agent is rebuilt or restarted). The start
    # route sent its command to the old websocket, so resume the stream here
    # rather than leaving connected viewers waiting forever for video.
    active_monitoring_session = (
        db.query(RemoteMonitoringSession)
        .filter(
            RemoteMonitoringSession.device_id == device_id,
            RemoteMonitoringSession.status == "active",
            RemoteMonitoringSession.is_authorized == True,
        )
        .order_by(RemoteMonitoringSession.id.desc())
        .first()
    )

    if active_monitoring_session:
        await websocket.send_json(
            {
                "type": "START_SCREEN_STREAM",
                "session_id": active_monitoring_session.id,
            }
        )

    try:
        while True:
            message = await websocket.receive()

            # Client disconnected (abrupt close, close frame, or an idle
            # websocket ping timeout). The raw `receive()` helper surfaces
            # this as a plain message dict instead of raising — without this
            # branch the loop would spin forever and the device would never be
            # marked offline. Raise so the WebSocketDisconnect cleanup below
            # runs.
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()

            # ---------------------------------------------------------
            # BINARY SCREEN FRAME
            # ---------------------------------------------------------
            if message.get("bytes") is not None:
                screen_frame = message["bytes"]

                active_sessions = (
                    db.query(RemoteMonitoringSession)
                    .filter(
                        RemoteMonitoringSession.device_id == device_id,
                        RemoteMonitoringSession.status == "active",
                        RemoteMonitoringSession.is_authorized == True,
                    )
                    .all()
                )

                print(
                    f"Received screen frame from device {device_id}: "
                    f"{len(screen_frame)} bytes"
                )

                print(
                    f"Active authorized monitoring sessions: "
                    f"{len(active_sessions)}"
                )

                for session in active_sessions:
                    print(
                        f"Forwarding screen frame → "
                        f"monitoring session {session.id}"
                    )

                    await monitoring_manager.broadcast_bytes(
                        session.id,
                        screen_frame,
                    )

                continue

            # ---------------------------------------------------------
            # TEXT / JSON MESSAGE
            # ---------------------------------------------------------
            text_message = message.get("text")

            if text_message is None:
                continue

            try:
                message = json.loads(text_message)
            except json.JSONDecodeError:
                continue

            message_type = message.get("type", "telemetry")

            # ---------------------------------------------------------
            # HEARTBEAT
            # ---------------------------------------------------------
            if message_type == "heartbeat":
                device.last_seen = datetime.now(timezone.utc)

                db.commit()

                await websocket.send_json(
                    {
                        "type": "heartbeat_ack",
                        "device_id": device_id,
                        "last_seen": device.last_seen.isoformat(),
                    }
                )

                continue

            # ---------------------------------------------------------
            # USER SESSION EVENT
            # ---------------------------------------------------------
            if message_type == "session":
                session_event = str(
                    message.get("event", "")
                ).lower()

                username = str(
                    message.get("username", "Unknown user")
                )

                last_session_activity = (
                    db.query(Activity)
                    .filter(
                        Activity.device_id == device_id,
                        Activity.activity_type.in_(
                            ["LOGIN", "LOGOUT"]
                        ),
                    )
                    .order_by(Activity.timestamp.desc())
                    .first()
                )

                if session_event == "login":
                    expected_description = (
                        f"{username} logged into "
                        f"{device.device_name}"
                    )

                    if (
                        last_session_activity
                        and last_session_activity.activity_type
                        == "LOGIN"
                        and last_session_activity.description
                        == expected_description
                    ):
                        device.last_seen = datetime.now(
                            timezone.utc
                        )

                        db.commit()

                        await websocket.send_json(
                            {
                                "type": "session_ack",
                                "device_id": device_id,
                                "ignored": True,
                                "reason": "duplicate_login",
                            }
                        )

                        continue

                    activity_type = "LOGIN"
                    description = expected_description

                elif session_event == "logout":
                    activity_type = "LOGOUT"
                    description = (
                        f"{username} logged out of "
                        f"{device.device_name}"
                    )

                else:
                    await websocket.send_json(
                        {
                            "type": "session_error",
                            "device_id": device_id,
                            "error": "Unknown session event",
                        }
                    )

                    continue

                activity = Activity(
                    device_id=device_id,
                    employee_id=device.employee_id,
                    activity_type=activity_type,
                    description=description,
                )

                db.add(activity)

                device.last_seen = datetime.now(timezone.utc)

                db.commit()
                db.refresh(activity)

                await websocket.send_json(
                    {
                        "type": "session_ack",
                        "device_id": device_id,
                        "activity": {
                            "id": activity.id,
                            "activity_type": activity.activity_type,
                            "description": activity.description,
                            "timestamp": activity.timestamp.isoformat(),
                        },
                    }
                )

                continue

            # ---------------------------------------------------------
            # APPLICATION ACTIVITY
            # ---------------------------------------------------------
            if message_type == "application_activity":
                application = str(
                    message.get("application", "")
                ).strip()

                if not application:
                    continue

                last_application_activity = (
                    db.query(Activity)
                    .filter(
                        Activity.device_id == device_id,
                        Activity.activity_type == "APPLICATION_ACTIVE",
                    )
                    .order_by(Activity.timestamp.desc())
                    .first()
                )

                description = (
                    f"{application} became active on "
                    f"{device.device_name}"
                )

                if (
                    last_application_activity
                    and last_application_activity.description
                    == description
                ):
                    device.last_seen = datetime.now(timezone.utc)

                    db.commit()

                    await websocket.send_json(
                        {
                            "type": "application_activity_ack",
                            "device_id": device_id,
                            "ignored": True,
                            "reason": "duplicate_application",
                        }
                    )

                    continue

                activity = Activity(
                    device_id=device_id,
                    employee_id=device.employee_id,
                    activity_type="APPLICATION_ACTIVE",
                    description=description,
                )

                db.add(activity)

                device.last_seen = datetime.now(timezone.utc)

                db.commit()
                db.refresh(activity)

                await websocket.send_json(
                    {
                        "type": "application_activity_ack",
                        "device_id": device_id,
                        "activity": {
                            "id": activity.id,
                            "activity_type": activity.activity_type,
                            "description": activity.description,
                            "timestamp": activity.timestamp.isoformat(),
                        },
                    }
                )

                continue

            # ---------------------------------------------------------
            # GPS LOCATION
            # ---------------------------------------------------------
            if message_type == "gps_location":
                # Coordinates are only accepted while collection is enabled
                # for this device — the toggle is the authorization signal.
                if not device.gps_enabled:
                    continue

                try:
                    latitude = float(message.get("latitude"))
                    longitude = float(message.get("longitude"))
                except (TypeError, ValueError):
                    await websocket.send_json(
                        {
                            "type": "location_ack",
                            "device_id": device_id,
                            "error": (
                                "latitude and longitude must be numbers"
                            ),
                        }
                    )

                    continue

                if not (-90.0 <= latitude <= 90.0) or not (
                    -180.0 <= longitude <= 180.0
                ):
                    await websocket.send_json(
                        {
                            "type": "location_ack",
                            "device_id": device_id,
                            "error": (
                                "latitude or longitude out of range"
                            ),
                        }
                    )

                    continue

                device.gps_latitude = latitude
                device.gps_longitude = longitude
                device.gps_updated_at = datetime.now(timezone.utc)
                device.last_seen = datetime.now(timezone.utc)

                db.commit()

                await websocket.send_json(
                    {
                        "type": "location_ack",
                        "device_id": device_id,
                        "data": {
                            "latitude": device.gps_latitude,
                            "longitude": device.gps_longitude,
                            "updated_at": (
                                device.gps_updated_at.isoformat()
                            ),
                        },
                    }
                )

                continue

            # ---------------------------------------------------------
            # TELEMETRY
            # ---------------------------------------------------------
            cpu_usage = float(
                message.get("cpu_usage", 0)
            )

            memory_usage = float(
                message.get("memory_usage", 0)
            )

            storage_usage = float(
                message.get("storage_usage", 0)
            )

            network_speed_mbps = float(
                message.get("network_speed_mbps", 0)
            )

            telemetry = DeviceTelemetry(
                device_id=device_id,
                cpu_usage=cpu_usage,
                memory_usage=memory_usage,
                storage_usage=storage_usage,
                network_speed_mbps=network_speed_mbps,
            )

            device.cpu_usage = cpu_usage
            device.memory_usage = memory_usage
            device.storage_usage = storage_usage
            device.last_seen = datetime.now(timezone.utc)

            evaluate_metric_alert(
                db=db,
                device=device,
                alert_type="HIGH_CPU",
                severity="critical",
                title="High CPU usage",
                description=f"CPU usage reached {cpu_usage:.1f}%.",
                condition_active=cpu_usage >= CPU_ALERT_THRESHOLD,
            )

            evaluate_metric_alert(
                db=db,
                device=device,
                alert_type="HIGH_MEMORY",
                severity="warning",
                title="High memory usage",
                description=f"Memory usage reached {memory_usage:.1f}%.",
                condition_active=memory_usage >= MEMORY_ALERT_THRESHOLD,
            )

            evaluate_metric_alert(
                db=db,
                device=device,
                alert_type="LOW_STORAGE",
                severity="warning",
                title="Low storage space",
                description=f"Storage usage reached {storage_usage:.1f}%.",
                condition_active=storage_usage >= STORAGE_ALERT_THRESHOLD,
            )

            db.add(telemetry)
            db.commit()

            await websocket.send_json(
                {
                    "type": "telemetry_ack",
                    "device_id": device_id,
                    "data": {
                        "cpu_usage": cpu_usage,
                        "memory_usage": memory_usage,
                        "storage_usage": storage_usage,
                        "network_speed_mbps": network_speed_mbps,
                    },
                }
            )

            # ---------------------------------------------------------
            # REMOTE MONITORING TELEMETRY BROADCAST
            # ---------------------------------------------------------
            active_sessions = (
                db.query(RemoteMonitoringSession)
                .filter(
                    RemoteMonitoringSession.device_id == device_id,
                    RemoteMonitoringSession.status == "active",
                    RemoteMonitoringSession.is_authorized == True,
                )
                .all()
            )

            for session in active_sessions:
                await monitoring_manager.broadcast(
                    session.id,
                    {
                        "type": "monitoring_data",
                        "session_id": session.id,
                        "device_id": device_id,
                        "data": {
                            "cpu_usage": cpu_usage,
                            "memory_usage": memory_usage,
                            "storage_usage": storage_usage,
                            "network_speed_mbps": network_speed_mbps,
                        },
                    },
                )

    except WebSocketDisconnect:
        # Deregister first: the result tells us whether this socket was still
        # the device's live connection or had already been superseded by a
        # reconnect. A superseded socket must not be treated as the device
        # going offline — the agent is still connected on the newer socket.
        was_current = device_manager.disconnect(device_id, websocket)

        if was_current:
            # `last_seen` is deliberately left alone. It records when the
            # backend last heard from the device, so back-dating it to "now"
            # here would make a device that just went away look freshly seen
            # and skew the derived status. The heartbeat that stopped arriving
            # is what ages the device out.
            db.add(
                Activity(
                    device_id=device_id,
                    employee_id=device.employee_id,
                    activity_type="DEVICE_DISCONNECTED",
                    description=(
                        f"{device.device_name} disconnected from GBOS"
                    ),
                )
            )

            active_offline_alert = (
                db.query(Alert)
                .filter(
                    Alert.device_id == device_id,
                    Alert.alert_type == "DEVICE_OFFLINE",
                    Alert.is_resolved == False,
                )
                .first()
            )

            if active_offline_alert is None:
                db.add(
                    Alert(
                        device_id=device_id,
                        employee_id=device.employee_id,
                        alert_type="DEVICE_OFFLINE",
                        severity="critical",
                        title="Device went offline",
                        description=(
                            f"{device.device_name} disconnected from GBOS."
                        ),
                    )
                )

            db.commit()

    finally:
        # Idempotent: returns False if the `except` block above already
        # deregistered this socket.
        device_manager.disconnect(
            device_id,
            websocket,
        )
        db.close()
