from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.activity import Activity
from app.models.device import Device
from app.models.employee import Employee
from app.schemas.activity import ActivityCreate, ActivityResponse


router = APIRouter(
    prefix="/activities",
    tags=["Activity"],
)


@router.post(
    "",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_activity(
    activity_data: ActivityCreate,
    db: Session = Depends(get_db),
):
    device = (
        db.query(Device)
        .filter(Device.id == activity_data.device_id)
        .first()
    )

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    if activity_data.employee_id is not None:
        employee = (
            db.query(Employee)
            .filter(Employee.id == activity_data.employee_id)
            .first()
        )

        if not employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee not found",
            )

    activity = Activity(
        device_id=activity_data.device_id,
        employee_id=activity_data.employee_id,
        activity_type=activity_data.activity_type,
        description=activity_data.description,
    )

    db.add(activity)
    db.commit()
    db.refresh(activity)

    return activity


@router.get(
    "",
    response_model=list[ActivityResponse],
)
def get_activities(
    db: Session = Depends(get_db),
):
    return (
        db.query(Activity)
        .order_by(Activity.timestamp.desc())
        .all()
    )


@router.get(
    "/{activity_id}",
    response_model=ActivityResponse,
)
def get_activity(
    activity_id: int,
    db: Session = Depends(get_db),
):
    activity = (
        db.query(Activity)
        .filter(Activity.id == activity_id)
        .first()
    )

    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    return activity


@router.delete(
    "/{activity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_activity(
    activity_id: int,
    db: Session = Depends(get_db),
):
    activity = (
        db.query(Activity)
        .filter(Activity.id == activity_id)
        .first()
    )

    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    db.delete(activity)
    db.commit()

    return None
