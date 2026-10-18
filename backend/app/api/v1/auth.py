from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import (clear_failures, create_access_token, record_failure,
                               throttled, verify_password)
from app.deps import get_current_user
from app.models import User
from app.schemas import LoginIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    key = f"{request.client.host if request.client else 'unknown'}:{body.email.lower()}"
    if throttled(key):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if not user or not user.is_active or not verify_password(user.password_hash, body.password):
        record_failure(key)
        raise HTTPException(401, "Invalid email or password")
    clear_failures(key)
    token = create_access_token(user.id, user.role, user.tenant_id)
    response.set_cookie("access_token", token, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=settings.access_token_minutes * 60, path="/")
    return user


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie("access_token", path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
