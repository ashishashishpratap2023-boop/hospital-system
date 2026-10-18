import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import app.services.whatsapp as wa
from app.main import app
from app.models import Appointment
from tests.test_appointments import DAY, book, clinic  # noqa: F401  (clinic is a fixture)

SECRET, VERIFY = "s3cret-app-value", "verify-token-123456"


class Fake:
    def __init__(self):
        self.sent, self.script = [], []

    def send(self, to, body, tpl, params, ref=None):
        self.sent.append((to, body, tpl, params))
        if self.script:
            r = self.script.pop(0)
            if isinstance(r, Exception):
                raise r
        return f"wamid.{len(self.sent)}"

    def check(self):
        return "+91 99999 00000"


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(wa, "get_provider", lambda conn: f)
    return f


def connect(c, test=True):
    r = c["a"].put("/api/v1/whatsapp/connection", json={"phone_number_id": "123456789", "business_account_id": "987654321",
                   "access_token": "EAAG-secret-token-value", "app_secret": SECRET, "verify_token": VERIFY})
    assert r.status_code == 200
    return c["a"].post("/api/v1/whatsapp/test").json() if test else r.json()


def consent(c, pid, granted=True):
    assert c["recep"].post(f"/api/v1/whatsapp/consent/{pid}", json={"granted": granted}).status_code == 201


def sign(body: bytes):
    return {"x-hub-signature-256": "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(), "content-type": "application/json"}


def test_render_drops_empty_optional_lines():
    ctx = {"hospital_name": "City", "patient_name": "Asha", "facebook_url": "", "hospital_phone": "0522-1"}
    out = wa.render("Dear {patient_name}\nFacebook: {facebook_url}\nContact: {hospital_phone}", ctx)
    assert out == "Dear Asha\nContact: 0522-1" and "{" not in out and "Facebook" not in out


def test_default_message_uses_right_hospital_and_skips_missing_fields(clinic, fake):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    assert book(clinic, clinic["p1"]["id"], "09:00").status_code == 201
    (to, body, tpl, params), = fake.sent
    assert to == "+919876543210" and "City Hospital A" in body and "Care Clinic B" not in body
    assert "Doctor: Dr. Meera Rao" in body and "Rs. 500" in body
    assert "Facebook" not in body and "Google Maps" not in body and "{" not in body   # not configured -> no empty lines


def test_credentials_never_returned_and_status_honest(clinic, fake):
    r = clinic["a"].get("/api/v1/whatsapp/connection").json()
    assert r["status"] == "not_configured"
    r = connect(clinic, test=False)
    assert r["status"] == "configured" and r["token_set"] is True            # saved but NOT connected yet
    assert "EAAG" not in json.dumps(r) and SECRET not in json.dumps(r)
    assert connect(clinic)["status"] == "connected"
    assert clinic["a"].post("/api/v1/whatsapp/disconnect").json()["status"] == "disconnected"
    assert clinic["recep"].get("/api/v1/whatsapp/connection").status_code == 403


def test_failed_connection_test_is_not_connected(clinic, monkeypatch):
    class Bad:
        def check(self):
            raise wa.ProviderError("Invalid OAuth access token", auth=True)
    monkeypatch.setattr(wa, "get_provider", lambda conn: Bad())
    r = connect(clinic)
    assert r["status"] == "error" and "OAuth" in r["last_error"]


def test_no_message_without_connection_or_consent(clinic, fake):
    assert book(clinic, clinic["p1"]["id"], "09:00").status_code == 201      # not connected
    connect(clinic)
    assert book(clinic, clinic["p1"]["id"], "09:30").status_code == 201      # connected, no consent
    consent(clinic, clinic["p2"]["id"])
    consent(clinic, clinic["p2"]["id"], granted=False)                        # opted out
    assert book(clinic, clinic["p2"]["id"], "10:00").status_code == 201
    assert fake.sent == []


def test_duplicate_prevention(clinic, fake, Session):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    book(clinic, clinic["p1"]["id"], "09:00")
    with Session() as db:
        appt = db.query(Appointment).one()
        assert wa.enqueue(db, appt, "confirmation") is None                   # same event again -> no second job
    assert len(clinic["a"].get("/api/v1/whatsapp/messages").json()) == 1 and len(fake.sent) == 1


def test_retry_transient_then_permanent_and_manual_retry(clinic, fake, Session):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    fake.script = [wa.ProviderError("HTTP 503", transient=True)]
    book(clinic, clinic["p1"]["id"], "09:00")
    j = clinic["a"].get("/api/v1/whatsapp/messages").json()[0]
    assert j["status"] == "queued" and j["attempts"] == 1
    with Session() as db:                                                      # worker: make it due and process
        from app.models import NotificationJob
        from datetime import datetime, timedelta, timezone
        db.query(NotificationJob).update({"next_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        db.commit()
        assert wa.process_due(db) == 1
    j = clinic["a"].get("/api/v1/whatsapp/messages").json()[0]
    assert j["status"] == "sent" and len(fake.sent) == 2
    assert clinic["a"].post(f"/api/v1/whatsapp/messages/{j['id']}/retry").status_code == 409   # sent: not retryable
    fake.script = [wa.ProviderError("Recipient not on WhatsApp")]
    book(clinic, clinic["p1"]["id"], "09:30")
    failed = clinic["a"].get("/api/v1/whatsapp/messages", params={"status": "failed"}).json()
    assert len(failed) == 1 and "not on WhatsApp" in failed[0]["error"]
    assert clinic["a"].post(f"/api/v1/whatsapp/messages/{failed[0]['id']}/retry").json()["status"] in ("queued", "sent")
    assert clinic["a"].get("/api/v1/whatsapp/messages", params={"status": "sent"}).json()


def test_reschedule_and_cancel_messages(clinic, fake):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    a = book(clinic, clinic["p1"]["id"], "09:00").json()
    clinic["recep"].post(f"/api/v1/appointments/{a['id']}/reschedule", json={"date": DAY, "time": "10:00"})
    clinic["recep"].post(f"/api/v1/appointments/{a['id']}/status", json={"status": "cancelled"})
    assert [t["type"] for t in clinic["a"].get("/api/v1/whatsapp/messages").json()] == ["cancellation", "reschedule", "confirmation"]
    assert "cancelled" in fake.sent[-1][1]


def test_template_validation_and_custom_template(clinic, fake):
    bad = clinic["a"].put("/api/v1/whatsapp/templates/confirmation", json={"body": "Hi {patient_name} {evil_var} {__class__}"})
    assert bad.status_code == 422
    ok = clinic["a"].put("/api/v1/whatsapp/templates/confirmation", json={"body": "Hello {patient_name}, see {doctor_name}", "meta_template_name": "appt_confirm"})
    assert ok.status_code == 200
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    book(clinic, clinic["p1"]["id"], "09:00")
    to, body, tpl, params = fake.sent[0]
    assert body == "Hello Asha Verma, see Dr. Meera Rao" and tpl == "appt_confirm" and params == ["Asha Verma", "Dr. Meera Rao"]


def test_webhook_verification_and_status_updates(clinic, fake):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    book(clinic, clinic["p1"]["id"], "09:00")
    c = TestClient(app)
    ok = c.get("/api/v1/webhooks/whatsapp/city-a", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "abc"})
    assert ok.status_code == 200 and ok.text == "abc"
    assert c.get("/api/v1/webhooks/whatsapp/city-a", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "abc"}).status_code == 403

    def ev(status, mid="wamid.1"):
        return json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": mid, "status": status}]}}]}]}).encode()
    assert c.post("/api/v1/webhooks/whatsapp/city-a", content=ev("delivered"), headers={"x-hub-signature-256": "sha256=bad"}).status_code == 401
    b = ev("read")
    assert c.post("/api/v1/webhooks/whatsapp/city-a", content=b, headers=sign(b)).status_code == 200
    b = ev("delivered")
    c.post("/api/v1/webhooks/whatsapp/city-a", content=b, headers=sign(b))      # late event must not downgrade
    assert clinic["a"].get("/api/v1/whatsapp/messages").json()[0]["status"] == "read"


def test_webhook_cannot_touch_other_tenants(clinic, fake):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    book(clinic, clinic["p1"]["id"], "09:00")
    clinic["b"].put("/api/v1/whatsapp/connection", json={"phone_number_id": "55555", "access_token": "EAAG-b-token-value-1", "app_secret": SECRET, "verify_token": VERIFY})
    b = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "read"}]}}]}]}).encode()
    TestClient(app).post("/api/v1/webhooks/whatsapp/care-b", content=b, headers=sign(b))   # B's webhook, A's message id
    assert clinic["a"].get("/api/v1/whatsapp/messages").json()[0]["status"] == "sent"
    assert clinic["b"].get("/api/v1/whatsapp/messages").json() == []


def test_stop_message_opts_patient_out(clinic, fake):
    connect(clinic)
    consent(clinic, clinic["p1"]["id"])
    b = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": "919876543210", "text": {"body": " stop "}}]}}]}]}).encode()
    assert TestClient(app).post("/api/v1/webhooks/whatsapp/city-a", content=b, headers=sign(b)).status_code == 200
    book(clinic, clinic["p1"]["id"], "09:00")
    assert fake.sent == []


