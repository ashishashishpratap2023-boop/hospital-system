from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import PW

P = {"full_name": "Asha Verma", "mobile": "98765 43210"}


def test_login_wrong_password_and_throttle(Session):
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/v1/auth/login", json={"email": "root@platform.com", "password": "bad"}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"email": "root@platform.com", "password": PW}).status_code == 429


def test_login_sets_httponly_cookie_and_logout(Session):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": "root@platform.com", "password": PW})
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/v1/auth/me").status_code == 200
    c.post("/api/v1/auth/logout")
    assert c.get("/api/v1/auth/me").status_code == 401


def test_unauthenticated_access_rejected(Session):
    c = TestClient(app)
    for path in ("/api/v1/patients", "/api/v1/users", "/api/v1/tenants"):
        assert c.get(path).status_code == 401


def test_role_permissions(world):
    w = world
    body = {"slug": "x-clinic", "name": "X Clinic", "admin_email": "x@x-clinic.com", "admin_name": "X", "admin_password": PW}
    assert w["a"].post("/api/v1/tenants", json=body).status_code == 403          # hospital admin
    assert w["recep"].post("/api/v1/tenants", json=body).status_code == 403      # receptionist
    assert w["recep"].post("/api/v1/users", json={"email": "n@city-a.com", "full_name": "New", "password": PW, "role": "doctor"}).status_code == 403
    assert w["doc"].post("/api/v1/patients", json=P).status_code == 403          # doctor cannot register
    assert w["root"].get("/api/v1/patients").status_code == 403                  # super admin has no patient access
    assert w["recep"].post("/api/v1/patients", json=P).status_code == 201


def test_cannot_create_super_admin_via_user_api(world):
    r = world["a"].post("/api/v1/users", json={"email": "s@city-a.com", "full_name": "Sneaky", "password": PW, "role": "super_admin"})
    assert r.status_code == 422


def test_cross_tenant_isolation(world):
    w = world
    pid = w["a"].post("/api/v1/patients", json=P).json()["id"]
    assert w["a"].get(f"/api/v1/patients/{pid}").status_code == 200
    assert w["b"].get(f"/api/v1/patients/{pid}").status_code == 404   # IDOR attempt
    assert w["b"].get("/api/v1/patients").json() == []
    assert w["b"].get("/api/v1/patients", params={"q": "9876543210"}).json() == []
    assert w["b"].get("/api/v1/patients", params={"q": "Asha"}).json() == []
    # B's own record with same mobile does not collide with A's
    assert w["b"].post("/api/v1/patients", json=P).status_code == 201
    assert len(w["a"].get("/api/v1/users").json()) == 3 and len(w["b"].get("/api/v1/users").json()) == 1


def test_tenant_id_in_body_is_ignored(world):
    r = world["b"].post("/api/v1/patients", json={**P, "tenant_id": 1})
    assert r.status_code == 201
    assert world["a"].get("/api/v1/patients").json() == []


def test_suspended_tenant_is_blocked(world):
    tid = next(t["id"] for t in world["root"].get("/api/v1/tenants").json() if t["slug"] == "city-a")
    assert world["root"].patch(f"/api/v1/tenants/{tid}/status", json={"status": "suspended"}).status_code == 200
    assert world["a"].get("/api/v1/patients").status_code == 403
    assert world["b"].get("/api/v1/patients").status_code == 200


def test_patient_validation_and_duplicates(world):
    r = world["recep"].post("/api/v1/patients", json=P)
    assert r.json()["mobile"] == "+919876543210" and r.json()["patient_code"] == "PT-000001"
    assert world["recep"].post("/api/v1/patients", json={**P, "full_name": "asha  verma"}).status_code == 409
    assert world["recep"].post("/api/v1/patients", json={**P, "mobile": "12345"}).status_code == 422
    assert world["recep"].post("/api/v1/patients", json={"full_name": "Ravi", "mobile": "+14155550123"}).status_code == 201
    assert world["recep"].get("/api/v1/patients", params={"q": "%"}).json() == []   # wildcard is escaped


def test_public_config_exposes_branding_only(world):
    c = TestClient(app)
    r = c.get("/api/v1/public/config", params={"slug": "city-a"})
    assert r.status_code == 200 and r.json()["name"] == "City Hospital A"
    assert "password" not in r.text and "admin@" not in r.text
    assert c.get("/api/v1/public/config", headers={"host": "b.hospital.com"}).json()["slug"] == "care-b"
    assert c.get("/api/v1/public/config", params={"slug": "nope"}).status_code == 404
