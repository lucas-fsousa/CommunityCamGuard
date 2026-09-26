"""Origin/Host policy without proxies, production tokens or camera operations."""

from http.cookies import SimpleCookie

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from backend.app import auth, config
from backend.app.api.auth import router
from backend.app.api.media import router as media_router
from backend.app.origin_policy import browser_origin_allowed, normalize_origin


@pytest.mark.parametrize("raw,expected", [
    ("https://EXAMPLE.org:443/", "https://example.org"),
    ("http://localhost:3200", "http://localhost:3200"),
    ("https://[2001:db8::1]:444", "https://[2001:db8::1]:444"),
    ("http://192.168.1.2:80", "http://192.168.1.2"),
])
def test_canonical_configuration(raw, expected):
    assert normalize_origin(raw) == expected
    assert config.Settings(dashboard_public_origin=raw).dashboard_public_origin == expected


@pytest.mark.parametrize("raw", [
    "null", "*", "ftp://example.org", "https://user:pass@example.org", "https://example.org/path",
    "https://example.org?", "https://example.org#", "https://example.org:0", "https://example.org:99999",
    " https://example.org", "https://example.org\n", "https://example.org:",
    "https://example.org\\evil", "https://example.org,evil", "https://[fe80::1%eth0]",
])
def test_invalid_configuration_fails_closed(raw):
    with pytest.raises(ValidationError):
        config.Settings(dashboard_public_origin=raw)


def make_app():
    app = FastAPI(); app.include_router(router); app.include_router(media_router)
    calls = []
    @app.api_route("/guarded", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   dependencies=[Depends(auth.require_auth)])
    def guarded():
        calls.append(True)
        return {"ok": True}
    return app, calls


@pytest.mark.parametrize("headers", [
    {"Origin": "https://other.invalid"}, {"Origin": "null"},
    {"Origin": "http://testserver.evil.invalid"}, {"Origin": "http://testserver:9999"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"Referer": "http://other.invalid/path"}, {"Content-Type": "application/x-www-form-urlencoded"},
    {"Origin": "https://other.invalid", "X-Forwarded-Host": "other.invalid", "X-Forwarded-Proto": "https"},
])
def test_login_logout_and_authenticated_mutations_reject_other_origins(headers):
    app, calls = make_app()
    with TestClient(app) as client:
        response = client.post("/api/login", json={"key": "test-secret-key"}, headers=headers)
        assert response.status_code == 403 and "set-cookie" not in response.headers
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, "/guarded", headers=headers).status_code == 403
        assert client.post("/api/logout", headers=headers).status_code == 403
        assert not calls


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://testserver"}, {"Referer": "http://testserver/path?view=recordings"},
    {"Sec-Fetch-Site": "same-origin"},
])
def test_local_and_script_flows_remain_usable(headers):
    app, calls = make_app()
    with TestClient(app) as client:
        assert client.post("/api/login", json={"key": "test-secret-key"}, headers=headers).status_code == 200
        assert client.post("/guarded", headers=headers).status_code == 200
        assert calls == [True]
        assert client.post("/api/logout", headers=headers).status_code == 200


def test_explicit_https_origin_behind_http_proxy_pins_host_and_cookie(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", "https://cameras.example.org")
    config.get_settings.cache_clear()
    app, calls = make_app()
    with TestClient(app, base_url="http://cameras.example.org") as client:
        response = client.post("/api/login", json={"key": "test-secret-key"},
                               headers={"Origin": "https://cameras.example.org", "X-Forwarded-Proto": "http"})
        assert response.status_code == 200
        cookie = SimpleCookie(response.headers["set-cookie"])[auth.COOKIE_NAME]
        assert cookie["secure"]
        # Model the browser sending its Secure cookie to the TLS proxy, which forwards
        # it on the restricted HTTP backend hop; TestClient cannot do that automatically.
        session = {"Cookie": f"{auth.COOKIE_NAME}={cookie.value}", "Origin": "https://cameras.example.org"}
        assert client.post("/guarded", headers=session).status_code == 200
        assert client.get("/guarded", headers={**session, "Host": "other.invalid"}).status_code == 403
        assert client.post("/guarded", headers={**session, "Origin": "http://cameras.example.org"}).status_code == 403
        assert client.get("/api/me", headers={**session, "Host": "localhost"}).status_code == 403
        assert client.post("/api/login", json={"key": "test-secret-key"},
                           headers={"Host": "other.invalid", "X-Forwarded-Host": "cameras.example.org"}).status_code == 403
        assert calls == [True]


def test_duplicate_headers_are_denied():
    for headers in [
        [(b"host", b"testserver"), (b"host", b"testserver")],
        [(b"host", b"testserver"), (b"origin", b"http://testserver"), (b"origin", b"http://testserver")],
        [(b"host", b"testserver"), (b"sec-fetch-site", b"same-origin"), (b"sec-fetch-site", b"cross-site")],
    ]:
        connection = HTTPConnection({"type": "http", "scheme": "http", "path": "/", "headers": headers})
        assert not browser_origin_allowed(connection)


def test_primary_media_rejects_cross_origin_before_proxy_work():
    app, _ = make_app()
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(
                "/api/go2rtc/ws?src=never-opened", headers={"Origin": "https://other.invalid"}):
            raise AssertionError("cross-origin socket accepted")
        assert caught.value.code == 1008
