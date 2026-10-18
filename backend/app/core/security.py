import time
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user_id: int, role: str, tenant_id: int | None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "role": role, "tid": tenant_id, "exp": exp},
                      settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


# In-memory login throttle (single process). Use Redis when running multiple workers.
_fails: dict[str, list[float]] = {}
MAX_FAILS, WINDOW = 5, 900


def throttled(key: str) -> bool:
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < WINDOW]
    return len(_fails[key]) >= MAX_FAILS


def record_failure(key: str) -> None:
    _fails.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    _fails.pop(key, None)


def reset_throttle() -> None:
    _fails.clear()
