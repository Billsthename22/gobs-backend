import json
from datetime import timedelta
from typing import Literal, Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.access import device_authenticated, require_admin, require_member
from app.core.auditing import AuditedRouter
from app.core.config import settings
from app.core.database import get_db
from app.models.agent_update import AgentRelease, AgentState, AgentUpdate
from app.models.device import Device
from app.models.user import User
from app.services.agent_updates import ACTIVE, VERSION, aware, expire, now, public_job, transition, verify_manifest, version_tuple
from app.services.device_status import get_device_status

router = AuditedRouter(prefix="/agent-updates", tags=["Agent updates"])
agent_router = APIRouter(prefix="/agents/updates", tags=["Agent updater protocol"])


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=VERSION)
    platform: Literal["windows", "macos"]
    architecture: Literal["x86_64", "aarch64"]
    url: str = Field(min_length=10, max_length=2048, pattern=r"^[\x21-\x7e]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=250_000_000)
    signature: str = Field(min_length=80, max_length=100)


class Approval(BaseModel):
    release_id: str
    device_ids: list[int] = Field(min_length=1, max_length=100)


class Result(BaseModel):
    job_id: str
    status: Literal["succeeded", "failed"]
    detail: Optional[str] = Field(default=None, max_length=500)


class Check(BaseModel):
    device_id: int
    version: str = Field(pattern=VERSION)
    platform: Literal["windows", "macos", "linux"]
    architecture: Literal["x86_64", "aarch64"]
    protocol: Literal[1] = 1
    updater_ready: bool = False
    public_key: Optional[str] = Field(default=None, max_length=100)
    result: Optional[Result] = None


class Progress(BaseModel):
    device_id: int
    status: Literal["installing", "failed"]
    detail: Optional[str] = Field(default=None, max_length=500)


def authenticated_device(db, device_id, credentials):
    # The same row lock serializes approvals, cancellation, polling and receipts.
    device = db.query(Device).filter(Device.id == device_id).with_for_update().first()
    if not device or not device.is_active or not device_authenticated(db, device_id, credentials.credentials):
        raise HTTPException(403, "A valid credential for this active device is required")
    return device


@router.get("", dependencies=[Depends(require_member)])
def overview(db: Session = Depends(get_db)):
    releases = db.query(AgentRelease).order_by(AgentRelease.created_at.desc()).all()
    states = {s.device_id: s for s in db.query(AgentState).all()}
    jobs = db.query(AgentUpdate).order_by(AgentUpdate.created_at.desc()).all()
    releases_by_id = {r.id: r for r in releases}
    latest_jobs = {}
    for job in jobs:
        # Read views do not mutate lifecycle state; the next poll/approval expires it.
        if job.device_id not in latest_jobs:
            latest_jobs[job.device_id] = public_job(job, releases_by_id[job.release_id])
    devices = []
    for device in db.query(Device).order_by(Device.id).all():
        state = states.get(device.id)
        compatible = [r for r in releases if not r.revoked and state and r.platform == state.platform
                      and r.architecture == state.architecture and version_tuple(r.version) > version_tuple(state.version)]
        latest = max(compatible, key=lambda r: version_tuple(r.version), default=None)
        devices.append(dict(id=device.id, name=device.device_name, hostname=device.hostname,
                            active=device.is_active, status=get_device_status(device.last_seen),
                            version=state.version if state else None, platform=state.platform if state else None,
                            architecture=state.architecture if state else None,
                            updater_ready=bool(state and state.updater_ready), reported_at=state.reported_at if state else None,
                            release_id=latest.id if latest else None, available_version=latest.version if latest else None,
                            job=latest_jobs.get(device.id)))
    return dict(configured=bool(settings.AGENT_UPDATE_PUBLIC_KEY and settings.AGENT_UPDATE_DOWNLOAD_HOSTS), devices=devices,
                releases=[dict(id=r.id, **json.loads(r.manifest), revoked=r.revoked, created_at=r.created_at) for r in releases])


@router.post("/releases", status_code=201)
def publish(manifest: Manifest, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    data = manifest.model_dump()
    verify_manifest(data)
    if db.query(AgentRelease).filter_by(version=manifest.version, platform=manifest.platform, architecture=manifest.architecture).first():
        raise HTTPException(409, "This version and platform already exist; publish a new version")
    release = AgentRelease(id=str(uuid4()), version=manifest.version, platform=manifest.platform,
                           architecture=manifest.architecture, manifest=json.dumps(data), created_at=now())
    db.add(release)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This version and platform already exist; publish a new version")
    return {"id": release.id}


@router.post("/releases/{release_id}/revoke")
def revoke(release_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.get(AgentRelease, release_id)
    if not release:
        raise HTTPException(404, "Release not found")
    release.revoked = True
    db.commit()
    return {"revoked": True}


@router.post("/approve", status_code=201)
def approve(data: Approval, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.get(AgentRelease, data.release_id)
    if not release or release.revoked:
        raise HTTPException(409, "Release is unavailable")
    verify_manifest(json.loads(release.manifest))
    try:
        approved = []
        # Stable order avoids deadlocks during overlapping bulk approvals.
        for device_id in sorted(set(data.device_ids)):
            device = db.query(Device).filter(Device.id == device_id).with_for_update().first()
            state = db.get(AgentState, device_id)
            if not device or not device.is_active or not state or not state.updater_ready:
                raise HTTPException(409, f"Device {device_id} needs a manual updater-enabled agent installation first")
            if (state.platform, state.architecture) != (release.platform, release.architecture) or version_tuple(release.version) <= version_tuple(state.version):
                raise HTTPException(409, f"Release is not a newer compatible version for device {device_id}")
            active = db.get(AgentUpdate, state.active_job_id) if state.active_job_id else None
            if active:
                expire(db, active)
                if active.status in ACTIVE:
                    raise HTTPException(409, f"Device {device_id} already has an active update")
            job = AgentUpdate(id=str(uuid4()), device_id=device_id, release_id=release.id,
                              requested_by=user.email, status="queued", created_at=now(), updated_at=now(), expires_at=now() + timedelta(days=7))
            db.add(job)
            state.active_job_id = job.id
            transition(db, job, "queued", "Approved; waiting for the device to check in", user.email)
            approved.append(job)
        db.commit()
        return [public_job(job, release) for job in approved]
    except Exception:
        db.rollback()
        raise



@router.post("/{job_id}/cancel")
def cancel(job_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.get(AgentUpdate, job_id)
    if not job:
        raise HTTPException(404, "Update not found")
    db.query(Device).filter(Device.id == job.device_id).with_for_update().first()
    db.refresh(job)
    if job.status != "queued":
        raise HTTPException(409, "Only queued updates can be cancelled")
    transition(db, job, "cancelled", "Cancelled by administrator", user.email)
    db.commit()
    return {"status": job.status}


@agent_router.post("/check")
def check(data: Check, credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()), db: Session = Depends(get_db)):
    authenticated_device(db, data.device_id, credentials)
    state = db.get(AgentState, data.device_id)
    if not state:
        state = AgentState(device_id=data.device_id)
        db.add(state)
    state.version, state.platform, state.architecture = data.version, data.platform, data.architecture
    state.updater_ready = bool(data.updater_ready and settings.AGENT_UPDATE_PUBLIC_KEY
                               and data.public_key == settings.AGENT_UPDATE_PUBLIC_KEY and data.platform in {"windows", "macos"})
    state.reported_at = now()
    job = db.get(AgentUpdate, state.active_job_id) if state.active_job_id else None
    if job:
        expire(db, job)
    if data.result:
        receipt = db.get(AgentUpdate, data.result.job_id)
        if receipt and receipt.device_id == data.device_id and receipt.status in {"installing", "expired"}:
            release = db.get(AgentRelease, receipt.release_id)
            if data.result.status == "failed":
                transition(db, receipt, "failed", data.result.detail or "Agent restored the previous version")
            elif (data.version, data.platform, data.architecture) == (release.version, release.platform, release.architecture):
                transition(db, receipt, "succeeded", "Updated agent reconnected and passed its startup check")
    result = None
    if job and job.status in {"queued", "downloading"}:
        release = db.get(AgentRelease, job.release_id)
        if release.revoked:
            transition(db, job, "cancelled", "Release was revoked", "system")
        elif not state.updater_ready or (state.platform, state.architecture) != (release.platform, release.architecture):
            transition(db, job, "failed", "Updater capability or device platform changed")
        elif version_tuple(data.version) >= version_tuple(release.version):
            transition(db, job, "cancelled", "Device already runs this version or newer")
        else:
            if job.status == "queued":
                transition(db, job, "downloading", "Device claimed the approved update")
                job.expires_at = now() + timedelta(minutes=20)
            result = dict(job_id=job.id, manifest=json.loads(release.manifest))
    db.commit()
    return {"update": result, "updater_accepted": state.updater_ready}


@agent_router.post("/{job_id}/status")
def progress(job_id: str, data: Progress, credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()), db: Session = Depends(get_db)):
    authenticated_device(db, data.device_id, credentials)
    job = db.get(AgentUpdate, job_id)
    if not job or job.device_id != data.device_id:
        raise HTTPException(404, "Update not found")
    expire(db, job)
    release = db.get(AgentRelease, job.release_id)
    if data.status == "installing" and release.revoked:
        transition(db, job, "cancelled", "Release was revoked before installation", "system")
        db.commit()
        raise HTTPException(409, "Release was revoked")
    if job.status not in {"downloading", "installing"} or (job.status == "installing" and data.status == "installing"):
        db.commit()
        raise HTTPException(409, "Invalid update transition")
    transition(db, job, data.status, data.detail)
    if data.status == "installing":
        job.expires_at = now() + timedelta(minutes=10)
    db.commit()
    return {"status": job.status}
