from datetime import datetime, timezone

from sqlalchemy import JSON, text, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

APPT_STATUSES = ("booked", "confirmed", "checked_in", "completed", "cancelled", "no_show")


def utcnow():
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")
    max_users: Mapped[int] = mapped_column(Integer, default=50)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settings: Mapped["TenantSettings"] = relationship(back_populates="tenant", uselist=False, cascade="all, delete-orphan")


class TenantDomain(Base):
    __tablename__ = "tenant_domains"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)


class TenantSettings(Base):
    __tablename__ = "tenant_settings"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    tenant: Mapped[Tenant] = relationship(back_populates="settings")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    name: Mapped[str] = mapped_column(String(200))
    qualification: Mapped[str | None] = mapped_column(String(200))
    specialization: Mapped[str | None] = mapped_column(String(200))
    consultation_fee: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    availability: Mapped[dict] = mapped_column(JSON, default=dict)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=15)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("tenant_id", "patient_code"), Index("ix_patients_tenant_mobile", "tenant_id", "mobile"))
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    patient_code: Mapped[str] = mapped_column(String(30))
    full_name: Mapped[str] = mapped_column(String(200))
    mobile: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    gender: Mapped[str | None] = mapped_column(String(20))
    date_of_birth: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("tenant_id", "appointment_number"), Index("ix_appt_doctor_start", "tenant_id", "doctor_id", "start_at"),
        Index("uq_appt_active_slot", "tenant_id", "doctor_id", "start_at", unique=True,
              sqlite_where=text("status NOT IN ('cancelled','no_show')"),
              postgresql_where=text("status NOT IN ('cancelled','no_show')")))
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    appointment_number: Mapped[str] = mapped_column(String(40))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="booked")
    fee: Mapped[float | None] = mapped_column(Numeric(10, 2))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    granted: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WhatsAppConnection(Base):
    __tablename__ = "whatsapp_connections"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    method: Mapped[str] = mapped_column(String(20), default="cloud_api")
    phone_number_id: Mapped[str | None] = mapped_column(String(40))
    business_account_id: Mapped[str | None] = mapped_column(String(40))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    app_secret_enc: Mapped[str | None] = mapped_column(Text)
    verify_token_enc: Mapped[str | None] = mapped_column(Text)
    template_language: Mapped[str] = mapped_column(String(10), default="en")
    status: Mapped[str] = mapped_column(String(20), default="not_configured")
    display_phone: Mapped[str | None] = mapped_column(String(40))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    meta_template_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationJob(Base):
    __tablename__ = "notification_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    params: Mapped[list] = mapped_column(JSON, default=list)
    meta_template_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued sending sent delivered read failed
    provider_message_id: Mapped[str | None] = mapped_column(String(120), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
