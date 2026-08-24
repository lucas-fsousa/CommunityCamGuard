"""Yoosee/Gwell first-time Wi-Fi QR artifacts recovered from the Android client."""
from __future__ import annotations

import base64
import io
from collections.abc import Mapping

import qrcode
from qrcode.image.svg import SvgPathImage

_CORE_FIELDS = (0, 1, 2, 3, 4, 5)
_ENCRYPTION_VALUES = {"open": "0", "wep": "1", "wpa": "2"}
_ERROR_CORRECTION = qrcode.constants.ERROR_CORRECT_H


def encode_fields(fields: Mapping[int, object | None]) -> str:
    """Encode the modern APK's ordered ``id + UTF-8 hex length + value`` format."""
    unknown = set(fields) - set(_CORE_FIELDS)
    if unknown:
        raise ValueError(f"unsupported QR field id(s): {sorted(unknown)}")
    parts: list[str] = []
    for field_id in _CORE_FIELDS:
        value = "" if fields.get(field_id) is None else str(fields[field_id])
        encoded = value.encode("utf-8")
        if len(encoded) > 255:
            raise ValueError(f"field {field_id} exceeds the one-byte length limit")
        parts.append(f"{field_id}{len(encoded):02x}{value}")
    return "".join(parts)


def build_wifi_payload(
    *,
    ssid: str,
    password: str,
    encryption: str = "wpa",
    user_id: int = 0,
    language: str = "8",
    config_token: str = "",
) -> str:
    """Build ``QRCodeInfo.createQRCodeStr()`` without contacting the vendor cloud."""
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("SSID must contain 1 to 32 UTF-8 bytes")
    if len(password.encode("utf-8")) > 63:
        raise ValueError("Wi-Fi password exceeds 63 UTF-8 bytes")
    try:
        encryption_value = _ENCRYPTION_VALUES[encryption.lower()]
    except KeyError as exc:
        raise ValueError("unsupported Wi-Fi encryption") from exc
    return encode_fields(
        {
            0: ssid,
            1: password,
            2: encryption_value,
            3: format(user_id, "X"),
            4: language,
            5: config_token,
        }
    )


def encryption_from_scan(security: str, password: str) -> str:
    """Translate scanner labels to the APK's OPEN/WEP/WPA enum."""
    normalized = security.strip().lower()
    if "wep" in normalized:
        return "wep"
    if not password and normalized in {"", "--", "open", "none"}:
        return "open"
    return "wpa"


def render_svg_base64(payload: str) -> str:
    """Render an SVG QR in memory and return only transport-safe base64."""
    qr = qrcode.QRCode(
        version=None,
        # The current APK's ``om.a.d`` renderer explicitly uses ZXing level H. Matching it also
        # makes a QR shown on a glossy browser display more tolerant of glare and lens blur.
        error_correction=_ERROR_CORRECTION,
        box_size=10,
        border=4,
        image_factory=SvgPathImage,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    output = io.BytesIO()
    qr.make_image().save(output)
    return base64.b64encode(output.getvalue()).decode("ascii")
