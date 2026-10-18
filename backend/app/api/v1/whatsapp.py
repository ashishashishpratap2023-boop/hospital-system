import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt
from app.core.db import get_db
from app.deps import tenant_staff
from app.models import (AuditLog, ConsentRecord, NotificationJob, NotificationTemplate, Patient, Tenant, User,
                        WhatsAppConnection)
from app.services import whatsapp as wa

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
hook = APIRouter(prefix="/webhooks", tags=["webhooks"])
admin = tenant_staff("hospital_admin")
front = tenant_staff("hospital_admin", "receptionist")


class ConnIn(BaseModel):
    phone_number_id: str | None = Field(default=None, pattern=r"^\d{5,30}$")
    business_account_id: str | None = Field(default=None, pattern=r"^\d{5,30}$")
    access_token: str | None = Field(default=None, min_length=10, max_length=1000)
    app_secret: str | None = Field(default=None, min_length=8, max_length=200)
    verify_token: str | None = Field(default=None, min_length=16, max_length=200)
    template_language: str | None = Field(default=None, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")


class TemplateIn(BaseModel):
    body: str = Field(min_length=10, max_length=1500)
    meta_template_name: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,120}$")
    is_active: bool = True


class ConsentIn(BaseModel):
    granted: bool
    source: str = Field(default="front_desk", max_length=40)


def _conn(db, tid, create=False):
    c = db.get(WhatsAppConnection, tid)
    if not c and create:
        c = WhatsAppConnection(tenant_id=tid)
        db.add(c)
    return c


def _view(c):
    if not c:
        return {"method": "cloud_api", "status": "not_configured", "token_set": False}
    return {"method": c.method, "status": c.status, "phone_number_id": c.phone_number_id, "business_account_id": c.business_account_id,
            "display_phone": c.display_phone, "token_set": bool(c.access_token_enc), "app_secret_set": bool(c.app_secret_enc),
            "verify_token_set": bool(c.verify_token_enc), "template_language": c.template_language,
            "last_checked_at": c.last_checked_at, "last_error": c.last_error}


@router.get("/connection")
def get_connection(db: Session = Depends(get_db), user: User = Depends(admin)):
    return _view(_conn(db, user.tenant_id))


@router.put("/connection")
def save_connection(body: ConnIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    c = _conn(db, user.tenant_id, create=True)
    c.method = "cloud_api"
    d = body.model_dump(exclude_none=True)
    for k in ("phone_number_id", "business_account_id", "template_language"):
        if k in d:
            setattr(c, k, d[k])
    for src, dst in (("access_token", "access_token_enc"), ("app_secret", "app_secret_enc"), ("verify_token", "verify_token_enc")):
        if src in d:
            setattr(c, dst, encrypt(d[src]))
    c.status = "configured" if (c.phone_number_id and c.access_token_enc) else "not_configured"  # never "connected" until tested
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="whatsapp.configure", entity="whatsapp"))
    db.commit()
    return _view(c)


@router.post("/test")
def test_connection(db: Session = Depends(get_db), user: User = Depends(admin)):
    c = _conn(db, user.tenant_id)
    if not c or not (c.phone_number_id and c.access_token_enc):
        raise HTTPException(409, "Configure phone number ID and access token first")
    c.last_checked_at = datetime.now(timezone.utc)
    try:
        c.display_phone, c.status, c.last_error = wa.get_provider(c).check(), "connected", None
    except wa.ProviderError as e:
        c.status, c.last_error = "error", e.message[:500]
    db.commit()
    return _view(c)


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db), user: User = Depends(admin)):
    c = _conn(db, user.tenant_id)
    if c and c.method == "qr":
        try:
            wa.bridge_call("DELETE", f"/sessions/{user.tenant_id}")
        except wa.ProviderError:
            pass  # bridge unreachable: mark disconnected here anyway
    if c:
        c.access_token_enc, c.status, c.display_phone = None, "disconnected", None
        db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="whatsapp.disconnect", entity="whatsapp"))
        db.commit()
    return _view(c)


def _qr_view(c, d):
    if d.get("status") == "connected":
        c.status, c.display_phone, c.last_error = "connected", d.get("phone"), None
    elif c.status == "connected":
        c.status = "disconnected"
    c.last_checked_at = datetime.now(timezone.utc)
    return {**_view(c), "bridge_status": d.get("status"), "qr": d.get("qr")}


@router.post("/qr/start")
def qr_start(db: Session = Depends(get_db), user: User = Depends(admin)):
    """Begin QR login (unofficial, free). Returns a QR image to scan from WhatsApp > Linked devices."""
    c = _conn(db, user.tenant_id, create=True)
    try:
        d = wa.bridge_call("POST", f"/sessions/{user.tenant_id}/start")
    except wa.ProviderError as e:
        raise HTTPException(503, e.message)
    c.method = "qr"
    if c.status != "connected":
        c.status = "configured"
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="whatsapp.qr_start", entity="whatsapp"))
    out = _qr_view(c, d)
    db.commit()
    return out


@router.get("/qr/status")
def qr_status(db: Session = Depends(get_db), user: User = Depends(admin)):
    c = _conn(db, user.tenant_id)
    if not c or c.method != "qr":
        raise HTTPException(409, "QR login has not been started")
    try:
        d = wa.bridge_call("GET", f"/sessions/{user.tenant_id}")
    except wa.ProviderError as e:
        raise HTTPException(503, e.message)
    out = _qr_view(c, d)
    db.commit()
    return out


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(admin)):
    rows = {t.type: t for t in db.scalars(select(NotificationTemplate).where(NotificationTemplate.tenant_id == user.tenant_id))}
    return [{"type": k, "body": rows[k].body if k in rows else wa.DEFAULTS[k], "meta_template_name": rows[k].meta_template_name if k in rows else None,
             "is_active": rows[k].is_active if k in rows else k != "reminder", "customised": k in rows} for k in wa.TYPES]


@router.put("/templates/{type_}")
def save_template(type_: str, body: TemplateIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    if type_ not in wa.TYPES:
        raise HTTPException(404, "Unknown template type")
    bad = set(wa.ordered_vars(body.body)) - wa.VARS
    if bad:
        raise HTTPException(422, f"Unknown variables: {', '.join(sorted(bad))}")
    t = db.scalar(select(NotificationTemplate).where(NotificationTemplate.tenant_id == user.tenant_id, NotificationTemplate.type == type_))
    if not t:
        t = NotificationTemplate(tenant_id=user.tenant_id, type=type_, body=body.body)
        db.add(t)
    t.body, t.meta_template_name, t.is_active = body.body, body.meta_template_name, body.is_active
    db.commit()
    return {"type": type_, "body": t.body, "meta_template_name": t.meta_template_name, "is_active": t.is_active}


def _job(j):
    return {"id": j.id, "appointment_id": j.appointment_id, "type": j.type, "recipient": j.recipient, "status": j.status,
            "error": j.error, "attempts": j.attempts, "created_at": j.created_at, "updated_at": j.updated_at}


@router.get("/messages")
def messages(status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             db: Session = Depends(get_db), user: User = Depends(admin)):
    q = select(NotificationJob).where(NotificationJob.tenant_id == user.tenant_id)
    if status:
        q = q.where(NotificationJob.status == status)
    return [_job(j) for j in db.scalars(q.order_by(NotificationJob.id.desc()).limit(limit).offset(offset))]


@router.post("/messages/{job_id}/retry")
def retry(job_id: int, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(admin)):
    j = db.scalar(select(NotificationJob).where(NotificationJob.id == job_id, NotificationJob.tenant_id == user.tenant_id))
    if not j:
        raise HTTPException(404, "Message not found")
    if j.status != "failed":
        raise HTTPException(409, "Only failed messages can be retried")
    j.status, j.attempts, j.next_attempt_at = "queued", 0, datetime.now(timezone.utc)
    db.commit()
    background.add_task(wa.run_delivery, j.id)
    return _job(j)


@router.post("/consent/{patient_id}", status_code=201)
def record_consent(patient_id: int, body: ConsentIn, db: Session = Depends(get_db), user: User = Depends(front)):
    if not db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.tenant_id == user.tenant_id)):
        raise HTTPException(404, "Patient not found")
    db.add(ConsentRecord(tenant_id=user.tenant_id, patient_id=patient_id, granted=body.granted, source=body.source))
    db.commit()
    return {"patient_id": patient_id, "whatsapp_consent": body.granted}


# ---- Meta webhook (public URL, authenticated by verify token / HMAC signature) ----
def _tenant_conn(db, slug):
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    c = db.get(WhatsAppConnection, t.id) if t else None
    if not c:
        raise HTTPException(404, "Not found")
    return t, c


@hook.get("/whatsapp/{slug}")
def verify(slug: str, request: Request, db: Session = Depends(get_db)):
    _, c = _tenant_conn(db, slug)
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and c.verify_token_enc and hmac.compare_digest(q.get("hub.verify_token", ""), decrypt(c.verify_token_enc)):
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


RANK = {"queued": 0, "sending": 0, "sent": 1, "delivered": 2, "read": 3}


@hook.post("/whatsapp/{slug}")
async def inbound(slug: str, request: Request, db: Session = Depends(get_db)):
    t, c = _tenant_conn(db, slug)
    raw = await request.body()
    if not c.app_secret_enc:
        raise HTTPException(403, "Webhook secret not configured")
    sig = request.headers.get("x-hub-signature-256", "")
    good = "sha256=" + hmac.new(decrypt(c.app_secret_enc).encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        raise HTTPException(401, "Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Bad payload")
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            for s in v.get("statuses", []):
                j = db.scalar(select(NotificationJob).where(NotificationJob.tenant_id == t.id, NotificationJob.provider_message_id == s.get("id")))
                if not j:
                    continue
                new = s.get("status")
                if new == "failed" and RANK.get(j.status, 0) < 2:
                    j.status = "failed"
                    j.error = ((s.get("errors") or [{}])[0].get("title") or "Delivery failed")[:500]
                elif new in RANK and RANK[new] > RANK.get(j.status, 0):
                    j.status = new
                j.updated_at = datetime.now(timezone.utc)
            for m in v.get("messages", []):
                if (m.get("text", {}).get("body", "")).strip().upper() in ("STOP", "UNSUBSCRIBE"):
                    for pid in db.scalars(select(Patient.id).where(Patient.tenant_id == t.id, Patient.mobile == "+" + m.get("from", ""))):
                        db.add(ConsentRecord(tenant_id=t.id, patient_id=pid, granted=False, source="whatsapp_stop"))
    db.commit()
    return {"ok": True}
