"""Exact-unit dynamic option enforcement, shared by listing and pre-write resolution."""
from __future__ import annotations

import time

from ...db.p2p import P2PEnrollment
from ..contracts import ControlDescriptor
from . import capability_resource_profiles as profiles
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from .capability_evidence import EvidenceState
from .capability_identity import CapabilityIdentity
from .capability_resources import ResourceObservation, ResourceProfile, certified_resources
from .p2p.alarm_voice import AlarmVoiceResource
from .p2p.alarm_voice_catalog import read_camera_alarm_voice_catalog
from .p2p.alarm_voice_selection import P2PAlarmVoiceWrite, set_camera_alarm_voice_resource
from .p2p.contracts import P2PProbeError
from .p2p.renewal import run_with_fresh_access

KEY = "alarm_voice"


def managed(camera_id: str) -> bool:
    selected = rollout.selected(camera_id)
    return selected is not None and KEY in selected[1]


def descriptor(camera_id: str, identity: CapabilityIdentity, *, now: float,
               evidence: EvidenceState) -> ControlDescriptor | None:
    profile = profiles.load(camera_id=camera_id, identity=identity, now=now)
    if (evidence != EvidenceState.SUPPORTED or profile is None
            or not profile.enumeration_verified or not profile.selections):
        return None
    # This is a proven selection operation, not a cached list of options. Each listing/write
    # still reads a fresh catalogue and intersects it with the resource-specific proofs.
    return ControlDescriptor(KEY, "choice", writable=True, dynamic_options=True)


def _profile(enrollment: P2PEnrollment) -> ResourceProfile:
    camera_id = enrollment.camera_id or ""
    selected = rollout.selected(camera_id)
    if selected is None or KEY not in selected[1] or selected[0].device_id != enrollment.device_id:
        raise P2PProbeError("resource control is not migrated for this camera")
    identity = selected[0]
    now = time.time()
    if snapshots.resolve(camera_id=camera_id, identity=identity, feature=KEY,
                         now=now) != EvidenceState.SUPPORTED:
        raise P2PProbeError("resource capability evidence is unavailable")
    profile = profiles.load(camera_id=camera_id, identity=identity, now=now)
    if profile is None or not profile.enumeration_verified or not profile.selections:
        raise P2PProbeError("resource selection proofs are unavailable")
    return profile


def _fresh_resources(enrollment: P2PEnrollment) -> tuple[AlarmVoiceResource, ...]:
    before = _profile(enrollment)
    catalogue = read_camera_alarm_voice_catalog(enrollment, require_correlated_response=True)
    if catalogue.device_id != enrollment.device_id:
        raise P2PProbeError("resource catalogue device mismatch")
    received_at = time.time()
    profile = _profile(enrollment)  # revocation/expiry during network I/O must take effect
    if profile.identity != before.identity:
        raise P2PProbeError("resource identity changed during collection")
    observation = ResourceObservation(profile.camera_id, profile.identity, received_at,
                                      received_at + 5, catalogue.resources, True, True)
    return certified_resources(observation, profile, camera_id=profile.camera_id,
                               identity=profile.identity, now=time.time())


def options(enrollment: P2PEnrollment) -> tuple[AlarmVoiceResource, ...]:
    return run_with_fresh_access(enrollment, _fresh_resources)


def select(enrollment: P2PEnrollment, key: str) -> P2PAlarmVoiceWrite:
    def operation(access: P2PEnrollment) -> P2PAlarmVoiceWrite:
        resources = _fresh_resources(access)
        resource = next((item for item in resources if item.key == key), None)
        if resource is None:
            raise P2PProbeError("resource is missing, replaced or not certified for this camera")
        return set_camera_alarm_voice_resource(access, resource, require_exact_resource=True)

    # Fresh resolution and selection share the existing per-device mutex. Never hold raw vendor
    # IDs supplied by a client or reuse an earlier dropdown response as a write authority.
    return run_with_fresh_access(enrollment, operation)
