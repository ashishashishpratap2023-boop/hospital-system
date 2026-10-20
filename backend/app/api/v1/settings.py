import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import get_db
from app.deps import tenant_staff
from app.models import AuditLog, Tenant, User

router = APIRouter(prefix="/settings", tags=["settings"])
anyone = tenant_staff("hospital_admin", "receptionist", "doctor")
admin = tenant_staff("hospital_admin")

PLAIN = ("phone", "email", "address", "wa_note")
URLS = ("maps_url", "website")
SOCIAL = ("facebook", "instagram", "youtube", "twitter", "google_reviews")


class SettingsIn(BaseModel):
    phone: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=120)
    address: str = Field(default="", max_length=300)
    wa_note: str = Field(default="", max_length=500)
    maps_url: str = Field(default="", max_length=300)
    website: str = Field(default="", max_length=300)
    facebook: str = Field(default="", max_length=300)
    instagram: str = Field(default="", max_length=300)
    youtube: str = Field(default="", max_length=300)
    twitter: str = Field(default="", max_length=300)
    google_reviews: str = Field(default="", max_length=300)

    @field_validator("maps_url", "website", "facebook", "instagram", "youtube", "twitter", "google_reviews")
    @classmethod
    def _link(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("Links must start with http:// or https://")
        return v


def _view(db: Session, tid: int):
    t = db.get(Tenant, tid)
    b = t.settings.branding or {}
    soc = b.get("social") or {}
    return {"name": t.name, "slug": t.slug, "has_logo": bool(b.get("logo_b64")), **{k: b.get(k) or "" for k in PLAIN + URLS}, "social": {k: soc.get(k) or "" for k in SOCIAL}}


@router.get("")
def get_settings(db: Session = Depends(get_db), user: User = Depends(anyone)):
    return _view(db, user.tenant_id)


@router.put("")
def save_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    t = db.get(Tenant, user.tenant_id)
    d = body.model_dump()
    b = dict(t.settings.branding or {})
    for k in PLAIN + URLS:
        b[k] = d[k].strip() or None
    b["social"] = {k: d[k].strip() for k in SOCIAL if d[k].strip()}
    t.settings.branding = b
    flag_modified(t.settings, "branding")
    db.add(AuditLog(tenant_id=t.id, user_id=user.id, action="settings.update", entity="tenant", entity_id=str(t.id)))
    db.commit()
    return _view(db, user.tenant_id)


class LogoIn(BaseModel):
    image: str = Field(max_length=450_000)


@router.put("/logo")
def save_logo(body: LogoIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    head, _, data = body.image.partition(",")
    if not head.startswith("data:image/") or ";base64" not in head:
        raise HTTPException(422, "Invalid image")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Invalid image")
    if len(raw) > 300_000:
        raise HTTPException(422, "Image too large (max 300 KB)")
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif raw[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise HTTPException(422, "Use a PNG, JPG or WEBP image")
    t = db.get(Tenant, user.tenant_id)
    b = dict(t.settings.branding or {})
    b["logo_b64"], b["logo_mime"] = base64.b64encode(raw).decode(), mime
    t.settings.branding = b
    flag_modified(t.settings, "branding")
    db.commit()
    return _view(db, user.tenant_id)


@router.delete("/logo")
def delete_logo(db: Session = Depends(get_db), user: User = Depends(admin)):
    t = db.get(Tenant, user.tenant_id)
    b = dict(t.settings.branding or {})
    b.pop("logo_b64", None)
    b.pop("logo_mime", None)
    t.settings.branding = b
    flag_modified(t.settings, "branding")
    db.commit()
    return _view(db, user.tenant_id)
