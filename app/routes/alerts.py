from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.alert import Alert
from app.models.device import Device
from app.models.employee import Employee
from app.schemas.alert import (
    AlertCreate,
    AlertResponse,
    AlertUpdate,
)


router = APIRouter(
    prefix="/alerts",
    tags=["Alerts"],
)


@router.post(
    "",
    response_model=AlertResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_alert(
    alert_data: AlertCreate,
    db: Session = Depends(get_db),
):
    device = (
        db.query(Device)
        .filter(Device.id == alert_data.device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    if alert_data.employee_id is not None:
        employee = (
            db.query(Employee)
            .filter(Employee.id == alert_data.employee_id)
            .first()
        )

        if not employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee not found",
            )

    alert = Alert(
        device_id=alert_data.device_id,
        employee_id=alert_data.employee_id,
        alert_type=alert_data.alert_type,
        severity=alert_data.severity,
        title=alert_data.title,
        description=alert_data.description,
    )

    db.add(alert)
    db.commit()
    db.refresh(alert)

    return alert


@router.get(
    "",
    response_model=list[AlertResponse],
)
def get_alerts(
    db: Session = Depends(get_db),
):
    return (
        db.query(Alert)
        .order_by(Alert.created_at.desc())
        .all()
    )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
)
def get_alert(
    alert_id: int,
    db: Session = Depends(get_db),
):
    alert = (
        db.query(Alert)
        .filter(Alert.id == alert_id)
        .first()
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )

    return alert


@router.patch(
    "/{alert_id}",
    response_model=AlertResponse,
)
def update_alert(
    alert_id: int,
    alert_data: AlertUpdate,
    db: Session = Depends(get_db),
):
    alert = (
        db.query(Alert)
        .filter(Alert.id == alert_id)
        .first()
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )

    update_data = alert_data.model_dump(
        exclude_unset=True
    )

    if "is_resolved" in update_data:
        is_resolved = update_data["is_resolved"]

        if is_resolved and not alert.is_resolved:
            alert.resolved_at = datetime.now(timezone.utc)

        elif not is_resolved:
            alert.resolved_at = None

    for field, value in update_data.items():
        setattr(alert, field, value)

    db.commit()
    db.refresh(alert)

    return alert


@router.delete(
    "/{alert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_alert(
    alert_id: int,
    db: Session = Depends(get_db),
):
    alert = (
        db.query(Alert)
        .filter(Alert.id == alert_id)
        .first()
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )

    db.delete(alert)
    db.commit()

    return None
