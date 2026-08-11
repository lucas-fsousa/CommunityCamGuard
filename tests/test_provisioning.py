from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api import routes
from backend.app.api.local_only import require_local_request
from backend.app.main import app
from backend.app.provisioning import LabelError, inspect_label
from backend.app.provisioning.wifi import (
    WifiNetwork,
    _parse_iw,
    _parse_nmcli,
    selected_ssid,
    sign_network,
)


def _request(
    client: str = "127.0.0.1",
    host: str = "localhost:3200",
    **headers: str,
) -> Request:
    raw_headers = [(b"host", host.encode())]
    raw_headers.extend((name.replace("_", "-").encode(), value.encode()) for name, value in headers.items())
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/provisioning/inspect",
            "raw_path": b"/api/provisioning/inspect",
            "query_string": b"",
            "headers": raw_headers,
            "client": (client, 43210),
            "server": ("127.0.0.1", 3200),
        }
    )


def test_local_guard_accepts_only_loopback_evidence():
    assert require_local_request(
        _request(origin="http://localhost:3200", forwarded="for=127.0.0.1;proto=http")
    ) is None
    assert require_local_request(_request(client="::1", host="[::1]:3200")) is None


@pytest.mark.parametrize(
    ("client", "host", "headers"),
    [
        ("192.168.1.20", "localhost:3200", {}),
        ("127.0.0.1", "camera.example.com", {}),
        ("127.0.0.1", "localhost:3200", {"origin": "https://evil.example"}),
        ("127.0.0.1", "localhost:3200", {"x_forwarded_for": "203.0.113.7"}),
        ("127.0.0.1", "localhost:3200", {"sec_fetch_site": "cross-site"}),
    ],
)
def test_local_guard_rejects_remote_or_forwarded_requests(client, host, headers):
    with pytest.raises(HTTPException) as caught:
        require_local_request(_request(client=client, host=host, **headers))
    assert caught.value.status_code == 403
    assert caught.value.detail == "provisioning is available only on localhost"


def test_scanned_label_is_normalised_and_decoded():
    out = inspect_label(label="http://yoosee.co/?D=0-7443576841-8034", firmware_version="40.1.14")
    assert out == {
        "device_id": "7443576841",
        "capability_code": "8034",
        "capability_mask": 0x8034,
        "setup_modes": ["softap", "bluetooth", "qr", "wired"],
        "preferred_mode": "qr",
        "firmware_version": "40.1.14",
        "mac": "",
    }


def test_manual_label_fields_and_mac_are_supported():
    out = inspect_label(
        device_id="7443576841", capability_code="0x8034", mac="AABBCCDDEEFF"
    )
    assert out["device_id"] == "7443576841"
    assert out["capability_code"] == "8034"
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"


def test_conflicting_manual_and_scanned_identity_is_rejected():
    with pytest.raises(LabelError, match="does not match"):
        inspect_label(
            label="http://yoosee.co/?D=0-7443576841-8034",
            device_id="7443576842",
        )


def test_start_fails_closed_until_transport_is_real():
    network_id = sign_network("Home Wi-Fi")
    body = routes.ProvisioningStartIn(
        label="http://yoosee.co/?D=0-7443576841-8034",
        wifi_network_id=network_id,
        wifi_password="not-persisted",
    )
    with pytest.raises(HTTPException) as caught:
        routes.provisioning_start(body)
    assert caught.value.status_code == 501
    assert "transport is not ready" in caught.value.detail
    assert "not-persisted" not in caught.value.detail


def test_wifi_selection_is_signed_and_cannot_be_edited():
    token = sign_network("Home Wi-Fi")
    assert selected_ssid(token) == "Home Wi-Fi"
    with pytest.raises(ValueError, match="invalid Wi-Fi selection"):
        selected_ssid(token + "tampered")


def test_wifi_scanner_parsers_dedupe_and_decode_networks():
    nmcli = _parse_nmcli("Home\\:Office:87:WPA2\nGuest:42:--\n")
    assert nmcli == [
        WifiNetwork("Home:Office", 87, "WPA2"),
        WifiNetwork("Guest", 42, "--"),
    ]
    iw = _parse_iw(
        "BSS aa:bb:cc:dd:ee:ff\n\tsignal: -55.00 dBm\n\tSSID: Home\n\tRSN:\n"
        "BSS 11:22:33:44:55:66\n\tsignal: -80.00 dBm\n\tSSID: Guest\n"
    )
    assert iw == [WifiNetwork("Home", 90, "secured"), WifiNetwork("Guest", 40, "open")]


def test_provisioning_api_accepts_authenticated_loopback_client():
    with TestClient(
        app, base_url="http://localhost", client=("127.0.0.1", 50000)
    ) as client:
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        status = client.get("/api/provisioning/status")
        assert status.status_code == 200
        assert status.json()["local_only"] is True
        inspected = client.post(
            "/api/provisioning/inspect",
            json={"label": "http://yoosee.co/?D=0-7443576841-8034"},
        )
        assert inspected.status_code == 200
        assert inspected.json()["device_id"] == "7443576841"


def test_provisioning_api_rejects_authenticated_remote_client():
    with TestClient(
        app, base_url="http://localhost", client=("192.0.2.40", 50000)
    ) as client:
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        response = client.get("/api/provisioning/status")
        assert response.status_code == 403
        assert response.json()["detail"] == "provisioning is available only on localhost"
