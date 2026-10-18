import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_token
from app.models import Tenant, User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired or invalid")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "Account not available")
    if user.tenant_id is not None:
        tenant = db.get(Tenant, user.tenant_id)
        if not tenant or tenant.status != "active":
            raise HTTPException(403, "This hospital account is not active")
    return user


def require_roles(*roles: str):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You do not have permission for this action")
        return user
    return dep


def tenant_staff(*roles: str):
    """Tenant-scoped routes: tenant comes ONLY from the authenticated user,
    never from the request body, query string or URL."""
    inner = require_roles(*roles)

    def dep(user: User = Depends(inner)) -> User:
        if user.tenant_id is None:
            raise HTTPException(403, "Tenant account required")
        return user
    return dep
