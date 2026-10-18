from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.deps import tenant_staff
from app.models import AuditLog, Department, Doctor, User

router = APIRouter(tags=["clinic"])
admin = tenant_staff("hospital_admin")
anyone = tenant_staff("hospital_admin", "receptionist", "doctor")
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class DeptIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class DoctorIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    department_id: int | None = None
    user_id: int | None = None
    qualification: str | None = Field(default=None, max_length=200)
    specialization: str | None = Field(default=None, max_length=200)
    consultation_fee: float = Field(default=0, ge=0, le=100000)
    slot_minutes: int = Field(default=15, ge=5, le=240)
    days: list[int] = Field(min_length=1)          # 0=Monday .. 6=Sunday
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)

    @model_validator(mode="after")
    def _check(self):
        if any(d < 0 or d > 6 for d in self.days):
            raise ValueError("days must be 0-6")
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class DoctorOut(BaseModel):
    id: int
    name: str
    department_id: int | None
    qualification: str | None
    specialization: str | None
    consultation_fee: float
    slot_minutes: int
    availability: dict
    is_active: bool
    model_config = {"from_attributes": True}


@router.post("/departments", status_code=201)
def create_department(body: DeptIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    d = Department(tenant_id=user.tenant_id, **body.model_dump())
    db.add(d)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Department already exists")
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="department.create", entity="department", entity_id=str(d.id)))
    db.commit()
    return {"id": d.id, "name": d.name, "description": d.description}


@router.get("/departments")
def list_departments(db: Session = Depends(get_db), user: User = Depends(anyone)):
    rows = db.scalars(select(Department).where(Department.tenant_id == user.tenant_id, Department.is_active).order_by(Department.name))
    return [{"id": d.id, "name": d.name, "description": d.description} for d in rows]


@router.post("/doctors", response_model=DoctorOut, status_code=201)
def create_doctor(body: DoctorIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    if body.department_id and not db.scalar(select(Department.id).where(Department.id == body.department_id, Department.tenant_id == user.tenant_id)):
        raise HTTPException(422, "Department not found")
    if body.user_id and not db.scalar(select(User.id).where(User.id == body.user_id, User.tenant_id == user.tenant_id, User.role == "doctor")):
        raise HTTPException(422, "Linked user must be a doctor account in this hospital")
    doc = Doctor(tenant_id=user.tenant_id, name=body.name, department_id=body.department_id, user_id=body.user_id,
                 qualification=body.qualification, specialization=body.specialization,
                 consultation_fee=body.consultation_fee, slot_minutes=body.slot_minutes,
                 availability={"days": sorted(set(body.days)), "start": body.start, "end": body.end})
    db.add(doc)
    db.flush()
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="doctor.create", entity="doctor", entity_id=str(doc.id)))
    db.commit()
    return doc


@router.get("/doctors", response_model=list[DoctorOut])
def list_doctors(db: Session = Depends(get_db), user: User = Depends(anyone)):
    return db.scalars(select(Doctor).where(Doctor.tenant_id == user.tenant_id, Doctor.is_active).order_by(Doctor.name)).all()


@router.post("/doctors/{doctor_id}/deactivate", response_model=DoctorOut)
def deactivate_doctor(doctor_id: int, db: Session = Depends(get_db), user: User = Depends(admin)):
    doc = db.scalar(select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == user.tenant_id))
    if not doc:
        raise HTTPException(404, "Doctor not found")
    doc.is_active = False
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="doctor.deactivate", entity="doctor", entity_id=str(doc.id)))
    db.commit()
    return doc
