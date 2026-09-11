"""Offline proof intersection for dynamic alarm resources; no runtime grants or I/O.

Enumerating a catalogue does not certify selection of every entry. A semantic key
can be reused for a different vendor resource, so write proofs pin its identity too.
Persistence/collector/catalogue rollout must be connected explicitly before use.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from ...camera_identity import valid_camera_id
from .capability_identity import CapabilityIdentity
from .p2p.alarm_voice import AlarmVoiceResource


@dataclass(frozen=True, slots=True)
class ResourceSelectionProof:
    key: str
    identity_digest: str


@dataclass(frozen=True, slots=True)
class ResourceProfile:
    camera_id: str
    identity: CapabilityIdentity
    enumeration_verified: bool
    selections: tuple[ResourceSelectionProof, ...]


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    camera_id: str
    identity: CapabilityIdentity
    collected_at: float
    expires_at: float
    resources: tuple[AlarmVoiceResource, ...]
    authenticated: bool = False
    complete: bool = False


def resource_identity_digest(resource: AlarmVoiceResource) -> str:
    """Pin the native identity, not its translated label or the audio file's contents."""
    encoded = json.dumps(
        [resource.key, resource.resource_id, resource.audio_format],
        ensure_ascii=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def certified_resources(
    observation: ResourceObservation,
    profile: ResourceProfile | None,
    *,
    camera_id: str,
    identity: CapabilityIdentity,
    now: float,
) -> tuple[AlarmVoiceResource, ...]:
    """Return only currently observed entries with exact-unit selection proofs.

    Missing/expired/future/ambiguous catalogues fail closed. A caller must re-resolve
    this intersection immediately before writing; never treat a UI key as authority.
    This pure function does not enable the current legacy dynamic control gate.
    """
    if (
        profile is None
        or not valid_camera_id(camera_id)
        or profile.enumeration_verified is not True
        or observation.authenticated is not True
        or observation.complete is not True
        or observation.camera_id != camera_id
        or profile.camera_id != camera_id
        or observation.identity != identity
        or profile.identity != identity
        or any(type(v) not in (int, float) or not math.isfinite(v)
               for v in (now, observation.collected_at, observation.expires_at))
        or not 0 < observation.collected_at <= now < observation.expires_at
        or observation.expires_at - observation.collected_at > 86400
        or len(observation.resources) > 200
        or len(profile.selections) > 200
    ):
        return ()
    proofs = {item.key: item.identity_digest for item in profile.selections}
    keys = {item.key for item in observation.resources}
    if len(proofs) != len(profile.selections) or len(keys) != len(observation.resources):
        return ()
    return tuple(
        item for item in observation.resources
        if proofs.get(item.key) == resource_identity_digest(item)
    )
