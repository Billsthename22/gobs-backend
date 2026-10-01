"""No production database or OS power calls are made by this suite."""
import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-only-key"
os.environ["POWER_ADMIN_EMAILS"] = "admin@example.test"

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.core.security import get_current_user
from app.routes.remote_monitor import router, get_power_admin, dispatch_power_command
from app.services.power_commands import PowerCommands, power_commands
from app.websocket.devices import device_manager, router as ws_router
from app.models.device import Device


class Socket:
    def __init__(self, status="completed"):
        self.status = status
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)
        for status in ("acknowledged", "executing", self.status):
            power_commands.report(1, self, dict(command_id=message["command_id"],
                command=message["type"].split("_")[0].lower(), status=status,
                detail="mock executor"))


class Endpoints(unittest.TestCase):
    def setUp(self):
        power_commands.records.clear()
        device_manager.connections.clear()
        self.device = SimpleNamespace(id=1, is_active=True, employee_id=None)
        self.db = MagicMock()
        self.db.query.return_value.filter.return_value.first.return_value = self.device
        self.db.query.return_value.filter.return_value.all.return_value = [self.device]
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(self.app)

    def login(self, email="admin@example.test"):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email=email)

    def test_unauthorized_and_non_admin(self):
        for path in ("device/1/restart", "device/1/shutdown", "devices/restart", "devices/shutdown"):
            self.assertIn(self.client.post("/remote-monitoring/" + path).status_code, (401, 403))
        self.login("ordinary@example.test")
        self.assertEqual(self.client.post("/remote-monitoring/devices/restart").status_code, 403)
        self.login("ADMIN@example.test")
        self.assertEqual(self.client.post("/remote-monitoring/devices/restart").status_code, 403)

    def test_individual_both_commands_and_receipts(self):
        self.login()
        for command in ("restart", "shutdown"):
            power_commands.records.clear()
            socket = Socket()
            device_manager.connections[1] = socket
            device_manager.last_received[1] = time.monotonic()
            response = self.client.post(f"/remote-monitoring/device/1/{command}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "completed")
            self.assertEqual(socket.messages[0]["type"], command.upper() + "_DEVICE")
            receipt = response.json()["command_id"]
            self.assertEqual(self.client.get(f"/remote-monitoring/power/commands/{receipt}").json()["status"], "completed")
            self.assertEqual(self.client.post(f"/remote-monitoring/device/1/{command}").status_code, 409)

    def test_invalid_missing_offline_and_failure(self):
        self.login()
        self.assertEqual(self.client.post("/remote-monitoring/device/no/restart").status_code, 422)
        self.assertEqual(self.client.post("/remote-monitoring/device/1/restart").status_code, 409)
        self.db.query.return_value.filter.return_value.first.return_value = None
        self.assertEqual(self.client.post("/remote-monitoring/device/999/restart").status_code, 404)
        self.db.query.return_value.filter.return_value.first.return_value = self.device
        device_manager.connections[1] = Socket("failed")
        device_manager.last_received[1] = time.monotonic()
        self.assertEqual(self.client.post("/remote-monitoring/device/1/shutdown").status_code, 409)

    def test_no_acknowledgement_is_not_success_and_stale_is_offline(self):
        self.login()
        class Silent:
            async def send_json(self, message): pass
        device_manager.connections[1] = Silent()
        device_manager.last_received[1] = time.monotonic() - 60
        self.assertEqual(self.client.post("/remote-monitoring/device/1/restart").status_code, 409)
        device_manager.last_received[1] = time.monotonic()
        data = self.client.post("/remote-monitoring/device/1/restart").json()
        self.assertEqual(data["status"], "sent")
        self.assertIn("unconfirmed", data["detail"])
        self.assertNotIn("success", data)

    def test_real_websocket_delivery_and_status(self):
        self.login()
        self.app.include_router(ws_router)
        self.device.device_name = "Test device"
        self.device.gps_enabled = False
        ws_db = MagicMock()
        def query(model):
            result = MagicMock()
            result.filter.return_value = result
            result.order_by.return_value = result
            result.first.return_value = self.device if model is Device else None
            return result
        ws_db.query.side_effect = query
        with patch("app.websocket.devices.SessionLocal", return_value=ws_db):
            with TestClient(self.app) as client:
                for command in ("restart", "shutdown"):
                    power_commands.records.clear()
                    with client.websocket_connect("/ws/devices/1") as socket:
                        self.assertEqual(socket.receive_json()["type"], "connection")
                        self.assertEqual(socket.receive_json()["type"], "GPS_COLLECTION")
                        with ThreadPoolExecutor() as pool:
                            response = pool.submit(client.post, f"/remote-monitoring/device/1/{command}")
                            message = socket.receive_json()
                            self.assertEqual(message["type"], command.upper() + "_DEVICE")
                            self.assertGreaterEqual(message["expires_at"], int(time.time()))
                            for status in ("acknowledged", "executing", "completed"):
                                socket.send_json(dict(type="command_status", command_id=message["command_id"],
                                                      command=command, status=status, detail="mock native executor"))
                            self.assertEqual(response.result(timeout=12).json()["status"], "completed")

    def test_bulk_both_commands_mixed_online(self):
        self.login()
        self.db.query.return_value.filter.return_value.all.return_value = [self.device, SimpleNamespace(id=2, is_active=True)]
        for command in ("restart", "shutdown"):
            power_commands.records.clear()
            device_manager.connections[1] = Socket()
            device_manager.last_received[1] = time.monotonic()
            data = self.client.post(f"/remote-monitoring/devices/{command}").json()
            self.assertEqual(data["counts"]["completed"], 1)
            self.assertEqual(data["counts"]["offline"], 1)
        self.assertEqual(self.client.get("/remote-monitoring/power/eligible").json()["device_ids"], [1])


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_correlation_duplicates_and_reconnection(self):
        tracker = PowerCommands()
        socket = object()
        record = tracker.start(1, "restart", socket)
        self.assertIsNone(tracker.start(1, "shutdown", socket))
        message = dict(command_id=record["command_id"], command="restart", status="completed")
        self.assertFalse(tracker.report(2, socket, message))
        self.assertFalse(tracker.report(1, object(), message))
        self.assertTrue(tracker.report(1, socket, message))
        self.assertFalse(tracker.report(1, socket, {**message, "status": "executing"}))
        self.assertIsNotNone(tracker.start(1, "restart", object()))
        with self.assertRaises(ValueError): tracker.start(1, "shell", socket)

    async def test_failed_old_socket_does_not_remove_replacement(self):
        class Old:
            async def send_json(self, message):
                device_manager.connections[99] = new
                raise RuntimeError("disconnected")
        new = object()
        device_manager.connections[99] = Old()
        self.assertFalse(await device_manager.send_json(99, {}))
        self.assertIs(device_manager.connections[99], new)


if __name__ == "__main__":
    unittest.main()
