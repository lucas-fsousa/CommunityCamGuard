from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.auth import COOKIE_NAME, issue_token
from backend.app.diagnostics import yoosee_av_api as api
from backend.app.drivers.yoosee.p2p.av_route import AvRouteResult
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.services.camera_controls import ControlBusy
from tests.test_av_route import RESULT

PATH = "/api/internal/diagnostics/native-av"
CAMERA = "cam_" + "a" * 24


@pytest.fixture
def setup(monkeypatch):
    settings = SimpleNamespace(native_av_diagnostic_enabled=True,
                               native_av_diagnostic_camera_id=CAMERA,
                               native_av_diagnostic_device_id="123")
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return AvRouteResult(RESULT, True)
    monkeypatch.setattr(api, "run_reviewed_native_av", run)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
    client.cookies.set(COOKIE_NAME, issue_token())
    return client, settings, calls


def test_single_use_fixed_target_and_safe_counts(setup):
    client, _, calls = setup
    result = client.post(PATH)
    assert result.status_code == 200
    assert result.json()["route_release_acknowledged"] is True
    assert "123" not in result.text and "token" not in result.text
    assert calls == [dict(camera_id=CAMERA, reviewed_camera_id=CAMERA, reviewed_device_id="123")]
    assert client.post(PATH).status_code == 409
    assert len(calls) == 1
    assert PATH not in client.get("/openapi.json").json()["paths"]


def test_disabled_and_unconfigured_never_run(setup):
    client, settings, calls = setup
    settings.native_av_diagnostic_enabled = False
    assert client.post(PATH).status_code == 404
    settings.native_av_diagnostic_enabled = True
    settings.native_av_diagnostic_camera_id = ""
    assert client.post(PATH).status_code == 409
    assert not calls


def test_authentication_required(setup):
    client, _, calls = setup
    client.cookies.clear()
    assert client.post(PATH).status_code == 401
    assert not calls


@pytest.mark.parametrize("headers", [{"x-forwarded-for": "127.0.0.1"},
                                      {"forwarded": "for=127.0.0.1"},
                                      {"x-forwarded-host": "localhost"},
                                      {"origin": "https://attacker.invalid"}])
def test_forwarding_and_cross_origin_are_rejected(setup, headers):
    client, _, calls = setup
    assert client.post(PATH, headers=headers).status_code == 403
    assert not calls


def test_direct_lan_is_not_enough(setup):
    original, _, calls = setup
    client = TestClient(original.app, base_url="http://192.168.1.5", client=("192.168.1.6", 1234))
    client.cookies.set(COOKIE_NAME, issue_token())
    assert client.post(PATH).status_code == 403
    assert not calls


@pytest.mark.parametrize("kwargs", [{"params": {"camera_id": "other"}}, {"json": {"duration": 999}}])
def test_request_cannot_override_target_or_duration(setup, kwargs):
    client, _, calls = setup
    assert client.post(PATH, **kwargs).status_code == 400
    assert not calls


def test_failed_operation_is_sanitized_and_consumes_attempt(setup, monkeypatch):
    client, _, calls = setup
    def fail(**kwargs):
        calls.append(kwargs)
        raise P2PProbeError("private endpoint and credentials")
    monkeypatch.setattr(api, "run_reviewed_native_av", fail)
    result = client.post(PATH)
    assert result.status_code == 502 and "private" not in result.text
    assert client.post(PATH).status_code == 409 and len(calls) == 1


def test_busy_camera_consumes_attempt_without_retry(setup, monkeypatch):
    client, _, calls = setup
    def busy(**kwargs):
        calls.append(kwargs)
        raise ControlBusy("busy")
    monkeypatch.setattr(api, "run_reviewed_native_av", busy)
    assert client.post(PATH).status_code == 409
    assert client.post(PATH).status_code == 409 and len(calls) == 1


def test_bootstrap_failure_returns_only_safe_observations(setup, monkeypatch):
    client, _, calls = setup
    def fail(**kwargs):
        calls.append(kwargs)
        raise api.AvBootstrapError(direct_acknowledged=False, meter_acknowledged=True, datagrams=3)
    monkeypatch.setattr(api, "run_reviewed_native_av", fail)
    response = client.post(PATH)
    assert response.status_code == 502
    assert response.json()["detail"] == dict(message="native AV bootstrap failed; attempt consumed",
                                             phase="media_meter", direct_acknowledged=False,
                                             meter_acknowledged=True, datagrams=3)
    assert client.post(PATH).status_code == 409 and len(calls) == 1
