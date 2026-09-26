"""Synthetic Wi-Fi failures must not echo passwords, signed tokens or provider text."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import SignatureExpired

from backend.app import auth
from backend.app.api import provisioning_ble as ble
from backend.app.api import provisioning_network as network
from backend.app.api.provisioning_errors import wifi_selection_failure
from backend.app.provisioning import wifi

SECRET = "SYNTHETIC_WIFI_SECRET"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(network.router)
    app.include_router(ble.router)
    identity = {"device_id": "12345678", "setup_modes": ["qr", "bluetooth"]}

    def forbidden(*args, **kwargs):
        raise AssertionError("must not call provider or hardware")

    for module in (network, ble):
        monkeypatch.setattr(module, "inspect_provisioning_label", lambda body: identity)
        monkeypatch.setattr(module, "onboarding", forbidden)
    monkeypatch.setattr(network, "scan_wifi_networks", lambda: ([], "", ""))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as http:
        http.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield http


@pytest.mark.parametrize("reason,expected", [
    (wifi.WifiSelectionReason.INVALID, "invalid Wi-Fi selection; select the network again"),
    (wifi.WifiSelectionReason.EXPIRED, "Wi-Fi selection expired; scan again"),
    (wifi.WifiSelectionReason.SSID_LENGTH, "SSID must contain 1 to 32 UTF-8 bytes"),
    (wifi.WifiSelectionReason.SECURITY, "unsupported Wi-Fi security"),
])
@pytest.mark.parametrize("route", ["networks/manual", "start", "ble/prepare"])
def test_selection_errors_are_projected_without_raw_text(client, monkeypatch, reason, expected, route):
    class Poison(wifi.WifiSelectionError):
        def __str__(self):
            raise AssertionError("must not stringify selection error")

    def fail(*args):
        raise Poison(SECRET, reason=reason)

    monkeypatch.setattr(network, "manual_network", fail)
    monkeypatch.setattr(network, "selected_network", fail)
    monkeypatch.setattr(ble, "selected_network", fail)
    body = ({"ssid": SECRET} if route == "networks/manual" else
            {"wifi_network_id": SECRET, "wifi_password": SECRET})
    response = client.post(f"/api/provisioning/{route}", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": expected}
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_unknown_reason_is_safe_even_if_unhashable():
    error = wifi.WifiSelectionError(SECRET)
    error.reason = [SECRET]
    public = wifi_selection_failure(error)
    assert public.detail == "invalid Wi-Fi selection; select the network again"


def test_qr_provider_error_does_not_echo_credentials(client, monkeypatch):
    def fail(**kwargs):
        assert kwargs["password"] == SECRET
        raise ValueError(f"provider rejected password={SECRET}")

    monkeypatch.setattr(network, "selected_network", lambda token: wifi.WifiNetwork("synthetic"))
    monkeypatch.setattr(network, "onboarding", lambda: SimpleNamespace(build_wifi_qr=fail))
    response = client.post("/api/provisioning/start", json={
        "wifi_network_id": SECRET, "wifi_password": SECRET,
    })
    assert response.status_code == 422
    assert response.json() == {
        "detail": "cannot generate Wi-Fi QR; check the SSID, password and security mode",
    }
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("ssid,security,reason", [
    ("", "wpa", wifi.WifiSelectionReason.SSID_LENGTH),
    ("é" * 17, "wpa", wifi.WifiSelectionReason.SSID_LENGTH),
    ("synthetic", SECRET, wifi.WifiSelectionReason.SECURITY),
])
def test_manual_validation_assigns_typed_reasons(ssid, security, reason):
    with pytest.raises(wifi.WifiSelectionError) as caught:
        wifi.manual_network(ssid, security)
    assert caught.value.reason is reason


def test_expired_selection_assigns_typed_reason(monkeypatch):
    def expired(*args, **kwargs):
        raise SignatureExpired(SECRET)

    monkeypatch.setattr(wifi, "_serializer", lambda: SimpleNamespace(loads=expired))
    with pytest.raises(wifi.WifiSelectionError) as caught:
        wifi.selected_network(SECRET)
    assert caught.value.reason is wifi.WifiSelectionReason.EXPIRED


def test_invalid_selection_assigns_typed_reason():
    with pytest.raises(wifi.WifiSelectionError) as caught:
        wifi.selected_network(SECRET)
    assert caught.value.reason is wifi.WifiSelectionReason.INVALID


def test_unauthenticated_request_never_reaches_selection(client, monkeypatch):
    def forbidden(*args):
        raise AssertionError("must authenticate before selecting Wi-Fi")

    monkeypatch.setattr(network, "selected_network", forbidden)
    client.cookies.clear()
    response = client.post("/api/provisioning/start", json={"wifi_network_id": SECRET})
    assert response.status_code == 401
