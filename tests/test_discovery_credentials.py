"""Discovery migration uses a fake scanner and an isolated registry; no LAN traffic."""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import discovery, discovery_input
from backend.app.db import registry


@pytest.fixture
def client(monkeypatch):
    registry.init_db()
    seen = []
    monkeypatch.setattr(discovery.active_scan, "scan", lambda **kwargs: seen.append(kwargs) or [])
    app = FastAPI(); app.include_router(discovery.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield client, seen


def test_json_credentials_and_legacy_no_body_scan(client):
    http, seen = client
    response = http.post("/api/discovery/scan", json={"username": "test", "password": "SYNTHETIC_SECRET"})
    assert response.status_code == 200 and "SYNTHETIC_SECRET" not in response.text
    assert seen == [{"username": "test", "password": "SYNTHETIC_SECRET"}]
    assert http.post("/api/discovery/scan").status_code == 200
    assert seen[-1] == {"username": "", "password": ""}


@pytest.mark.parametrize("query", ["password=SYNTHETIC_SECRET", "username=test", "password=", "other=1"])
def test_query_credentials_are_not_accepted_or_echoed(client, query):
    http, seen = client
    response = http.post("/api/discovery/scan?" + query)
    assert response.status_code == 400 and not seen
    assert "SYNTHETIC_SECRET" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [
    b'{"password":{"token":"SYNTHETIC_SECRET"}}', b'{"password":"SYNTHETIC_SECRET",',
    b'{"password":"SYNTHETIC_SECRET","unknown":1}', b'{"password":"SYNTHETIC_SECRET","password":"x"}',
    b'null', b'[]', b'{"username":123}', b'{"password":"' + b'x' * 1025 + b'"}', b'\xff',
])
def test_invalid_bodies_are_generic_and_never_scan(client, body):
    http, seen = client
    response = http.post("/api/discovery/scan", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and not seen
    assert response.json() == {"detail": "invalid discovery credentials"}
    assert response.headers["cache-control"] == "no-store"


def test_size_auth_and_origin_boundaries(client):
    http, seen = client
    assert http.post("/api/discovery/scan", content=b'x' * 16385).status_code == 413
    assert http.post("/api/discovery/scan", json={}, headers={"Origin": "https://other.invalid"}).status_code == 403
    http.cookies.clear()
    assert http.post("/api/discovery/scan", content=b'{bad json').status_code == 401
    assert not seen


def test_schema_does_not_advertise_query_credentials(client):
    http, _ = client
    spec = http.get("/openapi.json").json()["paths"]["/api/discovery/scan"]["post"]
    assert not spec.get("parameters")
    schema = spec["requestBody"]["content"]["application/json"]["schema"]
    assert schema["properties"]["password"]["writeOnly"]


@pytest.mark.parametrize("mode,code", [("large", 413), ("slow", 408), ("disconnect", 400)])
def test_actual_chunks_and_timeout_without_content_length(monkeypatch, mode, code):
    monkeypatch.setattr(discovery_input, "BODY_TIMEOUT", 0.01)
    async def run():
        async def receive():
            if mode == "slow":
                await asyncio.Future()
            if mode == "disconnect":
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b'x' * 16385, "more_body": False}
        request = Request({"type": "http", "method": "POST", "path": "/api/discovery/scan",
                           "scheme": "http", "query_string": b"", "headers": [(b"host", b"testserver"),
                           (b"cookie", f"{auth.COOKIE_NAME}={auth.issue_token()}".encode())]}, receive)
        with pytest.raises(HTTPException) as caught:
            await discovery_input.read_scan_credentials(request)
        assert caught.value.status_code == code
    asyncio.run(run())
