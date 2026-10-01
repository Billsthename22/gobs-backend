"""Local backend -> actual Rust agent -> dry-run executor. Never uses production."""
import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "local-test-only"
os.environ["POWER_ADMIN_EMAILS"] = "admin@example.test"

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
import uvicorn
from fastapi import FastAPI, WebSocket
from app.core.database import get_db
from app.core.security import get_current_user
from app.routes.remote_monitor import router
from app.services.power_commands import power_commands
from app.websocket.devices import device_manager

app = FastAPI()
app.include_router(router)
device = SimpleNamespace(id=1, is_active=True, employee_id=None)
db = MagicMock()
db.query.return_value.filter.return_value.first.return_value = device
db.query.return_value.filter.return_value.all.return_value = [device]
app.dependency_overrides[get_db] = lambda: db
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email="admin@example.test")


@app.post("/agents/register")
async def register():
    return dict(device_id=1, device_name="Isolated test", hostname="isolated", status="online", websocket_url="/ws/devices/1")


@app.websocket("/ws/devices/1")
async def socket(ws: WebSocket):
    await ws.accept()
    await device_manager.connect(1, ws)
    try:
        while True:
            message = await ws.receive_json()
            if message.get("type") == "command_status":
                assert power_commands.report(1, ws, message)
    except Exception:
        pass
    finally:
        device_manager.disconnect(1, ws)


async def main():
    binary = Path(__file__).resolve().parents[2] / "agent/target/debug/agent"
    if sys.platform == "win32": binary = binary.with_suffix(".exe")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="error"))
    task = asyncio.create_task(server.serve())
    process = None
    try:
        while not server.started:
            if task.done(): await task
            await asyncio.sleep(0.05)
        with tempfile.TemporaryFile() as output:
            process = await asyncio.create_subprocess_exec(str(binary), env={**os.environ,
                "GBOS_API_URL": "http://127.0.0.1:8766", "GBOS_POWER_COMMAND_DRY_RUN": "1"},
                stdout=output, stderr=output)
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8766", timeout=15) as client:
                for _ in range(100):
                    if device_manager.is_connected(1): break
                    await asyncio.sleep(0.1)
                assert device_manager.is_connected(1), "Agent did not connect to local test server"
                for command in ("restart", "shutdown"):
                    power_commands.records.clear()  # Deliberately reset test-only duplicate lock.
                    response = await client.post(f"/remote-monitoring/device/1/{command}")
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert result["status"] == "completed", result
                    assert "dry-run only" in result["detail"], result
                    print(f"PASS: backend -> actual agent -> {command} dry-run -> completed receipt")
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try: await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
