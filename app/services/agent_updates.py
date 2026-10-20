"""Signed release manifests and persistent, device-bound update receipts."""
import base64
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from app.core.config import settings
from app.models.audit import AuditLog

ACTIVE = {"queued", "downloading", "installing"}
VERSION = r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$"
MANIFEST_FIELDS = ("architecture", "platform", "sha256", "size", "url", "version")


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def version_tuple(value):
    if not re.fullmatch(VERSION, value):
        raise ValueError("A stable major.minor.patch version is required")
    return tuple(map(int, value.split(".")))


def verify_manifest(manifest):
    if not settings.AGENT_UPDATE_PUBLIC_KEY:
        raise HTTPException(503, "Release signing public key has not been configured")
    url = urlsplit(manifest["url"])
    hosts = {v.strip().lower() for v in settings.AGENT_UPDATE_DOWNLOAD_HOSTS.split(",") if v.strip()}
    try:
        valid_url = (url.scheme == "https" and url.hostname in hosts and url.port in (None, 443)
                     and not url.username and not url.password and not url.fragment)
    except ValueError:
        valid_url = False
    if not valid_url:
        raise HTTPException(422, "Release URL must use HTTPS on an approved download host, without credentials or fragments")
    try:
        payload = json.dumps({key: manifest[key] for key in MANIFEST_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.AGENT_UPDATE_PUBLIC_KEY, validate=True))
        key.verify(base64.b64decode(manifest["signature"], validate=True), payload)
    except Exception:
        raise HTTPException(422, "Invalid release signature")


def transition(db, job, status, detail=None, actor=None):
    job.status = status
    job.detail = detail
    job.updated_at = now()
    db.add(AuditLog(actor_email=actor or f"device:{job.device_id}", action="agent.update." + status,
                    resource=f"device:{job.device_id}/update:{job.id}", result=status))


def expire(db, job):
    if job.status in ACTIVE and aware(job.expires_at) < now():
        transition(db, job, "expired", "Update deadline passed. Installation outcome is unconfirmed if the device stopped reporting.", "system")


def public_job(job, release):
    overdue = job.status in ACTIVE and aware(job.expires_at) < now()
    return dict(id=job.id, device_id=job.device_id, release_id=job.release_id, version=release.version,
                status="expired" if overdue else job.status,
                detail="Deadline passed; investigate the device before retrying" if overdue else job.detail, requested_by=job.requested_by,
                created_at=job.created_at, updated_at=job.updated_at)
