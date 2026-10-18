from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Appointment

DAY = (date.today() + timedelta(days=3)).isoformat()
DOC = {"name": "Dr. Meera Rao", "specialization": "General", "consultation_fee": 500, "slot_minutes": 30,
       "days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "12:00"}


@pytest.fixture()
def clinic(world):
    w = world
    dep = w["a"].post("/api/v1/departments", json={"name": "General Medicine"}).json()
    uid = next(u["id"] for u in w["a"].get("/api/v1/users").json() if u["role"] == "doctor")
    doc = w["a"].post("/api/v1/doctors", json={**DOC, "department_id": dep["id"], "user_id": uid}).json()
    pat = w["recep"].post("/api/v1/patients", json={"full_name": "Asha Verma", "mobile": "9876543210"}).json()
    pat2 = w["recep"].post("/api/v1/patients", json={"full_name": "Ravi Nair", "mobile": "9123456780"}).json()
    return {**w, "doctor": doc, "p1": pat, "p2": pat2}


def book(c, pid, t, client=None, **kw):
    return (client or c["recep"]).post("/api/v1/appointments", json={"patient_id": pid, "doctor_id": c["doctor"]["id"], "date": DAY, "time": t, **kw})


def test_slots_listed(clinic):
    r = clinic["recep"].get("/api/v1/appointments/slots", params={"doctor_id": clinic["doctor"]["id"], "date": DAY})
    assert r.json()["slots"] == ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]


def test_book_and_double_booking_blocked(clinic):
    r = book(clinic, clinic["p1"]["id"], "09:00")
    assert r.status_code == 201, r.text
    assert r.json()["appointment_number"].startswith("APT-") and r.json()["fee"] == 500
    assert book(clinic, clinic["p2"]["id"], "09:00").status_code == 409
    assert book(clinic, clinic["p2"]["id"], "09:30").status_code == 201
    free = clinic["recep"].get("/api/v1/appointments/slots", params={"doctor_id": clinic["doctor"]["id"], "date": DAY}).json()["slots"]
    assert "09:00" not in free and "09:30" not in free


def test_unique_appointment_numbers(clinic):
    nums = {book(clinic, clinic["p1"]["id"], t).json()["appointment_number"] for t in ("09:00", "09:30", "10:00")}
    assert len(nums) == 3


def test_invalid_slots_rejected(clinic):
    assert book(clinic, clinic["p1"]["id"], "13:00").status_code == 422      # outside hours
    assert book(clinic, clinic["p1"]["id"], "09:10").status_code == 422      # not slot-aligned
    past = clinic["recep"].post("/api/v1/appointments", json={"patient_id": clinic["p1"]["id"], "doctor_id": clinic["doctor"]["id"], "date": "2020-01-06", "time": "09:00"})
    assert past.status_code == 422


def test_database_constraint_blocks_duplicate_slot(clinic, Session):
    a = book(clinic, clinic["p1"]["id"], "10:00").json()
    with Session() as db:
        src = db.get(Appointment, a["id"])
        db.add(Appointment(tenant_id=src.tenant_id, appointment_number="DUP-1", patient_id=src.patient_id, doctor_id=src.doctor_id,
                           start_at=src.start_at, end_at=src.end_at, status="booked"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_cancel_frees_slot_and_reschedule(clinic):
    a = book(clinic, clinic["p1"]["id"], "09:00").json()
    b = book(clinic, clinic["p2"]["id"], "09:30").json()
    rs = lambda i, t: clinic["recep"].post(f"/api/v1/appointments/{i}/reschedule", json={"date": DAY, "time": t})
    assert rs(b["id"], "09:00").status_code == 409
    assert rs(b["id"], "11:00").json()["start_local"].endswith("11:00")
    assert clinic["recep"].post(f"/api/v1/appointments/{a['id']}/status", json={"status": "cancelled"}).status_code == 200
    assert book(clinic, clinic["p2"]["id"], "09:00").status_code == 201
    assert rs(a["id"], "10:00").status_code == 409                          # cancelled cannot be rescheduled


def test_status_transitions_and_doctor_scope(clinic):
    a = book(clinic, clinic["p1"]["id"], "09:00").json()
    st = lambda cl, s: cl.post(f"/api/v1/appointments/{a['id']}/status", json={"status": s})
    assert st(clinic["doc"], "cancelled").status_code == 403
    assert st(clinic["doc"], "completed").status_code == 409                 # must check in first
    assert st(clinic["recep"], "checked_in").status_code == 200
    assert st(clinic["doc"], "completed").status_code == 200
    assert st(clinic["recep"], "booked" if False else "confirmed").status_code == 409
    assert len(clinic["doc"].get("/api/v1/appointments").json()) == 1


def test_cross_tenant_booking_blocked(clinic):
    assert book(clinic, clinic["p1"]["id"], "09:00", client=clinic["b"]).status_code == 404   # B admin, A's doctor+patient
    a = book(clinic, clinic["p1"]["id"], "09:00").json()
    assert clinic["b"].get("/api/v1/appointments").json() == []
    assert clinic["b"].post(f"/api/v1/appointments/{a['id']}/status", json={"status": "cancelled"}).status_code == 404
    assert clinic["b"].get("/api/v1/doctors").json() == []


def test_doctor_management_permissions(clinic):
    assert clinic["recep"].post("/api/v1/doctors", json=DOC).status_code == 403
    assert clinic["a"].post("/api/v1/doctors", json={**DOC, "department_id": 9999}).status_code == 422
    assert clinic["a"].post("/api/v1/doctors", json={**DOC, "start": "12:00", "end": "09:00"}).status_code == 422
    clinic["a"].post(f"/api/v1/doctors/{clinic['doctor']['id']}/deactivate")
    assert book(clinic, clinic["p1"]["id"], "09:00").status_code == 422
