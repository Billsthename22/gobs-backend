import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.main import app
from app.core.security import create_access_token, hash_password
from app.core.access import token_hash
from app.models.user import User
from app.models.device import Device
from app.models.access import AccessGrant, DeviceCredential
from app.models.audit import AuditLog
from app.models.remote_session import RemoteMonitoringSession


@pytest.fixture
def workspace(isolated_database):
    with isolated_database() as db:
        users = [User(email=email, full_name=role, hashed_password=hash_password("long-test-password"))
                 for email, role in [("admin@example.org", "admin"), ("viewer@example.org", "viewer"), ("operator@example.org", "operator"), ("outsider@example.org", "outsider")]]
        db.add_all(users)
        db.add_all([AccessGrant(email="viewer@example.org", role="viewer"), AccessGrant(email="operator@example.org", role="operator")])
        db.add(Device(id=1, hostname="workstation", device_name="Workstation", operating_system="Windows"))
        db.add(DeviceCredential(device_id=1, token_hash=token_hash("device-secret")))
        db.add(RemoteMonitoringSession(id=1, device_id=1, status="active", is_authorized=True))
        db.commit()
        headers = {user.full_name: {"Authorization": f"Bearer {create_access_token(user.id)}"} for user in users}
    return TestClient(app), headers, isolated_database


def test_reads_and_mutations_require_workspace_role(workspace):
    client, headers, _ = workspace
    for path in ["/devices", "/employees", "/alerts", "/activities", "/dashboard/overview", "/analytics/overview"]:
        assert client.get(path).status_code in (401, 403)
        assert client.get(path, headers=headers["outsider"]).status_code == 403
        assert client.get(path, headers=headers["viewer"]).status_code == 200
    assert client.patch("/devices/1", json={"device_name": "Changed"}, headers=headers["viewer"]).status_code == 403
    assert client.patch("/devices/1", json={"device_name": "Changed"}, headers=headers["operator"]).status_code == 200
    assert client.get("/access/audit-logs", headers=headers["viewer"]).status_code == 403
    assert client.post("/remote-monitoring/start", json={"device_id": 1}, headers=headers["viewer"]).status_code == 403
    assert client.post("/remote-monitoring/device/1/restart", headers=headers["operator"]).status_code == 403


def test_invitation_registration_and_revocation(workspace):
    client, headers, sessions = workspace
    payload = {"email": "new@example.org", "full_name": "New User", "password": "long-test-password"}
    assert client.post("/auth/register", json=payload).status_code == 403
    response = client.post("/access/invitations", json={"email": payload["email"], "role": "viewer"}, headers=headers["admin"])
    assert response.status_code == 200
    token = response.json()["invitation_token"]
    assert client.post("/auth/register", json={**payload, "invitation_token": token}).status_code == 201
    assert client.post("/auth/register", json={**payload, "invitation_token": token}).status_code == 403
    login = client.post("/auth/login", json={"email": payload["email"], "password": payload["password"]})
    new_headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get("/devices", headers=new_headers).status_code == 200
    assert client.delete("/access/members/new@example.org", headers=headers["admin"]).status_code == 204
    assert client.get("/devices", headers=new_headers).status_code == 403
    assert client.delete("/access/members/admin@example.org", headers=headers["admin"]).status_code == 409
    with sessions() as db:
        audit_text = str([(x.action, x.resource, x.actor_email, x.result) for x in db.query(AuditLog).all()])
        assert token not in audit_text
        assert payload["password"] not in audit_text


def test_agent_credentials_are_device_bound_and_rotatable(workspace):
    client, headers, _ = workspace
    payload = {"hostname": "workstation", "device_name": "Test", "operating_system": "Windows"}
    assert client.post("/agents/register", json=payload).status_code in (401, 403)
    assert client.post("/agents/register", json=payload, headers={"Authorization": "Bearer wrong"}).status_code == 403
    good = {"Authorization": "Bearer device-secret"}
    assert client.post("/agents/register", json=payload, headers=good).status_code == 201
    assert client.post("/agents/register", json={**payload, "hostname": "another-host"}, headers=good).status_code == 403
    token = client.post("/access/devices/1/credential", headers=headers["admin"]).json()["device_token"]
    assert client.post("/agents/register", json=payload, headers=good).status_code == 403
    assert client.post("/agents/register", json=payload, headers={"Authorization": "Bearer " + token}).status_code == 201
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/devices/1", headers=good):
            pass
    with client.websocket_connect("/ws/devices/1", headers={"Authorization": "Bearer " + token}) as ws:
        assert ws.receive_json()["type"] == "connection"


def test_monitoring_requires_operator_and_closes_on_stop(workspace):
    client, headers, _ = workspace
    for role in ("viewer", "outsider"):
        with client.websocket_connect("/ws/monitoring/1") as ws:
            ws.send_json({"token": headers[role]["Authorization"].split()[1]})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    with client.websocket_connect("/ws/monitoring/1") as ws:
        ws.send_json({"token": headers["operator"]["Authorization"].split()[1]})
        assert ws.receive_json()["type"] == "monitoring_connection"
        assert client.post("/remote-monitoring/1/stop", headers=headers["operator"]).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_audit_records_allowed_and_denied_actions(workspace):
    client, headers, sessions = workspace
    client.patch("/devices/1", json={"device_name": "Changed"}, headers=headers["operator"])
    client.patch("/devices/1", json={"device_name": "Forbidden"}, headers=headers["viewer"])
    client.post("/remote-monitoring/device/1/restart", headers=headers["admin"])
    logs = client.get("/access/audit-logs", headers=headers["admin"]).json()
    assert any(x["actor_email"] == "operator@example.org" and x["result"] == "succeeded" for x in logs)
    assert any(x["actor_email"] == "viewer@example.org" and x["result"] == "denied" for x in logs)
    assert any(x["action"] == "power.restart" and x["result"] == "offline" for x in logs)


def test_audit_storage_failure_prevents_mutations(workspace, monkeypatch):
    _, headers, sessions = workspace
    from app.core import auditing
    def unavailable():
        raise RuntimeError("Audit storage unavailable")
    monkeypatch.setattr(auditing, "SessionLocal", unavailable)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.patch("/devices/1", json={"device_name": "Must not change"}, headers=headers["operator"]).status_code == 500
    with sessions() as db:
        assert db.get(Device, 1).device_name == "Workstation"
