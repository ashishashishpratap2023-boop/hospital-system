from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import hash_password
from app.deps import require_roles
from app.models import AuditLog, Tenant, TenantDomain, TenantSettings, User
from app.schemas import TenantCreate, TenantOut, TenantStatusIn

router = APIRouter(prefix="/tenants", tags=["super-admin"])
super_admin = require_roles("super_admin")

DEFAULT_BRANDING = {"primary_color": "#0f766e", "theme": "classic", "logo_url": None,
                    "phone": None, "email": None, "address": None, "maps_url": None,
                    "website": None, "social": {}}
DEFAULT_RULES = {"appointment_prefix": "APT", "timezone": "Asia/Kolkata", "slip_layout": "a4"}


@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(body: TenantCreate, db: Session = Depends(get_db), actor: User = Depends(super_admin)):
    if db.scalar(select(Tenant).where(Tenant.slug == body.slug)):
        raise HTTPException(409, "Slug already in use")
    if db.scalar(select(User).where(User.email == body.admin_email.lower())):
        raise HTTPException(409, "Admin email already registered")
    if body.domain and db.scalar(select(TenantDomain).where(TenantDomain.domain == body.domain)):
        raise HTTPException(409, "Domain already mapped to another hospital")
    t = Tenant(slug=body.slug, name=body.name)
    t.settings = TenantSettings(branding={**DEFAULT_BRANDING, "name": body.name},
                                rules=dict(DEFAULT_RULES))
    db.add(t)
    db.flush()
    if body.domain:
        db.add(TenantDomain(tenant_id=t.id, domain=body.domain))
    db.add(User(tenant_id=t.id, email=body.admin_email.lower(), full_name=body.admin_name,
                password_hash=hash_password(body.admin_password), role="hospital_admin"))
    db.add(AuditLog(tenant_id=t.id, user_id=actor.id, action="tenant.create", entity="tenant", entity_id=str(t.id)))
    db.commit()
    return t


@router.get("", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), _: User = Depends(super_admin)):
    return db.scalars(select(Tenant).order_by(Tenant.id)).all()


@router.patch("/{tenant_id}/status", response_model=TenantOut)
def set_status(tenant_id: int, body: TenantStatusIn, db: Session = Depends(get_db),
               actor: User = Depends(super_admin)):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "Hospital not found")
    t.status = body.status
    db.add(AuditLog(tenant_id=t.id, user_id=actor.id, action=f"tenant.{body.status}", entity="tenant", entity_id=str(t.id)))
    db.commit()
    return t
