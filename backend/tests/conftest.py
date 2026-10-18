import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.core.security import hash_password, reset_throttle
from app.main import app
from app.models import User

PW = "Str0ng-Passw0rd!"


@pytest.fixture()
def Session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    import app.services.whatsapp as wa
    orig_session, wa.SessionLocal = wa.SessionLocal, S
    reset_throttle()
    with S() as db:
        db.add(User(email="root@platform.com", full_name="Root", role="super_admin", password_hash=hash_password(PW)))
        db.commit()
    yield S
    wa.SessionLocal = orig_session
    app.dependency_overrides.clear()


def login(email, pw=PW):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def world(Session):
    """Two hospitals, each with an admin, plus a receptionist and doctor in A."""
    root = login("root@platform.com")
    for slug, name, dom in (("city-a", "City Hospital A", "a.hospital.com"), ("care-b", "Care Clinic B", "b.hospital.com")):
        r = root.post("/api/v1/tenants", json={"slug": slug, "name": name, "domain": dom,
                      "admin_email": f"admin@{slug}.com", "admin_name": "Admin", "admin_password": PW})
        assert r.status_code == 201, r.text
    a = login("admin@city-a.com")
    for role in ("receptionist", "doctor"):
        assert a.post("/api/v1/users", json={"email": f"{role}@city-a.com", "full_name": role.title() + " A",
                      "password": PW, "role": role}).status_code == 201
    return {"root": root, "a": a, "b": login("admin@care-b.com"),
            "recep": login("receptionist@city-a.com"), "doc": login("doctor@city-a.com")}
