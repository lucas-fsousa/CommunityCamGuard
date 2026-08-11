"""Parse the identity printed on a factory-new Yoosee/Gwell camera.

The parser is intentionally independent from the transport.  It lets the UI validate a scanned
label now while SoftAP/BLE/QR transports are implemented behind the same provisioning API.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

_DEVICE_ID = re.compile(r"^[0-9]{6,20}$")
_CAPABILITIES = re.compile(r"^(?:0x)?([0-9a-fA-F]{1,8})$")
_MAC = re.compile(r"^[0-9a-fA-F]{12}$")

CAPABILITY_MODES = {
    0x0001: "smartlink",
    0x0002: "sound",
    0x0004: "softap",
    0x0008: "simpleconfig",
    0x0010: "bluetooth",
    0x0020: "qr",
    0x0040: "server_notify",
    0x8000: "wired",
}


class LabelError(ValueError):
    """The supplied label is incomplete or not a recognised camera identity."""


def _from_qr(label: str) -> tuple[str, str]:
    text = label.strip()
    if not text:
        return "", ""
    query = parse_qs(urlsplit(text).query)
    encoded = (query.get("D") or query.get("d") or [""])[0]
    match = re.fullmatch(r"0-([0-9]{6,20})-([0-9a-fA-F]{1,8})", encoded)
    if not match:
        raise LabelError("unrecognised label QR code")
    return match.group(1), match.group(2)


def _normalise_mac(value: str) -> str:
    compact = re.sub(r"[:-]", "", value.strip())
    if not compact:
        return ""
    if not _MAC.fullmatch(compact):
        raise LabelError("invalid MAC address")
    return ":".join(compact[i : i + 2] for i in range(0, 12, 2)).lower()


def inspect_label(
    *,
    label: str = "",
    device_id: str = "",
    capability_code: str = "",
    firmware_version: str = "",
    mac: str = "",
) -> dict:
    """Return a normalised, non-secret camera identity and advertised setup modes."""
    qr_device, qr_caps = _from_qr(label) if label.strip() else ("", "")
    supplied_device = device_id.strip()
    supplied_caps = capability_code.strip()
    if qr_device and supplied_device and qr_device != supplied_device:
        raise LabelError("device ID does not match the scanned label")
    if qr_caps and supplied_caps:
        left = int(qr_caps, 16)
        match = _CAPABILITIES.fullmatch(supplied_caps)
        if match is None or left != int(match.group(1), 16):
            raise LabelError("capability code does not match the scanned label")

    final_device = qr_device or supplied_device
    if not _DEVICE_ID.fullmatch(final_device):
        raise LabelError("device ID must contain 6 to 20 digits")

    final_caps = qr_caps or supplied_caps
    match = _CAPABILITIES.fullmatch(final_caps)
    if match is None:
        raise LabelError("capability code must be 1 to 8 hexadecimal digits")
    mask = int(match.group(1), 16)
    modes = [name for bit, name in CAPABILITY_MODES.items() if mask & bit]
    if not modes:
        raise LabelError("the label does not advertise a supported setup mode")

    return {
        "device_id": final_device,
        "capability_code": f"{mask:X}",
        "capability_mask": mask,
        "setup_modes": modes,
        "preferred_mode": next(
            (mode for mode in ("qr", "softap", "bluetooth", "wired") if mode in modes),
            modes[0],
        ),
        "firmware_version": firmware_version.strip(),
        "mac": _normalise_mac(mac),
    }
