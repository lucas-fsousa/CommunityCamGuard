"""Fail-closed interpretation of Yoosee camera-side storage state.

The IoTVideo SDK being present is not evidence that a particular camera has a usable card.
This module deliberately separates parsing the read-only ``tfInfo`` value from deciding whether
onboard recordings may be advertised for one exact camera.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TF_INFO_PATH = "ProReadonly.tfInfo"
TF_CARD_NOT_INSERTED = 0
TF_CARD_NORMAL = 1
TF_CARD_ERROR = 3
_WRAPPER_KEYS = ("tfInfo", "ProReadonly", "stVal", "setVal")
_MAX_EXACT_JSON_INTEGER = (1 << 53) - 1


@dataclass(frozen=True, slots=True)
class OnboardStorageState:
    """Sanitized native units returned by ``tfInfo``.

    The APK does not establish whether ``total`` and ``remain`` are bytes, KiB or another unit, so
    the driver must not expose a guessed unit through the generic API.
    """

    total_units: int
    remaining_units: int
    status_code: int
    card_id: int | str | None = None
    reported_total_units: int | None = None

    @property
    def present(self) -> bool:
        return self.total_units > 0


def _integral_number(value: object) -> int | None:
    """Accept JSON integers and finite integral doubles without accepting bools."""

    if type(value) is int:
        parsed = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    else:
        return None
    if abs(parsed) > _MAX_EXACT_JSON_INTEGER:
        return None
    return parsed


def extract_onboard_storage_state(value: object) -> OnboardStorageState | None:
    """Extract one complete and internally consistent ``tfInfo`` object.

    Unknown wrappers, booleans masquerading as numbers and impossible capacity relationships are
    rejected. The APK models capacity as JSON ``double``, so finite integral floats are valid.

    Firmware 40.1.14 on the authorized camera 3 reproducibly reports a normal 16 GB card as a
    negative ``total`` alongside a positive, smaller ``remain``. That exact internally consistent
    shape is normalized while retaining the signed value for diagnostics. Other negative shapes
    continue to fail closed. A zero-capacity response is valid state, but not a present card.
    """

    if not isinstance(value, dict):
        return None
    if {"total", "remain", "stat"}.issubset(value):
        reported_total = _integral_number(value.get("total"))
        remaining = _integral_number(value.get("remain"))
        status = _integral_number(value.get("stat"))
        if reported_total is None or remaining is None or status is None:
            return None
        if remaining < 0 or status < 0:
            return None
        if reported_total < 0:
            if status != TF_CARD_NORMAL or remaining > abs(reported_total):
                return None
            total = abs(reported_total)
        else:
            total = reported_total
        if remaining > total:
            return None
        card_id = value.get("cid")
        if card_id is not None and type(card_id) not in (int, str):
            return None
        return OnboardStorageState(
            total,
            remaining,
            status,
            card_id,
            reported_total if reported_total < 0 else None,
        )
    for key in _WRAPPER_KEYS:
        if key in value:
            parsed = extract_onboard_storage_state(value[key])
            if parsed is not None:
                return parsed
    return None


def can_advertise_onboard_recordings(
    state: OnboardStorageState | None,
    *,
    readable_statuses: frozenset[int],
    profile_verified: bool = False,
    playback_probe_verified: bool = False,
) -> bool:
    """Apply the exact-camera capability gate without family-wide assumptions.

    Status codes are firmware-specific and therefore have no built-in optimistic default. A
    caller must supply a status allowlist recovered for the selected profile and also prove that
    profile, or successfully perform a harmless read-only playback listing probe.
    """

    if state is None or not state.present or state.status_code not in readable_statuses:
        return False
    return profile_verified or playback_probe_verified
