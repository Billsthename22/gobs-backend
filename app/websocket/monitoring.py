from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.remote_session import RemoteMonitoringSession


router = APIRouter()


class MonitoringConnectionManager:
    def __init__(self):
        self.connections: Dict[int, Set[WebSocket]] = {}

    async def connect(
        self,
        session_id: int,
        websocket: WebSocket,
    ):
        await websocket.accept()

        if session_id not in self.connections:
            self.connections[session_id] = set()

        self.connections[session_id].add(websocket)

    def disconnect(
        self,
        session_id: int,
        websocket: WebSocket,
    ):
        if session_id not in self.connections:
            return

        self.connections[session_id].discard(websocket)

        if not self.connections[session_id]:
            del self.connections[session_id]

    async def broadcast(
        self,
        session_id: int,
        message: dict,
    ):
        connections = (
            self.connections
            .get(session_id, set())
            .copy()
        )

        for websocket in connections:
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(
                    session_id,
                    websocket,
                )

    async def broadcast_bytes(
        self,
        session_id: int,
        data: bytes,
    ):
        connections = (
            self.connections
            .get(session_id, set())
            .copy()
        )

        for websocket in connections:
            try:
                await websocket.send_bytes(data)
            except Exception:
                self.disconnect(
                    session_id,
                    websocket,
                )


monitoring_manager = MonitoringConnectionManager()


@router.websocket("/ws/monitoring/{session_id}")
async def monitoring_websocket(
    websocket: WebSocket,
    session_id: int,
):
    db: Session = SessionLocal()

    session = (
        db.query(RemoteMonitoringSession)
        .filter(
            RemoteMonitoringSession.id == session_id
        )
        .first()
    )

    if not session:
        await websocket.close(code=1008)
        db.close()
        return

    if session.status != "active":
        await websocket.close(code=1008)
        db.close()
        return

    await monitoring_manager.connect(
        session_id,
        websocket,
    )

    await websocket.send_json(
        {
            "type": "monitoring_connection",
            "status": "connected",
            "session_id": session_id,
            "device_id": session.device_id,
            "is_authorized": session.is_authorized,
        }
    )

    try:
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        monitoring_manager.disconnect(
            session_id,
            websocket,
        )

    finally:
        db.close()