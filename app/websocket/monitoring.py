from typing import Dict, Set
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.remote_session import RemoteMonitoringSession
from app.models.user import User
from app.core.security import decode_access_token
from app.core.access import workspace_role


router = APIRouter()


class MonitoringConnectionManager:
    def __init__(self):
        self.connections: Dict[int, Set[WebSocket]] = {}

    async def close_session(self, session_id):
        for websocket in self.connections.pop(session_id, set()).copy():
            try:
                await websocket.close(code=1008)
            except RuntimeError:
                pass

    async def close_all(self):
        for session_id in list(self.connections):
            await self.close_session(session_id)

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
    # Browsers send credentials in the first frame, never in a URL or log.
    await websocket.accept()
    try:
        auth = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
        token = auth.get("token") if isinstance(auth, dict) else None
        user_id = decode_access_token(token) if isinstance(token, str) else None
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await websocket.close(code=1008)
        return
    db: Session = SessionLocal()

    def authorized():
        db.expire_all()
        user = db.get(User, user_id) if user_id else None
        return bool(user and user.is_active and decode_access_token(token) == user.id
                    and workspace_role(user.email, db) in {"admin", "operator"})

    if not authorized():
        await websocket.close(code=1008)
        db.close()
        return

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

    monitoring_manager.connections.setdefault(session_id, set()).add(websocket)

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
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=15)
            except asyncio.TimeoutError:
                pass
            db.expire_all()
            current_session = db.get(RemoteMonitoringSession, session_id)
            if not authorized() or not current_session or current_session.status != "active":
                await websocket.close(code=1008)
                break

    except WebSocketDisconnect:
        monitoring_manager.disconnect(
            session_id,
            websocket,
        )

    finally:
        monitoring_manager.disconnect(session_id, websocket)
        db.close()
