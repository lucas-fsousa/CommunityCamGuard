"""Management HTTP contract, isolated DB and no production credential/camera access."""

import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import access_keys, auth
from backend.app.api.access_keys import router
from backend.app.api.auth import router as auth_router
from backend.app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(access_keys, "_now", lambda: datetime(2026, 9, 24, 12, tzinfo=UTC))
    app = FastAPI()
    app.include_router(router)
    app.include_router(auth_router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield client


def body():
    return {"label": "Guest", "expires_at": "2026-09-25T09:00:00-03:00"}


def check(response, code):
    assert response.status_code == code, response.status_code
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_create_list_revoke_and_login_still_disabled(client):
    result = check(client.post("/api/access-keys", json=body()), 201)
    assert result["login_enabled"] is False
    secret, key_id = result["secret"], result["metadata"]["id"]
    assert result["metadata"]["expires_at"] == "2026-09-25T12:00:00Z"
    assert "verifier" not in str(result)
    listed = check(client.get("/api/access-keys"), 200)
    assert listed == {"items": [result["metadata"]], "limit": 50, "offset": 0, "login_enabled": False}
    assert secret not in str(listed)
    assert secret.encode() not in get_settings().db_path.read_bytes()
    assert client.post("/api/login", json={"key": secret}).status_code == 401
    revoked = check(client.post(f"/api/access-keys/{key_id}/revoke", json={}), 200)
    assert revoked["status"] == "revoked"
    assert check(client.post(f"/api/access-keys/{key_id}/revoke", json={}), 200) == revoked
    assert access_keys.authenticate(secret) is None


@pytest.mark.parametrize("kind,status", [("missing", 401), ("invalid", 401), ("legacy", 403), ("temporary", 401)])
def test_every_operation_requires_primary_before_storage(client, monkeypatch, kind, status):
    client.cookies.clear()
    if kind == "legacy":
        client.cookies.set(auth.COOKIE_NAME, auth._serializer().dumps({"ok": True}))
    elif kind == "temporary":
        client.cookies.set(auth.COOKIE_NAME, auth._serializer().dumps(
            {"v": 1, "authentication": "temporary", "sid": "a" * 32, "key_id": "b" * 32}))
    elif kind == "invalid":
        client.cookies.set(auth.COOKIE_NAME, "invalid")
    def forbidden(*args, **kwargs):
        raise AssertionError("Unauthorized request reached storage")
    for name in ("list_keys", "create", "revoke"):
        monkeypatch.setattr(access_keys, name, forbidden)
    check(client.get("/api/access-keys"), status)
    check(client.post("/api/access-keys", json=body()), status)
    check(client.post("/api/access-keys/" + "a" * 32 + "/revoke", json={}), status)


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "https://attacker.invalid"}, 403),
    ({"Origin": "null"}, 403),
    ({"Origin": "http://testserver.evil"}, 403),
    ({"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"}, 403),
    ({"Origin": "http://testserver", "Content-Type": "text/plain"}, 415),
    ({"Origin": "http://testserver"}, 201),
])
def test_write_origin_and_content_type(client, headers, status):
    check(client.post("/api/access-keys", json=body(), headers=headers), status)
    assert len(access_keys.list_keys()) == (1 if status == 201 else 0)


def test_revoke_enforces_write_boundary(client):
    created = check(client.post("/api/access-keys", json=body()), 201)
    path = "/api/access-keys/" + created["metadata"]["id"] + "/revoke"
    check(client.post(path, json={}, headers={"Origin": "https://attacker.invalid"}), 403)
    check(client.post(path, content="{}", headers={"Content-Type": "text/plain"}), 403)
    check(client.post(path, content="{}", headers={"Content-Type": "text/plain", "Origin": "http://testserver"}), 415)
    assert access_keys.active_key(created["metadata"]["id"]) is not None


@pytest.mark.parametrize("invalid", [
    {}, {"label": "Guest"}, {"label": "", "expires_at": "2026-09-25T12:00:00Z"},
    {"label": "Guest", "expires_at": "2026-09-24T12:00:00Z"},
    {"label": "Guest", "expires_at": "2026-09-23T12:00:00Z"},
    {"label": "Guest", "expires_at": "2026-09-25T12:00:00"},
    {"label": "Guest", "expires_at": 9999999999},
    {"label": "Guest", "expires_at": "9999-12-31T23:59:59-03:00"},
    {"label": "Guest", "expires_at": "2026-09-25T12:00:00Z", "secret": "never-echo-this"},
])
def test_invalid_create_never_writes_or_echoes_inputs(client, invalid):
    response = client.post("/api/access-keys", json=invalid)
    check(response, 422)
    assert "never-echo-this" not in response.text
    assert access_keys.list_keys() == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=true", "offset=-1", "offset=1.5"])
def test_invalid_pagination(client, query):
    check(client.get("/api/access-keys?" + query), 422)


def test_pagination_and_expired_metadata(client, monkeypatch):
    for _ in range(3):
        check(client.post("/api/access-keys", json=body()), 201)
    first = check(client.get("/api/access-keys?limit=2"), 200)["items"]
    second = check(client.get("/api/access-keys?limit=2&offset=2"), 200)["items"]
    assert len(first) == 2 and len(second) == 1
    assert not {x["id"] for x in first} & {x["id"] for x in second}
    monkeypatch.setattr(access_keys, "_now", lambda: datetime(2026, 9, 26, tzinfo=UTC))
    assert all(x["status"] == "expired" for x in check(client.get("/api/access-keys"), 200)["items"])


def test_unknown_id_and_strict_revoke_body(client):
    path = "/api/access-keys/" + "a" * 32 + "/revoke"
    check(client.post(path, json={}), 404)
    check(client.post(path, json={"revoked": False}), 422)
    check(client.post("/api/access-keys/not-an-id/revoke", json={}), 422)


def test_storage_failures_are_sanitized_and_uncacheable(client, monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("sensitive-internal-db-path")
    for name in ("list_keys", "create", "revoke"):
        monkeypatch.setattr(access_keys, name, broken)
    responses = [client.get("/api/access-keys"), client.post("/api/access-keys", json=body()),
                 client.post("/api/access-keys/" + "a" * 32 + "/revoke", json={})]
    for response in responses:
        check(response, 503)
        assert "sensitive-internal" not in response.text


def test_mounted_origin_ignores_root_path_and_forwarded_headers(client):
    app = FastAPI(root_path="/nvr")
    app.include_router(router)
    with TestClient(app) as mounted:
        mounted.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        check(mounted.post("/api/access-keys", json=body(), headers={"Origin": "http://testserver"}), 201)
        check(mounted.post("/api/access-keys", json=body(), headers={
            "Origin": "https://external.invalid", "X-Forwarded-Host": "external.invalid",
            "X-Forwarded-Proto": "https"}), 403)


def test_real_app_registers_routes_without_starting_services():
    from backend.app.main import app
    # No lifespan: no recording, media or camera workers are started.
    client = TestClient(app)
    assert client.get("/api/access-keys").status_code == 401
    client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
    check(client.get("/api/access-keys"), 200)


def test_expired_primary_session_cannot_manage(client, monkeypatch):
    from itsdangerous import TimestampSigner
    original = TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: original(self) + auth.MAX_AGE + 1)
    check(client.get("/api/access-keys"), 401)
