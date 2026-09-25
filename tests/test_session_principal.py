"""Primary-key management gate and fail-closed legacy cookie migration."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from backend.app import auth
from backend.app.api.auth import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)

    @app.get("/ordinary", dependencies=[Depends(auth.require_auth)])
    def ordinary():
        return {"ok": True}

    @app.get("/management", dependencies=[Depends(auth.require_primary_session)])
    def management():
        return {"ok": True}

    with TestClient(app) as value:
        yield value


def test_primary_login_mints_distinct_verified_sessions(client):
    ids = []
    for _ in range(2):
        response = client.post("/api/login", json={"key": "test-secret-key"})
        assert response.status_code == 200
        token = client.cookies.get(auth.COOKIE_NAME)
        principal = auth.token_principal(token)
        assert principal.authentication == "primary" and principal.can_manage
        ids.append(principal.session_id)
        assert client.get("/ordinary").status_code == 200
        assert client.get("/management").status_code == 200
        assert client.get("/api/me").json() == {
            "authenticated": True, "authentication": "primary", "can_manage": True,
        }
        assert "sid" not in client.get("/api/me").text
        assert client.get("/api/me").headers["cache-control"] == "no-store"
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert ids[0] != ids[1]


def test_legacy_cookie_never_silently_gains_management(client):
    client.cookies.set(auth.COOKIE_NAME, auth._serializer().dumps({"ok": True}))
    assert client.get("/ordinary").status_code == 200
    assert client.get("/management").status_code == 403
    assert client.get("/api/me").json() == {
        "authenticated": True, "authentication": "legacy", "can_manage": False,
    }


@pytest.mark.parametrize("payload", [
    {}, [], "primary", None, {"ok": False}, {"ok": 1}, {"ok": True, "admin": True},
    {"v": True, "authentication": "primary", "sid": "a" * 32},
    {"v": 2, "authentication": "primary", "sid": "a" * 32},
    {"v": 1, "authentication": "temporary", "sid": "a" * 32},
    {"v": 1, "authentication": "primary", "sid": "invalid"},
    {"v": 1, "authentication": "primary", "sid": 123},
    {"v": 1, "authentication": "primary", "sid": "a" * 32, "can_manage": True},
])
def test_even_signed_unsupported_payloads_are_rejected(client, payload):
    token = auth._serializer().dumps(payload)
    assert not auth.verify_token(token)
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.get("/ordinary").status_code == 401
    assert client.get("/management").status_code == 401


@pytest.mark.parametrize("legacy", [False, True])
def test_both_cookie_formats_expire_without_renewing_on_read(client, monkeypatch, legacy):
    now = [1000000]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda _self: now[0])
    token = auth._serializer().dumps({"ok": True}) if legacy else auth.issue_token()
    client.cookies.set(auth.COOKIE_NAME, token)
    now[0] += auth.MAX_AGE + 1
    assert not auth.verify_token(token)
    assert client.get("/ordinary").status_code == 401
    assert client.get("/management").status_code == 401
    assert client.get("/api/me").json() == {
        "authenticated": False, "authentication": None, "can_manage": False,
    }


def test_untrusted_claims_do_not_grant_access(client):
    assert client.post("/api/login", json={"key": "wrong", "authentication": "primary"}).status_code == 401
    assert client.get("/management", headers={"X-Role": "primary"},
                      params={"can_manage": "true"}).status_code == 401
    client.cookies.set(auth.COOKIE_NAME, auth.issue_token() + "x")
    assert client.get("/management").status_code == 401
    assert auth.token_principal("x" * 2049) is None


def test_logout_clears_cookie_but_is_not_revocation(client):
    client.post("/api/login", json={"key": "test-secret-key"})
    captured = client.cookies.get(auth.COOKIE_NAME)
    assert client.post("/api/logout").status_code == 200
    assert client.get("/management").status_code == 401
    # Deliberately record this limitation; server-side revocation is a separate task.
    assert auth.verify_token(captured)
