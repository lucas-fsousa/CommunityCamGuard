"""Sanitized driver observations; neither a fresh-cache claim nor an operation grant."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .capability_evidence import EvidenceState, _integer, enum_property_evidence
from .capability_identity import CapabilityIdentity, normalize_identity
from .p2p.contracts import P2PPropertyRead


@dataclass(frozen=True, slots=True)
class PropertyEvidence:
    feature: str
    state: EvidenceState
    property_timestamp: int | None


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    camera_id: str
    identity: CapabilityIdentity
    collected_at: float
    evidence: tuple[PropertyEvidence, ...]


def normalize_snapshot(
    observations: tuple[P2PPropertyRead, ...],
    *,
    camera_id: str,
    device_id: str,
    collected_at: float,
) -> CapabilitySnapshot | None:
    """Normalize one correlated collector batch; never combine separate sessions.

    collected_at is backend receipt time. APK setters write t from the phone's
    clock, so t is neither receipt time nor a safe expiry/order key. Missing or
    failed feature reads remain unknown; invalid identity invalidates the batch.
    """
    if (
        not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id)
        or type(collected_at) not in (int, float)
        or not math.isfinite(collected_at)
        or collected_at <= 0
    ):
        return None
    paths = (
        "ProConst._productInfo",
        "ProConst._versionInfo",
        "ProWritable.videoParm",
        "ProWritable.guardParm",
    )
    if len(observations) > len(paths):
        return None
    reads: dict[str, P2PPropertyRead] = {}
    for read in observations:
        if (
            read.device_id != device_id
            or read.authenticated is not True
            or read.property_path not in paths
            or read.property_path in reads
        ):
            return None
        reads[read.property_path] = read
    if any(path not in reads for path in paths[:2]):
        return None
    identity = normalize_identity(reads[paths[0]], reads[paths[1]], device_id=device_id)
    if identity is None:
        return None
    evidence = []
    # Only APK-proven enum domains. These are property observations, not driver
    # control descriptors; do not infer siren/audio/SD support from these fields.
    for feature, path, field, supported, unsupported in (
        ("night_vision", paths[2], "nightViewMode", frozenset({0, 1, 2}), frozenset()),
        ("cry_detection", paths[3], "cryDetectEn", frozenset({1, 2}), frozenset({0})),
        ("orientation", paths[2], "multiFlip", frozenset({1, 3}), frozenset({-1})),
        ("smart_protection", paths[3], "enable", frozenset({0, 1}), frozenset()),
    ):
        property_read = reads.get(path)
        payload = None
        if (
            property_read is not None
            and type(property_read.error_code) is int
            and property_read.error_code == 0
        ):
            payload = property_read.value
        timestamp = _integer(payload.get("t")) if isinstance(payload, dict) else None
        # APK uses a signed int. Preserve -1 as its unavailable sentinel, not a date.
        if timestamp is None or not -1 <= timestamp <= 0x7FFFFFFF:
            payload, timestamp = None, None
        state = enum_property_evidence(
            payload,
            field=field,
            supported_values=supported,
            unsupported_values=unsupported,
        )
        evidence.append(PropertyEvidence(feature, state, timestamp))
    return CapabilitySnapshot(camera_id, identity, collected_at, tuple(evidence))
