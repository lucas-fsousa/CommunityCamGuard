"""Complete delegated authentication over ASGI; no worker/camera/production state."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import access_keys, auth
from backend.app.api.access_keys import router as keys_router
from backend.app.api.auth import router as auth_router


@pytest.mark.parametrize("expiry", [None, "2036-01-01T00:00:00Z"])
def test_owner_creates_selected_grants_guest_cannot_manage_and_revocation_logs_out(expiry):
    app = FastAPI(); app.include_router(auth_router); app.include_router(keys_router)
    owner, guest = TestClient(app), TestClient(app)
    assert owner.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
    response = owner.post("/api/access-keys", json={"label": "Limited", "expires_at": expiry,
                                                   "permissions": ["intercom", "ptz"]})
    assert response.status_code == 201
    key = response.json()
    assert key["login_enabled"] is True
    assert guest.post("/api/login", json={"key": key["secret"]}).status_code == 200
    assert guest.get("/api/me").json() == {"authenticated": True, "authentication": "temporary",
                                         "can_manage": False, "permissions": ["intercom", "ptz"]}
    assert guest.get("/api/access-keys").status_code == 403
    assert guest.post("/api/access-keys", json={"label": "elevated", "expires_at": None}).status_code == 403
    token = guest.cookies.get(auth.COOKIE_NAME)
    assert auth.verify_intercom_token(token)
    assert not auth.verify_permission_token(token, "live")
    assert not auth.verify_channel_token(token)  # Never open the unrestricted go2rtc proxy.
    assert owner.post("/api/access-keys/" + key["metadata"]["id"] + "/revoke", json={}).status_code == 200
    assert guest.get("/api/me").json()["authenticated"] is False
    assert not auth.verify_intercom_token(token)
    assert guest.post("/api/login", json={"key": key["secret"]}).status_code == 401
    assert owner.get("/api/me").json()["can_manage"] is True


def test_exact_expiration_closes_authentication_even_with_valid_signed_cookie(monkeypatch):
    now = datetime(2026, 9, 27, tzinfo=UTC)
    monkeypatch.setattr(access_keys, "_now", lambda: now)
    key = access_keys.create(access_keys.CreateKey(label="Timed", expires_at=now + timedelta(minutes=1), permissions=("live",)))
    app = FastAPI(); app.include_router(auth_router)
    guest = TestClient(app)
    assert guest.post("/api/login", json={"key": key.secret}).status_code == 200
    monkeypatch.setattr(access_keys, "_now", lambda: now + timedelta(minutes=1))
    assert guest.get("/api/me").json()["authenticated"] is False
    assert not auth.verify_permission_token(guest.cookies.get(auth.COOKIE_NAME), "live")
    assert guest.post("/api/login", json={"key": key.secret}).status_code == 401
