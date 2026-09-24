"""Staged sessions: fresh persistent validity, limited HTTP policy, no login/stream activation."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from backend.app import access_keys, auth
from backend.app.api.access_keys import router as keys_router
from backend.app.api.auth import router as auth_router
from backend.app.api.settings import router as settings_router
from backend.app.session_permissions import temporary_http_allowed


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 9, 24, 12, tzinfo=UTC)]
    monkeypatch.setattr(access_keys, "_now", lambda: now[0])
    return now


def issue(clock):
    key = access_keys.create(access_keys.CreateKey(label="Guest", expires_at=clock[0] + timedelta(days=10)))
    token = auth.issue_temporary_token(key.secret)
    assert token is not None
    return key, token


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(keys_router)
    app.include_router(settings_router)
    for method, path in [("GET", "/api/cameras"), ("POST", "/api/cameras"),
                         ("GET", "/api/recordings/file"), ("GET", "/api/recordings/download"),
                         ("GET", "/api/new-feature"), ("DELETE", "/api/cameras/{camera_id}"),
                         ("POST", "/api/provisioning/start")]:
        def route():
            return {"ok": True}
        app.add_api_route(path, route, methods=[method], dependencies=[Depends(auth.require_auth)])
    with TestClient(app) as value:
        yield value


def test_credentials_required_and_cookie_contains_no_secret(clock):
    key, token = issue(clock)
    second = auth.issue_temporary_token(key.secret)
    assert second and token != second
    assert auth.issue_temporary_token(key.metadata.id) is None
    assert auth.issue_temporary_token("invalid") is None
    payload = auth._serializer().loads(token)
    assert set(payload) == {"v", "authentication", "sid", "key_id"}
    assert key.secret not in str(payload)
    principal = auth.token_principal(token)
    assert principal.authentication == "temporary" and not principal.can_manage
    assert principal.key_id == key.metadata.id
    assert auth.verify_token(token)
    assert not auth.verify_channel_token(token)  # Independent transports not ready.


def test_revocation_invalidates_all_sessions_without_affecting_primary(clock, client):
    key, first = issue(clock)
    second = auth.issue_temporary_token(key.secret)
    _other, other_token = issue(clock)
    primary = auth.issue_token()
    client.cookies.set(auth.COOKIE_NAME, first)
    assert client.get("/api/cameras").status_code == 200
    assert client.get("/api/me").json() == {
        "authenticated": True, "authentication": "temporary", "can_manage": False,
    }
    access_keys.revoke(key.metadata.id)
    for token in (first, second):
        assert not auth.verify_token(token)
        client.cookies.set(auth.COOKIE_NAME, token)
        assert client.get("/api/cameras").status_code == 401
        assert client.get("/api/me").json()["authenticated"] is False
    assert auth.verify_token(primary) and auth.verify_token(other_token)
    assert auth.issue_temporary_token(key.secret) is None


def test_exact_key_expiry_and_seven_day_cookie_ceiling(clock, monkeypatch):
    key, token = issue(clock)
    clock[0] = key.metadata.expires_at
    assert not auth.verify_token(token)
    clock[0] -= timedelta(days=10)
    signed_now = [1000000]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda _: signed_now[0])
    token = auth.issue_temporary_token(key.secret)
    signed_now[0] += auth.MAX_AGE + 1
    assert access_keys.active_key(key.metadata.id) is not None
    assert not auth.verify_token(token)


def test_database_outage_fails_closed_and_primary_never_needs_key_store(clock, monkeypatch):
    key, token = issue(clock)
    primary = auth.issue_token()
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("sensitive storage detail")
    monkeypatch.setattr(access_keys.repository, "get", unavailable)
    assert auth.token_principal(token) is None
    assert auth.issue_temporary_token(key.secret) is None
    assert auth.token_principal(primary).can_manage
    assert auth.verify_channel_token(primary)


@pytest.mark.parametrize("change", [
    {"key_id": "invalid"}, {"key_id": "0" * 32}, {"key_id": []},
    {"v": True}, {"v": 2}, {"sid": []}, {"sid": "invalid"},
    {"authentication": []}, {"authentication": {}}, {"can_manage": True},
    {"authentication": "primary"},
])
def test_malformed_or_elevated_signed_claims_fail(clock, change):
    _key, token = issue(clock)
    payload = auth._serializer().loads(token)
    payload.update(change)
    assert not auth.verify_token(auth._serializer().dumps(payload))


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/cameras"), ("DELETE", "/api/cameras/test"),
    ("GET", "/api/recordings/file"), ("GET", "/api/recordings/download"),
    ("GET", "/api/new-feature"), ("POST", "/api/provisioning/start"),
    ("GET", "/api/settings"), ("GET", "/api/access-keys"),
    ("POST", "/api/access-keys"), ("POST", "/api/access-keys/" + "a" * 32 + "/revoke"),
])
def test_real_temporary_session_cannot_manage_or_reach_uncovered_operations(clock, client, method, path):
    key, token = issue(clock)
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.request(method, path, json={}).status_code == 403
    assert access_keys.active_key(key.metadata.id) is not None


@pytest.mark.parametrize("method,path", [("HEAD", "/api/cameras"), ("POST", "/api/cameras"),
                                       ("GET", "/api/cameras/extra"), ("GET", None)])
def test_policy_uses_exact_method_and_route_template(method, path):
    assert not temporary_http_allowed(method, path)


def test_primary_permissions_and_login_remain_unchanged(clock, client):
    key, _token = issue(clock)
    assert client.post("/api/login", json={"key": key.secret}).status_code == 401
    assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
    assert client.post("/api/cameras").status_code == 200
    assert client.get("/api/new-feature").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_revocation_during_issuance_cannot_leave_valid_cookie(clock, monkeypatch):
    key, _ = issue(clock)
    original = access_keys.authenticate
    def racing(secret):
        metadata = original(secret)
        access_keys.revoke(key.metadata.id)
        return metadata
    monkeypatch.setattr(access_keys, "authenticate", racing)
    token = auth.issue_temporary_token(key.secret)
    assert token is not None and not auth.verify_token(token)


def test_real_app_denies_uncovered_routes_and_websockets_before_work(clock):
    from starlette.websockets import WebSocketDisconnect

    from backend.app.main import app
    _key, token = issue(clock)
    client = TestClient(app)  # No lifespan/workers or camera operations.
    client.cookies.set(auth.COOKIE_NAME, token)
    for method, path in [
        ("POST", "/api/cameras"), ("POST", "/api/media/restart"),
        ("POST", "/api/discovery/scan"), ("GET", "/api/provisioning/status"),
        ("GET", "/api/recordings/download?path=never-read.mp4"),
        ("GET", "/api/recordings/file?path=never-read.mp4"),
        ("POST", "/api/internal/diagnostics/native-av"),
    ]:
        assert client.request(method, path, json={}).status_code == 403
    for url in ("/api/go2rtc/ws?src=never-opened", "/api/cameras/cam_" + "a" * 24 + "/intercom/stream"):
        with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(url):
            raise AssertionError("Temporary transport was accepted")
        assert caught.value.code == 1008
