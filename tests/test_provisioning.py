from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api import routes
from backend.app.api.local_only import require_local_or_remote_ble_request, require_local_request
from backend.app.config import get_settings
from backend.app.db import p2p
from backend.app.main import app
from backend.app.provisioning import (
    BleCodecError,
    BleMessageAssembler,
    LabelError,
    begin_ble_provisioning_attempt,
    build_ble_provisioning_frames,
    build_wifi_payload,
    decrypt_ble_payload,
    encrypt_ble_payload,
    encryption_from_scan,
    fragment_ble_message,
    inspect_label,
    load_ble_provisioning_material,
    parse_ble_frame,
    render_svg_base64,
)
from backend.app.provisioning import privileged as privileged_module
from backend.app.provisioning.ble import (
    _clear_ble_provisioning_attempts_for_tests,
    ble_provisioning_attempt,
)
from backend.app.provisioning.privileged import (
    VendorBindResult,
    VendorOnlineResult,
    _clear_privileged_enrollments_for_tests,
    bind_vendor_device,
    pending_privileged_enrollment,
    privileged_enrollment_status,
    query_vendor_device_online,
    remember_privileged_handoff,
)
from backend.app.provisioning.wifi import (
    WifiNetwork,
    _parse_iw,
    _parse_nmcli,
    manual_network,
    selected_network,
    selected_ssid,
    sign_network,
)
from backend.app.vendor_p2p import P2PInventory, P2PRouteProbe

SUBSCRIPTION_TOKEN = "ab" * 64


def _request(
    client: str = "127.0.0.1",
    host: str = "localhost:3200",
    **headers: str,
) -> Request:
    raw_headers = [(b"host", host.encode())]
    raw_headers.extend(
        (name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()
    )
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


@pytest.fixture(autouse=True)
def _clear_privileged_state():
    _clear_privileged_enrollments_for_tests()
    _clear_ble_provisioning_attempts_for_tests()
    yield
    _clear_privileged_enrollments_for_tests()
    _clear_ble_provisioning_attempts_for_tests()


def _start_ble_attempt(path, device_id="7443576841"):
    material = load_ble_provisioning_material(
        path, expected_device_id=device_id, max_age_seconds=1800
    )
    return begin_ble_provisioning_attempt(material).attempt_id


def test_local_guard_accepts_loopback_and_same_origin_private_lan():
    assert (
        require_local_request(
            _request(origin="http://localhost:3200", forwarded="for=127.0.0.1;proto=http")
        )
        is None
    )
    assert require_local_request(_request(client="::1", host="[::1]:3200")) is None
    assert (
        require_local_request(
            _request(
                client="192.168.1.20",
                host="192.168.1.10:3200",
                origin="http://192.168.1.10:3200",
                sec_fetch_site="same-origin",
            )
        )
        is None
    )


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
    assert (
        caught.value.detail == "provisioning is available only from the authenticated local network"
    )


def test_remote_ble_guard_requires_explicit_opt_in_and_same_origin_https(monkeypatch):
    request = _request(
        host="camera-example.ngrok-free.app",
        origin="https://camera-example.ngrok-free.app",
        x_forwarded_proto="https",
        x_forwarded_for="203.0.113.7",
        sec_fetch_site="same-origin",
    )
    with pytest.raises(HTTPException, match="local network"):
        require_local_or_remote_ble_request(request)

    monkeypatch.setenv("PROVISIONING_REMOTE_BLE_ENABLED", "true")
    get_settings.cache_clear()
    assert require_local_or_remote_ble_request(request) is None

    insecure = _request(
        host="camera-example.ngrok-free.app",
        origin="http://camera-example.ngrok-free.app",
        x_forwarded_proto="http",
        sec_fetch_site="same-origin",
    )
    with pytest.raises(HTTPException, match="HTTPS tunnel"):
        require_local_or_remote_ble_request(insecure)


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
    out = inspect_label(device_id="7443576841", capability_code="0x8034", mac="AABBCCDDEEFF")
    assert out["device_id"] == "7443576841"
    assert out["capability_code"] == "8034"
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"


def test_conflicting_manual_and_scanned_identity_is_rejected():
    with pytest.raises(LabelError, match="does not match"):
        inspect_label(
            label="http://yoosee.co/?D=0-7443576841-8034",
            device_id="7443576842",
        )


def test_recovered_modern_qr_payload_matches_apk_format():
    assert build_wifi_payload(ssid="Casa 2G", password="segredo") == (
        "007Casa 2G107segredo201230104018500"
    )
    assert encryption_from_scan("WEP", "secret") == "wep"
    assert encryption_from_scan("open", "") == "open"
    assert encryption_from_scan("WPA2", "secret") == "wpa"


def test_recovered_ble_fragment_header_matches_native_sdk():
    assert fragment_ble_message(
        command=0x70,
        data=b"abc",
        encrypted=False,
        message_id=1,
        mtu=23,
    ) == [bytes.fromhex("01701003") + b"abc"]

    assert [
        frame.hex()
        for frame in fragment_ble_message(
            command=0x82,
            data=bytes(range(25)),
            encrypted=True,
            message_id=2,
            mtu=12,
        )
    ] == [
        "128240080001020304050607",
        "1282410808090a0b0c0d0e0f",
        "128242081011121314151617",
        "1282430118",
    ]


def test_recovered_ble_aes_matches_vendor_full_block_semantics():
    encrypted = encrypt_ble_payload(
        bytes(range(35)),
        "00112233445566778899aabbccddeeff",
    )
    assert encrypted.hex() == (
        "2f91bab6d230ca7ac75dba0c2d2c0ac331d157546278685b867e493090c1bfff202122"
    )
    assert encrypted[-3:] == bytes((32, 33, 34))
    with pytest.raises(BleCodecError, match="TanKey"):
        encrypt_ble_payload(b"data", "not-hex")


def test_recovered_ble_aes_decrypts_full_blocks_and_preserves_tail():
    source = bytes(range(35))
    key = "00112233445566778899aabbccddeeff"
    assert decrypt_ble_payload(encrypt_ble_payload(source, key), key) == source


def test_ble_notifications_are_parsed_and_reassembled_out_of_order():
    payload = b'{"ssid":"Home Wi-Fi","rssi":-42}'
    frames = fragment_ble_message(
        command=0x81,
        data=payload,
        encrypted=True,
        message_id=7,
        mtu=12,
    )
    first = parse_ble_frame(frames[0])
    assert (first.command, first.encrypted, first.message_id) == (0x81, True, 7)

    assembler = BleMessageAssembler()
    message = None
    for frame in reversed(frames):
        message = assembler.add(frame) or message
    assert message is not None
    assert message.command == 0x81
    assert message.encrypted is True
    assert message.data == payload


def test_malformed_ble_notifications_fail_closed():
    with pytest.raises(BleCodecError, match="shorter"):
        parse_ble_frame(b"\x01\x71")
    with pytest.raises(BleCodecError, match="length"):
        parse_ble_frame(bytes.fromhex("01711003") + b"ab")


def test_ble_handshake_material_is_owner_only_scoped_and_builds_exact_stages(tmp_path):
    path = tmp_path / "ble.json"
    path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-token",
                "serverUserId": 0x81234567,
                "captured_at": 100,
            }
        )
    )
    path.chmod(0o600)
    material = load_ble_provisioning_material(
        path, expected_device_id="7443576841", max_age_seconds=60, now=120
    )
    wifi_payload = build_wifi_payload(
        ssid="Home",
        password="secret",
        user_id=material.server_user_id,
        config_token=material.config_token,
    )
    stages = build_ble_provisioning_frames(material, wifi_payload=wifi_payload, mtu=23)
    assert set(stages) == {"challenge", "link_type", "wifi_list", "wifi_config", "finish"}

    link_assembler = BleMessageAssembler()
    link_message = None
    for frame in stages["link_type"]:
        link_message = link_assembler.add(frame) or link_message
    assert link_message is not None and link_message.command == 0x72 and link_message.encrypted
    assert json.loads(decrypt_ble_payload(link_message.data, material.tan_key)) == {
        "linkType": 1,
        "linkTypeName": "WIFI",
    }

    challenge = BleMessageAssembler().add(stages["challenge"][0])
    assert challenge is None  # randomNumber spans two frames at conservative browser MTU
    challenge_assembler = BleMessageAssembler()
    for frame in stages["challenge"]:
        challenge = challenge_assembler.add(frame) or challenge
    assert challenge is not None and challenge.command == 0x70
    assert challenge.data == material.random_number.encode()

    config_assembler = BleMessageAssembler()
    config = None
    for frame in stages["wifi_config"]:
        config = config_assembler.add(frame) or config
    assert config is not None and config.command == 0x82 and config.encrypted
    assert decrypt_ble_payload(config.data, material.tan_key).decode() == wifi_payload

    path.chmod(0o644)
    with pytest.raises(BleCodecError, match="owner"):
        load_ble_provisioning_material(
            path, expected_device_id="7443576841", max_age_seconds=60, now=120
        )


def test_qr_is_rendered_as_an_in_memory_svg():
    encoded = render_svg_base64(build_wifi_payload(ssid="Home", password="secret"))
    svg = base64.b64decode(encoded)
    assert b"<svg" in svg
    assert b"secret" not in svg


def test_qr_uses_the_same_high_error_correction_as_the_apk(monkeypatch):
    captured = {}
    real_qr = routes.render_svg_base64.__globals__["qrcode"].QRCode

    def recording_qr(*args, **kwargs):
        captured.update(kwargs)
        return real_qr(*args, **kwargs)

    monkeypatch.setattr(routes.render_svg_base64.__globals__["qrcode"], "QRCode", recording_qr)
    render_svg_base64(build_wifi_payload(ssid="Home", password="secret"))
    assert (
        captured["error_correction"]
        == routes.render_svg_base64.__globals__["qrcode"].constants.ERROR_CORRECT_H
    )


def test_start_returns_experimental_qr_without_leaking_plain_credentials():
    network_id = sign_network("Home Wi-Fi", "WPA2")
    body = routes.ProvisioningStartIn(
        label="http://yoosee.co/?D=0-7443576841-8034",
        wifi_network_id=network_id,
        wifi_password="not-persisted",
    )
    response = Response()
    result = routes.provisioning_start(body, response)
    assert result["status"] == "awaiting_camera_scan"
    assert result["transport"] == "qr"
    assert result["experimental"] is True
    assert result["cloud_token_used"] is False
    assert result["qr"]["mime_type"] == "image/svg+xml"
    assert b"<svg" in base64.b64decode(result["qr"]["data_base64"])
    assert "not-persisted" not in json.dumps(result)
    assert response.headers["cache-control"] == "no-store"


def test_start_fails_closed_for_camera_without_qr_capability():
    body = routes.ProvisioningStartIn(
        device_id="7443576841",
        capability_code="4",
        wifi_network_id=sign_network("Home Wi-Fi", "WPA2"),
        wifi_password="not-persisted",
    )
    with pytest.raises(HTTPException) as caught:
        routes.provisioning_start(body, Response())
    assert caught.value.status_code == 501
    assert "SoftAP is not ready" in caught.value.detail
    assert "not-persisted" not in caught.value.detail


def test_ble_prepare_returns_only_encrypted_wire_frames(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
            }
        )
    )
    material_path.chmod(0o600)
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            provisioning_ble_material_file=material_path,
            provisioning_ble_material_max_age_seconds=1800,
        ),
    )
    body = routes.ProvisioningStartIn(
        label="http://yoosee.co/?D=0-7443576841-8034",
        wifi_network_id=sign_network("Home Wi-Fi", "WPA2"),
        wifi_password="not-returned",
    )
    response = Response()
    result = routes.provisioning_ble_prepare(body, response)
    serialized = json.dumps(result)
    assert result["status"] == "ready_for_explicit_browser_send"
    assert result["expected_responses"] == {
        "challenge": 0x71,
        "wifi_list": 0x81,
        "wifi_config_ack": 0x83,
        "wifi_connection": 0x85,
    }
    assert len(result["attempt_id"]) >= 20
    assert 0 < result["attempt_expires_in"] <= 180
    assert len(result["frames"]["challenge"]) == 1
    assert len(result["frames"]["wifi_config"]) == 1
    assert "not-returned" not in serialized
    assert "bind-secret" not in serialized
    assert "00112233445566778899aabbccddeeff" not in serialized
    assert response.headers["cache-control"] == "no-store"


def test_ble_attempt_pins_one_key_and_expires_independently_of_the_material_file(tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "first-token",
                "serverUserId": 0x81234567,
                "captured_at": 100,
            }
        )
    )
    material_path.chmod(0o600)
    material = load_ble_provisioning_material(
        material_path, expected_device_id="7443576841", max_age_seconds=60, now=120
    )
    attempt = begin_ble_provisioning_attempt(material, now=120)

    # A capture refresh cannot change the key/token of an exchange already in flight.
    material_path.write_text("{}")
    pinned = ble_provisioning_attempt(attempt.attempt_id, expected_device_id="7443576841", now=299)
    assert pinned.material.config_token == "first-token"
    with pytest.raises(BleCodecError, match="expired"):
        ble_provisioning_attempt(attempt.attempt_id, expected_device_id="7443576841", now=300)


def test_ble_response_decoder_uses_server_key_without_returning_it(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    key = "00112233445566778899aabbccddeeff"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": key,
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
            }
        )
    )
    material_path.chmod(0o600)
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            provisioning_ble_material_file=material_path,
            provisioning_ble_material_max_age_seconds=1800,
        ),
    )
    source = b'{"wifiList":[{"ssid":"Camera-visible 2G","level":80}]}'
    body = routes.ProvisioningBleResponseIn(
        label="http://yoosee.co/?D=0-7443576841-8034",
        attempt_id=_start_ble_attempt(material_path),
        command=0x81,
        encrypted=True,
        data_base64=base64.b64encode(encrypt_ble_payload(source, key)).decode(),
    )
    result = routes.provisioning_ble_decode_response(body, Response())
    assert result["json"]["wifiList"][0]["ssid"] == "Camera-visible 2G"
    assert key not in json.dumps(result)
    assert "bind-secret" not in json.dumps(result)


def test_ble_response_decoder_validates_short_random_challenge_echo(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    random_number = "1234567890ABCDEF1232B6F2EF737FF9"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": random_number,
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
            }
        )
    )
    material_path.chmod(0o600)
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            provisioning_ble_material_file=material_path,
            provisioning_ble_material_max_age_seconds=300,
        ),
    )
    body = routes.ProvisioningBleResponseIn(
        label="http://yoosee.co/?D=0-7443576841-8034",
        attempt_id=_start_ble_attempt(material_path),
        command=0x71,
        encrypted=True,
        data_base64=base64.b64encode(b"2B6F2EF737FF9").decode(),
    )
    result = routes.provisioning_ble_decode_response(body, Response())
    assert result["valid"] is True
    assert result["text"] == ""
    assert result["hex"] == ""
    assert random_number not in json.dumps(result)


def test_ble_wifi_ack_never_returns_decrypted_wifi_credentials(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    key = "00112233445566778899aabbccddeeff"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": key,
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
            }
        )
    )
    material_path.chmod(0o600)
    source = b"005Home Wi-Fi10fsuper-secret-password2013088123456740185020bind-secret"
    result = routes.provisioning_ble_decode_response(
        routes.ProvisioningBleResponseIn(
            label="http://yoosee.co/?D=0-7443576841-8034",
            attempt_id=_start_ble_attempt(material_path),
            command=0x83,
            encrypted=True,
            data_base64=base64.b64encode(encrypt_ble_payload(source, key)).decode(),
        ),
        Response(),
    )
    serialized = json.dumps(result)
    assert result["configuration_acknowledged"] is True
    assert result["text"] == ""
    assert result["json"] is None
    assert result["hex"] == ""
    assert "super-secret-password" not in serialized
    assert "bind-secret" not in serialized


def test_ble_wifi_confirmation_does_not_bind_or_expose_privileged_proof(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
            }
        )
    )
    material_path.chmod(0o600)
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            provisioning_ble_material_file=material_path,
            provisioning_ble_material_max_age_seconds=300,
        ),
    )
    body = routes.ProvisioningBleResponseIn(
        label="http://yoosee.co/?D=0-7443576841-8034",
        attempt_id=_start_ble_attempt(material_path),
        command=0x85,
        encrypted=False,
        data_base64=base64.b64encode(b'{"confirmKey":"one-time-proof","connectStatus":0}').decode(),
    )

    result = routes.provisioning_ble_decode_response(body, Response())

    assert result["wifi_connection"] == {
        "connected": True,
        "status": 0,
        "privileged_handoff_advertised": True,
        "privileged_handoff_ready": True,
    }
    assert result["json"] == {"connectStatus": 0}
    assert "one-time-proof" not in json.dumps(result)
    assert "binding" not in result
    status = privileged_enrollment_status("7443576841")
    assert status["handoff_ready"] is True
    assert status["bound"] is False


def test_privileged_binding_is_a_separate_explicit_action(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
                "cloudAuth": {
                    "accessToken": "11" * 64,
                    "common": {"language": "pt", "terminalOS": "2", "accessId": "123"},
                    "headers": {"x-iotvideo-accessid": "123"},
                },
            }
        )
    )
    material_path.chmod(0o600)
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            provisioning_ble_material_file=material_path,
            provisioning_ble_material_max_age_seconds=300,
        ),
    )
    decoded = routes.provisioning_ble_decode_response(
        routes.ProvisioningBleResponseIn(
            label="http://yoosee.co/?D=0-7443576841-8034",
            attempt_id=_start_ble_attempt(material_path),
            command=0x85,
            encrypted=False,
            data_base64=base64.b64encode(
                b'{"confirmKey":"one-time-proof","connectStatus":0}'
            ).decode(),
        ),
        Response(),
    )
    assert decoded["wifi_connection"]["connected"] is True
    called = []

    def fake_bind(item, *, time_area, time_zone):
        called.append((item.device_id, item.confirm_key, time_area, time_zone))
        return VendorBindResult(True, 0, "", SUBSCRIPTION_TOKEN)

    monkeypatch.setattr(routes, "bind_vendor_device", fake_bind)
    response = routes.provisioning_privileged_bind(
        routes.ProvisioningPrivilegedBindIn(
            label="http://yoosee.co/?D=0-7443576841-8034",
            time_area="America/Sao_Paulo",
            time_zone=-10_800,
        ),
        Response(),
    )

    assert called == [("7443576841", "one-time-proof", "America/Sao_Paulo", -10_800)]
    assert response == {
        "device_id": "7443576841",
        "p2p_binding": "bound",
        "subscription_material_ready": True,
        "p2p_session": "pending",
        "rtsp": "pending",
    }
    assert SUBSCRIPTION_TOKEN not in json.dumps(response)
    assert privileged_enrollment_status("7443576841")["bound"] is True
    _clear_privileged_enrollments_for_tests()
    restarted = privileged_enrollment_status("7443576841")
    assert restarted["bound"] is True
    assert restarted["subscription_material_ready"] is True


def test_privileged_binding_without_fresh_handoff_fails_closed():
    with pytest.raises(HTTPException) as caught:
        routes.provisioning_privileged_bind(
            routes.ProvisioningPrivilegedBindIn(
                label="http://yoosee.co/?D=0-7443576841-8034",
            ),
            Response(),
        )
    assert caught.value.status_code == 409
    assert "fresh" in caught.value.detail


def test_privileged_p2p_probe_returns_only_sanitized_inventory(monkeypatch):
    p2p.upsert_enrollment(
        "7443576841",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=SUBSCRIPTION_TOKEN,
    )
    monkeypatch.setattr(
        routes,
        "probe_account_inventory",
        lambda _enrollment: P2PInventory(
            device_id="7443576841",
            authenticated=True,
            device_count=3,
            online_count=3,
            target_visible=True,
            target_online=True,
            target_term_resolved=True,
            skipped_incomplete_nodes=1,
        ),
    )

    result = routes.provisioning_privileged_p2p_probe(
        routes.ProvisioningLabelIn(label="http://yoosee.co/?D=0-7443576841-8034"),
        Response(),
    )

    assert result == {
        "device_id": "7443576841",
        "authenticated": True,
        "device_count": 3,
        "online_count": 3,
        "target_visible": True,
        "target_online": True,
        "target_term_resolved": True,
        "skipped_incomplete_nodes": 1,
        "camera_contacted": False,
    }
    assert SUBSCRIPTION_TOKEN not in json.dumps(result)


def test_privileged_p2p_probe_requires_durable_material():
    with pytest.raises(HTTPException) as caught:
        routes.provisioning_privileged_p2p_probe(
            routes.ProvisioningLabelIn(label="http://yoosee.co/?D=0-7443576841-8034"),
            Response(),
        )
    assert caught.value.status_code == 409
    assert "durable" in caught.value.detail


def test_privileged_p2p_route_probe_returns_no_peer_or_session_secrets(monkeypatch):
    p2p.upsert_enrollment(
        "7443576841",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=SUBSCRIPTION_TOKEN,
    )
    monkeypatch.setattr(
        routes,
        "probe_camera_route",
        lambda _enrollment: P2PRouteProbe(
            device_id="7443576841",
            authenticated=True,
            target_visible=True,
            target_online=True,
            broker_acknowledged=True,
            route_advertised=True,
            direct_datagrams=6,
            direct_handshake=True,
            camera_contacted=True,
            broker_error_code=None,
        ),
    )

    result = routes.provisioning_privileged_p2p_route_probe(
        routes.ProvisioningLabelIn(label="http://yoosee.co/?D=0-7443576841-8034"),
        Response(),
    )

    assert result["direct_handshake"] is True
    assert result["camera_contacted"] is True
    assert result["media_opened"] is False
    assert result["command_sent"] is False
    serialized = json.dumps(result)
    assert SUBSCRIPTION_TOKEN not in serialized
    assert "peer" not in serialized


def test_vendor_bind_wire_contract_includes_proof_without_returning_secrets(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
                "cloudAuth": {
                    "accessToken": "11" * 64,
                    "common": {"language": "pt", "terminalOS": "2", "accessId": "123"},
                    "headers": {
                        "x-iotvideo-accessid": "123",
                        "x-iotvideo-area": "area",
                        "x-iotvideo-appver": "6.36",
                        "x-iotvideo-appid": "app",
                        "x-iotvideo-uniqueid": "terminal",
                    },
                },
            }
        )
    )
    material_path.chmod(0o600)
    material = load_ble_provisioning_material(
        material_path, expected_device_id="7443576841", max_age_seconds=300
    )
    remember_privileged_handoff(material, confirm_key="one-time-proof")
    pending = pending_privileged_enrollment("7443576841")
    captured = {}

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"code": 0, "data": {"devToken": SUBSCRIPTION_TOKEN}}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return Reply()

    monkeypatch.setattr(privileged_module, "urlopen", fake_urlopen)
    result = bind_vendor_device(pending, time_area="America/Sao_Paulo", time_zone=-10_800)

    assert captured["url"].endswith("/openapi/app/user/device/bind")
    assert captured["body"]["devId"] == "7443576841"
    assert captured["body"]["bindToken"] == "bind-secret"
    assert captured["body"]["confirmKey"] == "one-time-proof"
    assert captured["body"]["permission"] == 3
    assert captured["body"]["linkType"] == 1
    assert any(key.lower() == "x-iotvideo-signature" for key in captured["headers"])
    assert result == VendorBindResult(True, 0, "", SUBSCRIPTION_TOKEN)


def test_vendor_online_lookup_and_null_confirm_key_match_apk_fallback(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
                "cloudAuth": {
                    "accessToken": "11" * 64,
                    "common": {"language": "pt", "terminalOS": "2", "accessId": "123"},
                    "headers": {"x-iotvideo-accessid": "123"},
                },
            }
        )
    )
    material_path.chmod(0o600)
    material = load_ble_provisioning_material(
        material_path, expected_device_id="7443576841", max_age_seconds=300
    )
    requests = []

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"code":0,"data":{"status":1,"devId":"7443576841"}}'

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, json.loads(request.data), timeout))
        return Reply()

    monkeypatch.setattr(privileged_module, "urlopen", fake_urlopen)
    result = query_vendor_device_online(material)
    assert result == VendorOnlineResult(True, 0, "", True, False, "7443576841")
    assert requests[0][0].endswith("/openapi/netcfg/cloud/netcfg/devresult")
    assert requests[0][1]["token"] == "bind-secret"

    remember_privileged_handoff(material, confirm_key=None)
    body = json.loads(
        privileged_module._bind_body(
            pending_privileged_enrollment("7443576841"),
            time_area="America/Sao_Paulo",
            time_zone=-10_800,
        )
    )
    assert body["bindToken"] == "bind-secret"
    assert "confirmKey" not in body


def test_online_status_route_retains_null_proof_for_explicit_bind(monkeypatch, tmp_path):
    material_path = tmp_path / "ble.json"
    material_path.write_text(
        json.dumps(
            {
                "device_id": "7443576841",
                "tanKey": "00112233445566778899aabbccddeeff",
                "randNumber": "0123456789abcdef0123456789abcdef",
                "configToken": "bind-secret",
                "serverUserId": 0x81234567,
                "captured_at": int(time.time()),
            }
        )
    )
    material_path.chmod(0o600)
    attempt_id = _start_ble_attempt(material_path)
    monkeypatch.setattr(
        routes,
        "query_vendor_device_online",
        lambda _material: VendorOnlineResult(True, 0, "", True, False, "7443576841"),
    )
    result = routes.provisioning_privileged_online_status(
        routes.ProvisioningOnlineStatusIn(
            label="http://yoosee.co/?D=0-7443576841-8034",
            attempt_id=attempt_id,
        ),
        Response(),
    )
    assert result["online"] is True
    assert result["privileged_handoff_ready"] is True
    assert pending_privileged_enrollment("7443576841").confirm_key is None


def test_wifi_selection_is_signed_and_cannot_be_edited():
    token = sign_network("Home Wi-Fi", "WPA2")
    assert selected_ssid(token) == "Home Wi-Fi"
    assert selected_network(token) == WifiNetwork("Home Wi-Fi", security="WPA2")
    with pytest.raises(ValueError, match="invalid Wi-Fi selection"):
        selected_ssid(token + "tampered")


def test_manual_network_is_validated_before_it_is_signed():
    assert manual_network("  Home Wi-Fi  ", "wpa") == WifiNetwork("Home Wi-Fi", security="WPA/WPA2")
    with pytest.raises(ValueError, match="1 to 32 UTF-8 bytes"):
        manual_network("é" * 17, "wpa")
    with pytest.raises(ValueError, match="unsupported"):
        manual_network("Home", "unknown")


def test_manual_network_api_is_available_only_without_a_scanner(monkeypatch):
    monkeypatch.setattr(routes, "scan_wifi_networks", lambda: ([], "", "no radio"))
    response = Response()
    result = routes.provisioning_manual_network(
        routes.ProvisioningManualNetworkIn(ssid="Home Wi-Fi", security="wpa"), response
    )
    selected = selected_network(result["network"]["id"])
    assert selected == WifiNetwork("Home Wi-Fi", security="WPA/WPA2")
    assert response.headers["cache-control"] == "no-store"

    monkeypatch.setattr(
        routes,
        "scan_wifi_networks",
        lambda: ([WifiNetwork("Detected")], "nmcli", ""),
    )
    with pytest.raises(HTTPException) as caught:
        routes.provisioning_manual_network(
            routes.ProvisioningManualNetworkIn(ssid="Other", security="wpa"), Response()
        )
    assert caught.value.status_code == 409


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
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        status = client.get("/api/provisioning/status")
        assert status.status_code == 200
        assert status.json()["local_only"] is False
        assert status.json()["lan_only"] is True
        assert status.json()["transport_ready"] is True
        assert status.json()["transports"]["qr"] == "experimental-ready"
        inspected = client.post(
            "/api/provisioning/inspect",
            json={"label": "http://yoosee.co/?D=0-7443576841-8034"},
        )
        assert inspected.status_code == 200
        assert inspected.json()["device_id"] == "7443576841"
        started = client.post(
            "/api/provisioning/start",
            json={
                "label": "http://yoosee.co/?D=0-7443576841-8034",
                "wifi_network_id": sign_network("Home Wi-Fi", "WPA2"),
                "wifi_password": "api-not-persisted",
            },
        )
        assert started.status_code == 200
        assert started.headers["cache-control"] == "no-store"
        assert started.json()["status"] == "awaiting_camera_scan"
        assert "api-not-persisted" not in started.text


def test_provisioning_api_accepts_authenticated_private_lan_client():
    with TestClient(app, base_url="http://192.168.1.10", client=("192.168.1.20", 50000)) as client:
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        status = client.get(
            "/api/provisioning/status",
            headers={"origin": "http://192.168.1.10", "sec-fetch-site": "same-origin"},
        )
        assert status.status_code == 200
        assert status.json()["lan_only"] is True


def test_provisioning_api_rejects_authenticated_public_client():
    with TestClient(app, base_url="http://localhost", client=("192.0.2.40", 50000)) as client:
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        response = client.get("/api/provisioning/status")
        assert response.status_code == 403
        assert (
            response.json()["detail"]
            == "provisioning is available only from the authenticated local network"
        )
