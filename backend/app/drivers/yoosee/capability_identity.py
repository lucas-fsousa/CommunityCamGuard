"""Strict normalization of driver-collected ProConst identity, not certification.

Only pass responses from the correlated backend collector. The DTO's authenticated
flag alone cannot prove provenance; never expose this as a client write endpoint.
Identity roots have plain fields, not writable-property setVal/t envelopes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .p2p.contracts import P2PPropertyRead


@dataclass(frozen=True, slots=True)
class CapabilityIdentity:
    device_id: str
    product_id: str
    model: str
    revision: int
    firmware: str
    sdk: str
    hardware: str


def _text(value: object, *, empty: bool = False) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    if value != value.strip() or any(not char.isprintable() for char in value):
        return None
    return value if value or empty else None


def _product_id(value: object) -> str | None:
    # APK declares a string; JSON captures can encode the ID as an integer.
    # Do not coerce floats (precision loss), bools, signs or alternate bases.
    if type(value) is int:
        return str(value) if 0 < value < 1 << 64 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value):
        return value if int(value) < 1 << 64 else None
    return None


def normalize_identity(
    product: P2PPropertyRead,
    version: P2PPropertyRead,
    *,
    device_id: str,
) -> CapabilityIdentity | None:
    """Return unknown (None) for partial, failed or cross-device identity reads.

    No clock is inferred from revisionUtc: it is not the collection time. This
    identity neither enables a feature nor establishes that broker cache is fresh.
    """
    if not re.fullmatch(r"[1-9][0-9]{5,19}", device_id):
        return None
    for response, path in (
        (product, "ProConst._productInfo"),
        (version, "ProConst._versionInfo"),
    ):
        if (
            response.device_id != device_id
            or response.property_path != path
            or response.authenticated is not True
            or type(response.error_code) is not int
            or response.error_code != 0
            or not isinstance(response.value, dict)
        ):
            return None
    product_data, version_data = product.value, version.value
    assert isinstance(product_data, dict) and isinstance(version_data, dict)
    pid = _product_id(product_data.get("productID"))
    model = _text(product_data.get("productModel"))
    revision = product_data.get("revision")
    firmware = _text(version_data.get("swVer"))
    sdk = _text(version_data.get("sdkVer"))
    hardware = _text(version_data.get("hwVer"), empty=True)
    if (
        pid is None
        or model is None
        or firmware is None
        or sdk is None
        or hardware is None
        or type(revision) is not int
        or not 0 <= revision <= 0x7FFFFFFF
    ):
        return None
    return CapabilityIdentity(device_id, pid, model, revision, firmware, sdk, hardware)
