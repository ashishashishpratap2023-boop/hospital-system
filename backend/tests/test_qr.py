import httpx
import pytest

import app.services.whatsapp as wa
from app.core.config import settings
from tests.test_appointments import book, clinic  # noqa: F401
from tests.test_whatsapp import Fake, consent  # noqa: F401


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


@pytest.fixture()
def bridge(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://bridge.local")
    state = {"status": "qr", "qr": "data:image/png;base64,AAA", "phone": None}
    calls = []

    def fake(method, path, json=None):
        calls.append((method, path))
        if method == "DELETE":
            state.update(status="disconnected", qr=None, phone=None)
        return dict(state)
    monkeypatch.setattr(wa, "bridge_call", fake)
    return state, calls


def test_qr_not_configured_is_not_faked(clinic):
    r = clinic["a"].post("/api/v1/whatsapp/qr/start")
    assert r.status_code == 503 and "not configured" in r.text
    assert clinic["a"].get("/api/v1/whatsapp/connection").json()["status"] != "connected"


def test_qr_login_flow_status_is_honest(clinic, bridge):
    state, calls = bridge
    r = clinic["a"].post("/api/v1/whatsapp/qr/start").json()
    assert r["qr"].startswith("data:image") and r["status"] == "configured" and r["method"] == "qr"   # waiting, NOT connected
    assert clinic["a"].get("/api/v1/whatsapp/qr/status").json()["status"] == "configured"
    state.update(status="connected", qr=None, phone="919800000000")                                    # owner scanned the QR
    r = clinic["a"].get("/api/v1/whatsapp/qr/status").json()
    assert r["status"] == "connected" and r["display_phone"] == "919800000000" and r["qr"] is None
    state.update(status="disconnected")                                                                # phone unlinked the device
    assert clinic["a"].get("/api/v1/whatsapp/qr/status").json()["status"] == "disconnected"
    assert clinic["recep"].post("/api/v1/whatsapp/qr/start").status_code == 403
    assert clinic["b"].get("/api/v1/whatsapp/qr/status").status_code == 409                           # B never started QR


def test_submit_booking_sends_whatsapp_via_hospital_session(clinic, bridge, monkeypatch):
    state, _ = bridge
    clinic["a"].post("/api/v1/whatsapp/qr/start")
    state.update(status="connected", qr=None, phone="919800000000")
    clinic["a"].get("/api/v1/whatsapp/qr/status")
    sent = []
    def send(to, body, tpl, params, ref=None):
        sent.append((to, body, ref)); return "BAE5ABC"
    monkeypatch.setattr(wa, "get_provider", lambda conn: type("P", (), {"send": staticmethod(send)})())
    r = book(clinic, clinic["p1"]["id"], "09:00", send_whatsapp=True)       # one submit: book + message
    assert r.status_code == 201
    (to, body, ref), = sent
    assert to == "+919876543210" and "Asha Verma" in body and "City Hospital A" in body and ref.startswith("job-")
    assert clinic["a"].get("/api/v1/whatsapp/messages").json()[0]["status"] == "sent"
    assert book(clinic, clinic["p2"]["id"], "09:30").status_code == 201     # checkbox off -> no message
    assert len(sent) == 1


def test_patient_stop_is_not_overridden_by_front_desk(clinic, bridge, monkeypatch):
    state, _ = bridge
    clinic["a"].post("/api/v1/whatsapp/qr/start")
    state.update(status="connected", qr=None, phone="1")
    clinic["a"].get("/api/v1/whatsapp/qr/status")
    f = Fake()
    monkeypatch.setattr(wa, "get_provider", lambda conn: f)
    clinic["recep"].post(f"/api/v1/whatsapp/consent/{clinic['p1']['id']}", json={"granted": False, "source": "whatsapp_stop"})
    assert book(clinic, clinic["p1"]["id"], "09:00", send_whatsapp=True).status_code == 201
    assert f.sent == []


def test_disconnect_logs_out_bridge_session(clinic, bridge):
    state, calls = bridge
    clinic["a"].post("/api/v1/whatsapp/qr/start")
    assert clinic["a"].post("/api/v1/whatsapp/disconnect").json()["status"] == "disconnected"
    assert ("DELETE", f"/sessions/{clinic['a'].get('/api/v1/auth/me').json()['tenant_id']}") in calls


@pytest.mark.parametrize("code,data,transient,auth", [(409, {}, False, True), (404, {}, False, False),
                         (429, {"error": "queue_full"}, True, False), (429, {"error": "daily_limit"}, False, False), (502, {}, True, False)])
def test_bridge_error_mapping(monkeypatch, code, data, transient, auth):
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://bridge.local")
    monkeypatch.setattr(httpx, "request", lambda *a, **k: Resp(code, data))
    with pytest.raises(wa.ProviderError) as e:
        wa.bridge_call("POST", "/sessions/1/send", {})
    assert (e.value.transient, e.value.auth) == (transient, auth)


def test_bridge_unreachable_is_transient(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://bridge.local")
    def boom(*a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "request", boom)
    with pytest.raises(wa.ProviderError) as e:
        wa.bridge_call("GET", "/sessions/1")
    assert e.value.transient
