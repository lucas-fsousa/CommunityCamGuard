"""Stable public camera identities decoupled from driver-native identifiers."""

from __future__ import annotations

import hashlib
import re

_NAMESPACE = b"community-cam-guard/camera-id/v1\x00"
_KINDS = frozenset({"mac", "serial", "vendor_device"})


def normalize_identity(kind: str, value: str) -> tuple[str, str]:
    """Normalize one durable hardware/driver identity before deriving its public ID."""

    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in _KINDS:
        raise ValueError("unsupported camera identity kind")
    raw = str(value).strip()
    if normalized_kind == "mac":
        compact = re.sub(r"[:-]", "", raw).lower()
        if re.fullmatch(r"[0-9a-f]{12}", compact) is None:
            raise ValueError("camera MAC must contain exactly 12 hexadecimal digits")
        raw = compact
    elif not raw or len(raw) > 256 or any(ord(char) < 0x20 for char in raw):
        raise ValueError("camera identity value is invalid")
    return normalized_kind, raw


def stable_camera_id(kind: str, value: str) -> str:
    """Return an opaque deterministic ID; driver-native values never become API identifiers."""

    normalized_kind, normalized_value = normalize_identity(kind, value)
    digest = hashlib.sha256(
        _NAMESPACE + normalized_kind.encode("ascii") + b"\x00" + normalized_value.encode("utf-8")
    ).hexdigest()
    return f"cam_{digest[:24]}"


def valid_camera_id(value: str) -> bool:
    return re.fullmatch(r"cam_[0-9a-f]{24}", str(value)) is not None
