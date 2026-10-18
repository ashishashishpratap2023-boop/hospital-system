from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import hash_password
from app.deps import tenant_staff
from app.models import AuditLog, Tenant, User
from app.schemas import UserCreate, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), actor: User = Depends(tenant_staff("hospital_admin"))):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email already registered")
    tenant = db.get(Tenant, actor.tenant_id)
    count = db.scalar(select(func.count()).select_from(User).where(User.tenant_id == actor.tenant_id))
    if count >= tenant.max_users:
        raise HTTPException(409, "User limit reached for this hospital")
    u = User(tenant_id=actor.tenant_id, email=body.email.lower(), full_name=body.full_name,
             password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    db.add(AuditLog(tenant_id=actor.tenant_id, user_id=actor.id, action="user.create", entity="user", entity_id=str(u.id)))
    db.commit()
    return u


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), actor: User = Depends(tenant_staff("hospital_admin"))):
    return db.scalars(select(User).where(User.tenant_id == actor.tenant_id).order_by(User.id)).all()
