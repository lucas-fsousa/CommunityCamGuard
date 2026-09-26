"""Untrusted connection replies expose only a bounded integer status."""

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee import ble_onboarding as decoder

SECRET = "SYNTHETIC_REPLY_SECRET"


@pytest.mark.parametrize("status", [0, 1, -1, None, False, True, "0", SECRET, [SECRET],
                                   {SECRET: SECRET}, 2**40])
def test_connection_payload_and_logs_are_allowlisted(monkeypatch, caplog, status):
    monkeypatch.setattr(decoder, "ble_provisioning_attempt", lambda *args, **kwargs:
                        SimpleNamespace(material=SimpleNamespace()))
    retained = []
    monkeypatch.setattr(decoder, "remember_privileged_handoff",
                        lambda material, **kwargs: retained.append(kwargs))
    result = decoder.decode_response(
        device_id="12345678", attempt_id="synthetic", command=0x85, encrypted=False,
        raw=json.dumps({"connectStatus": status, "confirmKey": SECRET,
                        "accessToken": SECRET, SECRET: {"nested": SECRET}}).encode(),
    )
    valid = type(status) is int and -(2**31) <= status < 2**31
    assert result.public_payload == ({"connectStatus": status} if valid else None)
    assert result.wifi_connection["status"] == (status if valid else None)
    assert result.wifi_connection["connected"] is (valid and status == 0)
    assert bool(retained) is (valid and status == 0)
    assert SECRET not in json.dumps(asdict(result))
    assert SECRET not in caplog.text
    assert result.hex_preview == ""


@pytest.mark.parametrize("raw", [SECRET.encode(), json.dumps([SECRET]).encode(), b"null"])
def test_non_object_connection_reply_is_not_reflected(monkeypatch, caplog, raw):
    monkeypatch.setattr(decoder, "ble_provisioning_attempt", lambda *args, **kwargs:
                        SimpleNamespace(material=SimpleNamespace()))
    result = decoder.decode_response(device_id="12345678", attempt_id="synthetic",
                                     command=0x85, encrypted=False, raw=raw)
    assert result.public_payload is None
    assert result.text == result.hex_preview == ""
    assert not result.wifi_connection["connected"]
    assert SECRET not in json.dumps(asdict(result)) + caplog.text
