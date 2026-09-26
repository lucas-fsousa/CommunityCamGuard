"""BLE recovery guidance comes from typed reasons, never raw provider messages."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import provisioning_ble as routes
from backend.app.api.provisioning_errors import ble_input_failure
from backend.app.drivers.onboarding import (
    OnboardingAccountError,
    OnboardingInputError,
    OnboardingInputReason,
    OnboardingTransportError,
)
from backend.app.drivers.yoosee import ble, ble_onboarding
from backend.app.drivers.yoosee.onboarding import YooseeOnboarding
from backend.app.provisioning.wifi import WifiNetwork

SECRET = "SYNTHETIC_BLE_SECRET"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "inspect_provisioning_label", lambda body: {
        "device_id": "12345678", "setup_modes": ["bluetooth"],
    })
    monkeypatch.setattr(routes, "selected_network", lambda token: WifiNetwork("synthetic"))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as http:
        http.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield http


@pytest.mark.parametrize("reason", list(OnboardingInputReason))
@pytest.mark.parametrize("route", ["prepare", "decode-response"])
def test_ble_input_projection(client, monkeypatch, reason, route):
    class Poison(OnboardingInputError):
        def __str__(self):
            raise AssertionError("must not stringify provider input error")

    error = Poison(SECRET, reason=reason)

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(routes, "onboarding", lambda: SimpleNamespace(prepare_ble=fail, decode_ble=fail))
    body = ({"wifi_network_id": SECRET, "wifi_password": SECRET} if route == "prepare" else
            {"attempt_id": SECRET * 2, "command": 0x71})
    response = client.post(f"/api/provisioning/ble/{route}", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": ble_input_failure(error).detail}
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("error,status", [
    (LookupError(SECRET), 503), (OnboardingAccountError(SECRET), 502),
    (OnboardingTransportError(SECRET), 502),
])
def test_ble_material_failure_is_safe(client, monkeypatch, error, status):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(routes, "onboarding", lambda: SimpleNamespace(prepare_ble=fail))
    response = client.post("/api/provisioning/ble/prepare", json={"wifi_network_id": SECRET})
    assert response.status_code == status
    assert SECRET not in response.text
    assert "vendor account" in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store"


def test_unknown_reason_fails_closed():
    error = OnboardingInputError(SECRET)
    error.reason = [SECRET]
    assert ble_input_failure(error).detail.startswith("invalid Bluetooth provisioning data")


def test_missing_attempt_produces_expiry_reason():
    with pytest.raises(OnboardingInputError) as caught:
        ble_onboarding.decode_response(device_id="12345678", attempt_id=SECRET,
                                       command=0x71, encrypted=False, raw=b"")
    assert caught.value.reason is OnboardingInputReason.SESSION_EXPIRED


@pytest.mark.parametrize("reason", list(OnboardingInputReason))
def test_decoder_preserves_typed_codec_reason(monkeypatch, reason):
    def fail(*args, **kwargs):
        raise ble.BleCodecError(SECRET, reason=reason)

    monkeypatch.setattr(ble_onboarding, "ble_provisioning_attempt", fail)
    with pytest.raises(OnboardingInputError) as caught:
        ble_onboarding.decode_response(device_id="12345678", attempt_id=SECRET,
                                       command=0x71, encrypted=False, raw=b"")
    assert caught.value.reason is reason
    assert SECRET not in str(caught.value)


def test_missing_material_produces_renew_reason(tmp_path):
    with pytest.raises(ble.BleCodecError) as caught:
        ble.load_ble_provisioning_material(tmp_path / "absent", expected_device_id="12345678",
                                          max_age_seconds=60)
    assert caught.value.reason is OnboardingInputReason.RENEW_MATERIAL


@pytest.mark.parametrize("reason", list(OnboardingInputReason))
def test_preparation_preserves_codec_reason_without_text(monkeypatch, reason):
    def fail(*args, **kwargs):
        raise ble.BleCodecError(SECRET, reason=reason)

    monkeypatch.setattr(YooseeOnboarding, "ble_material", fail)
    with pytest.raises(OnboardingInputError) as caught:
        YooseeOnboarding().prepare_ble(device_id="12345678", ssid="synthetic",
                                      password=SECRET, security="wpa",
                                      fallback_file=None, max_age_seconds=60)
    assert caught.value.reason is reason
    assert SECRET not in str(caught.value)
