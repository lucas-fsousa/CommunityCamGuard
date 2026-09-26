"""Driver status dictionaries cannot implicitly extend the public response."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import provisioning_privileged as routes
from backend.app.drivers.onboarding import OnboardingStateError, OnboardingTransportError

SECRET = "SYNTHETIC_STATUS_SECRET"
DEVICE = "12345678"
STATUS = {
    "device_id": DEVICE, "expires_in": 30, "handoff_ready": True, "bound": False,
    "subscription_material_ready": False, "p2p_access_ready": False, "rtsp_ready": False,
}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "inspect_provisioning_label", lambda body: {"device_id": DEVICE})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as http:
        http.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield http


def install(monkeypatch, value):
    monkeypatch.setattr(routes, "onboarding", lambda: SimpleNamespace(privileged_status=lambda device: value))


def test_only_known_status_fields_are_returned(client, monkeypatch):
    install(monkeypatch, {**STATUS, "dev_token": SECRET, "nested": {"accessToken": SECRET}})
    response = client.post("/api/provisioning/privileged/status", json={})
    assert response.status_code == 200
    assert response.json() == STATUS
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("field,value", [
    ("device_id", SECRET), ("device_id", None), ("expires_in", True),
    ("expires_in", -1), ("expires_in", 2**40), ("expires_in", SECRET),
    *[(flag, value) for flag in ("handoff_ready", "bound", "subscription_material_ready",
                               "p2p_access_ready", "rtsp_ready") for value in (1, "true", None)],
])
def test_malformed_status_is_not_coerced(client, monkeypatch, field, value):
    install(monkeypatch, {**STATUS, field: value})
    response = client.post("/api/provisioning/privileged/status", json={})
    assert response.status_code == 502
    assert response.json() == {"detail": "camera driver returned an invalid enrollment status"}
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("value", [None, [], SECRET, {}, {"device_id": DEVICE}])
def test_incomplete_or_non_object_status_fails_closed(client, monkeypatch, value):
    install(monkeypatch, value)
    response = client.post("/api/provisioning/privileged/status", json={})
    assert response.status_code == 502
    assert SECRET not in response.text


@pytest.mark.parametrize("kind,status", [(OnboardingStateError, 409), (OnboardingTransportError, 502)])
def test_status_domain_failures_are_safe(client, monkeypatch, kind, status):
    def fail(device):
        raise kind(SECRET)

    monkeypatch.setattr(routes, "onboarding", lambda: SimpleNamespace(privileged_status=fail))
    response = client.post("/api/provisioning/privileged/status", json={})
    assert response.status_code == status
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_authentication_still_precedes_dispatch(client, monkeypatch):
    def forbidden():
        raise AssertionError("unauthenticated provider dispatch")

    monkeypatch.setattr(routes, "onboarding", forbidden)
    client.cookies.clear()
    assert client.post("/api/provisioning/privileged/status", json={}).status_code == 401
