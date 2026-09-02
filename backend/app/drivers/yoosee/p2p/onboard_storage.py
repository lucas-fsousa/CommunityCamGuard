"""Fail-closed interpretation of Yoosee camera-side storage state.

The IoTVideo SDK being present is not evidence that a particular camera has a usable card.
This module deliberately separates parsing the read-only ``tfInfo`` value from deciding whether
onboard recordings may be advertised for one exact camera.
"""

from __future__ import annotations

from dataclasses import dataclass

TF_INFO_PATH = "ProReadonly.tfInfo"
_WRAPPER_KEYS = ("tfInfo", "ProReadonly", "setVal")


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

    @property
    def present(self) -> bool:
        return self.total_units > 0


def extract_onboard_storage_state(value: object) -> OnboardStorageState | None:
    """Extract one complete and internally consistent ``tfInfo`` object.

    Unknown wrappers, booleans masquerading as integers, negative values and impossible capacity
    relationships are rejected. A zero-capacity response is valid parsed state, but not a present
    or readable card.
    """

    if not isinstance(value, dict):
        return None
    if {"total", "remain", "stat"}.issubset(value):
        total = value.get("total")
        remaining = value.get("remain")
        status = value.get("stat")
        if any(type(item) is not int for item in (total, remaining, status)):
            return None
        if total < 0 or remaining < 0 or remaining > total or status < 0:
            return None
        card_id = value.get("cid")
        if card_id is not None and type(card_id) not in (int, str):
            return None
        return OnboardStorageState(total, remaining, status, card_id)
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
