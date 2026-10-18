from datetime import date as Date, datetime, time as Time, timedelta, timezone
from zoneinfo import ZoneInfo

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.deps import tenant_staff
from app.services import whatsapp as wa
from app.models import Appointment, AuditLog, Doctor, Patient, TenantSettings, User

router = APIRouter(prefix="/appointments", tags=["appointments"])
staff = tenant_staff("hospital_admin", "receptionist")
anyone = tenant_staff("hospital_admin", "receptionist", "doctor")
INACTIVE = ("cancelled", "no_show")
TRANSITIONS = {"booked": {"confirmed", "checked_in", "cancelled", "no_show"},
               "confirmed": {"checked_in", "cancelled", "no_show"},
               "checked_in": {"completed", "cancelled"}}
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class BookIn(BaseModel):
    patient_id: int
    doctor_id: int
    date: Date
    time: str = Field(pattern=HHMM)
    reason: str | None = Field(default=None, max_length=1000)
    send_whatsapp: bool = False   # receptionist confirms the patient agreed to receive WhatsApp messages


class RescheduleIn(BaseModel):
    date: Date
    time: str = Field(pattern=HHMM)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(confirmed|checked_in|completed|cancelled|no_show)$")


def utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def notify(background, db, appt, kind):
    """Queue a WhatsApp message AFTER the appointment is saved. Messaging problems never fail a booking."""
    try:
        jid = wa.enqueue(db, appt, kind)
        if jid:
            background.add_task(wa.run_delivery, jid)
    except Exception:
        logging.getLogger("whatsapp").exception("could not queue notification")


def tenant_rules(db, tenant_id):
    s = db.get(TenantSettings, tenant_id)
    return (s.rules if s else {}) or {}


def window(doctor: Doctor, d: Date, tz: ZoneInfo):
    """All slot start times (local, tz-aware) the doctor offers on date d."""
    a = doctor.availability or {}
    if d.weekday() not in a.get("days", []):
        return []
    h1, m1 = map(int, a["start"].split(":"))
    h2, m2 = map(int, a["end"].split(":"))
    cur = datetime.combine(d, Time(h1, m1), tzinfo=tz)
    end = datetime.combine(d, Time(h2, m2), tzinfo=tz)
    step = timedelta(minutes=doctor.slot_minutes)
    out = []
    while cur + step <= end:
        out.append(cur)
        cur += step
    return out


def validate_slot(doctor: Doctor, d: Date, t: str, tz: ZoneInfo):
    h, m = map(int, t.split(":"))
    local = datetime.combine(d, Time(h, m), tzinfo=tz)
    if local not in window(doctor, d, tz):
        raise HTTPException(422, "Doctor is not available at this date/time")
    start = utc(local)
    if start <= datetime.now(timezone.utc):
        raise HTTPException(422, "Appointment time must be in the future")
    return start, start + timedelta(minutes=doctor.slot_minutes)


def has_conflict(db, tenant_id, doctor_id, s, e, exclude_id=None):
    q = select(Appointment.id).where(Appointment.tenant_id == tenant_id, Appointment.doctor_id == doctor_id,
                                     Appointment.status.notin_(INACTIVE), Appointment.start_at < e, Appointment.end_at > s)
    if exclude_id:
        q = q.where(Appointment.id != exclude_id)
    return db.scalar(q.limit(1)) is not None


def scoped(db, model, id_, tenant_id, what):
    row = db.scalar(select(model).where(model.id == id_, model.tenant_id == tenant_id))
    if not row:
        raise HTTPException(404, f"{what} not found")
    return row


def present(a: Appointment, patient_name, doctor_name, tz):
    return {"id": a.id, "appointment_number": a.appointment_number, "patient_id": a.patient_id,
            "patient_name": patient_name, "doctor_id": a.doctor_id, "doctor_name": doctor_name,
            "start_local": utc(a.start_at).astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            "status": a.status, "fee": float(a.fee) if a.fee is not None else None, "reason": a.reason}


def row_out(db, a, tz):
    p = db.get(Patient, a.patient_id)
    d = db.get(Doctor, a.doctor_id)
    return present(a, p.full_name, d.name, tz)


@router.get("/slots")
def free_slots(doctor_id: int, date: Date, db: Session = Depends(get_db), user: User = Depends(anyone)):
    doc = scoped(db, Doctor, doctor_id, user.tenant_id, "Doctor")
    tz = ZoneInfo(tenant_rules(db, user.tenant_id).get("timezone", "Asia/Kolkata"))
    now = datetime.now(timezone.utc)
    free = []
    for s in window(doc, date, tz):
        st = utc(s)
        if st > now and not has_conflict(db, user.tenant_id, doc.id, st, st + timedelta(minutes=doc.slot_minutes)):
            free.append(s.strftime("%H:%M"))
    return {"doctor_id": doc.id, "date": date.isoformat(), "slots": free}


@router.post("", status_code=201)
def book(body: BookIn, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(staff)):
    tid = user.tenant_id
    patient = scoped(db, Patient, body.patient_id, tid, "Patient")
    doc = scoped(db, Doctor, body.doctor_id, tid, "Doctor")
    if not doc.is_active:
        raise HTTPException(422, "Doctor is not accepting appointments")
    rules = tenant_rules(db, tid)
    tz = ZoneInfo(rules.get("timezone", "Asia/Kolkata"))
    start, end = validate_slot(doc, body.date, body.time, tz)
    db.execute(select(Doctor.id).where(Doctor.id == doc.id).with_for_update())  # serialises bookings per doctor on PostgreSQL
    if has_conflict(db, tid, doc.id, start, end):
        raise HTTPException(409, "This slot is already booked")
    prefix = rules.get("appointment_prefix", "APT")
    for _ in range(5):
        n = db.scalar(select(func.count()).select_from(Appointment).where(Appointment.tenant_id == tid)) + 1
        a = Appointment(tenant_id=tid, appointment_number=f"{prefix}-{body.date:%Y%m%d}-{n:04d}", patient_id=patient.id,
                        doctor_id=doc.id, start_at=start, end_at=end, status="booked",
                        fee=doc.consultation_fee, reason=body.reason)
        db.add(a)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            if has_conflict(db, tid, doc.id, start, end):
                raise HTTPException(409, "This slot is already booked")
    else:
        raise HTTPException(503, "Could not allocate appointment number, please retry")
    db.add(AuditLog(tenant_id=tid, user_id=user.id, action="appointment.book", entity="appointment", entity_id=str(a.id)))
    db.commit()
    if body.send_whatsapp:
        wa.grant_front_desk_consent(db, tid, patient.id)
    notify(background, db, a, "confirmation")
    return row_out(db, a, tz)


@router.get("")
def list_appointments(date_from: Date | None = None, date_to: Date | None = None, doctor_id: int | None = None,
                      status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                      db: Session = Depends(get_db), user: User = Depends(anyone)):
    tz = ZoneInfo(tenant_rules(db, user.tenant_id).get("timezone", "Asia/Kolkata"))
    q = select(Appointment).where(Appointment.tenant_id == user.tenant_id)
    if user.role == "doctor":  # doctors only ever see their own appointments
        mine = select(Doctor.id).where(Doctor.user_id == user.id, Doctor.tenant_id == user.tenant_id)
        q = q.where(Appointment.doctor_id.in_(mine))
    elif doctor_id:
        q = q.where(Appointment.doctor_id == doctor_id)
    if status:
        q = q.where(Appointment.status == status)
    if date_from:
        q = q.where(Appointment.start_at >= utc(datetime.combine(date_from, Time.min, tzinfo=tz)))
    if date_to:
        q = q.where(Appointment.start_at < utc(datetime.combine(date_to + timedelta(days=1), Time.min, tzinfo=tz)))
    rows = db.scalars(q.order_by(Appointment.start_at).limit(limit).offset(offset)).all()
    return [row_out(db, a, tz) for a in rows]


@router.post("/{appt_id}/reschedule")
def reschedule(appt_id: int, body: RescheduleIn, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(staff)):
    a = scoped(db, Appointment, appt_id, user.tenant_id, "Appointment")
    if a.status not in ("booked", "confirmed"):
        raise HTTPException(409, f"A {a.status} appointment cannot be rescheduled")
    doc = db.get(Doctor, a.doctor_id)
    tz = ZoneInfo(tenant_rules(db, user.tenant_id).get("timezone", "Asia/Kolkata"))
    start, end = validate_slot(doc, body.date, body.time, tz)
    db.execute(select(Doctor.id).where(Doctor.id == doc.id).with_for_update())
    if has_conflict(db, user.tenant_id, doc.id, start, end, exclude_id=a.id):
        raise HTTPException(409, "This slot is already booked")
    a.start_at, a.end_at = start, end
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="appointment.reschedule", entity="appointment", entity_id=str(a.id)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This slot is already booked")
    notify(background, db, a, "reschedule")
    return row_out(db, a, tz)


@router.post("/{appt_id}/status")
def change_status(appt_id: int, body: StatusIn, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(anyone)):
    a = scoped(db, Appointment, appt_id, user.tenant_id, "Appointment")
    if user.role == "doctor":
        own = db.scalar(select(Doctor.id).where(Doctor.id == a.doctor_id, Doctor.user_id == user.id))
        if not own:
            raise HTTPException(404, "Appointment not found")
        if body.status not in ("checked_in", "completed", "no_show"):
            raise HTTPException(403, "Doctors cannot set this status")
    if body.status not in TRANSITIONS.get(a.status, set()):
        raise HTTPException(409, f"Cannot change a {a.status} appointment to {body.status}")
    a.status = body.status
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action=f"appointment.{body.status}", entity="appointment", entity_id=str(a.id)))
    db.commit()
    if body.status == "cancelled":
        notify(background, db, a, "cancellation")
    tz = ZoneInfo(tenant_rules(db, user.tenant_id).get("timezone", "Asia/Kolkata"))
    return row_out(db, a, tz)
