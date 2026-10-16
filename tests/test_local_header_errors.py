"""Malformed forwarding/origin metadata must deny before protected handlers run."""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.api.local_only import require_local_or_remote_ble_request, require_local_request
from backend.app.config import get_settings


@pytest.mark.parametrize("name,value", [
    ("Host", "["), ("Origin", "http://["), ("Referer", "http://[broken]/"),
    ("X-Forwarded-For", "["), ("X-Real-IP", "[broken]"),
    ("CF-Connecting-IP", "[::1"), ("True-Client-IP", "["),
    ("Forwarded", 'for="["'),
])
def test_malformed_local_headers_are_denied_not_server_errors(name, value):
    app = FastAPI()
    called = []
    @app.get("/guarded", dependencies=[Depends(require_local_request)])
    def guarded():
        called.append(True)
        return {}
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        response = client.get("/guarded", headers={name: value})
    assert response.status_code == 403
    assert not called
    assert value not in response.text


@pytest.mark.parametrize("name", ["Origin", "Referer"])
def test_remote_ble_malformed_source_is_denied_with_opt_in(monkeypatch, name):
    monkeypatch.setenv("PROVISIONING_REMOTE_BLE_ENABLED", "true")
    get_settings.cache_clear()
    app = FastAPI()
    called = []
    @app.get("/guarded", dependencies=[Depends(require_local_or_remote_ble_request)])
    def guarded():
        called.append(True)
        return {}
    with TestClient(app, base_url="https://camera.example", client=("127.0.0.1", 1234),
                    raise_server_exceptions=False) as client:
        response = client.get("/guarded", headers={name: "https://[", "X-Forwarded-Proto": "https"})
    assert response.status_code == 403 and not called
    assert "https://[" not in response.text
