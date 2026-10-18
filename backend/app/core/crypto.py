import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings

_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest()))


def encrypt(v: str) -> str:
    return _f.encrypt(v.encode()).decode()


def decrypt(v: str) -> str:
    return _f.decrypt(v.encode()).decode()
