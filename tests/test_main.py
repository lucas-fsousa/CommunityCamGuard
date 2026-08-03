"""App-level tests via the ASGI TestClient — exercises main.py (lifespan wiring, health, static
mount) and the auth cookie flow end to end. Services don't auto-start (conftest sets
AUTOSTART_SERVICES=false), so no go2rtc binary or hardware is needed.
"""
from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_is_open_and_ok():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_protected_endpoint_requires_auth():
    with TestClient(app) as client:
        assert client.get("/api/cameras").status_code == 401


def test_login_flow_sets_session_and_unlocks_api():
    with TestClient(app) as client:
        # not logged in yet
        assert client.get("/api/me").json() == {"authenticated": False}
        # wrong key is rejected
        assert client.post("/api/login", json={"key": "nope"}).status_code == 401
        # right key (conftest sets DASHBOARD_SECRET_KEY=test-secret-key) sets the cookie
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        assert client.get("/api/me").json() == {"authenticated": True}
        # and the session now unlocks a protected route
        assert client.get("/api/cameras").status_code == 200
        # logout clears it
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/me").json() == {"authenticated": False}


def test_openapi_schema_is_served():
    with TestClient(app) as client:
        schema = client.get("/api/openapi.json")
        assert schema.status_code == 200
        assert schema.json()["info"]["title"] == "Community Cam Guard"
