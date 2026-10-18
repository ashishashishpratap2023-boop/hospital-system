import re

from pydantic import BaseModel, EmailStr, Field, field_validator

_IN = re.compile(r"^(?:\+?91|0)?([6-9]\d{9})$")
_INTL = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_mobile(v: str) -> str:
    v = re.sub(r"[\s\-()]", "", v)
    m = _IN.match(v)
    if m:
        return "+91" + m.group(1)
    if _INTL.match(v):
        return v
    raise ValueError("Enter a valid mobile number (Indian 10-digit or +country code format)")


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    tenant_id: int | None
    model_config = {"from_attributes": True}


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    name: str = Field(min_length=2, max_length=200)
    domain: str | None = Field(default=None, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")
    admin_email: EmailStr
    admin_name: str = Field(min_length=2, max_length=200)
    admin_password: str = Field(min_length=10, max_length=200)


class TenantOut(BaseModel):
    id: int
    slug: str
    name: str
    status: str
    model_config = {"from_attributes": True}


class TenantStatusIn(BaseModel):
    status: str = Field(pattern="^(active|suspended|inactive)$")


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=10, max_length=200)
    role: str = Field(pattern="^(hospital_admin|receptionist|doctor)$")


class PatientIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    mobile: str
    email: EmailStr | None = None
    gender: str | None = Field(default=None, max_length=20)
    date_of_birth: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v):
        return normalize_mobile(v)

    @field_validator("full_name")
    @classmethod
    def _name(cls, v):
        return " ".join(v.split())


class PatientOut(BaseModel):
    id: int
    patient_code: str
    full_name: str
    mobile: str
    email: str | None
    gender: str | None
    date_of_birth: str | None
    model_config = {"from_attributes": True}
