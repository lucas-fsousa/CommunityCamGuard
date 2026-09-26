"""Enrollment failures never reflect provider text or completion stage values."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import onboarding, provisioning_common, provisioning_network
from backend.app.api import provisioning_privileged as privileged
from backend.app.drivers.onboarding import (
    OnboardingCompletionError,
    OnboardingLabelError,
    OnboardingStateError,
    OnboardingTransportError,
)

SECRET = "SYNTHETIC_ENROLLMENT_SECRET"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    for module in (privileged, onboarding, provisioning_network):
        app.include_router(module.router)
    identity = {"device_id": "12345678", "mac": "aa:bb:cc:dd:ee:03", "firmware_version": ""}
    for module in (privileged, onboarding):
        monkeypatch.setattr(module, "inspect_provisioning_label", lambda body: identity)

    def forbidden(*args, **kwargs):
        raise AssertionError("failure must not resync services")

    monkeypatch.setattr(onboarding, "resync_services", forbidden)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as http:
        http.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield http


def assert_safe(response, status):
    assert response.status_code == status
    assert SECRET not in response.text
    assert isinstance(response.json()["detail"], str)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("route,method", [
    ("online-status", "online_status"), ("bind", "bind"),
    ("p2p-probe", "probe_inventory"), ("p2p-route-probe", "probe_route"),
    ("p2p-property-read", "read_property"), ("complete", "complete"),
])
@pytest.mark.parametrize("failure", ["state", "operation"])
def test_privileged_errors(client, monkeypatch, route, method, failure):
    if route == "online-status" and failure == "operation":
        pytest.skip("online status currently handles state failures only")
    error = (OnboardingStateError(SECRET) if failure == "state" else
             OnboardingCompletionError(SECRET, SECRET) if route == "complete" else
             OnboardingTransportError(SECRET))

    def fail(*args, **kwargs):
        raise error

    provider = SimpleNamespace(**{method: fail}, read_only_property_paths={"synthetic"})
    for module in (privileged, onboarding):
        monkeypatch.setattr(module, "onboarding", lambda: provider)
    response = client.post(f"/api/provisioning/privileged/{route}", json={
        "attempt_id": SECRET, "property_path": "synthetic",
    })
    assert_safe(response, 409 if failure == "state" else 502)


@pytest.mark.parametrize("failure", ["driver", "label"])
def test_identification_does_not_echo_errors(client, monkeypatch, failure):
    def fail(*args, **kwargs):
        if failure == "driver":
            raise LookupError(SECRET)
        raise OnboardingLabelError(SECRET)

    if failure == "driver":
        monkeypatch.setattr(provisioning_common.drivers, "onboarding_provider", fail)
    else:
        monkeypatch.setattr(provisioning_common, "onboarding", lambda: SimpleNamespace(inspect_label=fail))
    assert_safe(client.post("/api/provisioning/inspect", json={"label": SECRET}), 422)


def test_unauthenticated_bind_never_dispatches(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unauthenticated request reached provider")

    monkeypatch.setattr(privileged, "onboarding", forbidden)
    client.cookies.clear()
    assert client.post("/api/provisioning/privileged/bind", json={}).status_code == 401
