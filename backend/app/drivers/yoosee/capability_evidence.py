"""Pure per-device evidence rules; enrollment alone is never feature evidence.

These rules interpret model observations, not physical certification. Their output must
be combined with an exact profile/transport validation before advertising a control.
No network or registry access belongs in this module.
"""

from __future__ import annotations

import math
from enum import StrEnum

from .guard_plan import parse_guard_plan


class EvidenceState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


# One fixed allowlist shared by collection and normalization. Never accept arbitrary
# writable roots from a caller. The last root adds speaker-volume evidence only.
CAPABILITY_PATHS = (
    "ProConst._productInfo",
    "ProConst._versionInfo",
    "ProWritable.videoParm",
    "ProWritable.guardParm",
    "ProWritable.volume",
)


def _integer(value: object) -> int | None:
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def enum_property_evidence(
    observation: object,
    *,
    field: str,
    supported_values: frozenset[int],
    unsupported_values: frozenset[int] = frozenset(),
) -> EvidenceState:
    """Read an APK-style timestamped setVal property without assuming missing means off.

    Pass the exact property object, e.g. ProWritable.videoParm, and its selected
    field. Invalid timestamp -1 explicitly denotes unavailable; absent or malformed
    data stays unknown. Zero timestamps are placeholders and provide no evidence.
    """

    if supported_values & unsupported_values:
        raise ValueError("capability evidence value sets overlap")
    if not isinstance(observation, dict):
        return EvidenceState.UNKNOWN
    timestamp = _integer(observation.get("t"))
    if timestamp == -1:
        return EvidenceState.UNSUPPORTED
    if timestamp is None or timestamp <= 0:
        return EvidenceState.UNKNOWN
    values = observation.get("setVal")
    if not isinstance(values, dict):
        return EvidenceState.UNKNOWN
    value = _integer(values.get(field))
    if value in supported_values:
        return EvidenceState.SUPPORTED
    if value in unsupported_values:
        return EvidenceState.UNSUPPORTED
    return EvidenceState.UNKNOWN


def guard_schedule_evidence(observation: object) -> EvidenceState:
    """Require the exact timestamped guard root and a complete setVal.plan.

    Guard enable=0 is not absence of scheduling. Never search alternative nested
    objects for a valid-looking plan when the requested property's plan is invalid.
    """
    if not isinstance(observation, dict):
        return EvidenceState.UNKNOWN
    timestamp = _integer(observation.get("t"))
    if timestamp == -1:
        return EvidenceState.UNSUPPORTED
    if timestamp is None or not 0 < timestamp <= 0x7FFFFFFF:
        return EvidenceState.UNKNOWN
    values = observation.get("setVal")
    if not isinstance(values, dict) or parse_guard_plan(values.get("plan")) is None:
        return EvidenceState.UNKNOWN
    return EvidenceState.SUPPORTED


def speaker_volume_evidence(observation: object) -> EvidenceState:
    """Exact scalar volume property; mute is supported, not an absent speaker.

    Do not recursively search nested fields: a timestamp, unrelated setting or
    legacy envelope must not masquerade as a valid 0..10 raw speaker volume.
    This proves neither talkback nor any particular writable volume option.
    """
    if not isinstance(observation, dict):
        return EvidenceState.UNKNOWN
    timestamp = _integer(observation.get("t"))
    if timestamp == -1:
        return EvidenceState.UNSUPPORTED
    if timestamp is None or not 0 < timestamp <= 0x7FFFFFFF:
        return EvidenceState.UNKNOWN
    raw = observation.get("setVal")
    if type(raw) is not int or not 0 <= raw <= 10:
        return EvidenceState.UNKNOWN
    return EvidenceState.SUPPORTED
