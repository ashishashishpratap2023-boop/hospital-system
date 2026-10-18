import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.crypto import decrypt
from app.core.db import SessionLocal
from app.models import (Appointment, ConsentRecord, Department, Doctor, NotificationJob, NotificationTemplate,
                        Patient, Tenant, WhatsAppConnection)

log = logging.getLogger("whatsapp")
MAX_ATTEMPTS = 3
TYPES = ("confirmation", "reschedule", "cancellation", "reminder")
VARS = {"hospital_name", "patient_name", "patient_mobile", "appointment_number", "doctor_name", "department_name",
        "appointment_date", "appointment_time", "consultation_fee", "hospital_address", "hospital_maps_url",
        "hospital_website", "facebook_url", "instagram_url", "youtube_url", "linkedin_url", "hospital_phone", "hospital_email"}
_PH = re.compile(r"\{(\w+)\}")
_HEAD = ("Hospital: {hospital_name}\n\nDear {patient_name},\n\n")
_DETAILS = ("Appointment ID: {appointment_number}\nPatient Name: {patient_name}\nPatient Mobile: {patient_mobile}\n"
            "Doctor: {doctor_name}\nDepartment: {department_name}\nDate: {appointment_date}\nTime: {appointment_time}\n"
            "Consultation Fee: {consultation_fee}\n\n")
_FOOT = ("Hospital Address: {hospital_address}\nGoogle Maps: {hospital_maps_url}\nWebsite: {hospital_website}\n"
         "Facebook: {facebook_url}\nInstagram: {instagram_url}\nYouTube: {youtube_url}\nContact: {hospital_phone}\n\n"
         "Thank you for choosing {hospital_name}.")
DEFAULTS = {
    "confirmation": _HEAD + "Your appointment has been booked successfully.\n\n" + _DETAILS + _FOOT,
    "reschedule": _HEAD + "Your appointment has been rescheduled.\n\n" + _DETAILS + _FOOT,
    "cancellation": _HEAD + "Your appointment {appointment_number} on {appointment_date} at {appointment_time} has been cancelled.\n\n"
                    "Contact: {hospital_phone}\n\nThank you for choosing {hospital_name}.",
    "reminder": _HEAD + "This is a reminder for your appointment.\n\n" + _DETAILS + "Hospital Address: {hospital_address}\nGoogle Maps: {hospital_maps_url}\nContact: {hospital_phone}",
}


class ProviderError(Exception):
    def __init__(self, message, transient=False, auth=False):
        super().__init__(message)
        self.message, self.transient, self.auth = message, transient, auth


class CloudAPIProvider:
    base = "https://graph.facebook.com/v20.0"

    def __init__(self, conn: WhatsAppConnection):
        self.pid, self.lang = conn.phone_number_id, conn.template_language
        self.h = {"Authorization": f"Bearer {decrypt(conn.access_token_enc)}"}

    def _call(self, method, url, **kw):
        try:
            r = httpx.request(method, url, headers=self.h, timeout=15, **kw)
        except httpx.HTTPError as e:
            raise ProviderError(f"Network error: {e.__class__.__name__}", transient=True)
        if r.status_code == 200:
            return r.json()
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = f"HTTP {r.status_code}"
        raise ProviderError(msg, transient=r.status_code >= 500 or r.status_code == 429, auth=r.status_code == 401)

    def check(self) -> str:
        d = self._call("GET", f"{self.base}/{self.pid}", params={"fields": "display_phone_number"})
        return d.get("display_phone_number", "")

    def send(self, to, body, template_name, params, ref=None) -> str:
        payload = {"messaging_product": "whatsapp", "to": to.lstrip("+")}
        if template_name:  # business-initiated messages require an approved template
            payload |= {"type": "template", "template": {"name": template_name, "language": {"code": self.lang},
                        "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]}}
        else:  # free text only works inside the 24h customer-service window
            payload |= {"type": "text", "text": {"body": body}}
        return self._call("POST", f"{self.base}/{self.pid}/messages", json=payload)["messages"][0]["id"]


def bridge_call(method, path, json=None):
    """Talk to the local QR bridge (whatsapp-qr/). Errors are mapped to ProviderError; nothing is ever faked."""
    if not settings.whatsapp_bridge_url:
        raise ProviderError("QR bridge is not configured on this server")
    try:
        r = httpx.request(method, settings.whatsapp_bridge_url.rstrip("/") + path, json=json, timeout=95,
                          headers={"x-bridge-secret": settings.bridge_secret})
    except httpx.HTTPError:
        raise ProviderError("WhatsApp bridge is not reachable", transient=True)
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code == 200:
        return data
    err = data.get("error", "")
    if r.status_code == 409:
        raise ProviderError("WhatsApp session is disconnected - scan the QR code again", auth=True)
    if r.status_code == 404:
        raise ProviderError("This number is not on WhatsApp")
    if r.status_code == 429:
        raise ProviderError("Daily sending limit reached" if err == "daily_limit" else "Sender busy, retrying", transient=err != "daily_limit")
    raise ProviderError(f"Bridge error ({r.status_code})", transient=r.status_code >= 500)


class QRBridgeProvider:
    """Sends through the hospital's own QR-linked WhatsApp session (unofficial, free)."""
    def __init__(self, tenant_id):
        self.tid = tenant_id

    def check(self) -> str:
        d = bridge_call("GET", f"/sessions/{self.tid}")
        if d.get("status") != "connected":
            raise ProviderError("Scan the QR code to connect", auth=True)
        return d.get("phone") or ""

    def send(self, to, body, template_name, params, ref=None) -> str:
        return bridge_call("POST", f"/sessions/{self.tid}/send", {"to": to, "text": body, "ref": ref})["message_id"]


def get_provider(conn):
    return QRBridgeProvider(conn.tenant_id) if conn.method == "qr" else CloudAPIProvider(conn)


def grant_front_desk_consent(db, tenant_id, patient_id) -> bool:
    """Receptionist confirms the patient agreed to WhatsApp messages. A patient's own STOP is never overridden."""
    last = db.scalar(select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id, ConsentRecord.patient_id == patient_id,
                     ConsentRecord.channel == "whatsapp").order_by(ConsentRecord.id.desc()).limit(1))
    if last and not last.granted and last.source == "whatsapp_stop":
        return False
    if not (last and last.granted):
        db.add(ConsentRecord(tenant_id=tenant_id, patient_id=patient_id, granted=True, source="front_desk_booking"))
        db.commit()
    return True


def ordered_vars(text):
    seen = []
    for v in _PH.findall(text):
        if v not in seen:
            seen.append(v)
    return seen


def render(text, ctx):
    """Safe substitution: only whitelisted {vars}; a line whose variables are empty is dropped entirely."""
    out = []
    for line in text.split("\n"):
        names = _PH.findall(line)
        if names and any(not ctx.get(n) for n in names):
            continue
        out.append(_PH.sub(lambda m: str(ctx.get(m.group(1), "")), line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def build_context(db, appt):
    t = db.get(Tenant, appt.tenant_id)
    b, rules = t.settings.branding or {}, t.settings.rules or {}
    soc = b.get("social") or {}
    p, d = db.get(Patient, appt.patient_id), db.get(Doctor, appt.doctor_id)
    dep = db.get(Department, d.department_id) if d.department_id else None
    local = _utc(appt.start_at).astimezone(ZoneInfo(rules.get("timezone", "Asia/Kolkata")))
    fee = float(appt.fee or 0)
    return {"hospital_name": t.name, "patient_name": p.full_name, "patient_mobile": p.mobile,
            "appointment_number": appt.appointment_number, "doctor_name": d.name, "department_name": dep.name if dep else "",
            "appointment_date": local.strftime("%d %b %Y"), "appointment_time": local.strftime("%I:%M %p"),
            "consultation_fee": f"Rs. {fee:g}" if fee > 0 else "", "hospital_address": b.get("address") or "",
            "hospital_maps_url": b.get("maps_url") or "", "hospital_website": b.get("website") or "",
            "facebook_url": soc.get("facebook") or "", "instagram_url": soc.get("instagram") or "",
            "youtube_url": soc.get("youtube") or "", "linkedin_url": soc.get("linkedin") or "",
            "hospital_phone": b.get("phone") or "", "hospital_email": b.get("email") or ""}


def has_consent(db, tenant_id, patient_id) -> bool:
    last = db.scalar(select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id, ConsentRecord.patient_id == patient_id,
                                                 ConsentRecord.channel == "whatsapp").order_by(ConsentRecord.id.desc()).limit(1))
    return bool(last and last.granted)


def enqueue(db, appt, type_):
    """Call only AFTER the appointment is committed. Returns job id or None (not connected / no consent / duplicate)."""
    conn = db.get(WhatsAppConnection, appt.tenant_id)
    if not conn or conn.status != "connected" or not has_consent(db, appt.tenant_id, appt.patient_id):
        return None
    tpl = db.scalar(select(NotificationTemplate).where(NotificationTemplate.tenant_id == appt.tenant_id, NotificationTemplate.type == type_))
    if tpl and not tpl.is_active:
        return None
    text = tpl.body if tpl else DEFAULTS[type_]
    ctx = build_context(db, appt)
    job = NotificationJob(tenant_id=appt.tenant_id, appointment_id=appt.id, type=type_, recipient=ctx["patient_mobile"],
                          body=render(text, ctx), params=[ctx.get(v) or "-" for v in ordered_vars(text)],
                          meta_template_name=tpl.meta_template_name if tpl else None,
                          idempotency_key=f"{type_}:{appt.id}:{_utc(appt.start_at).isoformat()}")
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return job.id


def deliver(db, job_id):
    claimed = db.execute(update(NotificationJob).where(NotificationJob.id == job_id, NotificationJob.status == "queued")
                         .values(status="sending"))
    db.commit()
    if claimed.rowcount == 0:
        return  # someone else is sending it, or it is already done: never send twice
    job = db.get(NotificationJob, job_id)
    conn = db.get(WhatsAppConnection, job.tenant_id)
    now = datetime.now(timezone.utc)
    job.updated_at = now
    appt = db.get(Appointment, job.appointment_id)
    if not conn or conn.status != "connected":
        job.status, job.error = "failed", "WhatsApp is not connected"
    elif not has_consent(db, job.tenant_id, appt.patient_id):
        job.status, job.error = "failed", "Patient has not given (or has withdrawn) WhatsApp consent"
    else:
        job.attempts += 1
        try:
            job.provider_message_id = get_provider(conn).send(job.recipient, job.body, job.meta_template_name, job.params, ref=f"job-{job.id}")
            job.status, job.error = "sent", None
        except ProviderError as e:
            job.error = e.message[:500]
            if e.auth:
                conn.status, conn.last_error = "error", e.message[:500]
            if e.transient and job.attempts < MAX_ATTEMPTS:
                job.status, job.next_attempt_at = "queued", now + timedelta(minutes=2 ** job.attempts)
            else:
                job.status = "failed"
    db.commit()


def run_delivery(job_id):
    with SessionLocal() as db:
        deliver(db, job_id)


def process_due(db):
    ids = db.scalars(select(NotificationJob.id).where(NotificationJob.status == "queued",
                     NotificationJob.next_attempt_at <= datetime.now(timezone.utc))).all()
    for i in ids:
        deliver(db, i)
    return len(ids)
