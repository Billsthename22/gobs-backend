import base64
import json
from datetime import timedelta
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.access import token_hash
from app.core.security import create_access_token, hash_password
from app.models.access import AccessGrant, DeviceCredential
from app.models.agent_update import AgentUpdate
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.user import User
from app.services.agent_updates import MANIFEST_FIELDS, now


@pytest.fixture
def setup(isolated_database, monkeypatch):
    key = Ed25519PrivateKey.from_private_bytes(bytes([42] * 32))
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(settings, "AGENT_UPDATE_PUBLIC_KEY", public)
    monkeypatch.setattr(settings, "AGENT_UPDATE_DOWNLOAD_HOSTS", "downloads.example.org")
    with isolated_database() as db:
        headers = {}
        for role in ("admin", "operator", "viewer"):
            user = User(email=f"{role}@example.org", full_name=role, hashed_password=hash_password("test-password"))
            db.add(user)
            db.flush()
            if role != "admin":
                db.add(AccessGrant(email=user.email, role=role))
            headers[role] = {"Authorization": "Bearer " + create_access_token(user.id)}
        for number in (1, 2, 3):
            db.add(Device(id=number, hostname=f"device-{number}", device_name=f"Device {number}", operating_system="Windows"))
            db.add(DeviceCredential(device_id=number, token_hash=token_hash(f"secret-{number}")))
        db.commit()
    return TestClient(app), headers, isolated_database, key, public


def signed(key, **changes):
    data = dict(version="0.1.5", platform="windows", architecture="x86_64", url="https://downloads.example.org/agent.exe", size=10, sha256="a" * 64)
    data.update(changes)
    payload = json.dumps({k: data[k] for k in MANIFEST_FIELDS}, sort_keys=True, separators=(",", ":")).encode()
    return {**data, "signature": base64.b64encode(key.sign(payload)).decode()}


def report(client, public, device_id=1, **changes):
    data = dict(device_id=device_id, version="0.1.4", platform="windows", architecture="x86_64", updater_ready=True, public_key=public)
    data.update(changes)
    return client.post("/agents/updates/check", json=data, headers={"Authorization": f"Bearer secret-{device_id}"})


def publish(client, headers, key, **changes):
    response = client.post("/agent-updates/releases", json=signed(key, **changes), headers=headers["admin"])
    assert response.status_code == 201, response.text
    return response.json()["id"]


def approve(client, headers, release_id, ids=None):
    return client.post("/agent-updates/approve", json={"release_id": release_id, "device_ids": ids or [1]}, headers=headers["admin"])


def test_admin_only_approvals_and_signed_release_validation(setup):
    client, headers, _, key, public = setup
    for role in ("operator", "viewer"):
        assert client.post("/agent-updates/releases", json=signed(key), headers=headers[role]).status_code == 403
    assert client.get("/agent-updates").status_code in (401, 403)
    assert client.get("/agent-updates", headers=headers["viewer"]).status_code == 200
    bad = signed(key)
    bad["sha256"] = "b" * 64
    assert client.post("/agent-updates/releases", json=bad, headers=headers["admin"]).status_code == 422
    for url in ("http://downloads.example.org/agent.exe", "https://evil.example.org/agent.exe", "https://user:password@downloads.example.org/a", "https://downloads.example.org:8443/a"):
        assert client.post("/agent-updates/releases", json=signed(key, url=url), headers=headers["admin"]).status_code == 422
    release = publish(client, headers, key)
    report(client, public)
    for role in ("operator", "viewer"):
        assert client.post("/agent-updates/approve", json={"release_id": release, "device_ids": [1]}, headers=headers[role]).status_code == 403
    assert client.post("/agent-updates/releases", json=signed(key), headers=headers["admin"]).status_code == 409


def test_offline_queue_claim_install_and_confirmed_success(setup):
    client, headers, sessions, key, public = setup
    report(client, public)
    release = publish(client, headers, key)
    approved = approve(client, headers, release)
    assert approved.status_code == 201
    job = approved.json()[0]
    assert job["status"] == "queued"
    assert approve(client, headers, release).status_code == 409
    assert report(client, public, device_id=2).json()["update"] is None
    claimed = report(client, public).json()["update"]
    assert claimed["job_id"] == job["id"]
    assert claimed["manifest"] == signed(key)
    progress = f'/agents/updates/{job["id"]}/status'
    assert client.post(progress, json={"device_id": 2, "status": "installing"}, headers={"Authorization": "Bearer secret-2"}).status_code == 404
    assert client.post(progress, json={"device_id": 1, "status": "installing"}, headers={"Authorization": "Bearer secret-2"}).status_code == 403
    assert client.post(progress, json={"device_id": 1, "status": "installing"}, headers={"Authorization": "Bearer secret-1"}).status_code == 200
    # A success receipt without the target binary's reported version is insufficient.
    report(client, public, result={"job_id": job["id"], "status": "succeeded"})
    assert client.get("/agent-updates", headers=headers["admin"]).json()["devices"][0]["job"]["status"] == "installing"
    report(client, public, version="0.1.5", result={"job_id": job["id"], "status": "succeeded"})
    report(client, public, version="0.1.5", result={"job_id": job["id"], "status": "succeeded"})
    overview = client.get("/agent-updates", headers=headers["viewer"]).json()
    assert overview["devices"][0]["job"]["status"] == "succeeded"
    assert overview["devices"][0]["available_version"] is None
    with sessions() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "agent.update.succeeded").count() == 1
        assert db.query(AgentUpdate).first().requested_by == "admin@example.org"


def test_legacy_wrong_key_platform_and_downgrade_are_rejected_atomically(setup):
    client, headers, sessions, key, public = setup
    release = publish(client, headers, key)
    assert approve(client, headers, release).status_code == 409
    report(client, public, public_key="wrong-key")
    assert approve(client, headers, release).status_code == 409
    report(client, public, architecture="aarch64")
    assert approve(client, headers, release).status_code == 409
    report(client, public, version="0.1.10")
    assert approve(client, headers, release).status_code == 409
    report(client, public)
    assert approve(client, headers, release, [1, 3]).status_code == 409
    with sessions() as db:
        assert db.query(AgentUpdate).count() == 0
    assert approve(client, headers, release).status_code == 201


def test_revocation_cancellation_and_timeout(setup):
    client, headers, sessions, key, public = setup
    report(client, public)
    release = publish(client, headers, key)
    job = approve(client, headers, release).json()[0]
    assert client.post(f'/agent-updates/{job["id"]}/cancel', headers=headers["admin"]).status_code == 200
    assert report(client, public).json()["update"] is None
    job = approve(client, headers, release).json()[0]
    report(client, public)
    assert client.post(f'/agent-updates/{job["id"]}/cancel', headers=headers["admin"]).status_code == 409
    assert client.post(f"/agent-updates/releases/{release}/revoke", headers=headers["admin"]).status_code == 200
    assert client.post(f'/agents/updates/{job["id"]}/status', json={"device_id": 1, "status": "installing"}, headers={"Authorization": "Bearer secret-1"}).status_code == 409
    assert approve(client, headers, release).status_code == 409
    release2 = publish(client, headers, key, version="0.1.6")
    job2 = approve(client, headers, release2).json()[0]
    with sessions() as db:
        db.get(AgentUpdate, job2["id"]).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert report(client, public).json()["update"] is None
    assert approve(client, headers, release2).status_code == 201


def test_rollback_receipt_and_duplicate_install_do_not_repeat(setup):
    client, headers, _, key, public = setup
    report(client, public)
    release = publish(client, headers, key)
    job = approve(client, headers, release).json()[0]
    report(client, public)
    path = f'/agents/updates/{job["id"]}/status'
    auth = {"Authorization": "Bearer secret-1"}
    assert client.post(path, json={"device_id": 1, "status": "installing"}, headers=auth).status_code == 200
    assert client.post(path, json={"device_id": 1, "status": "installing"}, headers=auth).status_code == 409
    assert report(client, public).json()["update"] is None
    report(client, public, result={"job_id": job["id"], "status": "failed", "detail": "Previous agent restored"})
    overview = client.get("/agent-updates", headers=headers["admin"]).json()
    assert overview["devices"][0]["job"]["status"] == "failed"
    assert overview["devices"][0]["version"] == "0.1.4"
    assert approve(client, headers, release).status_code == 201


def test_offline_signing_tool_creates_verifiable_manifests(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    script = Path(__file__).resolve().parents[1] / "scripts" / "sign_agent_release.py"
    key = tmp_path / "release.pem"
    artifact = tmp_path / "agent.exe"
    manifest_path = tmp_path / "release.json"
    artifact.write_bytes(b"test-artifact-bytes")
    result = subprocess.run([sys.executable, str(script), "generate-key", "--output", str(key)], check=True, capture_output=True, text=True)
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(result.stdout.strip()))
    if os.name != "nt":
        assert key.stat().st_mode & 0o777 == 0o600
    subprocess.run([sys.executable, str(script), "sign", "--key", str(key), "--artifact", str(artifact),
                    "--version", "0.1.5", "--platform", "windows", "--architecture", "x86_64",
                    "--url", "https://downloads.example.org/agent.exe", "--output", str(manifest_path)], check=True, capture_output=True)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["size"] == artifact.stat().st_size
    payload = json.dumps({k: manifest[k] for k in MANIFEST_FIELDS}, sort_keys=True, separators=(",", ":")).encode()
    public.verify(base64.b64decode(manifest["signature"]), payload)
    # Generating over an existing key must never silently replace the trust root.
    assert subprocess.run([sys.executable, str(script), "generate-key", "--output", str(key)], capture_output=True).returncode != 0
