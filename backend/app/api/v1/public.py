from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Tenant, TenantDomain

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/config")
def public_config(request: Request, slug: str | None = None, db: Session = Depends(get_db)):
    """Branding only. Resolves tenant by slug or by the request's Host header (custom domain)."""
    tenant = None
    if slug:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    else:
        host = request.headers.get("host", "").split(":")[0].lower()
        d = db.scalar(select(TenantDomain).where(TenantDomain.domain == host))
        tenant = db.get(Tenant, d.tenant_id) if d else None
    if not tenant or tenant.status != "active":
        raise HTTPException(404, "Hospital not found")
    return {"slug": tenant.slug, "name": tenant.name, "branding": tenant.settings.branding,
            "rules": {"appointment_prefix": tenant.settings.rules.get("appointment_prefix")}}
