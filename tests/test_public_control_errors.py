"""Synthetic secret-bearing errors must never become public control/account messages."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import cameras, controls, intercom, provisioning_account, vendor_controls
from backend.app.api.provisioning_common import ProvisioningVendorAccountLoginIn
from backend.app.drivers import ControlNotReady, ControlOperationError, Unsupported
from backend.app.drivers.onboarding import OnboardingAccountError
from backend.app.services.camera_controls import CameraNotFound, ControlBusy
from backend.app.services.camera_runtime import resync_services

SECRET = "rtsp://synthetic-user:DO_NOT_EXPOSE@192.0.2.1/live?token=DO_NOT_EXPOSE"
CAMERA = "cam_" + "a" * 24


@pytest.mark.parametrize("module", [controls, intercom, vendor_controls])
@pytest.mark.parametrize("kind,status", [(CameraNotFound, 404), (Unsupported, 501),
                                        (ControlBusy, 409), (ControlNotReady, 409), (ControlOperationError, 502)])
def test_driver_errors_project_status_without_exception_text(module, kind, status):
    error = module._failure(kind(SECRET))
    assert error.status_code == status
    assert "DO_NOT_EXPOSE" not in str(error.detail)
    assert "rtsp://" not in str(error.detail)
    assert error.headers == {"Cache-Control": "no-store"}


def test_real_control_response_never_echoes_transport_error(monkeypatch):
    def fail(*_):
        raise ControlOperationError(SECRET)
    monkeypatch.setattr(controls, "read_control", fail)
    app = FastAPI(); app.include_router(controls.router)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        response = client.get(f"/api/cameras/{CAMERA}/controls/white_light")
        assert response.status_code == 502 and response.json() == {"detail": "camera control failed"}
        assert "DO_NOT_EXPOSE" not in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("kind,status", [(ControlNotReady, 409), (ControlBusy, 409),
                                        (ControlOperationError, 502), (ValueError, 400)])
def test_ptz_driver_error_is_sanitized_without_hardware(monkeypatch, kind, status):
    def fail(*_):
        raise kind(SECRET)
    monkeypatch.setattr(cameras, "resolve_camera", lambda _: SimpleNamespace(camera_id=CAMERA))
    monkeypatch.setattr(cameras.drivers, "for_camera", lambda _: SimpleNamespace(ptz=fail))
    with pytest.raises(HTTPException) as caught:
        cameras.ptz_move(CAMERA, cameras.PtzIn(direction="left"))
    assert caught.value.status_code == status and "DO_NOT_EXPOSE" not in str(caught.value.detail)


@pytest.mark.parametrize("operation,kind,status", [
    ("login", ValueError, 422), ("login", OnboardingAccountError, 502),
    ("refresh", LookupError, 409), ("refresh", OnboardingAccountError, 502),
])
def test_account_failures_never_echo_passwords_tokens_or_provider_responses(monkeypatch, operation, kind, status):
    def fail(*_):
        raise kind(SECRET)
    provider = SimpleNamespace(login=fail, refresh_account=fail)
    monkeypatch.setattr(provisioning_account, "onboarding", lambda *args: provider)
    with pytest.raises(HTTPException) as caught:
        if operation == "login":
            body = ProvisioningVendorAccountLoginIn(account_type="email", account="test@example.invalid", password="DO_NOT_EXPOSE")
            provisioning_account.provisioning_vendor_account_login(body, Response())
        else:
            provisioning_account.provisioning_vendor_account_refresh(Response())
    assert caught.value.status_code == status and "DO_NOT_EXPOSE" not in str(caught.value.detail)
    assert caught.value.headers == {"Cache-Control": "no-store"}
    assert caught.value.__suppress_context__


def test_resync_log_keeps_error_type_without_transport_text(caplog):
    def fail():
        raise RuntimeError(SECRET)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=SimpleNamespace(restart=fail))))
    resync_services(request)
    assert "RuntimeError" in caplog.text and "DO_NOT_EXPOSE" not in caplog.text
