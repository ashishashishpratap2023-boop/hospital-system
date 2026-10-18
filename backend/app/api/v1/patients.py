from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.deps import tenant_staff
from app.models import AuditLog, Patient, User
from app.schemas import PatientIn, PatientOut, normalize_mobile

router = APIRouter(prefix="/patients", tags=["patients"])
staff = tenant_staff("hospital_admin", "receptionist")


@router.post("", response_model=PatientOut, status_code=201)
def create_patient(body: PatientIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    dup = db.scalar(select(Patient).where(
        Patient.tenant_id == user.tenant_id, Patient.mobile == body.mobile,
        func.lower(Patient.full_name) == body.full_name.lower()))
    if dup:
        raise HTTPException(409, f"Patient already registered as {dup.patient_code}")
    for _ in range(5):  # retry if two receptionists race for the same code
        n = db.scalar(select(func.count()).select_from(Patient).where(Patient.tenant_id == user.tenant_id)) + 1
        p = Patient(tenant_id=user.tenant_id, patient_code=f"PT-{n:06d}", **body.model_dump())
        db.add(p)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
    else:
        raise HTTPException(503, "Could not allocate patient ID, please retry")
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="patient.create", entity="patient", entity_id=str(p.id)))
    db.commit()
    return p


@router.get("", response_model=list[PatientOut])
def search_patients(q: str | None = Query(default=None, max_length=100), limit: int = Query(20, ge=1, le=100),
                    offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(staff)):
    stmt = select(Patient).where(Patient.tenant_id == user.tenant_id)
    if q:
        q = q.strip()
        conds = [Patient.full_name.icontains(q, autoescape=True), Patient.patient_code.icontains(q, autoescape=True)]
        try:
            conds.append(Patient.mobile == normalize_mobile(q))
        except ValueError:
            digits = "".join(c for c in q if c.isdigit())
            if len(digits) >= 4:
                conds.append(Patient.mobile.contains(digits, autoescape=True))
        stmt = stmt.where(or_(*conds))
    return db.scalars(stmt.order_by(Patient.id.desc()).limit(limit).offset(offset)).all()


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    p = db.scalar(select(Patient).where(Patient.id == patient_id, Patient.tenant_id == user.tenant_id))
    if not p:
        raise HTTPException(404, "Patient not found")  # same answer whether it exists elsewhere or not
    return p
