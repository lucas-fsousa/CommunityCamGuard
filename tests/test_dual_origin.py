"""Pinned HTTPS proxy plus opt-in direct LAN, without production credentials."""

from http.cookies import SimpleCookie

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection

from backend.app import access_keys, auth, config
from backend.app.api.access_keys import router as keys_router
from backend.app.api.auth import router
from backend.app.origin_policy import browser_origin_allowed, secure_cookie


@pytest.fixture(autouse=True)
def dual_origin(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", "https://cameras.example.org")
    monkeypatch.setenv("DASHBOARD_ALLOW_LOCAL_ORIGIN", "true")
    config.get_settings.cache_clear()


@pytest.mark.parametrize("base,peer,origin,secure", [
    ("http://cameras.example.org", "127.0.0.1", "https://cameras.example.org", True),
    ("http://localhost:3200", "127.0.0.1", "http://localhost:3200", False),
    ("http://127.0.0.1:3200", "127.0.0.1", "http://127.0.0.1:3200", False),
    ("http://192.168.1.20:3200", "192.168.1.30", "http://192.168.1.20:3200", False),
    ("https://192.168.1.20:3200", "192.168.1.30", "https://192.168.1.20:3200", True),
])
def test_temporary_login_cookie_permissions_and_revocation(base, peer, origin, secure):
    issued = access_keys.create(access_keys.CreateKey(label="Test", expires_at=None, permissions=("live",)))
    app = FastAPI(); app.include_router(router); app.include_router(keys_router)
    with TestClient(app, base_url=base, client=(peer, 1234)) as client:
        headers = {"Origin": origin, "Sec-Fetch-Site": "same-origin"}
        response = client.post("/api/login", json={"key": issued.secret}, headers=headers)
        assert response.status_code == 200
        cookie = SimpleCookie(response.headers["set-cookie"])[auth.COOKIE_NAME]
        assert bool(cookie["secure"]) is secure
        assert cookie["httponly"] and not cookie["domain"]
        # Simulate TLS termination forwarding the browser's Secure cookie over HTTP.
        headers["Cookie"] = f"{auth.COOKIE_NAME}={cookie.value}"
        assert client.get("/api/me", headers=headers).json() == {
            "authenticated": True, "authentication": "temporary", "can_manage": False,
            "permissions": ["live"],
        }
        assert client.get("/api/access-keys", headers=headers).status_code == 403
        access_keys.revoke(issued.metadata.id)
        assert client.get("/api/me", headers=headers).json()["authenticated"] is False


@pytest.mark.parametrize("host,peer,extra", [
    ("other.example.org", "127.0.0.1", {}),
    ("localhost:3200", "192.168.1.10", {}),
    ("127.0.0.1:3200", "203.0.113.4", {}),
    ("192.168.1.20:3200", "203.0.113.4", {}),
    ("100.64.0.1:3200", "192.168.1.10", {}),
    ("169.254.1.2:3200", "192.168.1.10", {}),
    ("192.168.1.20:3200", "192.168.1.10", {"Origin": "https://cameras.example.org"}),
    ("192.168.1.20:3200", "192.168.1.10", {"Origin": "http://192.168.1.20:9999"}),
    ("192.168.1.20:3200", "192.168.1.10", {"Sec-Fetch-Site": "cross-site"}),
    ("192.168.1.20:3200", "192.168.1.10", {"Sec-Fetch-Site": "same-site"}),
])
def test_exception_does_not_allow_remote_or_cross_origin_requests(host, peer, extra):
    app = FastAPI(); app.include_router(router)
    with TestClient(app, base_url=f"http://{host}", client=(peer, 1234)) as client:
        response = client.post("/api/login", json={}, headers={"Origin": f"http://{host}", **extra})
        assert response.status_code == 403
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("name", ["forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip",
                                  "true-client-ip", "x-forwarded-host", "x-forwarded-proto",
                                  "x-forwarded-port"])
def test_even_empty_forwarding_headers_cannot_use_local_exception(name):
    app = FastAPI(); app.include_router(router)
    with TestClient(app, base_url="http://localhost:3200", client=("127.0.0.1", 1234)) as client:
        assert client.post("/api/login", json={}, headers={name: ""}).status_code == 403


@pytest.mark.parametrize("scheme,origin", [("ws", "http://localhost:3200"),
                                         ("wss", "https://localhost:3200")])
def test_websocket_policy_uses_direct_scheme(scheme, origin):
    connection = HTTPConnection({"type": "websocket", "scheme": scheme, "path": "/",
        "client": ("127.0.0.1", 1234), "headers": [
            (b"host", b"localhost:3200"), (b"origin", origin.encode()),
        ]})
    assert browser_origin_allowed(connection)
    assert secure_cookie(connection) is (scheme == "wss")


@pytest.mark.parametrize("host,peer,allowed", [
    ("[::1]:3200", "::1", True),
    ("[::ffff:127.0.0.1]:3200", "::ffff:127.0.0.1", True),
    ("[fd00::1]:3200", "fd00::2", True),
    ("[::1]:3200", "fd00::2", False),
    ("[fd00::1]:3200", "2001:4860:4860::8888", False),
])
def test_literal_ipv6_at_asgi_boundary(host, peer, allowed):
    connection = HTTPConnection({"type": "http", "scheme": "http", "path": "/",
        "client": (peer, 1234), "headers": [
            (b"host", host.encode()), (b"origin", f"http://{host}".encode()),
        ]})
    assert browser_origin_allowed(connection) is allowed
