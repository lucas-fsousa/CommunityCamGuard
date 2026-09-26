"""Optional BLE metadata must not forward arbitrary decoded data."""

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee import ble_onboarding as decoder
from backend.app.drivers.yoosee.ble_public_payload import network_metadata

SECRET = "SYNTHETIC_METADATA_SECRET"


@pytest.mark.parametrize("command,payload,expected", [
    (0x81, {"wifiList": [{"ssid": "Home", "level": 80, "password": SECRET}],
            "accessToken": SECRET}, {"wifiList": [{"ssid": "Home", "level": 80}]}),
    (0x73, {"linkType": 1, "linkTypeName": SECRET, "token": SECRET},
     {"linkType": 1, "linkTypeName": "WIFI"}),
    (0x73, {"linkType": 2, "linkTypeName": SECRET}, {"linkType": 2}),
    (0x73, {"linkType": False}, None),
    (0x73, {"linkType": SECRET}, None),
    (0x81, {"wifiList": SECRET}, None),
    (0x81, [SECRET], None),
])
def test_decoder_projects_json_text_and_hex(monkeypatch, caplog, command, payload, expected):
    monkeypatch.setattr(decoder, "ble_provisioning_attempt", lambda *args, **kwargs:
                        SimpleNamespace(material=SimpleNamespace()))
    result = decoder.decode_response(device_id="12345678", attempt_id="synthetic",
                                     command=command, encrypted=False,
                                     raw=json.dumps(payload).encode())
    assert result.public_payload == expected
    assert (json.loads(result.text) if result.text else None) == expected
    assert result.hex_preview == ""
    assert SECRET not in json.dumps(asdict(result)) + caplog.text


@pytest.mark.parametrize("command", [0x73, 0x81])
@pytest.mark.parametrize("raw", [SECRET.encode(), b"\xff\xfe", b"[" * 1200 + b"]" * 1200])
def test_invalid_payload_never_becomes_raw_text_or_hex(monkeypatch, command, raw):
    monkeypatch.setattr(decoder, "ble_provisioning_attempt", lambda *args, **kwargs:
                        SimpleNamespace(material=SimpleNamespace()))
    result = decoder.decode_response(device_id="12345678", attempt_id="synthetic",
                                     command=command, encrypted=False, raw=raw)
    assert result.public_payload is None
    assert result.text == result.hex_preview == ""


def test_network_metadata_bounds_and_types():
    payload = {"wifiList": [None, {"ssid": ""}, {"ssid": "é" * 17},
                            {"ssid": "\ud800"}, {"ssid": {"token": SECRET}},
                            {"ssid": "Home", "level": True},
                            {"ssid": "é" * 16, "level": -60}]}
    assert network_metadata(0x81, payload) == {"wifiList": [
        {"ssid": "Home"}, {"ssid": "é" * 16, "level": -60},
    ]}
    result = network_metadata(0x81, {"wifiList": [{"ssid": "Home"}] * 150})
    assert len(result["wifiList"]) == 100
