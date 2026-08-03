"""Tests for ONVIF device control (backend/app/control/device.py). The SOAP poster `_post` is
stubbed with canned responses, so these test the parsing/logic offline.
"""
import pytest

from backend.app.control import device
from backend.app.db.registry import Camera

_DEV_INFO = """<soap:Envelope><soap:Body>
  <tds:GetDeviceInformationResponse>
    <tds:Manufacturer>Yoosee</tds:Manufacturer>
    <tds:Model>PTZ-Cam</tds:Model>
    <tds:FirmwareVersion>1.2.3</tds:FirmwareVersion>
    <tds:SerialNumber>SN123</tds:SerialNumber>
    <tds:HardwareId>HW9</tds:HardwareId>
  </tds:GetDeviceInformationResponse></soap:Body></soap:Envelope>"""

_NET_IFACES = """<soap:Envelope><soap:Body><tds:GetNetworkInterfacesResponse>
  <tds:Info><tds:HwAddress>00-00-00-00-00-00</tds:HwAddress></tds:Info>
  <tds:Info><tds:HwAddress>AA-BB-CC-DD-EE-01</tds:HwAddress></tds:Info>
</tds:GetNetworkInterfacesResponse></soap:Body></soap:Envelope>"""


# --- _normalize_mac (pure) ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("AA-BB-CC-DD-EE-01", "aa:bb:cc:dd:ee:01"),
    ("aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:01"),
    ("AABBCCDDEE01", "aa:bb:cc:dd:ee:01"),
])
def test_normalize_mac_accepts_common_formats(raw, expected):
    assert device._normalize_mac(raw) == expected


@pytest.mark.parametrize("raw", ["", "not-a-mac", "AA-BB-CC", "00:00:00:00:00:00", "zzzzzzzzzzzz"])
def test_normalize_mac_rejects_invalid_or_zero(raw):
    assert device._normalize_mac(raw) is None


# --- info ---------------------------------------------------------------------------

def test_info_parses_all_fields(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda *a, **k: (200, _DEV_INFO))
    assert device.info("10.0.0.1") == {
        "manufacturer": "Yoosee", "model": "PTZ-Cam", "firmware": "1.2.3",
        "serial": "SN123", "hardware": "HW9",
    }


def test_info_none_on_non_200(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda *a, **k: (None, ""))
    assert device.info("10.0.0.1") is None


# --- mac_address --------------------------------------------------------------------

def test_mac_address_skips_zero_and_returns_first_valid(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda *a, **k: (200, _NET_IFACES))
    assert device.mac_address("10.0.0.1") == "aa:bb:cc:dd:ee:01"


def test_mac_address_none_on_non_200(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda *a, **k: (500, "err"))
    assert device.mac_address("10.0.0.1") is None


def test_mac_address_none_when_no_valid_address(monkeypatch):
    body = "<x><tds:HwAddress>00-00-00-00-00-00</tds:HwAddress></x>"
    monkeypatch.setattr(device, "_post", lambda *a, **k: (200, body))
    assert device.mac_address("10.0.0.1") is None


# --- supports_reboot / reboot -------------------------------------------------------

def test_supports_reboot_reflects_info(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda *a, **k: (200, _DEV_INFO))
    assert device.supports_reboot("10.0.0.1") is True
    monkeypatch.setattr(device, "_post", lambda *a, **k: (None, ""))
    assert device.supports_reboot("10.0.0.1") is False


def test_reboot_without_ip_is_false():
    assert device.reboot(Camera(mac="aa:bb:cc:00:00:20")) is False


def test_reboot_returns_true_on_200(monkeypatch):
    sent = []
    monkeypatch.setattr(device, "_post", lambda ip, body, **k: sent.append((ip, body)) or (200, ""))
    assert device.reboot(Camera(mac="aa:bb:cc:00:00:20", last_ip="10.0.0.20")) is True
    assert sent[0][0] == "10.0.0.20" and "SystemReboot" in sent[0][1]


def test_reboot_false_on_rejection(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda *a, **k: (500, "no"))
    assert device.reboot(Camera(mac="aa:bb:cc:00:00:20", last_ip="10.0.0.20")) is False
