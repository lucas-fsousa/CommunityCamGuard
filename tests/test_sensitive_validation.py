"""Malformed synthetic credentials must not appear in HTTP schema-validation errors."""

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from backend.app import auth, main
from backend.app.api import cameras, controls, onboarding, provisioning_account, provisioning_ble
from backend.app.validation_errors import invalid_request

SECRET = "SYNTHETIC_DO_NOT_EXPOSE"


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input must not reach hardware/storage/provider work")
    monkeypatch.setattr(cameras.registry, "upsert_camera", forbidden)
    monkeypatch.setattr(cameras.rtsp, "check_credentials", forbidden)
    monkeypatch.setattr(controls, "write_control", forbidden)
    monkeypatch.setattr(provisioning_account, "onboarding", forbidden)
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, invalid_request)
    for module in (cameras, controls, onboarding, provisioning_account, provisioning_ble):
        app.include_router(module.router)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as http:
        http.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield http


def assert_safe(response):
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request parameters"}
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("payload", [
    {"mac": "aa:bb:cc:dd:ee:03", "password": {"token": SECRET}},
    {"password": SECRET}, [SECRET],
    {"mac": "aa:bb:cc:dd:ee:03", "password": SECRET, "rtsp_port": SECRET},
])
def test_camera_validation_never_echoes_input(client, payload):
    assert_safe(client.post("/api/cameras", json=payload))


@pytest.mark.parametrize("payload", [
    {"account_type": "email", "account": "test@example.invalid", "password": {"token": SECRET}},
    {"account_type": "email", "account": "test@example.invalid", "password": SECRET * 20},
    {"account_type": SECRET, "account": "test@example.invalid", "password": SECRET},
])
def test_secretstr_model_does_not_make_invalid_inputs_public(client, payload):
    assert_safe(client.post("/api/provisioning/vendor-account/login", json=payload))


def test_wifi_and_ble_validation_are_safe(client):
    assert_safe(client.post("/api/provisioning/ble/prepare", json={
        "wifi_network_id": "synthetic", "wifi_password": {"password": SECRET},
    }))
    assert_safe(client.post("/api/provisioning/ble/decode-response", json={
        "attempt_id": SECRET, "command": SECRET, "data_base64": SECRET,
    }))


def test_invalid_json_and_invalid_path_do_not_echo_any_input(client):
    assert_safe(client.post("/api/cameras", content='{"password":"' + SECRET,
                            headers={"Content-Type": "application/json"}))
    assert_safe(client.put(f"/api/cameras/{SECRET}/controls/{SECRET}", json={"value": True}))


def test_query_validation_is_also_redacted():
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, invalid_request)

    @app.get("/synthetic")
    def endpoint(count: int):
        raise AssertionError("invalid query must not reach endpoint")

    with TestClient(app) as http:
        assert_safe(http.get("/synthetic", params={"count": SECRET}))


def test_main_registers_the_same_boundary_for_all_http_routes():
    assert main.app.exception_handlers[RequestValidationError] is invalid_request


def test_authentication_denial_is_not_converted_to_validation_error(client):
    client.cookies.clear()
    response = client.post("/api/cameras", json={"password": SECRET})
    assert response.status_code == 401 and SECRET not in response.text


def test_exception_internals_are_not_read_or_serialized():
    import asyncio
    class Poison(Exception):
        def errors(self):
            raise AssertionError("must not inspect input-bearing errors")
        def __str__(self):
            raise AssertionError("must not stringify raw exception")
    response = asyncio.run(invalid_request(None, Poison()))
    assert response.body == b'{"detail":"Invalid request parameters"}'
