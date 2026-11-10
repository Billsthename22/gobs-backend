import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.main import app
from app.core.access import token_hash
from app.models.access import DeviceCredential
from app.models.audit import AuditLog
from app.models.device import Device
from app.routes.agents import AgentRegisterRequest, register_agent


TOKEN = "locally-generated-device-credential-123456789"
PAYLOAD = {
    "hostname": "new-workstation",
    "device_name": "New Workstation",
    "operating_system": "Windows 11",
    "auto_enroll": True,
}


def enroll(client, token=TOKEN, **changes):
    return client.post("/agents/register", json={**PAYLOAD, **changes},
                       headers={"Authorization": f"Bearer {token}"})


def test_automatic_enrollment_persists_identity_and_retries_idempotently(isolated_database):
    client = TestClient(app)
    first = enroll(client)
    assert first.status_code == 201
    device_id = first.json()["device_id"]
    assert enroll(client).json()["device_id"] == device_id
    # Legacy registration still works once the agent has an identity.
    assert enroll(client, auto_enroll=False).status_code == 201
    with isolated_database() as db:
        assert db.query(Device).count() == 1
        device = db.get(Device, device_id)
        assert device.hostname == PAYLOAD["hostname"]
        assert device.operating_system == "Windows 11"
        assert device.is_active
        credential = db.get(DeviceCredential, device_id)
        assert credential.token_hash == token_hash(TOKEN)
        logs = db.query(AuditLog).all()
        assert all(log.actor_email == f"device:{device_id}" and log.result == "succeeded" for log in logs)
        assert TOKEN not in str([(log.action, log.resource, log.actor_email) for log in logs])
    # The generated credential is usable by the existing authenticated socket.
    with client.websocket_connect(f"/ws/devices/{device_id}", headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
        assert ws.receive_json()["type"] == "connection"


def test_automatic_enrollment_binds_an_uncredentialed_precreated_device(isolated_database):
    with isolated_database() as db:
        device = Device(hostname=PAYLOAD["hostname"], device_name="Administrator's name", operating_system="Windows")
        db.add(device)
        db.commit()
        device_id = device.id
    response = enroll(TestClient(app))
    assert response.status_code == 201
    assert response.json()["device_id"] == device_id
    assert response.json()["device_name"] == "Administrator's name"
    with isolated_database() as db:
        assert db.query(Device).count() == 1
        assert db.get(DeviceCredential, device_id).token_hash == token_hash(TOKEN)


def test_automatic_enrollment_cannot_replace_an_existing_identity(isolated_database):
    client = TestClient(app)
    device_id = enroll(client).json()["device_id"]
    assert enroll(client, token="another-long-generated-device-credential-123456789").status_code == 403
    with isolated_database() as db:
        assert db.get(DeviceCredential, device_id).token_hash == token_hash(TOKEN)
    assert enroll(client).status_code == 201


@pytest.mark.parametrize("has_credential", [False, True])
def test_automatic_enrollment_rejects_inactive_devices(isolated_database, has_credential):
    with isolated_database() as db:
        device = Device(hostname=PAYLOAD["hostname"], device_name="Inactive", operating_system="Windows", is_active=False)
        db.add(device)
        db.flush()
        if has_credential:
            db.add(DeviceCredential(device_id=device.id, token_hash=token_hash(TOKEN)))
        db.commit()
    assert enroll(TestClient(app)).status_code == 403
    with isolated_database() as db:
        assert db.query(DeviceCredential).count() == int(has_credential)


@pytest.mark.parametrize("auto_enroll, token", [(False, TOKEN), (True, "short-token")])
def test_new_identity_requires_explicit_enrollment_and_a_generated_credential(isolated_database, auto_enroll, token):
    assert enroll(TestClient(app), token=token, auto_enroll=auto_enroll).status_code == 403
    with isolated_database() as db:
        assert db.query(Device).count() == 0
        assert db.query(DeviceCredential).count() == 0


def test_existing_manual_credentials_remain_compatible(isolated_database):
    with isolated_database() as db:
        device = Device(hostname=PAYLOAD["hostname"], device_name="Legacy", operating_system="Windows")
        db.add(device)
        db.flush()
        db.add(DeviceCredential(device_id=device.id, token_hash=token_hash("old-short-secret")))
        db.commit()
    assert enroll(TestClient(app), token="old-short-secret", auto_enroll=False).status_code == 201
    assert enroll(TestClient(app), token="old-short-secret").status_code == 201


@pytest.mark.parametrize("winner_token, expected", [(TOKEN, 201), ("another-generated-credential-123456789", 403)])
def test_concurrent_enrollment_authenticates_the_winning_identity(isolated_database, monkeypatch, winner_token, expected):
    with isolated_database() as db:
        original_commit, original_rollback = db.commit, db.rollback
        attempts = 0

        def conflicting_commit():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise IntegrityError("concurrent enrollment", {}, Exception("duplicate hostname"))
            original_commit()

        def rollback_and_commit_winner():
            original_rollback()
            with isolated_database() as other:
                winner = Device(hostname=PAYLOAD["hostname"], device_name="Concurrent install", operating_system="Windows")
                other.add(winner)
                other.flush()
                other.add(DeviceCredential(device_id=winner.id, token_hash=token_hash(winner_token)))
                other.commit()

        monkeypatch.setattr(db, "commit", conflicting_commit)
        monkeypatch.setattr(db, "rollback", rollback_and_commit_winner)
        request = Request({"type": "http"})
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=TOKEN)
        if expected == 201:
            result = register_agent(AgentRegisterRequest(**PAYLOAD), request, credentials, db)
            assert result.device_name == "Concurrent install"
        else:
            with pytest.raises(HTTPException) as error:
                register_agent(AgentRegisterRequest(**PAYLOAD), request, credentials, db)
            assert error.value.status_code == expected
    with isolated_database() as db:
        assert db.query(Device).count() == 1
        assert db.query(DeviceCredential).one().token_hash == token_hash(winner_token)


def test_failed_enrollment_does_not_leave_a_partially_created_device(isolated_database, monkeypatch):
    with isolated_database() as db:
        def failed_commit():
            raise IntegrityError("credential insert", {}, Exception("constraint failure"))
        monkeypatch.setattr(db, "commit", failed_commit)
        with pytest.raises(HTTPException) as error:
            register_agent(AgentRegisterRequest(**PAYLOAD), Request({"type": "http"}),
                           HTTPAuthorizationCredentials(scheme="Bearer", credentials=TOKEN), db)
        assert error.value.status_code == 409
    with isolated_database() as db:
        assert db.query(Device).count() == 0
        assert db.query(DeviceCredential).count() == 0


@pytest.mark.parametrize("changes", [
    {"hostname": "   "}, {"hostname": "x" * 256},
    {"device_name": ""}, {"operating_system": "x" * 101},
])
def test_automatic_enrollment_validates_device_details(isolated_database, changes):
    assert enroll(TestClient(app), **changes).status_code == 422
    with isolated_database() as db:
        assert db.query(Device).count() == 0
        assert db.query(DeviceCredential).count() == 0
